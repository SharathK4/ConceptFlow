"""ConceptFlow's MCP server: the studio's external tools behind one protocol.

Agents in both frameworks reach these tools through the Model Context
Protocol rather than by importing Python functions:

* the workflow's own steps call them over an in-memory MCP session
  (``client.py``) - the same JSON-RPC messages, no network needed;
* ADK ``LlmAgent``s use them through ``McpToolset`` and CrewAI agents through
  their native ``mcps=[MCPServerHTTP(...)]``, both over Streamable HTTP at
  ``/mcp`` on the studio server;
* any MCP client (the MCP Inspector, Claude Desktop) can use them too, either
  at ``/mcp`` or by launching ``python -m conceptflow.tools.server`` (stdio).

Tool results are Pydantic models, published as structured output schemas.
"""

from __future__ import annotations

import sys
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from . import impl
from .models import ConceptLookup, DocumentDigest, LibrarySearch, MathCheck

mcp = FastMCP(
    "conceptflow-tools",
    instructions=(
        "Tools for a studio that turns mathematical ideas into animated lessons: parse uploaded documents, "
        "look a concept up in Wikipedia, check mathematics with a computer algebra system, and search the "
        "library of lessons already made."
    ),
    streamable_http_path="/",
    stateless_http=True,
    json_response=True,
)


@mcp.tool(title="Read a document")
def read_document(path: str, max_chars: int = 15000) -> DocumentDigest:
    """Parse an uploaded PDF, Markdown or text file into title, abstract, section headings, key terms and a text excerpt.

    Only files inside the studio's upload folders can be read.
    """
    return impl.read_document(path, max_chars)


@mcp.tool(title="Look up a concept")
def lookup_concept(query: str, sentences: int = 3) -> ConceptLookup:
    """Fetch a short encyclopedic summary of a mathematical or scientific concept from Wikipedia, with its URL."""
    return impl.lookup_concept(query, sentences)


@mcp.tool(title="Check mathematics")
def check_math(latex: str, operation: str = "simplify", variable: str = "x") -> MathCheck:
    """Check a LaTeX expression or equation with SymPy.

    operation: parse | simplify | evaluate | differentiate | integrate | solve | verify.
    An equation such as '(x+h)^2 - x^2 = 2xh + h^2' is verified as an identity.
    """
    return impl.check_math(latex, operation, variable)


@mcp.tool(title="Search the lesson library")
def search_lessons(query: str, limit: int = 5) -> LibrarySearch:
    """Find lessons the studio has already made on similar concepts."""
    return impl.search_lessons(query, limit)


if __name__ == "__main__":
    # Stand-alone stdio server for external MCP clients.
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    mcp.run("stdio")
