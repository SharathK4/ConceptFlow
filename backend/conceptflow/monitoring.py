"""Monitoring through callbacks: every step, model call, tool call and error.

Sources, all writing to one per-lesson log (``data/lessons/<id>/monitor.jsonl``)
and to the process log (``data/logs/conceptflow.log``):

* ``MonitoringPlugin`` - a Google ADK plugin. Plugins receive the callbacks of
  *every* agent, model and tool in a runner: before/after agent, before/after
  model (latency, tokens, finish reason), before/after tool, and the error
  callbacks for models, tools, agents and the run itself.
* ``GuardrailPlugin``  - an ADK plugin that validates tool arguments before a
  call and type-checks tool results after it (see guardrails.py).
* CrewAI event-bus listeners - task, agent, tool and LLM events of the crew.
* the model router - one record per LLM call in either framework, with tokens
  (from the API's usage metadata, or estimated in replay mode).

The same records feed the Performance view and scripts/evaluate.py.
"""

from __future__ import annotations

import contextvars
import json
import logging
import logging.handlers
import threading
import time
from pathlib import Path
from typing import Any, Callable

from google.adk.plugins.base_plugin import BasePlugin

from .config import get_settings

logger = logging.getLogger("conceptflow")
# The studio's records go to its own log file; only warnings reach the console.
logger.propagate = False
logger.setLevel(logging.INFO)
_console = logging.StreamHandler()
_console.setLevel(logging.WARNING)
_console.setFormatter(logging.Formatter("conceptflow %(levelname)s %(message)s"))
logger.addHandler(_console)
_configured = False


def configure_logging() -> None:
    global _configured
    if _configured:
        return
    _configured = True
    folder = get_settings().data_dir / "logs"
    folder.mkdir(parents=True, exist_ok=True)
    handler = logging.handlers.RotatingFileHandler(folder / "conceptflow.log", maxBytes=5_000_000, backupCount=3, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)


class RunLog:
    """One lesson's monitoring log."""

    def __init__(self, job_dir: Path, emit: Callable[[dict], None] | None = None) -> None:
        self.dir = job_dir
        self.emit = emit
        self.started = time.time()

    def write(self, source: str, kind: str, level: str = "info", **fields: Any) -> None:
        record = {"t": round(time.time() - self.started, 3), "at": round(time.time(), 3), "source": source, "kind": kind, "level": level}
        record.update({k: v for k, v in fields.items() if v is not None})
        line = json.dumps(record, default=str, ensure_ascii=False)
        with _write_lock:  # one lock for every writer: the crew thread logs into the same file
            try:
                with (self.dir / "monitor.jsonl").open("a", encoding="utf-8") as fh:
                    fh.write(line + "\n")
            except OSError:
                pass
        getattr(logger, "error" if level == "error" else "warning" if level == "warning" else "info")(f"[{self.dir.name}] {source}.{kind} {json.dumps(fields, default=str)[:400]}")
        if level in ("error", "warning") and self.emit is not None:
            try:
                self.emit({"type": "log", "agent": fields.get("agent") or source, "text": f"{kind}: {fields.get('error') or fields.get('detail') or ''}"[:300], "level": level})
            except Exception:  # noqa: BLE001 - monitoring must never break the run
                pass


_write_lock = threading.Lock()
_current: contextvars.ContextVar[RunLog | None] = contextvars.ContextVar("conceptflow_runlog", default=None)
_active: RunLog | None = None  # fallback for callbacks that run on other threads


def bind(run_log: RunLog | None):
    """Make ``run_log`` the current log. Returns a token for unbind()."""
    global _active
    previous = _active
    _active = run_log
    return _current.set(run_log), previous


def unbind(token) -> None:
    global _active
    ctx_token, previous = token
    _active = previous
    try:
        _current.reset(ctx_token)
    except ValueError:  # reset from a different context
        _current.set(None)


def current() -> RunLog | None:
    return _current.get() or _active


def log(source: str, kind: str, level: str = "info", **fields: Any) -> None:
    run_log = current()
    if run_log is not None:
        run_log.write(source, kind, level, **fields)
    else:
        logger.info(f"{source}.{kind} {json.dumps(fields, default=str)[:300]}")


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


def record_llm_call(
    framework: str,
    role: str,
    model: str,
    seconds: float,
    tokens_in: int | None,
    tokens_out: int | None,
    *,
    estimated: bool = False,
    ok: bool = True,
    error: str | None = None,
) -> None:
    from opentelemetry import trace

    span = trace.get_current_span()
    if span.get_span_context().is_valid:
        span.set_attribute("gen_ai.request.model", model)
        if tokens_in is not None:
            span.set_attribute("gen_ai.usage.input_tokens", int(tokens_in))
        if tokens_out is not None:
            span.set_attribute("gen_ai.usage.output_tokens", int(tokens_out))
    log(
        "llm",
        "call" if ok else "call_failed",
        "info" if ok else "warning",
        framework=framework,
        role=role,
        model=model,
        seconds=round(seconds, 3),
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        estimated=estimated or None,
        error=error,
    )


