"""HTTP API, protocol endpoints and static hosting for the one-page studio.

Studio API
    POST   /api/lessons                    start a lesson (multipart: prompt, options, optional file)
    POST   /api/documents/preview          parse an upload with the MCP document tool, before submitting
    GET    /api/lessons                    the library, newest first
    GET    /api/lessons/{id}               one lesson: request, manifest, artifacts
    GET    /api/lessons/{id}/events        Server-Sent Events: what every agent is doing
    GET    /api/lessons/{id}/report        the structured final report (JSON; ?format=md for Markdown)
    GET    /api/lessons/{id}/metrics       running time, model calls, tokens, cost
    GET    /api/lessons/{id}/trace         the lesson's OpenTelemetry spans
    GET    /api/lessons/{id}/provenance    which agent made each artifact, from what
    GET    /api/lessons/{id}/monitor       the monitoring callbacks' log
    DELETE /api/lessons/{id}               remove a finished lesson
    GET    /api/health                     keys configured, mode, quota, tools, agents, tracing
    GET    /media/{id}/...                 videos, captions, keyframes (range requests)

Protocols
    /a2a/writers-room/                     the CrewAI crew as an A2A agent (JSON-RPC + streaming)
    /a2a/writers-room/.well-known/agent-card.json
    /mcp/                                  the studio's MCP tool server (Streamable HTTP)

    GET    /                               the page (frontend/dist), when built
"""

from __future__ import annotations

import json
import logging
import secrets
import shutil
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from . import metrics, monitoring, observability
from .agents.writers_room_a2a import MOUNT as A2A_MOUNT
from .agents.writers_room_a2a import agent_card
from .agents.writers_room_a2a import build_app as build_a2a_app
from .config import ROOT, get_settings
from .guardrails import screen_document, screen_prompt
from .jobs import TERMINAL, JobManager
from .llm import replay
from .llm.router import get_router
from .schemas import LANGUAGES, LessonRequest
from .tools.client import ToolError, call_tool, list_tools
from .tools.models import DocumentDigest
from .tools.server import mcp

MAX_UPLOAD = 15 * 1024 * 1024
UPLOAD_TYPES = {
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".txt": "text/plain",
    ".md": "text/markdown",
}
NEEDS_KEY = (
    "Making a lesson from an uploaded file needs a Gemini key: the Intake Reader has to read it, and without a key "
    "the studio can only replay its recorded showcase lessons. Add GOOGLE_API_KEY to .env and restart the server."
)

settings = get_settings()
monitoring.configure_logging()
EXPORTERS = observability.setup(settings)
monitoring.install_crewai_listeners()
for noisy in ("mcp", "httpx", "httpcore", "a2a", "mcp.server.streamable_http_manager"):
    logging.getLogger(noisy).setLevel(logging.WARNING)

manager: JobManager | None = None
_tools_cache: list[dict] | None = None
mcp_app = mcp.streamable_http_app()  # also creates mcp.session_manager


@asynccontextmanager
async def lifespan(app: FastAPI):
    global manager
    async with mcp.session_manager.run():
        manager = JobManager(settings)
        manager.start()
        yield


app = FastAPI(title="ConceptFlow AI", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5179", "http://127.0.0.1:5179"],
    allow_methods=["*"],
    allow_headers=["*"],
)
# Protocol endpoints, mounted before the page's catch-all route.
app.mount(A2A_MOUNT, build_a2a_app(settings))
app.mount("/mcp", mcp_app)


@app.api_route("/mcp", methods=["GET", "POST", "DELETE"], include_in_schema=False)
def mcp_without_slash():
    return RedirectResponse("/mcp/", status_code=307)


def _manager() -> JobManager:
    if manager is None:
        raise HTTPException(503, "starting up")
    return manager


def _job(job_id: str):
    job = _manager().get(job_id)
    if job is None:
        raise HTTPException(404, "no such lesson")
    return job


def _jsonl(path: Path, limit: int = 5000) -> list[dict]:
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8").splitlines()[-limit:]
    out = []
    for line in lines:
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


async def _upload(file: UploadFile | None) -> tuple[str, str, bytes] | None:
    if file is None or not file.filename:
        return None
    suffix = Path(file.filename).suffix.lower()
    if suffix not in UPLOAD_TYPES:
        raise HTTPException(415, f"unsupported file type {suffix}; use PDF, PNG, JPG, WEBP, TXT or MD")
    data = await file.read()
    if len(data) > MAX_UPLOAD:
        raise HTTPException(413, "file is larger than 15 MB")
    return file.filename, UPLOAD_TYPES[suffix], data


