"""The artifacts agents hand to one another.

Every hand-off in the pipeline is one of these models, so an agent never
receives prose it has to guess the shape of. The same classes are used as
Gemini response schemas (structured output), as CrewAI ``output_pydantic``
types and as ADK ``output_schema`` types, which is what lets the two
frameworks share work without a translation layer.

Kept deliberately flat - plain strings, ints, lists and nested models - since
that is what every provider's structured-output mode supports.
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, Field, field_validator

Audience = Literal["school", "undergraduate", "graduate"]
Length = Literal["brief", "standard", "deep"]
Quality = Literal["preview", "draft", "hd", "hd60"]
VoiceChoice = Literal["auto", "elevenlabs", "sarvam", "edge", "silent"]

LANGUAGES = {
    "en": ("English", "en-IN"),
    "hi": ("Hindi", "hi-IN"),
    "te": ("Telugu", "te-IN"),
    "ta": ("Tamil", "ta-IN"),
    "kn": ("Kannada", "kn-IN"),
    "ml": ("Malayalam", "ml-IN"),
    "bn": ("Bengali", "bn-IN"),
    "mr": ("Marathi", "mr-IN"),
    "gu": ("Gujarati", "gu-IN"),
    "od": ("Odia", "od-IN"),
    "pa": ("Punjabi", "pa-IN"),
}

# How long a lesson runs and how it is divided.
LENGTHS = {
    "brief": {"scenes": (3, 3), "beats": (2, 3), "minutes": "about 1"},
    "standard": {"scenes": (4, 5), "beats": (3, 4), "minutes": "2 to 3"},
    "deep": {"scenes": (6, 7), "beats": (3, 5), "minutes": "4 to 5"},
}


class LessonRequest(BaseModel):
    prompt: str
    audience: Audience = "undergraduate"
    length: Length = "standard"
    language: str = "en"
    voice: VoiceChoice = "auto"
    quality: Quality = "hd"
    upload_name: str | None = None
    upload_mime: str | None = None

    @field_validator("language")
    @classmethod
    def _known_language(cls, v: str) -> str:
        return v if v in LANGUAGES else "en"


# ------------------------------------------------------------------ intake


class ConceptBrief(BaseModel):
    """What the Intake reader extracted from an uploaded file."""

    concept: str = Field(description="The single concept to teach, stated precisely.")
    context: str = Field(description="Definitions, notation and worked details from the source worth keeping.")
    notation: list[str] = Field(default_factory=list, description="Symbols and their meanings, e.g. 'h: step size'.")
    source_summary: str = Field(description="One sentence on what the source material is.")


# --------------------------------------------------------- writers' room


class ConceptAnalysis(BaseModel):
    topic: str
    domain: str
    core_insight: str = Field(description="The one idea the viewer must leave with, in a sentence.")
    prerequisites: list[str]
    key_ideas: list[str]
    central_formula: str = Field(description="LaTeX for the central definition or result; empty if none.")
    intuitions: list[str] = Field(description="Visual or physical intuitions that make it click.")
    misconceptions: list[str]
    visual_opportunities: list[str] = Field(description="Things that can be *shown* moving, not just stated.")


class LessonSection(BaseModel):
    title: str
    goal: str
    explanation: str
    key_formula: str = Field(default="", description="LaTeX, or empty.")
    visual_idea: str


class QuizItem(BaseModel):
    question: str
    choices: list[str] = Field(description="Exactly four options.")
    answer: int = Field(description="Index (0-3) of the correct choice.")
    why: str


class LessonPlan(BaseModel):
    title: str
    hook: str = Field(description="The opening question that makes the viewer curious.")
    objectives: list[str]
    sections: list[LessonSection]
    takeaway: str
    quiz: list[QuizItem]


class StoryBeat(BaseModel):
    id: str = Field(description="Scene id + 'b' + number, e.g. 's2b3'.")
    narration: str = Field(description="What the narrator says: one or two spoken sentences, no LaTeX.")
    visual: str = Field(description="Precisely what is on screen and how it moves during this line.")


class StoryScene(BaseModel):
    id: str = Field(description="'s1', 's2', ...")
    title: str
    purpose: str
    beats: list[StoryBeat]


class Storyboard(BaseModel):
    title: str
    logline: str
    color_story: str = Field(description="Which palette colour stands for which idea, used consistently.")
    scenes: list[StoryScene]

    def beats(self) -> list[StoryBeat]:
        return [b for s in self.scenes for b in s.beats]

    def normalise_ids(self) -> "Storyboard":
        """Force ids into the s{n} / s{n}b{m} scheme the code relies on."""
        for i, scene in enumerate(self.scenes, 1):
            scene.id = f"s{i}"
            for j, beat in enumerate(scene.beats, 1):
                beat.id = f"s{i}b{j}"
        return self


# ------------------------------------------------------------------ review


Severity = Literal["high", "medium", "low"]


class Issue(BaseModel):
    scene: str = Field(description="Scene id, e.g. 's2'.")
    beat: str = Field(default="", description="Beat id if the problem is local to one beat.")
    severity: Severity
    problem: str
    fix: str = Field(description="A concrete instruction for the Manim coder.")


class VisualReview(BaseModel):
    visual_clarity: int = Field(description="1-10: can a viewer read every frame at a glance?")
    aesthetics: int = Field(description="1-10: composition, spacing, colour, restraint.")
    fidelity: int = Field(description="1-10: does the picture show what the storyboard intends?")
    issues: list[Issue]
    summary: str


class PedagogyReview(BaseModel):
    math_correctness: int = Field(description="1-10: every statement, formula and label correct.")
    pedagogy: int = Field(description="1-10: order, motivation, one idea at a time.")
    pacing: int = Field(description="1-10: time to absorb each step; nothing rushed or idle.")
    issues: list[Issue]
    summary: str


# ------------------------------------------------------------- utilities

_LATEX_AFTER_ESCAPE = re.compile(r"[\x08\x0c\t\r]")
_ESCAPES = {"\x08": "\\b", "\x0c": "\\f", "\t": "\\t", "\r": "\\r"}
# "\n" followed by the rest of a LaTeX command that starts with n.
_NEWLINE_COMMANDS = re.compile(r"\n(abla|eq|u\b|ot\b|ot\{|e\b|ewline|leq|geq|mid|subseteq|i\b|ormalsize)")


def repair_latex(text: str) -> str:
    r"""Undo JSON escapes that ate a LaTeX backslash.

    A model writing ``"\frac"`` inside JSON produces a form feed followed by
    "rac". Tabs, form feeds, backspaces and carriage returns never belong in a
    formula, so they are turned back into the command they came from.
    """
    text = _LATEX_AFTER_ESCAPE.sub(lambda m: _ESCAPES[m.group(0)], text)
    return _NEWLINE_COMMANDS.sub(lambda m: "\\n" + m.group(1), text)
