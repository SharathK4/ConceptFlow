"""One quota-aware gateway to Gemini, shared by both agent frameworks.

Gemini's free tier counts requests per model per day: Flash models allow a
few dozen, Flash-Lite a few hundred. A lesson takes around ten calls, and
some are far more demanding than others, so every *role* has its own ladder of
models, best first. The router walks a ladder, and when a model answers 429
it reads the quota that was hit:

* per-minute  - wait the delay Google asks for, then try the same model again
* per-day     - park that model until midnight Pacific (when the quota
                resets) and move down the ladder
* 404         - the model is not available to this key; never try it again

Because the parking lot is shared, a CrewAI agent that exhausts a model saves
an ADK agent from trying it a minute later. Usage is written to disk so the
page can show what today has cost.
"""

from __future__ import annotations

import asyncio
import contextvars
import datetime as dt
import json
import re
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

from .. import monitoring
from ..config import Settings, get_settings
from ..observability import span

PACIFIC = ZoneInfo("America/Los_Angeles")
MAX_MINUTE_WAIT = 45.0
MAX_OUTPUT_TOKENS = 12288  # direct calls: the writers' room and translation

# Set per job so router notices reach the right page.
reporter: contextvars.ContextVar[Callable[[dict], None] | None] = contextvars.ContextVar(
    "router_reporter", default=None
)


class QuotaExhausted(RuntimeError):
    """No model on a role's ladder can take a request right now."""


@dataclass
class Failure:
    kind: str  # "minute" | "daily" | "missing" | "busy" | "fatal"
    wait: float = 0.0


def _report(event: dict) -> None:
    fn = reporter.get()
    if fn is not None:
        try:
            fn(event)
        except Exception:  # a UI hiccup must never break a model call
            pass


def classify(exc: BaseException) -> Failure:
    code = getattr(exc, "code", None)
    if not isinstance(code, int):
        code = getattr(exc, "status_code", None)
    text = str(exc)
    details = getattr(exc, "details", None)
    blob = f"{text} {json.dumps(details, default=str) if details else ''}"

    if code == 429 or "RESOURCE_EXHAUSTED" in blob:
        if re.search(r"PerDay|per day|daily", blob, re.I):
            return Failure("daily")
        match = re.search(r'retryDelay"?\s*[:=]\s*"?(\d+(?:\.\d+)?)s', blob)
        wait = float(match.group(1)) if match else 20.0
        if wait > MAX_MINUTE_WAIT:
            return Failure("daily")  # effectively unavailable for this lesson
        return Failure("minute", wait)
    if code == 404 or "NOT_FOUND" in blob or "is not found" in blob:
        return Failure("missing")
    if code in (500, 502, 503, 504) or "UNAVAILABLE" in blob or "overloaded" in blob.lower():
        return Failure("busy")
    return Failure("fatal")


