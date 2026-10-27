"""Production: every workflow node after (and before) the writers' room.

ADK-native agents live here - ``LlmAgent``s for the Manim coder, the two
critics, the refiner and the upload reader, all on the shared model router -
alongside the deterministic nodes that do the physical work: intake (document
parsing and library lookup over MCP), narration, formula checks (SymPy over
MCP), rendering, the verdict that routes the correction cycle, and the final cut.

Hand-offs between nodes go through ADK session state; each one is also
emitted to the page as a message from one agent to the next, so the
conversation that produced a lesson is visible while it happens, and recorded
in the provenance log with the hashes of the artifacts it was made from.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from google.adk.agents import LlmAgent
from google.adk.agents.context import Context
from google.adk.agents.readonly_context import ReadonlyContext
from google.adk.models.llm_request import LlmRequest
from google.genai import types

from .. import monitoring, observability
from ..config import get_settings
from ..guardrails import dangerous_tex, screen_document, screen_narration
from ..llm import replay
from ..llm.adk_model import RoutedGemini
from ..llm.router import get_router
from ..render import contact
from ..render.assemble import Clip, assemble
from ..render.runner import SceneRenderer, media_url
from ..render.sandbox import check, extract_code
from ..schemas import (
    ConceptBrief,
    LessonPlan,
    LessonRequest,
    PedagogyReview,
    Storyboard,
    VisualReview,
)
from ..tools.client import ToolError, call_tool
from ..tools.models import DocumentDigest, MathCheck
from ..voice.narrator import Narrator
from .bus import current_job
from .fallback import with_fallbacks
from .writers_room import AUDIENCES

PROMPTS = Path(__file__).resolve().parents[1] / "prompts"
READER_MAX_PAGES = 40  # longer PDFs reach the reader as parsed text rather than as the file
MAX_FORMULA_CHECKS = 10


def _prompt(name: str) -> str:
    return (PROMPTS / f"{name}.md").read_text(encoding="utf-8")


def _board(state) -> Storyboard:
    return Storyboard.model_validate(state["storyboard"])


def _request(state) -> LessonRequest:
    return LessonRequest.model_validate(state["request"])


def _as_dict(value: Any) -> dict:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        text = value.strip()
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end != -1:
            return json.loads(text[start : end + 1])
    if hasattr(value, "model_dump"):
        return value.model_dump()
    raise ValueError("expected a JSON object")


def _model_for(role: str) -> str:
    """The model that served ``role``'s latest call, for provenance records."""
    script = replay.current.get()
    if script is not None:
        return f"replay:{script.slug}"
    return get_router().last.get(role, "")


# ------------------------------------------------------ agent lifecycle hooks


def _lifecycle(agent_id: str):
    """before/after callbacks that announce an LlmAgent to the page."""

    def before(callback_context) -> None:
        current_job().emit({"type": "agent", "agent": agent_id, "status": "start", "framework": "adk"})

    def after(callback_context) -> None:
        current_job().emit({"type": "agent", "agent": agent_id, "status": "done", "framework": "adk"})

    return before, after


# ----------------------------------------------------------------- 0. intake


async def _read_upload(job, upload: dict) -> tuple[DocumentDigest, bool]:
    """Parse an upload with the MCP document tool, then screen what it says.

    Returns the digest and whether the guardrail found instruction-like text in
    it - a flagged document reaches the model only as sanitised text, never as
    the raw file.
    """
    try:
        digest = await call_tool("read_document", emit=job.emit, path=upload["path"], max_chars=20000)
    except ToolError as exc:
        monitoring.log("mcp", "document_unreadable", "warning", agent="intake", detail=str(exc)[:300])
        digest = DocumentDigest(name=upload["name"], kind="unknown", warnings=["the document parser could not read this file"])
    flagged = False
    reasons: list[str] = []
    for name in ("title", "abstract", "excerpt"):
        verdict = screen_document(getattr(digest, name))
        if verdict.action == "sanitize":
            setattr(digest, name, verdict.text)
            reasons += verdict.reasons
            flagged = flagged or "indirect prompt injection" in verdict.categories
    if reasons:
        job.emit({"type": "guardrail", "stage": "document", "action": "sanitize", "reasons": reasons[:6]})
        monitoring.log("guardrail", "document_sanitized", "warning", agent="intake", detail="; ".join(reasons)[:400])
    observability.record_artifact(job.dir, "document", digest.model_dump(), "read_document", "mcp", note=upload["name"])
    job.emit(
        {
            "type": "artifact",
            "name": "document",
            "data": {
                "name": digest.name,
                "kind": digest.kind,
                "pages": digest.pages,
                "title": digest.title,
                "abstract": digest.abstract[:700],
                "sections": digest.sections[:14],
                "key_terms": digest.key_terms,
                "warnings": digest.warnings,
                "flagged": flagged,
            },
        }
    )
    return digest, flagged