def _text(content: Any, limit: int = 0) -> str:
    parts = getattr(content, "parts", None) or []
    text = "".join(getattr(p, "text", "") or "" for p in parts)
    return text[:limit] if limit else text


# ------------------------------------------------------------- ADK plugin


class MonitoringPlugin(BasePlugin):
    """Tracks every agent, model call and tool call in the ADK runner."""

    def __init__(self) -> None:
        super().__init__(name="conceptflow_monitor")
        self._started: dict[tuple[str, str], float] = {}

    def _key(self, ctx, what: str) -> tuple[str, str]:
        return (getattr(ctx, "invocation_id", "") or "", what)

    async def before_run_callback(self, *, invocation_context):
        log("adk", "run_start", invocation=invocation_context.invocation_id)
        return None

    async def after_run_callback(self, *, invocation_context):
        log("adk", "run_end", invocation=invocation_context.invocation_id)
        return None

    async def on_event_callback(self, *, invocation_context, event):
        route = event.actions.route if event.actions else None
        if route is not None:
            log("adk", "route", node=event.node_info.path, route=route)
        if event.error_message:
            log("adk", "event_error", "error", author=event.author, error=event.error_message)
        return None

    async def before_agent_callback(self, *, agent, callback_context):
        self._started[self._key(callback_context, f"agent:{agent.name}")] = time.perf_counter()
        log("adk", "agent_start", agent=agent.name)
        return None

    async def after_agent_callback(self, *, agent, callback_context):
        t0 = self._started.pop(self._key(callback_context, f"agent:{agent.name}"), None)
        log("adk", "agent_end", agent=agent.name, seconds=round(time.perf_counter() - t0, 3) if t0 else None)
        return None

    async def before_model_callback(self, *, callback_context, llm_request):
        self._started[self._key(callback_context, f"model:{callback_context.agent_name}")] = time.perf_counter()
        prompt_chars = sum(len(_text(c)) for c in llm_request.contents or [])
        log("adk", "model_request", agent=callback_context.agent_name, model=llm_request.model, prompt_chars=prompt_chars,
            tools=sorted((llm_request.tools_dict or {}).keys()) or None)
        return None

    async def after_model_callback(self, *, callback_context, llm_response):
        t0 = self._started.pop(self._key(callback_context, f"model:{callback_context.agent_name}"), None)
        usage = llm_response.usage_metadata
        calls = [p.function_call.name for p in (llm_response.content.parts if llm_response.content else []) or [] if p.function_call]
        log(
            "adk",
            "model_response",
            agent=callback_context.agent_name,
            seconds=round(time.perf_counter() - t0, 3) if t0 else None,
            tokens_in=getattr(usage, "prompt_token_count", None),
            tokens_out=getattr(usage, "candidates_token_count", None),
            finish=str(llm_response.finish_reason) if llm_response.finish_reason else None,
            output_chars=len(_text(llm_response.content)) if llm_response.content else 0,
            tool_calls=calls or None,
        )
        return None

    async def on_model_error_callback(self, *, callback_context, llm_request, error):
        log("adk", "model_error", "error", agent=callback_context.agent_name, model=llm_request.model, error=f"{type(error).__name__}: {error}"[:500])
        return None

    async def before_tool_callback(self, *, tool, tool_args, tool_context):
        self._started[self._key(tool_context, f"tool:{tool.name}")] = time.perf_counter()
        log("adk", "tool_call", agent=tool_context.agent_name, tool=tool.name, args=json.dumps(tool_args, default=str)[:300])
        return None

    async def after_tool_callback(self, *, tool, tool_args, tool_context, result):
        t0 = self._started.pop(self._key(tool_context, f"tool:{tool.name}"), None)
        log("adk", "tool_result", agent=tool_context.agent_name, tool=tool.name, seconds=round(time.perf_counter() - t0, 3) if t0 else None,
            result=json.dumps(result, default=str)[:300])
        return None

    async def on_tool_error_callback(self, *, tool, tool_args, tool_context, error):
        log("adk", "tool_error", "error", agent=tool_context.agent_name, tool=tool.name, error=f"{type(error).__name__}: {error}"[:500])
        return None

    async def on_agent_error_callback(self, *, agent, callback_context, error):
        log("adk", "agent_error", "error", agent=agent.name, error=f"{type(error).__name__}: {error}"[:500])
        return None

    async def on_run_error_callback(self, *, invocation_context, error):
        log("adk", "run_error", "error", error=f"{type(error).__name__}: {error}"[:500])
        return None


