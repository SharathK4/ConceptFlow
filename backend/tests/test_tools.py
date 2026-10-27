"""The MCP tool server: the tools, their typed results, and the protocol path."""

import asyncio

import pytest
from mcp.types import CallToolResult, TextContent

from conceptflow.config import get_settings
from conceptflow.tools import impl
from conceptflow.tools.client import ToolError, call_tool, list_tools
from conceptflow.tools.library import Library
from conceptflow.tools.models import DocumentDigest, MathCheck
from conceptflow.tools.validate import check_result


def test_server_lists_the_four_tools():
    names = {t["name"] for t in asyncio.run(list_tools())}
    assert names == {"read_document", "lookup_concept", "check_math", "search_lessons"}


@pytest.mark.parametrize(
    "latex, holds",
    [
        (r"(x+h)^2 - x^2 = 2xh + h^2", True),
        (r"\frac{d}{dx} x^3 = 3x^2", True),
        (r"\int_0^1 x \, dx = \frac{1}{2}", True),
        (r"(a+b)^2 = a^2 + b^2", False),
    ],
)
def test_check_math_verifies_identities(latex, holds):
    result = impl.check_math(latex, "verify")
    assert result.ok, result.error
    assert result.is_identity is holds


def test_check_math_operations_and_errors():
    assert impl.check_math(r"x^2", "differentiate").result == "2*x"
    assert impl.check_math(r"x^2 - 4", "solve").result == "[-2, 2]"
    broken = impl.check_math(r"\frac{1}{", "parse")
    assert not broken.ok and broken.error  # a parse error is a result, not a crash
    assert not impl.check_math(r"x^2", "verify").ok  # verify needs an equation


def test_read_document_parses_text_inside_the_upload_folder():
    folder = get_settings().lessons_dir / "doc-test" / "upload"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "notes.md"
    path.write_text("# Limits and continuity\n\nAbstract. We define limits.\n\n## Epsilon delta\nFor every epsilon there is a delta.\n", encoding="utf-8")
    digest = impl.read_document(str(path))
    assert isinstance(digest, DocumentDigest)
    assert digest.kind == "text" and "Limits and continuity" in digest.sections
    assert "epsilon" in digest.key_terms


def test_read_document_refuses_paths_outside_the_studio():
    with pytest.raises(ValueError):
        impl.read_document(__file__)


def test_tool_calls_go_through_mcp_and_are_type_checked():
    result = asyncio.run(call_tool("check_math", latex=r"\sin^2 x + \cos^2 x = 1", operation="verify"))
    assert isinstance(result, MathCheck) and result.ok and result.is_identity is True
    with pytest.raises(ToolError):
        asyncio.run(call_tool("read_document", path="C:/Windows/win.ini"))


@pytest.mark.parametrize(
    "latex, reading",
    [
        (r"\sin(x)^2 + \cos(x)^2 = 1", "Eq(sin(x)**2 + cos(x)**2, 1)"),  # (sin x)^2, not sin(x^2)
        (r"\sum_{k=1}^{n} k = \frac{n(n+1)}{2}", "Eq(Sum(k, (k, 1, n)), n*(n + 1)/2)"),  # n(n+1) is a product
        (r"f(x) = x^2", "Eq(f(x), x**2)"),  # f(x) is an application
    ],
)
def test_ambiguous_notation_is_read_like_a_mathematician(latex, reading):
    assert impl.check_math(latex, "parse").parsed == reading


def test_malformed_tool_results_are_rejected():
    good = CallToolResult(content=[TextContent(type="text", text='{"ok": true, "operation": "parse", "input": "x"}')])
    bad = CallToolResult(content=[TextContent(type="text", text='{"ok": "maybe"}')])
    assert check_result("check_math", good)[0]
    assert not check_result("check_math", bad)[0]
    assert check_result("check_math", {"ok": True, "operation": "parse", "input": "x"})[0]


def test_lookup_without_a_query_needs_no_network():
    result = impl.lookup_concept("   ")
    assert not result.ok and result.error == "empty query"


def test_library_search(tmp_path):
    library = Library(tmp_path / "library.db")
    library.upsert("a1", "The Derivative", "explain the derivative", "slopes of curves", 1.0, 90.0)
    library.upsert("b2", "Determinants", "what is a determinant", "area scaling", 2.0, 80.0)
    assert [h.id for h in library.search("derivatives of curves")] == ["a1"]
    library.remove("a1")
    assert library.search("derivative") == []