async def intake(ctx: Context, node_input: Any):
    """Gate: parse uploads, look in the library, route.

    Two MCP tools run here, for every lesson: the document parser on any
    upload, and the library search for lessons already made on the topic.
    """
    job = current_job()
    request = _request(ctx.state)
    upload = ctx.state.get("upload")
    job.emit({"type": "agent", "agent": "intake", "status": "start", "framework": "adk"})

    document: DocumentDigest | None = None
    if upload:
        document, flagged = await _read_upload(job, upload)
        ctx.state["document"] = document.model_dump()
        ctx.state["document_flagged"] = flagged

    topic = request.prompt or (document.title if document is not None else "")
    related: list[dict] = []
    if topic:
        try:
            found = await call_tool("search_lessons", emit=job.emit, query=topic[:200], limit=3)
            related = [hit.model_dump() for hit in found.hits if hit.id != job.id]
        except ToolError as exc:
            monitoring.log("mcp", "library_unavailable", "warning", agent="intake", detail=str(exc)[:200])
    if related:
        ctx.state["related"] = related
        job.emit({"type": "artifact", "name": "related", "data": {"hits": related}})
        titles = "; ".join(f"“{hit['title']}”" for hit in related)
        job.emit({"type": "message", "from": "intake", "to": "viewer", "text": f"The library already has {titles}. Making a new lesson as asked."})

    if upload and replay.current.get() is None:
        ctx.route = "upload"
        what = f"“{document.title}”, {document.pages} page(s)" if document is not None and document.title else upload["name"]
        job.emit({"type": "message", "from": "intake", "to": "intake_reader", "text": f"Parsed {what}. Read it and distil the concept to teach."})
    else:
        ctx.route = "direct"
        if upload:
            # Replay mode has recorded replies only - no model can read a new file.
            job.emit({"type": "log", "agent": "intake", "level": "warning", "text": "Replay mode cannot read uploads: the recorded showcase plays instead. Add GOOGLE_API_KEY to teach from your file."})
        job.emit({"type": "message", "from": "intake", "to": "concept_analyst", "text": f"New request: {(request.prompt or topic)[:220]}"})
    job.emit({"type": "agent", "agent": "intake", "status": "done", "framework": "adk"})
    return {"route": ctx.route, "document": document.title if document is not None else None, "related": len(related)}


def _add_to_user_turn(llm_request: LlmRequest, parts: list[types.Part]) -> None:
    """Attach parts to the request's final user turn (or start one)."""
    last = llm_request.contents[-1] if llm_request.contents else None
    if last is not None and last.role == "user":
        last.parts = [*(last.parts or []), *parts]
    else:
        llm_request.contents.append(types.Content(role="user", parts=parts))


def _reader_instruction(rctx: ReadonlyContext) -> str:
    request = LessonRequest.model_validate(rctx.state["request"])
    return (
        _prompt("reader")
        + f"\n\nThe learner's request: {request.prompt or '(none - choose the central idea)'}"
        + f"\nThe learner: {AUDIENCES[request.audience]}."
    )


def _digest_text(document: DocumentDigest, with_text: bool) -> str:
    rows = [f"The document parser (MCP tool read_document) read {document.name}: {document.kind}, {document.pages} page(s)."]
    if document.title:
        rows.append(f"Title: {document.title}")
    if document.abstract:
        rows.append(f"Abstract: {document.abstract}")
    if document.sections:
        rows.append("Sections: " + "; ".join(document.sections[:20]))
    if document.key_terms:
        rows.append("Frequent terms: " + ", ".join(document.key_terms))
    if with_text and document.excerpt:
        rows.append(f"\nText{' (the beginning)' if document.truncated else ''}:\n{document.excerpt[:15000]}")
    return "\n".join(rows)


async def _attach_upload(callback_context, llm_request: LlmRequest):
    """Give the reader the parser's digest, and the file itself when it is safe and small enough."""
    state = callback_context.state
    upload = state.get("upload")
    if not upload:
        return None
    document = DocumentDigest.model_validate(state["document"]) if state.get("document") else None
    is_image = upload["mime"].startswith("image/")
    send_file = (
        upload["kind"] == "binary"
        and not state.get("document_flagged")
        and (is_image or document is None or document.pages <= READER_MAX_PAGES)
    )
    parts: list[types.Part] = []
    if document is not None and document.kind in ("pdf", "text"):
        parts.append(types.Part(text=_digest_text(document, with_text=not send_file)))
    elif upload["kind"] == "text":
        text = Path(upload["path"]).read_text(encoding="utf-8", errors="replace")[:30000]
        parts.append(types.Part(text="Uploaded material:\n\n" + screen_document(text).text))
    if send_file:
        parts.append(types.Part(inline_data=types.Blob(mime_type=upload["mime"], data=Path(upload["path"]).read_bytes())))
    if parts:
        _add_to_user_turn(llm_request, parts)
    return None


