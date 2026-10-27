"""Calling the MCP tools from workflow steps.

The workflow's deterministic steps (intake, grounding, formula checks) call
the tools through a real MCP client session - initialize, call_tool, typed
structured result - connected to the server in memory, so they work in every
mode, in tests and on a server without outbound localhost access. Every call
is traced, logged, shown on the page, and its result type-checked against the
tool's Pydantic model before anyone uses it.
"""

from __future__ import annotations

import json
import time
from typing import Any, TypeVar

from mcp.shared.memory import create_connected_server_and_client_session
from pydantic import BaseModel

from .. import monitoring
from ..observability import span
from .server import mcp
from .validate import RESULT_MODELS, check_result, payload

M = TypeVar("M", bound=BaseModel)


class ToolError(RuntimeError):
    pass


async def call_tool(name: str, emit=None, **arguments: Any) -> BaseModel:
    model = RESULT_MODELS[name]
    started = time.perf_counter()
    with span(f"mcp.{name}", **{"mcp.tool": name, "mcp.arguments": json.dumps(arguments, default=str)[:500]}) as current:
        async with create_connected_server_and_client_session(mcp) as session:
            result = await session.call_tool(name, arguments)
        seconds = time.perf_counter() - started
        ok, detail = check_result(name, result)
        current.set_attribute("mcp.ok", ok)
        if result.isError or not ok:
            text = result.content[0].text if result.content else detail
            monitoring.log("mcp", "tool_error", "warning", tool=name, seconds=round(seconds, 3), error=str(text)[:300])
            raise ToolError(f"{name}: {text}")
        value = model.model_validate(payload(result))
    monitoring.log("mcp", "tool_call", tool=name, seconds=round(seconds, 3), args=json.dumps(arguments, default=str)[:200])
    if emit is not None:
        emit({"type": "tool", "tool": name, "seconds": round(seconds, 3), "ok": True})
    return value


async def list_tools() -> list[dict]:
    async with create_connected_server_and_client_session(mcp) as session:
        listed = await session.list_tools()
    return [{"name": t.name, "title": t.title, "description": (t.description or "").strip().split("\n")[0]} for t in listed.tools]