class GuardrailPlugin(BasePlugin):
    """Validates tool arguments and type-checks tool results for every ADK agent."""

    def __init__(self) -> None:
        super().__init__(name="conceptflow_guardrails")

    async def before_tool_callback(self, *, tool, tool_args, tool_context):
        from .guardrails import dangerous_tex

        problem = None
        if tool.name == "check_math":
            latex = str(tool_args.get("latex", ""))
            if len(latex) > 1000:
                problem = "expression longer than 1000 characters"
            elif dangerous_tex(latex):
                problem = f"TeX file/macro commands are not allowed ({', '.join(dangerous_tex(latex))})"
        elif tool.name == "lookup_concept" and len(str(tool_args.get("query", ""))) > 200:
            problem = "query longer than 200 characters"
        elif tool.name == "read_document":
            from .tools.impl import _safe_path

            try:
                _safe_path(str(tool_args.get("path", "")))
            except ValueError as exc:
                problem = str(exc)
        if problem:
            log("guardrail", "tool_args_rejected", "warning", agent=tool_context.agent_name, tool=tool.name, detail=problem)
            return {"error": f"blocked by guardrail: {problem}"}
        return None

    async def after_tool_callback(self, *, tool, tool_args, tool_context, result):
        from .tools.validate import check_result

        ok, detail = check_result(tool.name, result)
        if not ok:
            log("guardrail", "tool_result_rejected", "warning", agent=tool_context.agent_name, tool=tool.name, detail=detail)
            return {"error": f"tool returned malformed data: {detail}"}
        log("guardrail", "tool_result_checked", agent=tool_context.agent_name, tool=tool.name)
        return None

    async def before_model_callback(self, *, callback_context, llm_request):
        # Defence in depth: anything that reached a model's user turn is screened again.
        from .guardrails import screen_document

        for content in llm_request.contents or []:
            if content.role != "user":
                continue
            for part in content.parts or []:
                if part.text:
                    verdict = screen_document(part.text)
                    if verdict.action == "sanitize" and "indirect prompt injection" in verdict.categories:
                        part.text = verdict.text
                        log("guardrail", "model_input_sanitized", "warning", agent=callback_context.agent_name, detail=verdict.summary()[:300])
        return None


# --------------------------------------------------------- CrewAI listeners

_crew_lock = threading.Lock()
_crew_installed = False


def install_crewai_listeners() -> None:
    global _crew_installed
    with _crew_lock:
        if _crew_installed:
            return
        _crew_installed = True
    from crewai.events import crewai_event_bus
    from crewai.events.types.agent_events import AgentExecutionErrorEvent
    from crewai.events.types.task_events import TaskCompletedEvent, TaskFailedEvent, TaskStartedEvent
    from crewai.events.types.tool_usage_events import ToolUsageErrorEvent, ToolUsageFinishedEvent, ToolUsageStartedEvent

    @crewai_event_bus.on(TaskStartedEvent)
    def _task_started(source, event):
        log("crewai", "task_start", task=getattr(source, "name", None), agent=getattr(getattr(source, "agent", None), "role", None))

    @crewai_event_bus.on(TaskCompletedEvent)
    def _task_done(source, event):
        output = getattr(event, "output", None)
        log("crewai", "task_end", task=getattr(source, "name", None), agent=getattr(output, "agent", None),
            output_chars=len(getattr(output, "raw", "") or ""))

    @crewai_event_bus.on(TaskFailedEvent)
    def _task_failed(source, event):
        log("crewai", "task_failed", "error", task=getattr(source, "name", None), error=str(getattr(event, "error", ""))[:500])

    @crewai_event_bus.on(AgentExecutionErrorEvent)
    def _agent_error(source, event):
        log("crewai", "agent_error", "error", agent=getattr(getattr(event, "agent", None), "role", None), error=str(getattr(event, "error", ""))[:500])

    @crewai_event_bus.on(ToolUsageStartedEvent)
    def _tool_started(source, event):
        log("crewai", "tool_call", agent=event.agent_role, tool=event.tool_name, args=json.dumps(event.tool_args, default=str)[:300])

    @crewai_event_bus.on(ToolUsageFinishedEvent)
    def _tool_done(source, event):
        seconds = (event.finished_at - event.started_at).total_seconds() if event.finished_at and event.started_at else None
        log("crewai", "tool_result", agent=event.agent_role, tool=event.tool_name, seconds=seconds, cached=event.from_cache or None,
            result=str(event.output)[:300])

    @crewai_event_bus.on(ToolUsageErrorEvent)
    def _tool_error(source, event):
        log("crewai", "tool_error", "error", agent=event.agent_role, tool=event.tool_name, error=str(event.error)[:500])