def _reader_done(callback_context) -> None:
    job = current_job()
    job.emit({"type": "agent", "agent": "intake_reader", "status": "done", "framework": "adk"})
    brief = callback_context.state.get("brief")
    if brief:
        data = _as_dict(brief)
        inputs = {"document": observability.digest(callback_context.state["document"])} if callback_context.state.get("document") else {}
        observability.record_artifact(job.dir, "brief", data, "intake_reader", "adk", _model_for("reader"), inputs)
        job.emit({"type": "artifact", "name": "brief", "data": data})
        job.emit({"type": "message", "from": "intake_reader", "to": "concept_analyst", "text": f"From {data.get('source_summary', 'the upload')}: teach {data.get('concept')}"})


# ---------------------------------------------------------------- narration


async def narrator(ctx: Context, node_input: Any):
    job = current_job()
    job.emit({"type": "agent", "agent": "narrator", "status": "start", "framework": "adk"})
    board = _board(ctx.state)
    # Output guardrail: nothing abusive or harmful is ever voiced.
    verdict = screen_narration([b.narration for b in board.beats()])
    if not verdict.allowed:
        monitoring.log("guardrail", "narration_blocked", "error", agent="narrator", detail=verdict.summary()[:300])
        job.emit({"type": "guardrail", "stage": "narration", "action": "block", "reasons": verdict.reasons[:6]})
        raise RuntimeError(f"the narration failed the output guardrail ({verdict.summary()})")
    monitoring.log("guardrail", "narration_screened", stage="narration", passed=True, lines=len(board.beats()))
    result = await Narrator(get_settings(), job.dir, job.emit).narrate(board, _request(ctx.state))
    ctx.state["narration"] = result.as_state()
    observability.record_artifact(
        job.dir,
        "narration",
        {k: v["text"] for k, v in result.beats.items()},
        "narrator",
        "adk",
        f"{result.provider}:{result.voice}" if result.voice else result.provider,
        {"storyboard": observability.digest(ctx.state["storyboard"])},
    )
    total = sum(b["duration"] for b in result.beats.values())
    voice = f"{result.provider}" + (f" · {result.voice}" if result.voice else "")
    job.emit({"type": "artifact", "name": "narration", "data": {"provider": result.provider, "voice": result.voice, "language": result.language, "seconds": round(total, 1), "lines": {k: v["text"] for k, v in result.beats.items()}}})
    job.emit({"type": "message", "from": "narrator", "to": "renderer", "text": f"{len(result.beats)} lines recorded ({voice}), {total:.0f}s in all. Beat lengths are in timing.json."})
    job.emit({"type": "agent", "agent": "narrator", "status": "done", "framework": "adk"})
    return {"seconds": round(total, 1), "provider": result.provider}


# --------------------------------------------------------------- the coder


def _estimated(text: str) -> float:
    return round(max(1.6, len(text.split()) / 2.5 + 0.4), 1)


def _storyboard_for_code(board: Storyboard) -> str:
    rows = [f"Title: {board.title}", f"Colour story: {board.color_story}", ""]
    for scene in board.scenes:
        rows.append(f"## Scene {scene.id} -> class {scene.id.upper()}: {scene.title}")
        rows.append(f"Purpose: {scene.purpose}")
        for beat in scene.beats:
            rows.append(f"- beat {beat.id} (~{_estimated(beat.narration)}s)")
            rows.append(f"  narration: {beat.narration}")
            rows.append(f"  visual: {beat.visual}")
        rows.append("")
    return "\n".join(rows)


def _coder_instruction(rctx: ReadonlyContext) -> str:
    board = Storyboard.model_validate(rctx.state["storyboard"])
    return _prompt("coder") + "\n\n# The storyboard to animate\n\n" + _storyboard_for_code(board)


def _coder_done(callback_context) -> None:
    job = current_job()
    code = extract_code(str(callback_context.state.get("code_draft", "")))
    classes = re.findall(r"^class (\w+)\(", code, re.M)
    observability.record_artifact(
        job.dir, "code_round0", code, "manim_coder", "adk", _model_for("coder"),
        {"storyboard": observability.digest(callback_context.state["storyboard"])},
    )
    job.emit({"type": "artifact", "name": "code", "data": {"round": 0, "code": code}})
    job.emit({"type": "message", "from": "manim_coder", "to": "renderer", "text": f"{len(code.splitlines())} lines of Manim: {', '.join(classes)}. Please render."})
    job.emit({"type": "agent", "agent": "manim_coder", "status": "done", "framework": "adk"})


# ------------------------------------------------------------------ render


