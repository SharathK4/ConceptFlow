"""Guardrails: what the studio refuses, rewrites, or double-checks.

Layered, so no single check has to be perfect:

1. Input   - every prompt is screened at the API before any agent sees it:
             prompt-injection and jailbreak phrasing, abusive or threatening
             language, requests for harmful content, and personal data
             (redacted rather than refused).
2. Context - uploaded documents are screened for *indirect* prompt injection
             (instructions hidden in a PDF) and offending lines are removed
             before the text reaches a model.
3. Agents  - CrewAI task guardrails validate each crew output (and make the
             agent retry with feedback); an ADK plugin validates every tool
             call's arguments and type-checks every tool's return value, and
             re-screens what is sent to models.
4. Code    - the sandbox (render/sandbox.py) rejects generated Python that
             reaches outside Manim, and LaTeX that could read or write files.
5. Output  - the narration is screened before it is voiced and published.

Rules are deterministic and offline, so they work with or without a key; in
live mode Gemini's own safety filters sit behind them.
"""

# No "from __future__ import annotations" here: CrewAI inspects the guard
# functions' return annotations at runtime and needs real types, not strings.
import re
import unicodedata
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError

MAX_PROMPT = 2000

_INJECTION = [
    (r"\b(ignore|disregard|forget|override|bypass)\b[^.\n]{0,40}\b(previous|prior|above|earlier|all|any|your|the|these)\b[^.\n]{0,30}\b(instructions?|prompts?|rules?|directions?|guidelines|messages?)\b", "instruction override"),
    (r"\b(reveal|show|print|repeat|output|leak|display|tell me)\b[^.\n]{0,40}\b(system|hidden|initial|original|developer|internal)\b[^.\n]{0,20}\b(prompt|instructions?|message|rules)\b", "prompt extraction"),
    (r"\byou are (now|no longer)\b|\bfrom now on,? (you|act)\b|\bpretend (to be|you are)\b", "role hijack"),
    (r"\b(jailbreak|DAN mode|do anything now|developer mode|god mode)\b", "jailbreak"),
    (r"(^|\n)\s*(system|assistant|developer)\s*:", "role spoofing"),
    (r"</?\s*(system|instructions?|assistant)\s*>|\[/?INST\]|<\|im_(start|end)\|>|<\|endoftext\|>", "chat-template tokens"),
    (r"\b(api[_ -]?keys?|passwords?|secrets?|tokens?|credentials|env(ironment)? variables?|\.env)\b[^.\n]{0,40}\b(print|show|send|reveal|give|leak|exfiltrate)\b|\b(print|show|send|reveal|give|leak|exfiltrate)\b[^.\n]{0,40}\b(api[_ -]?keys?|passwords?|secrets?|credentials|env(ironment)? variables?|\.env)\b", "secret exfiltration"),
    (r"\b(import os|subprocess|__import__|os\.system|rm -rf|shutil\.rmtree)\b|\b(exec|eval)\s*\(", "code injection"),
]
_ABUSE = [
    (r"\b(fuck\w*|shit\w*|bitch\w*|bastard\w*|asshole\w*|cunt\w*|motherfuck\w*|dickhead\w*|wanker\w*)\b", "profanity"),
    (r"\b(i('ll| will)|we('ll| will)|gonna)\s+(kill|hurt|shoot|stab|destroy)\s+(you|him|her|them)\b|\bkill yourself\b|\bkys\b", "threat"),
    (r"\b(you('re| are)\s+(an?\s+)?(idiot|moron|stupid|worthless|retard\w*))\b", "harassment"),
]
_HARMFUL = [
    (r"\b(make|build|construct|synthes\w+|manufactur\w+|assembl\w+)\b[^.\n]{0,50}\b(bombs?|explosives?|nerve agents?|chemical weapons?|bioweapons?|meth(amphetamine)?|firearms?|guns?)\b", "weapons or drugs"),
    (r"\b(suicide|self[- ]harm|kill myself|end my life)\b", "self-harm"),
    (r"\b(child|minor|underage)\b[^.\n]{0,30}\b(sexual|nude|porn)\w*\b|\b(sexual|nude|porn)\w*\b[^.\n]{0,30}\b(child|minor|underage)\b", "sexual content involving minors"),
]
# Personal data. Mathematics is full of numbers, so the number patterns are
# narrow: phone numbers need a country code, brackets, dashes or an Indian
# mobile prefix, and card numbers must pass the Luhn checksum.
_PII = [
    (re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b"), "[email removed]", "email address"),
    (
        re.compile(r"(?<![\w+.])(?:\+\d{1,3}[\s-]?\d{3,5}[\s-]?\d{3,5}(?:[\s-]?\d{2,5})?|\(\d{3}\)\s?\d{3}[\s-]\d{4}|\d{3}-\d{3}-\d{4}|[6-9]\d{9})(?![\w.])"),
        "[phone removed]",
        "phone number",
    ),
    (re.compile(r"(?<![\d.])\d{4}([ -]?)\d{4}\1\d{4}\1\d{1,7}(?![\d.])"), "[number removed]", "card-like number"),
    (re.compile(r"\b(AIza[0-9A-Za-z_\-]{30,}|sk-[A-Za-z0-9]{20,}|xi-[A-Za-z0-9]{20,})\b"), "[key removed]", "API key"),
]
# In uploaded documents numbers are content (tables, matrices, data), so only these are redacted.
_DOCUMENT_PII = ("email address", "card-like number", "API key")
_TEX_DANGER = re.compile(r"\\(input|include|openin|openout|read|readline|write|immediate|write18|catcode|csname|special|jobname|InputIfFileExists|lstinputlisting|verbatiminput)\b")


class Verdict(BaseModel):
    """The outcome of a guardrail check."""

    allowed: bool
    action: Literal["allow", "sanitize", "block"]
    categories: list[str] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)
    text: str = ""

    def summary(self) -> str:
        return "; ".join(self.reasons) or "clean"


