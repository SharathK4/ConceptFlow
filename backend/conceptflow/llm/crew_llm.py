"""CrewAI side of the model router.

CrewAI lets a crew bring its own LLM by subclassing ``BaseLLM``. This one
turns CrewAI's chat messages into a Gemini request and sends it through the
same router the ADK agents use - same ladders, same parked models, same usage
ledger. When CrewAI asks for a Pydantic ``response_model`` (a task with
``output_pydantic``), the schema is handed to Gemini as a response schema so
the crew gets validated structured output rather than prose to parse.
"""

from __future__ import annotations

import json
import re
import time
from typing import Any

from crewai.llms.base_llm import BaseLLM
from google.genai import types
from pydantic import BaseModel

from .. import monitoring
from ..schemas import repair_latex
from . import replay
from .router import _report, get_router


def _to_gemini(messages: str | list[dict[str, Any]]) -> tuple[str, list[types.Content]]:
    if isinstance(messages, str):
        return "", [types.Content(role="user", parts=[types.Part(text=messages)])]
    system: list[str] = []
    contents: list[types.Content] = []
    for message in messages:
        role = message.get("role", "user")
        content = message.get("content")
        text = content if isinstance(content, str) else json.dumps(content, default=str)
        if role == "system":
            system.append(text)
            continue
        gemini_role = "model" if role == "assistant" else "user"
        if contents and contents[-1].role == gemini_role:
            contents[-1].parts.append(types.Part(text=text))
        else:
            contents.append(types.Content(role=gemini_role, parts=[types.Part(text=text)]))
    if not contents:
        contents = [types.Content(role="user", parts=[types.Part(text="Begin.")])]
    return "\n\n".join(system), contents


def _parse(text: str, response_model: type[BaseModel]) -> BaseModel:
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        text = text[text.find("{") :]
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end != -1:
        text = text[start : end + 1]
    data = loads_lenient(text)
    return response_model.model_validate(_repair(data))


# A backslash that does not begin a JSON escape - LaTeX such as \sqrt or \lambda.
_INVALID_ESCAPE = re.compile(r'\\(?!["\\/bfnrtu])')


def loads_lenient(text: str) -> Any:
    """json.loads that tolerates the LaTeX backslashes models leave unescaped."""
    try:
        return json.loads(text)
    except ValueError:
        return json.loads(_INVALID_ESCAPE.sub(r"\\\\", text))


def normalise_json(text: str) -> str:
    """Re-serialise the JSON in a reply so LaTeX survives CrewAI's own parsing.

    With tools attached, CrewAI runs its text-based ReAct loop and parses the
    ``Final Answer:`` (or ``Action Input:``) itself, without a response schema.
    A model writing ``"\\sqrt{T}"`` there produces invalid JSON, and ``"\\frac"``
    silently becomes a form feed. Parsing leniently, repairing the LaTeX and
    dumping the object again gives CrewAI JSON it can read.
    """
    marker = next((m for m in ("Final Answer:", "Action Input:") if m in text), None)
    head, body = (text.split(marker, 1)[0] + marker, text.split(marker, 1)[1]) if marker else ("", text)
    start, end = body.find("{"), body.rfind("}")
    if start == -1 or end <= start or (not marker and body[:start].strip("`\n pythonjson")):
        return text
    try:
        data = loads_lenient(body[start : end + 1])
    except ValueError:
        return text
    return head + body[:start] + json.dumps(_repair(data), ensure_ascii=False) + body[end + 1 :]


def _repair(value: Any) -> Any:
    if isinstance(value, str):
        return repair_latex(value)
    if isinstance(value, list):
        return [_repair(v) for v in value]
    if isinstance(value, dict):
        return {k: _repair(v) for k, v in value.items()}
    return value


class RoutedCrewLLM(BaseLLM):
    """A CrewAI LLM backed by the shared Gemini router."""

    role: str = "planner"
    llm_type: str = "conceptflow-router"
    provider: str = "google"

    def call(
        self,
        messages,
        tools=None,
        callbacks=None,
        available_functions=None,
        from_task=None,
        from_agent=None,
        response_model=None,
    ):
        script = replay.current.get()
        if script is not None:
            _report({"type": "model", "role": self.role, "model": f"replay:{script.slug}", "status": "calling"})
            started = time.perf_counter()
            script.sleep()
            text = script.next(self.role)
            _report({"type": "model", "role": self.role, "model": f"replay:{script.slug}", "status": "ok"})
            prompt = messages if isinstance(messages, str) else json.dumps(messages, default=str)
            monitoring.record_llm_call(
                "crewai", self.role, f"replay:{script.slug}", time.perf_counter() - started,
                monitoring.estimate_tokens(prompt), monitoring.estimate_tokens(text), estimated=True,
            )
        else:
            system, contents = _to_gemini(messages)
            text, _model = get_router().generate_sync(
                self.role,
                contents,
                system=system or None,
                schema=response_model,
                temperature=self.temperature,
                framework="crewai",
            )
        if response_model is not None:
            return _parse(text, response_model)
        return normalise_json(text)

    def supports_function_calling(self) -> bool:
        return False

    def supports_stop_words(self) -> bool:
        return False

    def get_context_window_size(self) -> int:
        return 1_000_000