class ModelRouter:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._lock = threading.Lock()
        self._parked: dict[str, float] = {}
        self._missing: set[str] = set()
        self._usage_path: Path = settings.cache_dir / "usage.json"
        self._usage = self._load_usage()
        self._client = None
        self.last: dict[str, str] = {}  # role -> the model that served its latest call

    # ------------------------------------------------------------- ladders

    def ladder(self, role: str) -> list[str]:
        """Models to try for ``role`` right now, best first."""
        now = time.time()
        with self._lock:
            models = [m for m in self.settings.ladders.get(role, []) if m not in self._missing]
            ready = [m for m in models if self._parked.get(m, 0) <= now]
        return ready

    def primary(self, role: str) -> str:
        ladder = self.settings.ladders.get(role) or ["gemini"]
        return ladder[0]

    def note_success(self, model: str, role: str, tokens: int | None = None) -> None:
        with self._lock:
            day = self._today()
            if self._usage.get("date") != day:
                self._usage = {"date": day, "calls": {}, "tokens": {}}
            self._usage["calls"][model] = self._usage["calls"].get(model, 0) + 1
            if tokens:
                self._usage["tokens"][model] = self._usage["tokens"].get(model, 0) + int(tokens)
            self._save_usage()
            self.last[role] = model
        _report({"type": "model", "role": role, "model": model, "status": "ok"})

    def note_failure(self, model: str, role: str, failure: Failure) -> None:
        with self._lock:
            if failure.kind == "daily":
                self._parked[model] = self._next_reset()
            elif failure.kind == "missing":
                self._missing.add(model)
            elif failure.kind == "busy":
                self._parked[model] = time.time() + 30
        _report({"type": "model", "role": role, "model": model, "status": failure.kind})

    def usage(self) -> dict[str, Any]:
        with self._lock:
            usage = dict(self._usage)
            parked = {m: t for m, t in self._parked.items() if t > time.time()}
        return {
            "date": usage.get("date"),
            "calls": usage.get("calls", {}),
            "parked": {m: dt.datetime.fromtimestamp(t, PACIFIC).isoformat() for m, t in parked.items()},
            "missing": sorted(self._missing),
        }

    # ---------------------------------------------------- direct generation

    @property
    def client(self):
        if self._client is None:
            from google import genai
            from google.genai import types

            if not self.settings.google_api_key:
                raise QuotaExhausted("GOOGLE_API_KEY is not set")
            # Four minutes: long enough for a thinking model writing a whole
            # Manim module, short enough that a hung call cannot stall a lesson.
            self._client = genai.Client(
                api_key=self.settings.google_api_key,
                http_options=types.HttpOptions(timeout=240_000),
            )
        return self._client

    def _config(self, system: str | None, schema: Any, temperature: float | None):
        from google.genai import types

        # A cap on output: a model that falls into repeating itself would otherwise
        # write tens of thousands of tokens (minutes) before the JSON breaks.
        kwargs: dict[str, Any] = {"max_output_tokens": MAX_OUTPUT_TOKENS}
        if system:
            kwargs["system_instruction"] = system
        if temperature is not None:
            kwargs["temperature"] = temperature
        if schema is not None:
            kwargs["response_mime_type"] = "application/json"
            kwargs["response_schema"] = schema
        return types.GenerateContentConfig(**kwargs)

    def generate_sync(
        self,
        role: str,
        contents: Any,
        *,
        system: str | None = None,
        schema: Any = None,
        temperature: float | None = None,
        framework: str = "crewai",
    ) -> tuple[str, str]:
        """Blocking call for code that runs in worker threads (CrewAI)."""
        config = self._config(system, schema, temperature)
        for model in self.ladder(role):
            for attempt in range(2):
                started = time.perf_counter()
                try:
                    _report({"type": "model", "role": role, "model": model, "status": "calling"})
                    with span(f"llm {role}", **_span_attrs(framework, role, model)) as current:
                        response = self.client.models.generate_content(model=model, contents=contents, config=config)
                        tokens_in, tokens_out = usage_of(response)
                        _set_usage(current, tokens_in, tokens_out)
                    monitoring.record_llm_call(framework, role, model, time.perf_counter() - started, tokens_in, tokens_out)
                    self.note_success(model, role, _tokens(response))
                    return response.text or "", model
                except Exception as exc:  # noqa: BLE001 - classified below
                    monitoring.record_llm_call(framework, role, model, time.perf_counter() - started, None, None, ok=False, error=f"{type(exc).__name__}: {str(exc)[:200]}")
                    failure = classify(exc)
                    if failure.kind == "fatal":
                        raise
                    if failure.kind == "minute" and attempt == 0:
                        _report({"type": "model", "role": role, "model": model, "status": "waiting", "seconds": failure.wait})
                        time.sleep(failure.wait)
                        continue
                    self.note_failure(model, role, failure)
                    break
        raise QuotaExhausted(self._exhausted_message(role)) from None

    async def generate(
        self,
        role: str,
        contents: Any,
        *,
        system: str | None = None,
        schema: Any = None,
        temperature: float | None = None,
        framework: str = "studio",
    ) -> tuple[str, str]:
        config = self._config(system, schema, temperature)
        for model in self.ladder(role):
            for attempt in range(2):
                started = time.perf_counter()
                try:
                    _report({"type": "model", "role": role, "model": model, "status": "calling"})
                    with span(f"llm {role}", **_span_attrs(framework, role, model)) as current:
                        response = await self.client.aio.models.generate_content(
                            model=model, contents=contents, config=config
                        )
                        tokens_in, tokens_out = usage_of(response)
                        _set_usage(current, tokens_in, tokens_out)
                    monitoring.record_llm_call(framework, role, model, time.perf_counter() - started, tokens_in, tokens_out)
                    self.note_success(model, role, _tokens(response))
                    return response.text or "", model
                except Exception as exc:  # noqa: BLE001
                    monitoring.record_llm_call(framework, role, model, time.perf_counter() - started, None, None, ok=False, error=f"{type(exc).__name__}: {str(exc)[:200]}")
                    failure = classify(exc)
                    if failure.kind == "fatal":
                        raise
                    if failure.kind == "minute" and attempt == 0:
                        _report({"type": "model", "role": role, "model": model, "status": "waiting", "seconds": failure.wait})
                        await asyncio.sleep(failure.wait)
                        continue
                    self.note_failure(model, role, failure)
                    break
        raise QuotaExhausted(self._exhausted_message(role)) from None

    def _exhausted_message(self, role: str) -> str:
        tried = ", ".join(self.settings.ladders.get(role, [])) or "none configured"
        return (
            f"No Gemini model is available for the {role.replace('_', ' ')} right now "
            f"(tried: {tried}). Free-tier quotas reset at midnight Pacific time."
        )

    # ------------------------------------------------------------- storage

    @staticmethod
    def _today() -> str:
        return dt.datetime.now(PACIFIC).date().isoformat()

    @staticmethod
    def _next_reset() -> float:
        now = dt.datetime.now(PACIFIC)
        tomorrow = (now + dt.timedelta(days=1)).replace(hour=0, minute=0, second=5, microsecond=0)
        return tomorrow.timestamp()

    def _load_usage(self) -> dict:
        try:
            data = json.loads(self._usage_path.read_text(encoding="utf-8"))
            if data.get("date") == self._today():
                return data
        except (OSError, ValueError):
            pass
        return {"date": self._today(), "calls": {}, "tokens": {}}

    def _save_usage(self) -> None:
        try:
            self._usage_path.write_text(json.dumps(self._usage, indent=2), encoding="utf-8")
        except OSError:
            pass


