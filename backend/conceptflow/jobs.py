"""Jobs: one lesson, from request to finished film, and its event stream.

A job owns a directory under ``data/lessons/<id>`` holding everything the
agents produced - request, narration audio, every code revision, every
render, the review sheets, the final film - plus ``events.jsonl``, the full
record of what each agent did and said. The page follows a job live over
Server-Sent Events; reopening an old lesson replays the same record.

Lessons run one at a time: rendering is CPU-bound and free-tier model quotas
are shared, so a queue is kinder to both than concurrency.

Each lesson runs inside one OpenTelemetry trace (exported to the lesson's
``trace.jsonl`` and, when configured, to Langfuse), with the monitoring and
guardrail plugins installed on the ADK app, and ends with ``metrics.json`` and
the structured ``report.json`` / ``report.md``.
"""

from __future__ import annotations

import asyncio
import json
import secrets
import shutil
import threading
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import AsyncIterator

from google.adk.apps import App
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types
from opentelemetry.trace import Status, StatusCode

from . import metrics, monitoring, observability, report
from .agents import bus
from .agents.workflow import build_workflow
from .config import Settings
from .llm import replay
from .llm import router as router_module
from .schemas import LessonRequest
from .tools.impl import library

TERMINAL = ("done", "failed")


@dataclass
class Job:
    id: str
    dir: Path
    request: dict
    created: float = field(default_factory=time.time)
    status: str = "queued"
    title: str = ""
    mode: str = "live"
    showcase: str | None = None
    manifest: dict | None = None
    error: str | None = None
    upload: dict | None = None
    trace_id: str | None = None
    events: list[dict] = field(default_factory=list)
    _subscribers: set = field(default_factory=set, repr=False)
    _loop: asyncio.AbstractEventLoop | None = field(default=None, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    # ------------------------------------------------------------ events

    def emit(self, event: dict) -> None:
        """Record an event and push it to live subscribers. Thread-safe."""
        loop = self._loop
        if loop is not None and loop.is_running():
            try:
                running = asyncio.get_running_loop()
            except RuntimeError:
                running = None
            if running is not loop:
                loop.call_soon_threadsafe(self._emit, event)
                return
        self._emit(event)

    def _emit(self, event: dict) -> None:
        with self._lock:
            event = {**event, "seq": len(self.events) + 1, "t": round(time.time() - self.created, 2)}
            self.events.append(event)
            with (self.dir / "events.jsonl").open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(event, ensure_ascii=False, default=str) + "\n")
        for queue in list(self._subscribers):
            queue.put_nowait(event)

    async def stream(self) -> AsyncIterator[dict]:
        queue: asyncio.Queue = asyncio.Queue()
        self._subscribers.add(queue)
        try:
            history = list(self.events)
            for event in history:
                yield event
            last = history[-1]["seq"] if history else 0
            if self.status in TERMINAL:
                return
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=15)
                except asyncio.TimeoutError:
                    yield {"type": "ping"}
                    if self.status in TERMINAL:
                        return
                    continue
                if event["seq"] <= last:
                    continue
                last = event["seq"]
                yield event
                if event["type"] in ("done", "failed"):
                    return
        finally:
            self._subscribers.discard(queue)

    # ------------------------------------------------------------ state

    def update(self, **fields) -> None:
        for key, value in fields.items():
            setattr(self, key, value)
        self.save()

    def summary(self) -> dict:
        manifest = self.manifest or {}
        return {
            "id": self.id,
            "status": self.status,
            "title": self.title or self.request.get("prompt", "")[:80],
            "prompt": self.request.get("prompt", ""),
            "created": self.created,
            "mode": self.mode,
            "showcase": self.showcase,
            "language": self.request.get("language", "en"),
            "poster": manifest.get("poster"),
            "duration": manifest.get("duration"),
            "error": self.error,
            "trace_id": self.trace_id,
        }

    def save(self) -> None:
        data = {**self.summary(), "request": self.request, "manifest": self.manifest, "upload": self.upload}
        (self.dir / "job.json").write_text(json.dumps(data, indent=2, ensure_ascii=False, default=str), encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> "Job":
        data = json.loads((path / "job.json").read_text(encoding="utf-8"))
        job = cls(
            id=data["id"],
            dir=path,
            request=data.get("request", {}),
            created=data.get("created", time.time()),
            status=data.get("status", "failed"),
            title=data.get("title", ""),
            mode=data.get("mode", "live"),
            showcase=data.get("showcase"),
            manifest=data.get("manifest"),
            error=data.get("error"),
            upload=data.get("upload"),
            trace_id=data.get("trace_id"),
        )
        events_path = path / "events.jsonl"
        if events_path.exists():
            for line in events_path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    job.events.append(json.loads(line))
        if job.status not in TERMINAL:
            job.status, job.error = "failed", "interrupted - the server stopped while this lesson was being made"
        return job


class JobManager:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.jobs: dict[str, Job] = {}
        self.queue: asyncio.Queue[Job] = asyncio.Queue()
        self.session_service = InMemorySessionService()
        # The workflow is the app's root node; the plugins see every agent, model and tool call in it.
        self.app = App(
            name="conceptflow",
            root_agent=build_workflow(),
            plugins=[monitoring.MonitoringPlugin(), monitoring.GuardrailPlugin()],
        )
        self.runner = Runner(app=self.app, session_service=self.session_service)
        self._worker: asyncio.Task | None = None
        for path in sorted(settings.lessons_dir.glob("*/job.json")):
            try:
                job = Job.load(path.parent)
                self.jobs[job.id] = job
            except (OSError, ValueError, KeyError):
                continue

    def start(self) -> None:
        if self._worker is None:
            self._worker = asyncio.create_task(self._work())

    def list(self) -> list[dict]:
        return [j.summary() for j in sorted(self.jobs.values(), key=lambda j: j.created, reverse=True)]

    def get(self, job_id: str) -> Job | None:
        return self.jobs.get(job_id)

    def delete(self, job_id: str) -> bool:
        job = self.jobs.get(job_id)
        if job is None or job.status not in TERMINAL:
            return False
        shutil.rmtree(job.dir, ignore_errors=True)
        del self.jobs[job_id]
        try:
            library().remove(job_id)
        except Exception:  # noqa: BLE001 - the index is rebuilt at start-up anyway
            pass
        return True

    async def submit(self, request: LessonRequest, upload: tuple[str, str, bytes] | None) -> Job:
        job_id = time.strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(3)
        job_dir = self.settings.lessons_dir / job_id
        job_dir.mkdir(parents=True, exist_ok=True)
        job = Job(id=job_id, dir=job_dir, request=request.model_dump(), mode=self.settings.llm_mode)
        job._loop = asyncio.get_running_loop()
        if upload is not None:
            name, mime, data = upload
            safe = "".join(c for c in name if c.isalnum() or c in "._-")[:80] or "upload"
            path = job_dir / "upload" / safe
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            kind = "text" if mime.startswith("text/") or safe.lower().endswith((".txt", ".md")) else "binary"
            job.upload = {"name": safe, "mime": mime, "path": str(path), "kind": kind}
        if job.mode == "replay":
            job.showcase = replay.choose(request.prompt)
        job.save()
        self.jobs[job_id] = job
        position = self.queue.qsize() + (1 if any(j.status == "running" for j in self.jobs.values()) else 0)
        job.emit({"type": "queued", "position": position, "mode": job.mode, "showcase": job.showcase})
        await self.queue.put(job)
        return job

    async def _work(self) -> None:
        while True:
            job = await self.queue.get()
            try:
                await self._run(job)
            except Exception:  # never let one lesson kill the worker
                traceback.print_exc()

    async def _run(self, job: Job) -> None:
        bus.bind(job)
        router_module.reporter.set(job.emit)
        log_token = monitoring.bind(monitoring.RunLog(job.dir, job.emit))
        script = None
        if job.mode == "replay" and job.showcase:
            script = replay.load(job.showcase)
        replay.current.set(script)

        request = LessonRequest.model_validate(job.request)
        state: dict = {}
        final: dict | None = None
        failure: str | None = None
        attributes = {
            "langfuse.trace.name": "conceptflow.lesson",
            "langfuse.session.id": job.id,
            "langfuse.user.id": "studio",
            "langfuse.trace.tags": ["conceptflow", job.mode],
            "langfuse.trace.input": json.dumps(
                {"prompt": request.prompt, "upload": request.upload_name, "audience": request.audience, "length": request.length, "language": request.language}
            ),
            "langfuse.trace.metadata.mode": job.mode,
            "conceptflow.job_id": job.id,
            "conceptflow.showcase": job.showcase,
        }
        try:
            with observability.span("lesson", **attributes) as root:
                observability.bind_trace(job.dir)
                trace_id, _ = observability.ids()
                job.update(status="running", trace_id=trace_id or None)
                job.emit({"type": "started", "mode": job.mode, "showcase": job.showcase, "replay_title": script.title if script else None, "trace_id": trace_id})
                monitoring.log("job", "start", mode=job.mode, showcase=job.showcase, prompt=request.prompt[:200], upload=request.upload_name)
                initial = {"request": job.request, "upload": job.upload, "round": 0, "job_id": job.id}
                session = await self.session_service.create_session(app_name="conceptflow", user_id="studio", state=initial)
                message = types.Content(role="user", parts=[types.Part(text=request.prompt or "Teach the uploaded material.")])
                try:
                    async for event in self.runner.run_async(user_id="studio", session_id=session.id, new_message=message):
                        route = event.actions.route if event.actions else None
                        if route is not None:
                            job.emit({"type": "route", "node": event.node_info.path, "route": route})
                    session = await self.session_service.get_session(app_name="conceptflow", user_id="studio", session_id=session.id)
                    state = dict(session.state)
                    final = state.get("final")
                    if not final:
                        raise RuntimeError("the workflow finished without producing a lesson")
                    self._persist(job, state)
                    root.set_attribute("langfuse.trace.output", json.dumps({"title": final.get("title"), "video": final.get("video"), "duration": final.get("duration")}))
                except Exception as exc:  # noqa: BLE001 - reported to the page
                    traceback.print_exc()
                    failure = str(exc) or type(exc).__name__
                    root.record_exception(exc)
                    root.set_status(Status(StatusCode.ERROR, failure[:300]))
                    monitoring.log("job", "failed", "error", error=failure[:500])
                finally:
                    await self.session_service.delete_session(app_name="conceptflow", user_id="studio", session_id=session.id)
            # The root span has ended and been written: measure the run, then announce the end.
            outcome = "done" if failure is None else "failed"
            measured = self._measure(job, state, final, outcome)
            if failure is None and final is not None:
                job.update(status="done", manifest=final, title=final.get("title") or job.title)
                job.emit({"type": "done", "manifest": final, "metrics": measured})
            else:
                job.update(status="failed", error=failure)
                job.emit({"type": "failed", "message": failure, "metrics": measured})
        finally:
            monitoring.unbind(log_token)

    def _measure(self, job: Job, state: dict, final: dict | None, outcome: str) -> dict:
        """metrics.json, report.json/.md and the library index; returns the headline numbers."""
        try:
            data = metrics.write(job.dir, self.settings, status=outcome, manifest=final or {})
        except Exception:  # noqa: BLE001 - measuring must never sink a finished lesson
            traceback.print_exc()
            return {}
        if outcome == "done" and final:
            job.manifest = final
            try:
                report.write(job, state, data)
            except Exception:  # noqa: BLE001
                traceback.print_exc()
            try:
                library().upsert(job.id, final.get("title", ""), job.request.get("prompt", ""), final.get("logline", ""), job.created, final.get("duration"))
            except Exception:  # noqa: BLE001
                traceback.print_exc()
        llm = data.get("llm", {})
        return {
            "wall_seconds": data.get("wall_seconds"),
            "llm_calls": llm.get("calls"),
            "tokens": (llm.get("tokens_in") or 0) + (llm.get("tokens_out") or 0),
            "tool_calls": data.get("tools", {}).get("calls"),
            "paid_tier_usd": data.get("cost", {}).get("paid_tier_total_usd"),
        }

    @staticmethod
    def _persist(job: Job, state: dict) -> None:
        keep = ("analysis", "plan", "storyboard", "reviews", "brief")
        lesson = {k: state.get(k) for k in keep}
        narration = state.get("narration") or {}
        lesson["narration"] = {
            "provider": narration.get("provider"),
            "voice": narration.get("voice"),
            "language": narration.get("language"),
            "lines": {k: v.get("text") for k, v in (narration.get("beats") or {}).items()},
        }
        (job.dir / "lesson.json").write_text(json.dumps(lesson, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