async def render(ctx: Context, node_input: Any):
    job = current_job()
    settings = get_settings()
    board = _board(ctx.state)
    round_no = int(ctx.state.get("round", 0)) + 1
    ctx.state["round"] = round_no
    job.emit({"type": "agent", "agent": "renderer", "status": "start", "framework": "adk", "round": round_no})

    scene_ids = [s.id for s in board.scenes]
    beat_ids = {s.id: [b.id for b in s.beats] for s in board.scenes}
    code = extract_code(str(ctx.state.get("code_draft", "")))
    verdict = check(code, scene_ids, beat_ids)
    security = [p for p in verdict.problems if "not allowed" in p or p.startswith("SyntaxError")]
    monitoring.log(
        "guardrail", "code_sandbox", "warning" if security else "info", stage="code sandbox", passed=not security,
        round=round_no, problems=len(verdict.problems), detail="; ".join(security)[:300] or None,
    )

    report: dict[str, Any] = {"round": round_no, "problems": verdict.problems, "scenes": {}, "sheets": [], "layout": []}
    if not security:
        renderable = [sid for sid in scene_ids if sid.upper() in verdict.scenes]
        results = await SceneRenderer(settings, job.dir, job.emit).render_all(code, renderable, beat_ids, "preview", f"r{round_no}")
        report["scenes"] = {sid: r.to_dict() for sid, r in results.items()}

    failed = [sid for sid in scene_ids if not report["scenes"].get(sid, {}).get("ok")]
    broken = bool(failed) or bool(verdict.problems)
    if broken:
        route = "exhausted" if round_no > settings.max_revisions else "broken"
    else:
        route = "rendered"
        frames, layout = [], []
        for sid in scene_ids:
            result = report["scenes"][sid]
            for beat in (result.get("timeline") or {}).get("beats", []):
                note = ", ".join(sorted({i["kind"] for i in beat.get("issues", [])}))
                if beat.get("overrun", 0) > 0.6:
                    note = (note + ", " if note else "") + f"overran {beat['overrun']:.1f}s"
                if beat.get("keyframe"):
                    frames.append((beat["id"], Path(result["out_dir"]) / "keyframes" / beat["keyframe"], note))
                for issue in beat.get("issues", []):
                    layout.append({"beat": beat["id"], **issue})
                if beat.get("overrun", 0) > 0.6:
                    layout.append({"beat": beat["id"], "kind": "overrun", "by": beat["overrun"]})
        sheets = contact.build(frames, job.dir / "review" / f"round{round_no}", "contact")
        report["sheets"] = [str(p) for p in sheets]
        report["layout"] = layout
        job.emit({"type": "artifact", "name": "contact", "data": {"round": round_no, "sheets": [media_url(job.dir, p) for p in sheets]}})

    ctx.state["render"] = report
    ctx.route = route
    if route == "rendered":
        text = f"Round {round_no}: all {len(scene_ids)} scenes render. {len(report['layout'])} layout notes. Over to the review panel."
        to = "review_panel"
    elif route == "broken":
        text = f"Round {round_no}: {len(failed)} scene(s) failed ({', '.join(failed) or 'structure'}); {len(verdict.problems)} structural problem(s). Sending the errors to the refiner."
        to = "refiner"
    else:
        text = f"Round {round_no}: {', '.join(failed) or 'some scenes'} still failing and no rounds left - the editor will use safe fallbacks."
        to = "editor"
    job.emit({"type": "message", "from": "renderer", "to": to, "text": text})
    job.emit({"type": "agent", "agent": "renderer", "status": "done", "framework": "adk", "round": round_no})
    return {"route": route, "round": round_no, "failed": failed}


# ------------------------------------------------------------------ critics


def _visual_instruction(rctx: ReadonlyContext) -> str:
    board = Storyboard.model_validate(rctx.state["storyboard"])
    report = rctx.state.get("render") or {}
    rows = [_prompt("visual_critic"), "", "# What each beat should show"]
    for scene in board.scenes:
        rows.append(f"## {scene.id}: {scene.title}")
        rows += [f"- {b.id}: {b.visual}" for b in scene.beats]
    rows.append("\n# Automatic layout findings")
    layout = report.get("layout") or []
    rows += [f"- {i.get('beat')}: {i.get('kind')} {i.get('what', i.get('detail', ''))} {i.get('by', '')}".rstrip() for i in layout] or ["- none"]
    rows.append("\nThe contact sheets follow as images.")
    return "\n".join(rows)


async def _attach_sheets(callback_context, llm_request: LlmRequest):
    report = callback_context.state.get("render") or {}
    parts = []
    for sheet in report.get("sheets", []):
        path = Path(sheet)
        if path.exists():
            parts.append(types.Part(inline_data=types.Blob(mime_type="image/jpeg", data=path.read_bytes())))
    if parts:
        _add_to_user_turn(llm_request, [types.Part(text="Contact sheets:"), *parts])
    return None


