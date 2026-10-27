"""Type-checking of tool results at the boundary (a guardrail)."""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, ValidationError

from .models import ConceptLookup, DocumentDigest, LibrarySearch, MathCheck

RESULT_MODELS: dict[str, type[BaseModel]] = {
    "read_document": DocumentDigest,
    "lookup_concept": ConceptLookup,
    "check_math": MathCheck,
    "search_lessons": LibrarySearch,
}


def payload(result: Any) -> Any:
    """The structured payload of an MCP tool result, however it arrives."""
    if hasattr(result, "model_dump"):
        result = result.model_dump(by_alias=True)
    if isinstance(result, dict):
        if result.get("structuredContent") is not None:
            return result["structuredContent"]
        if result.get("structured_content") is not None:
            return result["structured_content"]
        content = result.get("content")
        if isinstance(content, list) and content and isinstance(content[0], dict) and content[0].get("text"):
            try:
                return json.loads(content[0]["text"])
            except ValueError:
                return content[0]["text"]
    return result


def check_result(tool: str, result: Any) -> tuple[bool, str]:
    model = RESULT_MODELS.get(tool)
    if model is None:
        return True, "not an MCP studio tool"
    data = payload(result)
    if isinstance(data, dict) and data.get("isError"):
        return False, "the tool reported an error"
    try:
        model.model_validate(data)
    except ValidationError as exc:
        return False, f"{tool} result does not match {model.__name__}: {exc.errors()[0]['msg']}"
    return True, "ok"