def _tokens(response: Any) -> int | None:
    meta = getattr(response, "usage_metadata", None)
    return getattr(meta, "total_token_count", None) if meta else None


def usage_of(response: Any) -> tuple[int | None, int | None]:
    """(input, output) tokens of a Gemini response; output includes thinking, which is billed as output."""
    meta = getattr(response, "usage_metadata", None)
    if meta is None:
        return None, None
    out = (getattr(meta, "candidates_token_count", None) or 0) + (getattr(meta, "thoughts_token_count", None) or 0)
    return getattr(meta, "prompt_token_count", None), out or None


def _span_attrs(framework: str, role: str, model: str) -> dict[str, Any]:
    return {
        "gen_ai.system": "gemini",
        "gen_ai.operation.name": "chat",
        "gen_ai.request.model": model,
        "conceptflow.framework": framework,
        "conceptflow.role": role,
    }


def _set_usage(current, tokens_in: int | None, tokens_out: int | None) -> None:
    if tokens_in is not None:
        current.set_attribute("gen_ai.usage.input_tokens", int(tokens_in))
    if tokens_out is not None:
        current.set_attribute("gen_ai.usage.output_tokens", int(tokens_out))


_router: ModelRouter | None = None


def get_router() -> ModelRouter:
    global _router
    if _router is None:
        _router = ModelRouter(get_settings())
    return _router