def _formulas(code: str) -> list[str]:
    found = re.findall(r'(?:Eq|MathTex|Tex|safe_eq)\(\s*((?:r?"[^"\n]*"\s*,?\s*)+)', code)
    out = []
    for group in found:
        pieces = re.findall(r'r?"([^"\n]*)"', group)
        joined = " ".join(pieces).strip()
        if joined and joined not in out:
            out.append(joined)
    return out[:60]


# ----------------------------------------------------------- formula checks


def _derivation(formulas: list[str]) -> list[str]:
    """Checkable statements from formulas in reading order.

    A piece that starts with "=" continues a derivation, so it is checked
    against the step before it: "= (2xh + h^2)/h" then "= 2x + h" becomes
    "(2xh + h^2)/h = 2x + h". Labels in words (\\text{...}) and dangling
    pieces ("h =") cannot be checked symbolically and are left out.
    """
    out: list[str] = []
    previous = ""
    for raw in formulas:
        formula = (raw or "").strip().strip("$").strip()
        if not formula:
            previous = ""
            continue
        if formula.startswith("="):
            right = formula.lstrip("=").strip()
            statement = f"{previous} = {right}" if previous and right else ""
            previous = right
        else:
            statement = formula
            previous = formula.rsplit("=", 1)[1].strip() if "=" in formula else formula
        if (
            statement
            and 2 < len(statement) <= 300
            and not statement.endswith("=")
            and "\\text" not in statement
            and "?" not in statement
            and not dangerous_tex(statement)
        ):
            out.append(statement)
    return out


def _formula_candidates(state) -> list[str]:
    analysis = state.get("analysis") or {}
    plan = state.get("plan") or {}
    code = extract_code(str(state.get("code_draft", "")))
    found = _derivation([analysis.get("central_formula", "")])
    found += _derivation([section.get("key_formula", "") for section in plan.get("sections", [])])
    found += _derivation(_formulas(code))
    # Only statements with an equals sign can be checked for truth.
    return [f for f in dict.fromkeys(found) if "=" in f][:MAX_FORMULA_CHECKS]


def _check_status(check: MathCheck) -> str:
    if not check.ok:
        return "unchecked"
    if check.is_identity is True:
        return "holds"
    if check.is_identity is False:
        return "differs"
    return "parsed"


async def verify_formulas(ctx: Context, node_input: Any):
    """Check the lesson's formulas with the computer-algebra tool (SymPy over MCP).

    Runs beside the visual critic; its findings go to the pedagogy critic, so
    the maths review starts from symbolic evidence rather than from a reading.
    """
    job = current_job()
    job.emit({"type": "agent", "agent": "formula_checker", "status": "start", "framework": "adk"})
    checks: list[dict] = []
    for latex in _formula_candidates(ctx.state):
        try:
            result = await call_tool("check_math", emit=job.emit, latex=latex, operation="verify" if "=" in latex else "parse")
        except ToolError as exc:
            result = MathCheck(ok=False, operation="verify", input=latex, error=str(exc)[:200])
        status = _check_status(result)
        checks.append({"latex": latex, "status": status, "detail": result.error or result.result})
    ctx.state["math_checks"] = checks
    counts = {s: sum(1 for c in checks if c["status"] == s) for s in ("holds", "differs", "parsed", "unchecked")}
    code = extract_code(str(ctx.state.get("code_draft", "")))
    observability.record_artifact(job.dir, f"math_checks_round{ctx.state.get('round', 1)}", checks, "check_math", "mcp", "sympy", {"code": observability.digest(code)})
    job.emit({"type": "artifact", "name": "math_checks", "data": {"round": ctx.state.get("round", 1), "checks": checks, "counts": counts}})
    if checks:
        text = (
            f"SymPy checked {len(checks)} formula(s): {counts['holds']} identities hold, {counts['differs']} have sides that differ "
            f"(fine for definitions, wrong for identities), {counts['parsed']} parsed, {counts['unchecked']} could not be read."
        )
    else:
        text = "No formulas to check in this lesson."
    job.emit({"type": "message", "from": "formula_checker", "to": "pedagogy_critic", "text": text})
    job.emit({"type": "agent", "agent": "formula_checker", "status": "done", "framework": "adk"})
    return {"checked": len(checks), **counts}