def normalise(text: str) -> str:
    """NFKC-normalise and strip control/zero-width characters (defeats simple obfuscation)."""
    text = unicodedata.normalize("NFKC", text)
    text = re.sub(r"[\u200b-\u200f\u2060-\u2064\ufeff]", "", text)
    return "".join(ch for ch in text if ch in "\n\t" or unicodedata.category(ch)[0] != "C")


def _findings(text: str, rules) -> list[tuple[str, str]]:
    found = []
    for pattern, label in rules:
        match = re.search(pattern, text, re.I)
        if match:
            found.append((label, match.group(0)[:60]))
    return found


def _luhn(digits: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = int(ch)
        if i % 2:
            d = d * 2 - 9 if d > 4 else d * 2
        total += d
    return len(digits) >= 13 and total % 10 == 0


def redact(text: str, kinds: tuple[str, ...] | None = None) -> tuple[str, list[str]]:
    removed = []
    for pattern, replacement, label in _PII:
        if kinds is not None and label not in kinds:
            continue
        hits = 0

        def swap(match: re.Match, replacement: str = replacement, label: str = label) -> str:
            nonlocal hits
            if label == "card-like number" and not _luhn(re.sub(r"\D", "", match.group(0))):
                return match.group(0)
            hits += 1
            return replacement

        text = pattern.sub(swap, text)
        if hits:
            removed.append(label)
    return text, removed


def screen_prompt(text: str) -> Verdict:
    """Input guardrail for a learner's request."""
    clean = normalise(text).strip()
    if len(clean) > MAX_PROMPT:
        return Verdict(allowed=False, action="block", categories=["length"], reasons=[f"the request is longer than {MAX_PROMPT} characters"], text="")
    injection = _findings(clean, _INJECTION)
    abuse = _findings(clean, _ABUSE)
    harmful = _findings(clean, _HARMFUL)
    if injection or abuse or harmful:
        categories = sorted({label for label, _ in injection + abuse + harmful})
        reasons = [f"{label}: “{snippet}”" for label, snippet in injection + abuse + harmful]
        return Verdict(allowed=False, action="block", categories=categories, reasons=reasons, text="")
    redacted, removed = redact(clean)
    if removed:
        return Verdict(allowed=True, action="sanitize", categories=["personal data"], reasons=[f"removed {', '.join(removed)}"], text=redacted)
    return Verdict(allowed=True, action="allow", text=clean)


def screen_document(text: str) -> Verdict:
    """Context guardrail: strip instruction-like lines hidden in an uploaded document."""
    clean = normalise(text)
    kept, dropped = [], []
    for line in clean.split("\n"):
        hits = _findings(line, _INJECTION)
        if hits:
            dropped.append(f"{hits[0][0]}: “{line.strip()[:70]}”")
        else:
            kept.append(line)
    clean = "\n".join(kept)
    clean, removed = redact(clean, _DOCUMENT_PII)
    reasons = [f"removed instruction-like line - {d}" for d in dropped[:8]] + ([f"redacted {', '.join(removed)}"] if removed else [])
    if reasons:
        return Verdict(allowed=True, action="sanitize", categories=(["indirect prompt injection"] if dropped else []) + (["personal data"] if removed else []), reasons=reasons, text=clean)
    return Verdict(allowed=True, action="allow", text=clean)


def screen_narration(lines: list[str]) -> Verdict:
    """Output guardrail: nothing abusive or harmful gets voiced."""
    joined = "\n".join(lines)
    hits = _findings(joined, _ABUSE) + _findings(joined, _HARMFUL)
    if hits:
        return Verdict(allowed=False, action="block", categories=sorted({h[0] for h in hits}), reasons=[f"{label}: “{snippet}”" for label, snippet in hits])
    return Verdict(allowed=True, action="allow")


def dangerous_tex(code_or_tex: str) -> list[str]:
    """TeX primitives that could read or write files during typesetting."""
    return sorted({m.group(1) for m in _TEX_DANGER.finditer(code_or_tex)})


# ----------------------------------------------------- CrewAI task guardrails
#
# CrewAI calls these with the task output; returning (False, feedback) makes
# the agent redo the task with that feedback (up to guardrail_max_retries).


def logged(guard, name: str):
    """Wrap a task guardrail so every verdict - pass or retry - lands in the monitoring log.

    The wrapper deliberately has no return annotation: CrewAI checks guardrail
    signatures and accepts an unannotated one-argument callable.
    """

    def check(output):
        from . import monitoring

        ok, value = guard(output)
        monitoring.log("guardrail", f"crew_{name}", "info" if ok else "warning", stage=f"crew {name}", passed=ok, detail=None if ok else str(value)[:300])
        return ok, value

    check.__name__ = guard.__name__
    check.__doc__ = guard.__doc__
    return check


def _model(output: Any, cls: type[BaseModel]) -> BaseModel:
    if getattr(output, "pydantic", None) is not None:
        return cls.model_validate(output.pydantic.model_dump())
    raw = getattr(output, "raw", output)
    if not isinstance(raw, str):
        return cls.model_validate(raw)
    from .llm.crew_llm import _parse

    try:
        return _parse(raw, cls)  # tolerant of fences and unescaped LaTeX
    except ValueError as exc:
        raise ValidationError.from_exception_data(cls.__name__, [{"type": "json_invalid", "loc": (), "input": raw[:200], "ctx": {"error": str(exc)[:200]}}]) from exc


def guard_analysis(output: Any) -> tuple[bool, Any]:
    from .schemas import ConceptAnalysis

    try:
        analysis = _model(output, ConceptAnalysis)
    except ValidationError as exc:
        return False, f"Return the analysis as valid JSON matching the schema: {exc.errors()[0]['msg']}"
    if len(analysis.key_ideas) < 2:
        return False, "List at least two key ideas, in teaching order."
    if dangerous_tex(analysis.central_formula):
        return False, "The central formula must be plain mathematics without file or macro commands."
    return True, output


def guard_plan(output: Any) -> tuple[bool, Any]:
    from .schemas import LessonPlan

    try:
        plan = _model(output, LessonPlan)
    except ValidationError as exc:
        return False, f"Return the plan as valid JSON matching the schema: {exc.errors()[0]['msg']}"
    if not 2 <= len(plan.sections) <= 8:
        return False, "Plan between two and eight sections."
    for i, q in enumerate(plan.quiz):
        if len(q.choices) != 4 or not 0 <= q.answer < 4:
            return False, f"Quiz question {i + 1} needs exactly four choices and an answer index from 0 to 3."
    return True, output


def guard_storyboard(output: Any) -> tuple[bool, Any]:
    from .schemas import Storyboard

    try:
        board = _model(output, Storyboard)
    except ValidationError as exc:
        return False, f"Return the storyboard as valid JSON matching the schema: {exc.errors()[0]['msg']}"
    if not 2 <= len(board.scenes) <= 8:
        return False, "Write between two and eight scenes."
    for scene in board.scenes:
        if not 1 <= len(scene.beats) <= 6:
            return False, f"Scene '{scene.title}' needs between one and six beats."
        for beat in scene.beats:
            words = len(beat.narration.split())
            if words > 45:
                return False, f"Beat '{beat.id}' narration has {words} words; keep every line under 40 words."
            if "\\" in beat.narration or "$" in beat.narration:
                return False, f"Beat '{beat.id}' narration contains LaTeX; narration is spoken, so write maths in words."
            if not beat.visual.strip():
                return False, f"Beat '{beat.id}' needs a visual description."
    verdict = screen_narration([b.narration for b in board.beats()])
    if not verdict.allowed:
        return False, f"The narration must stay respectful and on topic ({verdict.summary()})."
    return True, output
