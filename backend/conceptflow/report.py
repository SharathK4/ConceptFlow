"""The lesson report: every lesson's structured final output.

``FinalReport`` is one Pydantic model that gathers what the agents made and
how they made it - the lesson (chapters, quiz, core insight), its sources,
the review scores, the formula checks, the guardrail actions, the provenance
of every artifact and the run's performance and cost. It is validated, then
saved twice: ``report.json`` for machines and ``report.md`` (Markdown tables)
for people. The page offers both as downloads.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from .schemas import QuizItem


class ChapterRow(BaseModel):
    scene: str
    title: str
    start: float
    end: float


class AgentStep(BaseModel):
    agent: str
    framework: str
    runs: int
    seconds: float


class ReviewRow(BaseModel):
    round: int
    mean: float
    verdict: str
    scores: dict[str, int]
    issues: int


class FormulaRow(BaseModel):
    latex: str
    status: str = Field(description="holds | differs | parsed | unchecked")
    detail: str = ""


class SourceRow(BaseModel):
    kind: str = Field(description="request | document | reference | library")
    title: str
    detail: str = ""


class GuardrailRow(BaseModel):
    stage: str
    outcome: str = Field(description="allow | passed | sanitize | block | intervened")
    count: int = 1
    detail: str = ""


class ProvenanceRow(BaseModel):
    artifact: str
    producer: str
    framework: str
    model: str | None = None
    sha: str
    inputs: dict[str, str] = Field(default_factory=dict)


class Performance(BaseModel):
    wall_seconds: float
    video_seconds: float | None = None
    llm_calls: int
    tokens_in: int
    tokens_out: int
    tokens_estimated: bool
    tool_calls: int
    a2a_seconds: float | None = None
    render_seconds: float
    review_rounds: int
    free_tier_usd: float
    paid_tier_usd: float


class FinalReport(BaseModel):
    lesson_id: str
    title: str
    logline: str
    created: str
    mode: str
    request: dict[str, Any]
    video: str
    duration: float
    language: str
    voice: str | None = None
    quality: str
    core_insight: str = ""
    objectives: list[str] = Field(default_factory=list)
    takeaway: str = ""
    chapters: list[ChapterRow]
    quiz: list[QuizItem] = Field(default_factory=list)
    agents: list[AgentStep]
    reviews: list[ReviewRow]
    formula_checks: list[FormulaRow] = Field(default_factory=list)
    sources: list[SourceRow] = Field(default_factory=list)
    guardrails: list[GuardrailRow] = Field(default_factory=list)
    provenance: list[ProvenanceRow] = Field(default_factory=list)
    performance: Performance
    trace_id: str | None = None


def _jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def build(job, state: dict, metrics: dict) -> FinalReport:
    manifest = state.get("final") or job.manifest or {}
    analysis = state.get("analysis") or {}
    plan = state.get("plan") or {}
    request = dict(job.request)

    sources = [SourceRow(kind="request", title=request.get("prompt") or "(no prompt)", detail=f"{request.get('audience')}, {request.get('length')}")]
    document = state.get("document")
    if document:
        sources.append(SourceRow(kind="document", title=document.get("title") or document.get("name", ""), detail=f"{document.get('name')}, {document.get('pages')} page(s), parsed with read_document"))
    grounding = state.get("grounding")
    if grounding and grounding.get("ok"):
        sources.append(SourceRow(kind="reference", title=grounding.get("title", ""), detail=grounding.get("url", "")))
    for hit in state.get("related") or []:
        sources.append(SourceRow(kind="library", title=hit.get("title", ""), detail=f"lesson {hit.get('id')}"))

    rows: dict[tuple[str, str], GuardrailRow] = {}

    def add(stage: str, outcome: str, detail: str) -> None:
        row = rows.get((stage, outcome))
        if row is None:
            rows[(stage, outcome)] = GuardrailRow(stage=stage, outcome=outcome, detail=detail[:240])
        else:
            row.count += 1

    for e in job.events:
        if e.get("type") == "guardrail":
            add(e.get("stage") or "?", e.get("action") or "?", "; ".join(e.get("reasons") or []))
    for r in _jsonl(job.dir / "monitor.jsonl"):
        if r.get("source") == "guardrail":
            intervened = r.get("level") in ("warning", "error")
            add(r.get("stage") or r.get("kind", "?"), "intervened" if intervened else "passed", str(r.get("detail") or ""))
    guardrails = list(rows.values())

    llm = metrics.get("llm", {})
    cost = metrics.get("cost", {})
    return FinalReport(
        lesson_id=job.id,
        title=manifest.get("title") or job.title,
        logline=manifest.get("logline", ""),
        created=dt.datetime.fromtimestamp(job.created).astimezone().isoformat(timespec="seconds"),
        mode=job.mode,
        request={k: request.get(k) for k in ("prompt", "audience", "length", "language", "voice", "quality", "upload_name")},
        video=manifest.get("video", ""),
        duration=float(manifest.get("duration") or 0),
        language=manifest.get("language", request.get("language", "en")),
        voice=manifest.get("voice"),
        quality=manifest.get("quality", request.get("quality", "")),
        core_insight=analysis.get("core_insight", ""),
        objectives=plan.get("objectives", []),
        takeaway=plan.get("takeaway", ""),
        chapters=[ChapterRow(scene=c["scene"], title=c["title"], start=c["start"], end=c["end"]) for c in manifest.get("chapters", [])],
        quiz=[QuizItem.model_validate(q) for q in manifest.get("quiz", [])],
        agents=[AgentStep(agent=s["agent"], framework=s.get("framework") or "?", runs=s["runs"], seconds=s["seconds"]) for s in metrics.get("stages", [])],
        reviews=[
            ReviewRow(round=r["round"], mean=r["mean"], verdict=r["verdict"], scores=r["scores"], issues=len(r.get("issues", [])))
            for r in state.get("reviews") or []
        ],
        formula_checks=[FormulaRow(latex=c["latex"], status=c["status"], detail=str(c.get("detail") or "")[:200]) for c in state.get("math_checks") or []],
        sources=sources,
        guardrails=guardrails[:30],
        provenance=[
            ProvenanceRow(artifact=p["artifact"], producer=p["producer"], framework=p["framework"], model=p.get("model"), sha=p["sha"], inputs=p.get("inputs") or {})
            for p in _jsonl(job.dir / "provenance.jsonl")
        ],
        performance=Performance(
            wall_seconds=metrics.get("wall_seconds", 0.0),
            video_seconds=metrics.get("video_seconds"),
            llm_calls=llm.get("calls", 0),
            tokens_in=llm.get("tokens_in", 0),
            tokens_out=llm.get("tokens_out", 0),
            tokens_estimated=bool(llm.get("estimated")),
            tool_calls=metrics.get("tools", {}).get("calls", 0),
            a2a_seconds=metrics.get("a2a", {}).get("seconds"),
            render_seconds=metrics.get("render", {}).get("render_seconds", 0.0),
            review_rounds=metrics.get("reviews", {}).get("rounds", 0),
            free_tier_usd=cost.get("free_tier_usd", 0.0),
            paid_tier_usd=cost.get("paid_tier_total_usd", 0.0),
        ),
        trace_id=metrics.get("trace", {}).get("trace_id"),
    )


def _cell(value: Any) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


def _table(headers: list[str], rows: list[list[Any]]) -> list[str]:
    out = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
    out += ["| " + " | ".join(_cell(v) for v in row) + " |" for row in rows]
    return out


def to_markdown(report: FinalReport) -> str:
    r, p = report, report.performance
    md = [f"# {r.title}", "", f"_{r.logline}_", ""]
    md += _table(
        ["Lesson", "Mode", "Length", "Language", "Voice", "Quality", "Created"],
        [[r.lesson_id, r.mode, f"{r.duration:.0f} s", r.language, r.voice or "-", r.quality, r.created]],
    )
    if r.core_insight:
        md += ["", f"**Core insight.** {r.core_insight}"]
    if r.objectives:
        md += ["", "**Objectives**", ""] + [f"- {o}" for o in r.objectives]
    if r.takeaway:
        md += ["", f"**Takeaway.** {r.takeaway}"]
    md += ["", "## Chapters", ""] + _table(["Scene", "Title", "Starts", "Ends"], [[c.scene, c.title, f"{c.start:.1f} s", f"{c.end:.1f} s"] for c in r.chapters])
    if r.quiz:
        md += ["", "## Quiz", ""]
        for i, q in enumerate(r.quiz, 1):
            md.append(f"{i}. {q.question}")
            md += [f"   - {'**' if j == q.answer else ''}{c}{'**' if j == q.answer else ''}" for j, c in enumerate(q.choices)]
            md.append(f"   - _Why:_ {q.why}")
    md += ["", "## Sources", ""] + _table(["Kind", "Title", "Detail"], [[s.kind, s.title, s.detail] for s in r.sources])
    md += ["", "## How the agents worked", ""] + _table(["Agent", "Framework", "Runs", "Seconds"], [[a.agent, a.framework, a.runs, f"{a.seconds:.1f}"] for a in r.agents])
    if r.reviews:
        keys = list(r.reviews[0].scores)
        md += ["", "## Review rounds", ""] + _table(
            ["Round", *keys, "Mean", "Issues", "Verdict"],
            [[v.round, *[v.scores.get(k, "") for k in keys], f"{v.mean:.2f}", v.issues, v.verdict] for v in r.reviews],
        )
    if r.formula_checks:
        md += ["", "## Formula checks (SymPy via MCP)", ""] + _table(["Formula", "Result", "Detail"], [[f"`{f.latex}`", f.status, f.detail] for f in r.formula_checks])
    md += ["", "## Guardrails", ""] + (
        _table(["Stage", "Outcome", "Times", "Detail"], [[g.stage, g.outcome, g.count, g.detail or "-"] for g in r.guardrails])
        if r.guardrails
        else ["No guardrail records."]
    )
    md += ["", "## Performance and cost", ""] + _table(
        ["Wall time", "Video", "LLM calls", "Tokens in / out", "Tool calls", "A2A round trip", "Render time", "Review rounds", "Cost (free tier)", "Cost (paid tier)"],
        [[
            f"{p.wall_seconds:.0f} s",
            f"{p.video_seconds:.0f} s" if p.video_seconds else "-",
            p.llm_calls,
            f"{p.tokens_in:,} / {p.tokens_out:,}{' (est.)' if p.tokens_estimated else ''}",
            p.tool_calls,
            f"{p.a2a_seconds:.1f} s" if p.a2a_seconds else "-",
            f"{p.render_seconds:.0f} s",
            p.review_rounds,
            f"${p.free_tier_usd:.4f}",
            f"${p.paid_tier_usd:.4f}",
        ]],
    )
    md += ["", "## Provenance", ""] + _table(
        ["Artifact", "Producer", "Framework", "Model", "SHA-256 (16)", "Made from"],
        [[a.artifact, a.producer, a.framework, a.model or "-", f"`{a.sha}`", ", ".join(f"{k}:{v[:8]}" for k, v in a.inputs.items()) or "-"] for a in r.provenance],
    )
    if r.trace_id:
        md += ["", f"Trace id `{r.trace_id}` (search for it in Langfuse, or see trace.jsonl)."]
    return "\n".join(md) + "\n"


def write(job, state: dict, metrics: dict) -> FinalReport:
    report = build(job, state, metrics)
    (job.dir / "report.json").write_text(report.model_dump_json(indent=2), encoding="utf-8")
    (job.dir / "report.md").write_text(to_markdown(report), encoding="utf-8")
    return report