def _pedagogy_instruction(rctx: ReadonlyContext) -> str:
    board = Storyboard.model_validate(rctx.state["storyboard"])
    plan = rctx.state.get("plan") or {}
    analysis = rctx.state.get("analysis") or {}
    narration = (rctx.state.get("narration") or {}).get("beats", {})
    code = extract_code(str(rctx.state.get("code_draft", "")))
    rows = [_prompt("pedagogy_critic"), "", "# Lesson plan", json.dumps(plan, ensure_ascii=False, indent=1)[:6000]]
    if analysis:
        rows += ["", "# Analyst's notes", f"Core insight: {analysis.get('core_insight')}", f"Misconceptions: {'; '.join(analysis.get('misconceptions', []))}"]
    rows += ["", "# Storyboard with the narration as recorded"]
    for scene in board.scenes:
        rows.append(f"## {scene.id}: {scene.title} - {scene.purpose}")
        for b in scene.beats:
            info = narration.get(b.id, {})
            spoken = info.get("text", b.narration)
            rows.append(f"- {b.id} ({info.get('duration', _estimated(b.narration))}s) says: {spoken}")
            if spoken != b.narration:
                rows.append(f"  (English original: {b.narration})")
            rows.append(f"  shows: {b.visual}")
    rows += ["", "# Formulas typeset in the animation"] + [f"- {f}" for f in _formulas(code)]
    checks = rctx.state.get("math_checks") or []
    if checks:
        rows += [
            "",
            "# Formula checks (SymPy, through the check_math tool)",
            "A formula whose sides differ is an error only if the lesson presents it as an identity; definitions and equations to be solved differ legitimately.",
        ]
        for c in checks:
            note = f" ({c['detail'][:160]})" if c.get("detail") and c["status"] != "holds" else ""
            rows.append(f"- {c['status']}: {c['latex']}{note}")
    return "\n".join(rows)


async def verdict(ctx: Context, node_input: Any):
    job = current_job()
    settings = get_settings()
    round_no = int(ctx.state.get("round", 1))
    visual = VisualReview.model_validate(_as_dict(ctx.state.get("visual_review")))
    pedagogy = PedagogyReview.model_validate(_as_dict(ctx.state.get("pedagogy_review")))
    scores = {
        "visual_clarity": visual.visual_clarity,
        "aesthetics": visual.aesthetics,
        "fidelity": visual.fidelity,
        "math_correctness": pedagogy.math_correctness,
        "pedagogy": pedagogy.pedagogy,
        "pacing": pedagogy.pacing,
    }
    mean = sum(scores.values()) / len(scores)
    issues = [dict(i.model_dump(), source="visual") for i in visual.issues] + [dict(i.model_dump(), source="pedagogy") for i in pedagogy.issues]
    high = [i for i in issues if i["severity"] == "high"]
    medium = [i for i in issues if i["severity"] == "medium"]
    good_enough = not high and mean >= 7.2 and len(medium) <= 2 and pedagogy.math_correctness >= 8
    out_of_rounds = round_no > settings.max_revisions
    approve = good_enough or out_of_rounds

    record = {"round": round_no, "scores": scores, "mean": round(mean, 2), "issues": issues, "visual_summary": visual.summary, "pedagogy_summary": pedagogy.summary, "verdict": "approve" if approve else "revise", "forced": out_of_rounds and not good_enough}
    observability.record_artifact(
        job.dir, f"review_round{round_no}", record, "review_panel", "adk",
        f"{_model_for('visual_critic')}, {_model_for('pedagogy_critic')}",
        {
            "code": observability.digest(extract_code(str(ctx.state.get("code_draft", "")))),
            "math_checks": observability.digest(ctx.state.get("math_checks") or []),
        },
    )
    history = list(ctx.state.get("reviews") or [])
    history.append(record)
    ctx.state["reviews"] = history
    ctx.route = "approve" if approve else "revise"

    job.emit({"type": "review", **record})
    if approve and not record["forced"]:
        text = f"Mean score {mean:.1f}/10 with no blocking issues. Approved for the final cut."
        to = "editor"
    elif approve:
        text = f"Mean {mean:.1f}/10 after {round_no} rounds; the remaining notes are recorded. Proceeding to the final cut."
        to = "editor"
    else:
        text = f"Mean {mean:.1f}/10; {len(high)} blocking and {len(medium)} medium issue(s). Please revise: " + "; ".join(f"{i['scene']}{('/' + i['beat']) if i.get('beat') else ''}: {i['problem']}" for i in (high + medium)[:3])
        to = "refiner"
    job.emit({"type": "message", "from": "review_panel", "to": to, "text": text})
    return {"verdict": ctx.route, "mean": round(mean, 2)}


# ------------------------------------------------------------------ refiner


