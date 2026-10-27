"""Typed results of the studio's MCP tools.

Every tool returns one of these models. The MCP server publishes their JSON
schemas (structured tool output), and every caller validates what comes back
against the same model - a tool that returns the wrong shape is caught at the
boundary instead of corrupting an agent's context.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class DocumentDigest(BaseModel):
    """What the document parser found in an uploaded file."""

    name: str
    kind: str = Field(description="pdf | text | image")
    pages: int = 0
    title: str = ""
    abstract: str = ""
    sections: list[str] = Field(default_factory=list, description="Section headings in reading order.")
    key_terms: list[str] = Field(default_factory=list)
    excerpt: str = Field(default="", description="Cleaned text from the start of the document.")
    chars_total: int = 0
    truncated: bool = False
    warnings: list[str] = Field(default_factory=list)


class ConceptLookup(BaseModel):
    """Encyclopedic grounding for a concept (Wikipedia)."""

    ok: bool
    query: str
    title: str = ""
    summary: str = ""
    url: str = ""
    source: str = "wikipedia"
    error: str = ""


class MathCheck(BaseModel):
    """Result of a symbolic check by the computer-algebra tool (SymPy)."""

    ok: bool
    operation: str
    input: str
    parsed: str = ""
    result: str = ""
    result_latex: str = ""
    is_identity: bool | None = Field(default=None, description="For equations: true when both sides are equal for all values.")
    error: str = ""


class LessonHit(BaseModel):
    id: str
    title: str
    prompt: str
    created: float
    duration: float | None = None


class LibrarySearch(BaseModel):
    """Lessons already in the studio's library that match a query."""

    query: str
    hits: list[LessonHit] = Field(default_factory=list)
