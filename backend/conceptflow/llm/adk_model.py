"""Google ADK side of the model router.

``RoutedGemini`` is an ADK ``BaseLlm``: an ``LlmAgent`` uses it exactly like
a model name, but each request walks the role's ladder through ADK's own
``Gemini`` class, so ADK features (output schemas, system instructions,
callbacks, tracing) behave exactly as they would with a plain model string.
"""

from __future__ import annotations

import asyncio
import time
from typing import AsyncGenerator

from google.adk.models.base_llm import BaseLlm
from google.adk.models.google_llm import Gemini
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.genai import types
from pydantic import PrivateAttr

from .. import monitoring
from . import replay
from .router import QuotaExhausted, _report, classify, get_router, usage_of


class RoutedGemini(BaseLlm):
    """A Gemini model chosen per request from a role's ladder."""

    role: str
    model: str = ""
    # How hard the model may think before answering: "low" | "medium" | "high", or "" for
    # the model's default. Thinking is billed against the output limit, so a coder that
    # thinks without bounds can run out of room before the module is written.
    thinking: str = ""
    _delegates: dict[str, Gemini] = PrivateAttr(default_factory=dict)

    def model_post_init(self, context) -> None:  # noqa: D401
        if not self.model:
            self.model = get_router().primary(self.role)

    def _delegate(self, name: str) -> Gemini:
        if name not in self._delegates:
            self._delegates[name] = Gemini(model=name)
        return self._delegates[name]

    async def generate_content_async(
        self, llm_request: LlmRequest, stream: bool = False
    ) -> AsyncGenerator[LlmResponse, None]:
        script = replay.current.get()
        if script is not None:
            async for response in _replay(script, self.role, llm_request):
                yield response
            return

        router = get_router()
        for name in router.ladder(self.role):
            saved = list(llm_request.contents)
            for attempt in range(2):
                llm_request.model = name
                _apply_thinking(llm_request, name, self.thinking)
                _report({"type": "model", "role": self.role, "model": name, "status": "calling"})
                started = time.perf_counter()
                try:
                    tokens = 0
                    usage: tuple[int | None, int | None] = (None, None)
                    async for response in self._delegate(name).generate_content_async(llm_request, stream):
                        meta = response.usage_metadata
                        tokens = getattr(meta, "total_token_count", 0) or tokens
                        if meta is not None:
                            usage = usage_of(response)
                        yield response
                    monitoring.record_llm_call("adk", self.role, name, time.perf_counter() - started, *usage)
                    router.note_success(name, self.role, tokens)
                    return
                except Exception as exc:  # noqa: BLE001 - classified below
                    monitoring.record_llm_call("adk", self.role, name, time.perf_counter() - started, None, None, ok=False, error=f"{type(exc).__name__}: {str(exc)[:200]}")
                    failure = classify(exc)
                    if failure.kind == "fatal":
                        raise
                    llm_request.contents[:] = saved
                    if failure.kind == "minute" and attempt == 0:
                        _report({"type": "model", "role": self.role, "model": name, "status": "waiting", "seconds": failure.wait})
                        await asyncio.sleep(failure.wait)
                        continue
                    router.note_failure(name, self.role, failure)
                    break
        raise QuotaExhausted(router._exhausted_message(self.role)) from None


_BUDGETS = {"low": 2048, "medium": 8192, "high": 24576}


def _apply_thinking(llm_request: LlmRequest, model: str, level: str) -> None:
    """Bound thinking in the form each model family understands.

    Gemini 3 models take a thinking level; Gemini 2.5 models take a token budget
    (and reject a level), so the setting is rewritten whenever the ladder moves.
    """
    if not level:
        return
    if llm_request.config is None:
        llm_request.config = types.GenerateContentConfig()
    if model.startswith("gemini-2"):
        config = types.ThinkingConfig(thinking_budget=_BUDGETS[level])
    else:
        config = types.ThinkingConfig(thinking_level=types.ThinkingLevel[level.upper()])
    llm_request.config.thinking_config = config


def _request_chars(llm_request: LlmRequest) -> int:
    config = llm_request.config
    chars = len(str(config.system_instruction or "")) if config is not None else 0
    for content in llm_request.contents or []:
        for part in content.parts or []:
            if part.text:
                chars += len(part.text)
            elif part.inline_data is not None and part.inline_data.data:
                chars += 258 * 4  # an image (or a PDF page) costs about 258 tokens
    return chars


async def _replay(script: replay.ReplayScript, role: str, llm_request: LlmRequest) -> AsyncGenerator[LlmResponse, None]:
    _report({"type": "model", "role": role, "model": f"replay:{script.slug}", "status": "calling"})
    started = time.perf_counter()
    await asyncio.sleep(script.pause())
    text = script.next(role)
    _report({"type": "model", "role": role, "model": f"replay:{script.slug}", "status": "ok"})
    # Replay makes no API call; tokens are estimated so that cost projections still work.
    tokens_in = max(1, _request_chars(llm_request) // 4)
    tokens_out = monitoring.estimate_tokens(text)
    monitoring.record_llm_call("adk", role, f"replay:{script.slug}", time.perf_counter() - started, tokens_in, tokens_out, estimated=True)
    usage = types.GenerateContentResponseUsageMetadata(prompt_token_count=tokens_in, candidates_token_count=tokens_out, total_token_count=tokens_in + tokens_out)
    yield LlmResponse(content=types.Content(role="model", parts=[types.Part(text=text)]), usage_metadata=usage)