def _fixer_instruction(rctx: ReadonlyContext) -> str:
    code = extract_code(str(rctx.state.get("code_draft", "")))
    report = rctx.state.get("render") or {}
    reviews = rctx.state.get("reviews") or []
    board = Storyboard.model_validate(rctx.state["storyboard"])
    rows = [_prompt("fixer"), "", "# Current module", "```python", code.rstrip(), "```", "", "# Render report (round %s)" % report.get("round")]
    for problem in report.get("problems", []):
        rows.append(f"- structure: {problem}")
    for sid, result in (report.get("scenes") or {}).items():
        if result.get("ok"):
            rows.append(f"- {sid}: rendered")
            continue
        err = result.get("error") or {}
        rows.append(f"- {sid}: FAILED in beat {err.get('beat') or '?'} - {err.get('type')}: {str(err.get('message', ''))[:600]}")
        where = err.get("where") or {}
        if where.get("context"):
            rows.append("  code around the failing line:\n" + "\n".join("    " + line for line in where["context"].splitlines()))
        latex = err.get("latex") or {}
        if latex:
            rows.append(f"  LaTeX source: {latex.get('source', '')[:300]}")
            rows += [f"  TeX says: {e[:240]}" for e in latex.get("errors", [])]
    if report.get("scenes"):
        for scene in board.scenes:
            if scene.id not in report["scenes"]:
                rows.append(f"- {scene.id}: not rendered (its class is missing)")
    elif report.get("problems"):
        rows.append("- nothing was rendered: the module was rejected before running")
    latest = reviews[-1] if reviews and reviews[-1]["round"] == report.get("round") else None
    if latest and not report.get("problems") and all(r.get("ok") for r in (report.get("scenes") or {}).values()):
        rows += ["", f"# Review panel (mean {latest['mean']}/10)", latest["visual_summary"], latest["pedagogy_summary"]]
        for i in latest["issues"]:
            rows.append(f"- [{i['severity']}] {i['scene']}{('/' + i['beat']) if i.get('beat') else ''} ({i['source']}): {i['problem']} -> {i['fix']}")
    rows += ["", "# Storyboard (for reference)", _storyboard_for_code(board)]
    return "\n".join(rows)


def _fixer_start(callback_context) -> None:
    current_job().emit({"type": "agent", "agent": "refiner", "status": "start", "framework": "adk"})


def _fixer_done(callback_context) -> None:
    job = current_job()
    raw = str(callback_context.state.get("code_draft", ""))
    code = extract_code(raw)
    changes = raw.split("CHANGES:", 1)[1].strip() if "CHANGES:" in raw else ""
    round_no = int(callback_context.state.get("round", 1))
    reviews = callback_context.state.get("reviews") or []
    inputs = {"render_report": observability.digest(callback_context.state.get("render") or {})}
    if reviews:
        inputs["review"] = observability.digest(reviews[-1])
    observability.record_artifact(job.dir, f"code_round{round_no}", code, "refiner", "adk", _model_for("fixer"), inputs, note=changes[:300])
    job.emit({"type": "artifact", "name": "code", "data": {"round": round_no, "code": code, "changes": changes}})
    summary = "; ".join(line.strip("-• ").strip() for line in changes.splitlines() if line.strip())[:400] or "revised the module"
    job.emit({"type": "message", "from": "refiner", "to": "renderer", "text": f"Revision {round_no}: {summary}. Please re-render."})
    job.emit({"type": "agent", "agent": "refiner", "status": "done", "framework": "adk"})


# ---------------------------------------------------------------- final cut


async def final_cut(ctx: Context, node_input: Any):
    job = current_job()
    settings = get_settings()
    job.emit({"type": "agent", "agent": "editor", "status": "start", "framework": "adk"})
    request = _request(ctx.state)
    board = _board(ctx.state)
    plan = LessonPlan.model_validate(ctx.state["plan"]) if ctx.state.get("plan") else None
    report = ctx.state.get("render") or {}
    scene_ids = [s.id for s in board.scenes]
    beat_ids = {s.id: [b.id for b in s.beats] for s in board.scenes}

    code = extract_code(str(ctx.state.get("code_draft", "")))
    broken = {sid for sid in scene_ids if not (report.get("scenes") or {}).get(sid, {}).get("ok")}
    if broken:
        job.emit({"type": "log", "agent": "editor", "text": f"Using storyboard fallbacks for {', '.join(sorted(broken))}."})
        code = with_fallbacks(code, board.scenes, broken, plan)

    renderer = SceneRenderer(settings, job.dir, job.emit)
    quality = request.quality
    job.emit({"type": "log", "agent": "editor", "text": f"Rendering the final cut at {quality}."})
    results = await renderer.render_all(code, scene_ids, beat_ids, quality, "final")
    still = {sid for sid, r in results.items() if not r.ok}
    if still:
        code = with_fallbacks(code, board.scenes, still, plan)
        retry = await renderer.render_all(code, sorted(still), beat_ids, quality, "final_fallback")
        results.update(retry)

    clips = []
    for scene in board.scenes:
        r = results.get(scene.id)
        if r and r.ok and r.video and r.timeline:
            clips.append(Clip(scene.id, scene.title, Path(r.video), r.timeline))
    if not clips:
        raise RuntimeError("no scene could be rendered")

    narration = (ctx.state.get("narration") or {}).get("beats", {})
    out = job.dir / "final"
    manifest = assemble(clips, narration, out)
    video_file = out / manifest["video"]
    if video_file.is_file():
        observability.record_artifact(
            job.dir, "video", {"sha256": observability.file_digest(video_file), "bytes": video_file.stat().st_size},
            "editor", "adk", None,
            {"code": observability.digest(code), "narration": observability.digest({k: v.get("text") for k, v in narration.items()})},
            note=f"{quality}, {manifest.get('duration', 0):.1f}s",
        )
    for key in ("video", "captions", "captions_json", "poster"):
        if manifest.get(key):
            manifest[key] = media_url(job.dir, out / manifest[key])
    for chapter in manifest["chapters"]:
        if chapter.get("thumb"):
            chapter["thumb"] = media_url(job.dir, out / chapter["thumb"])
    (job.dir / "code" / "lesson_final.py").write_text(code, encoding="utf-8")
    manifest.update(
        {
            "title": board.title,
            "logline": board.logline,
            "quality": quality,
            "language": (ctx.state.get("narration") or {}).get("language", request.language),
            "voice": (ctx.state.get("narration") or {}).get("provider"),
            "quiz": [q.model_dump() for q in plan.quiz] if plan else [],
            "fallback_scenes": sorted(broken | still),
            "rounds": int(ctx.state.get("round", 1)),
        }
    )
    ctx.state["final"] = manifest
    job.emit({"type": "message", "from": "editor", "to": "viewer", "text": f"\"{board.title}\" - {manifest['duration']:.0f}s, {len(clips)} chapters, captions and quiz included."})
    job.emit({"type": "agent", "agent": "editor", "status": "done", "framework": "adk"})
    return {"video": manifest["video"], "duration": manifest["duration"]}