@app.get("/api/health")
async def health():
    from .agents.workflow import NODES

    global _tools_cache
    if _tools_cache is None:
        _tools_cache = await list_tools()
    card = agent_card(settings)
    return {
        "providers": settings.providers(),
        "mode": settings.llm_mode,
        "quality": settings.default_quality,
        "max_revisions": settings.max_revisions,
        "languages": [{"code": k, "name": v[0]} for k, v in LANGUAGES.items()],
        "showcases": replay.showcases(),
        "usage": get_router().usage(),
        "ladders": settings.ladders,
        "nodes": [{"id": n[0], "label": n[1], "framework": n[2], "role": n[3]} for n in NODES],
        "tools": _tools_cache,
        "mcp": f"{settings.base_url}/mcp/",
        "a2a": {"agent": card.name, "card": f"{settings.base_url}{A2A_MOUNT}/.well-known/agent-card.json", "skills": [s.id for s in card.skills]},
        "observability": {"exporters": EXPORTERS, "langfuse": "langfuse" in EXPORTERS, "langfuse_host": settings.langfuse_host if "langfuse" in EXPORTERS else None},
    }


@app.post("/api/documents/preview")
async def preview_document(file: UploadFile = File(...)):
    """Parse an upload with the MCP document tool so the page can show what was understood."""
    upload = await _upload(file)
    if upload is None:
        raise HTTPException(422, "no file")
    name, mime, data = upload
    safe = "".join(c for c in name if c.isalnum() or c in "._-")[:80] or "upload"
    folder = settings.lessons_dir / "_previews" / secrets.token_hex(6)
    folder.mkdir(parents=True, exist_ok=True)
    try:
        (folder / safe).write_bytes(data)
        if mime.startswith("image/"):
            digest = DocumentDigest(name=safe, kind="image", warnings=["an image has no text layer; the vision model reads it directly"])
        else:
            try:
                digest = await call_tool("read_document", path=str(folder / safe), max_chars=6000)
            except ToolError as exc:
                raise HTTPException(422, f"could not read this file: {exc}") from exc
    finally:
        shutil.rmtree(folder, ignore_errors=True)
    verdict = screen_document("\n".join([digest.title, digest.abstract, digest.excerpt]))
    abstract = screen_document(digest.abstract).text
    return {
        "name": digest.name,
        "kind": digest.kind,
        "pages": digest.pages,
        "title": screen_document(digest.title).text,
        "abstract": abstract[:700],
        "sections": digest.sections[:12],
        "key_terms": digest.key_terms[:10],
        "warnings": digest.warnings,
        "chars": digest.chars_total,
        "guardrail": {"action": verdict.action, "categories": verdict.categories, "reasons": verdict.reasons[:5]},
        "needs_key": settings.llm_mode == "replay",
        "message": NEEDS_KEY if settings.llm_mode == "replay" else None,
    }


@app.post("/api/lessons")
async def create_lesson(
    prompt: str = Form(""),
    audience: str = Form("undergraduate"),
    length: str = Form("standard"),
    language: str = Form("en"),
    voice: str = Form("auto"),
    quality: str = Form(""),
    file: UploadFile | None = File(None),
):
    upload = await _upload(file)
    if not prompt.strip() and upload is None:
        raise HTTPException(422, "describe a concept or upload material to teach from")
    # Input guardrail: injection, abuse and harmful requests are refused; personal data is removed.
    verdict = screen_prompt(prompt)
    if not verdict.allowed:
        monitoring.log("guardrail", "prompt_blocked", "warning", detail=verdict.summary()[:300], categories=verdict.categories)
        raise HTTPException(422, {"message": "The studio's guardrails declined this request.", "categories": verdict.categories, "reasons": verdict.reasons})
    if upload is not None and settings.llm_mode == "replay":
        raise HTTPException(409, {"message": NEEDS_KEY, "categories": ["needs key"], "reasons": []})
    try:
        request = LessonRequest(
            prompt=verdict.text[:2000],
            audience=audience,
            length=length,
            language=language,
            voice=voice,
            quality=quality or settings.default_quality,
            upload_name=upload[0] if upload else None,
            upload_mime=upload[1] if upload else None,
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    job = await _manager().submit(request, upload)
    job.emit({"type": "guardrail", "stage": "prompt", "action": verdict.action, "reasons": verdict.reasons})
    return {"id": job.id, "mode": job.mode, "showcase": job.showcase, "guardrail": verdict.action}


@app.get("/api/lessons")
def list_lessons():
    return _manager().list()


@app.get("/api/lessons/{job_id}")
def get_lesson(job_id: str):
    job = _job(job_id)
    lesson_path = job.dir / "lesson.json"
    artifacts = json.loads(lesson_path.read_text(encoding="utf-8")) if lesson_path.exists() else {}
    code_path = job.dir / "code" / "lesson_final.py"
    return {
        **job.summary(),
        "request": job.request,
        "manifest": job.manifest,
        "artifacts": artifacts,
        "code": code_path.read_text(encoding="utf-8") if code_path.exists() else None,
        "has_report": (job.dir / "report.json").exists(),
    }


@app.get("/api/lessons/{job_id}/events")
async def lesson_events(job_id: str, request: Request):
    job = _job(job_id)

    async def stream():
        async for event in job.stream():
            if await request.is_disconnected():
                break
            if event.get("type") == "ping":
                yield ": ping\n\n"
                continue
            yield f"data: {json.dumps(event, ensure_ascii=False, default=str)}\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/api/lessons/{job_id}/report")
def lesson_report(job_id: str, format: str = "json"):
    job = _job(job_id)
    path = job.dir / ("report.md" if format == "md" else "report.json")
    if not path.exists():
        raise HTTPException(404, "the report is written when the lesson finishes")
    if format == "md":
        filename = f"conceptflow-{job.id}.md"
        return PlainTextResponse(path.read_text(encoding="utf-8"), media_type="text/markdown; charset=utf-8", headers={"Content-Disposition": f'attachment; filename="{filename}"'})
    return JSONResponse(json.loads(path.read_text(encoding="utf-8")))


@app.get("/api/lessons/{job_id}/metrics")
def lesson_metrics(job_id: str):
    job = _job(job_id)
    path = job.dir / "metrics.json"
    if path.exists():
        return JSONResponse(json.loads(path.read_text(encoding="utf-8")))
    if job.status in TERMINAL and (job.dir / "events.jsonl").exists():
        return metrics.write(job.dir, settings)  # lessons made before metrics existed
    return metrics.compute(job.dir, settings)  # a lesson still running: measured so far


@app.get("/api/lessons/{job_id}/trace")
def lesson_trace(job_id: str):
    job = _job(job_id)
    spans = _jsonl(job.dir / "trace.jsonl")
    return {"trace_id": job.trace_id, "exporters": EXPORTERS, "spans": spans}


@app.get("/api/lessons/{job_id}/provenance")
def lesson_provenance(job_id: str):
    return _jsonl(_job(job_id).dir / "provenance.jsonl")


@app.get("/api/lessons/{job_id}/monitor")
def lesson_monitor(job_id: str, limit: int = 800):
    return _jsonl(_job(job_id).dir / "monitor.jsonl", max(1, min(limit, 5000)))


@app.delete("/api/lessons/{job_id}")
def delete_lesson(job_id: str):
    if not _manager().delete(job_id):
        raise HTTPException(409, "only finished lessons can be deleted")
    return {"deleted": job_id}


@app.get("/media/{job_id}/{path:path}")
def media(job_id: str, path: str):
    base = (settings.lessons_dir / job_id).resolve()
    target = (base / path).resolve()
    if not target.is_file() or base not in target.parents:
        raise HTTPException(404, "not found")
    return FileResponse(target)


# ------------------------------------------------------------- the page

DIST = ROOT / "frontend" / "dist"
if DIST.exists():
    app.mount("/assets", StaticFiles(directory=DIST / "assets"), name="assets")

    @app.get("/{path:path}")
    def page(path: str):
        candidate = (DIST / path).resolve()
        if path and candidate.is_file() and DIST.resolve() in candidate.parents:
            return FileResponse(candidate)
        return FileResponse(DIST / "index.html")
else:

    @app.get("/")
    def page_missing():
        return JSONResponse({"message": "API running. Build the page with `npm run build` in frontend/, or use the Vite dev server on :5173."})


def serve_in_thread() -> str:
    """Run the whole studio on a background thread (scripts and tests). Returns its URL.

    The port is ``settings.port``: set PORT before importing conceptflow so that
    the A2A client and the MCP toolsets, which call ``settings.local_url``, find it.
    """
    import threading
    import time

    import uvicorn

    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=settings.port, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    for _ in range(400):
        if server.started:
            break
        time.sleep(0.05)
    if not server.started:
        raise RuntimeError(f"the studio did not start on port {settings.port}")
    return settings.local_url


def main() -> None:
    import uvicorn

    uvicorn.run("conceptflow.server:app", host=settings.host, port=settings.port, log_level="info", proxy_headers=True, forwarded_allow_ips="*")


if __name__ == "__main__":
    main()