# ------------------------------------------------------------ agent factory


def _critic_tools() -> list:
    """In live mode the pedagogy critic may call the SymPy checker itself, over MCP."""
    settings = get_settings()
    if settings.llm_mode != "live" or not settings.llm_tools:
        return []
    from google.adk.tools.mcp_tool import McpToolset, StreamableHTTPConnectionParams

    params = StreamableHTTPConnectionParams(url=f"{settings.local_url}/mcp/", timeout=20)
    return [McpToolset(connection_params=params, tool_filter=["check_math"])]


def llm_agents() -> dict[str, LlmAgent]:
    reader_before, _ = _lifecycle("intake_reader")
    coder_before, _ = _lifecycle("manim_coder")
    visual_before, visual_after = _lifecycle("visual_critic")
    pedagogy_before, pedagogy_after = _lifecycle("pedagogy_critic")
    return {
        "reader": LlmAgent(
            name="intake_reader",
            description="Reads an uploaded PDF, image or notes and distils the concept to teach.",
            model=RoutedGemini(role="reader"),
            instruction=_reader_instruction,
            include_contents="none",
            output_schema=ConceptBrief,
            output_key="brief",
            before_agent_callback=reader_before,
            after_agent_callback=_reader_done,
            before_model_callback=_attach_upload,
        ),
        "coder": LlmAgent(
            name="manim_coder",
            description="Writes the Manim module for the storyboard.",
            model=RoutedGemini(role="coder", thinking="medium"),
            instruction=_coder_instruction,
            include_contents="none",
            output_key="code_draft",
            generate_content_config=types.GenerateContentConfig(temperature=0.35, max_output_tokens=65536),
            before_agent_callback=coder_before,
            after_agent_callback=_coder_done,
        ),
        "visual_critic": LlmAgent(
            name="visual_critic",
            description="Reviews keyframes of every beat for clarity, composition and fidelity.",
            model=RoutedGemini(role="visual_critic"),
            instruction=_visual_instruction,
            include_contents="none",
            output_schema=VisualReview,
            output_key="visual_review",
            generate_content_config=types.GenerateContentConfig(temperature=0.2),
            before_agent_callback=visual_before,
            after_agent_callback=visual_after,
            before_model_callback=_attach_sheets,
        ),
        "pedagogy_critic": LlmAgent(
            name="pedagogy_critic",
            description="Checks mathematical correctness, pedagogy and pacing.",
            model=RoutedGemini(role="pedagogy_critic"),
            instruction=_pedagogy_instruction,
            include_contents="none",
            tools=_critic_tools(),
            output_schema=PedagogyReview,
            output_key="pedagogy_review",
            generate_content_config=types.GenerateContentConfig(temperature=0.2),
            before_agent_callback=pedagogy_before,
            after_agent_callback=pedagogy_after,
        ),
        "fixer": LlmAgent(
            name="refiner",
            description="Revises the Manim module from render errors and review notes.",
            model=RoutedGemini(role="fixer", thinking="medium"),
            instruction=_fixer_instruction,
            include_contents="none",
            output_key="code_draft",
            generate_content_config=types.GenerateContentConfig(temperature=0.25, max_output_tokens=65536),
            before_agent_callback=_fixer_start,
            after_agent_callback=_fixer_done,
        ),
    }
