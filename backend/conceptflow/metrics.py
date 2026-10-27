"""End-to-end performance of one lesson: running time, model calls, tokens and cost.

Computed when a lesson ends from the records written by the monitoring
callbacks (``monitor.jsonl``), the event stream (``events.jsonl``) and the
trace (``trace.jsonl``), and saved as ``metrics.json``. The Performance view
shows it; ``scripts/evaluate.py`` aggregates it across runs.

Cost model
    * Gemini on the free tier costs nothing. The projection prices the same
      tokens at the paid (Standard) tier, model by model.
    * Replay runs make no API calls: their tokens are estimated from the
      prompts and recorded replies, and priced as the role's first-choice model.
    * Voice: ElevenLabs and Sarvam are priced per character; Edge is free.
    * Hosting: Cloud Run, instance-based billing, for the lesson's wall time.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .config import Settings, get_settings

PRICES_AS_OF = "2026-10-13"
# USD per 1M tokens on the paid (Standard) tier: (input, output including thinking).
# Source: ai.google.dev/gemini-api/docs/pricing. Gemini 3.6/3.8 Flash list at
# $0.75/$3.75 until 31 Dec 2026, then $1.50/$7.50.
GEMINI_PRICES: dict[str, tuple[float, float]] = {
    "gemini-3.8-flash": (0.75, 3.75),
    "gemini-3.6-flash": (0.75, 3.75),
    "gemini-3-flash-preview": (0.50, 3.00),
    "gemini-3.5-flash-lite": (0.30, 2.50),
    "gemini-3.1-flash-lite": (0.25, 1.50),
    "gemini-2.5-flash": (0.30, 2.50),
    "gemini-2.5-flash-lite": (0.10, 0.40),
}
FALLBACK_PRICE = (0.75, 3.75)  # an unlisted model is priced as a Flash model
INR_PER_USD = 88.0  # assumption for converting Sarvam's rupee prices
VOICE_USD_PER_CHAR = {
    "elevenlabs": 0.10 / 1000,  # Multilingual v2, API rate
    "sarvam": 30 / 10_000 / INR_PER_USD,  # Bulbul v3: Rs 30 per 10K characters
    "edge": 0.0,
    "silent": 0.0,
}
TRANSLATE_USD_PER_CHAR = 20 / 10_000 / INR_PER_USD  # Sarvam Mayura: Rs 20 per 10K characters
# Cloud Run, Tier 1 region, instance-based billing (approximate list prices).
CLOUD_RUN = {"vcpu": 4, "gib": 8, "usd_per_vcpu_second": 0.000018, "usd_per_gib_second": 0.000002}


def _jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            try:
                rows.append(json.loads(line))
            except ValueError:
                continue
    return rows


def price_of(model: str, role: str, settings: Settings) -> tuple[str, tuple[float, float]]:
    """The model a call is priced as, and its (input, output) price per 1M tokens."""
    if model.startswith("replay:"):
        ladder = settings.ladders.get(role) or []
        model = ladder[0] if ladder else "gemini-3.8-flash"
    return model, GEMINI_PRICES.get(model, FALLBACK_PRICE)


def _llm(monitor: list[dict], settings: Settings) -> dict[str, Any]:
    calls = [r for r in monitor if r.get("source") == "llm"]
    ok = [r for r in calls if r.get("kind") == "call"]
    out: dict[str, Any] = {
        "calls": len(ok),
        "failed_calls": len(calls) - len(ok),
        "tokens_in": 0,
        "tokens_out": 0,
        "seconds": 0.0,
        "estimated": any(r.get("estimated") for r in ok),
        "paid_tier_usd": 0.0,
        "by_role": {},
        "by_model": {},
        "by_framework": {},
    }
    for r in ok:
        role, model = r.get("role", "?"), r.get("model", "?")
        tin, tout = int(r.get("tokens_in") or 0), int(r.get("tokens_out") or 0)
        priced_as, (p_in, p_out) = price_of(model, role, settings)
        usd = tin / 1e6 * p_in + tout / 1e6 * p_out
        out["tokens_in"] += tin
        out["tokens_out"] += tout
        out["seconds"] += float(r.get("seconds") or 0)
        out["paid_tier_usd"] += usd
        for key, name in (("by_role", role), ("by_model", priced_as), ("by_framework", r.get("framework", "?"))):
            row = out[key].setdefault(name, {"calls": 0, "tokens_in": 0, "tokens_out": 0, "seconds": 0.0, "usd": 0.0})
            row["calls"] += 1
            row["tokens_in"] += tin
            row["tokens_out"] += tout
            row["seconds"] = round(row["seconds"] + float(r.get("seconds") or 0), 3)
            row["usd"] = round(row["usd"] + usd, 6)
    out["seconds"] = round(out["seconds"], 3)
    out["paid_tier_usd"] = round(out["paid_tier_usd"], 6)
    return out


def _stages(events: list[dict]) -> list[dict]:
    stages: dict[str, dict] = {}
    opened: dict[str, float] = {}
    for e in events:
        if e.get("type") != "agent":
            continue
        agent = e.get("agent", "?")
        if e.get("status") == "start":
            opened[agent] = e.get("t", 0.0)
        elif e.get("status") == "done" and agent in opened:
            row = stages.setdefault(agent, {"agent": agent, "framework": e.get("framework"), "seconds": 0.0, "runs": 0, "first_start": opened[agent]})
            row["seconds"] = round(row["seconds"] + e.get("t", 0.0) - opened.pop(agent), 2)
            row["runs"] += 1
    return sorted(stages.values(), key=lambda r: r["first_start"])


def _tools(monitor: list[dict]) -> dict[str, Any]:
    by_tool: dict[str, dict] = {}
    for r in monitor:
        source, kind = r.get("source"), r.get("kind")
        if (source, kind) not in (("mcp", "tool_call"), ("mcp", "tool_error"), ("crewai", "tool_result"), ("adk", "tool_result")):
            continue
        row = by_tool.setdefault(r.get("tool", "?"), {"calls": 0, "errors": 0, "seconds": 0.0, "callers": []})
        row["calls"] += 1
        row["errors"] += int(kind == "tool_error")
        row["seconds"] = round(row["seconds"] + float(r.get("seconds") or 0), 3)
        caller = {"mcp": "workflow", "crewai": "crewai agent", "adk": "adk agent"}[source]
        if caller not in row["callers"]:
            row["callers"].append(caller)
    return {"calls": sum(r["calls"] for r in by_tool.values()), "by_tool": by_tool}


def guardrail_summary(events: list[dict], logs: list[dict]) -> dict[str, Any]:
    """Every guardrail check of a lesson, by stage: how many ran and how many intervened."""
    stages: dict[str, dict] = {}
    for e in events:
        row = stages.setdefault(e.get("stage") or "?", {"checks": 0, "interventions": 0})
        row["checks"] += 1
        row["interventions"] += int(e.get("action") not in (None, "allow"))
    for r in logs:
        row = stages.setdefault(r.get("stage") or r.get("kind") or "?", {"checks": 0, "interventions": 0})
        row["checks"] += 1
        row["interventions"] += int(r.get("level") in ("warning", "error"))
    return {
        "checks": sum(r["checks"] for r in stages.values()),
        "interventions": sum(r["interventions"] for r in stages.values()),
        "by_stage": stages,
    }


def compute(job_dir: Path, settings: Settings | None = None, status: str | None = None, manifest: dict | None = None) -> dict[str, Any]:
    """Measure a lesson. ``status`` and ``manifest`` are given when measuring just before the end is announced."""
    settings = settings or get_settings()
    events = _jsonl(job_dir / "events.jsonl")
    monitor = _jsonl(job_dir / "monitor.jsonl")
    trace = _jsonl(job_dir / "trace.jsonl")

    started = next((e.get("t", 0.0) for e in events if e.get("type") == "started"), 0.0)
    ended_event = next((e for e in reversed(events) if e.get("type") in ("done", "failed")), None)
    ended = ended_event.get("t", 0.0) if ended_event else (events[-1].get("t", 0.0) if events else 0.0)
    wall = max(0.0, ended - started)
    status = status or (ended_event or {}).get("type", "running")
    manifest = manifest if manifest is not None else (ended_event or {}).get("manifest") or {}

    llm = _llm(monitor, settings)

    scenes = [e for e in events if e.get("type") == "scene" and e.get("status") in ("rendered", "failed")]
    render = {
        "scene_renders": len(scenes),
        "failed": sum(1 for e in scenes if e.get("status") == "failed"),
        "render_seconds": round(sum(float(e.get("seconds") or 0) for e in scenes), 1),
        "rounds": max([e.get("round", 0) for e in events if e.get("type") == "agent" and e.get("agent") == "renderer"] or [0]),
    }

    narration = next((e.get("data") or {} for e in reversed(events) if e.get("type") == "artifact" and e.get("name") == "narration"), {})
    lines = list((narration.get("lines") or {}).values())
    chars = sum(len(line) for line in lines)
    provider = narration.get("provider") or "silent"
    language = narration.get("language") or "en"
    voice_usd = chars * VOICE_USD_PER_CHAR.get(provider, 0.0)
    translate_usd = chars * TRANSLATE_USD_PER_CHAR if language != "en" and settings.sarvam_key else 0.0
    voice = {
        "provider": provider,
        "language": language,
        "lines": len(lines),
        "characters": chars,
        "speech_seconds": narration.get("seconds"),
        "usd": round(voice_usd + translate_usd, 6),
    }

    a2a = [e for e in events if e.get("type") == "a2a"]
    reviews = [e for e in events if e.get("type") == "review"]
    guardrail_events = [e for e in events if e.get("type") == "guardrail"]
    guardrail_logs = [r for r in monitor if r.get("source") == "guardrail"]
    compute_usd = wall * (CLOUD_RUN["vcpu"] * CLOUD_RUN["usd_per_vcpu_second"] + CLOUD_RUN["gib"] * CLOUD_RUN["usd_per_gib_second"])
    scopes: dict[str, int] = {}
    for span in trace:
        scopes[span.get("scope") or "?"] = scopes.get(span.get("scope") or "?", 0) + 1

    return {
        "status": status,
        "mode": next((e.get("mode") for e in events if e.get("type") == "started"), None),
        "wall_seconds": round(wall, 1),
        "video_seconds": manifest.get("duration"),
        "realtime_factor": round(wall / manifest["duration"], 2) if manifest.get("duration") else None,
        "stages": _stages(events),
        "llm": llm,
        "tools": _tools(monitor),
        "a2a": {
            "exchanges": sum(1 for e in a2a if e.get("phase") == "completed"),
            "seconds": next((e.get("seconds") for e in a2a if e.get("phase") == "completed"), None),
            "stream_updates": next((e.get("updates") for e in a2a if e.get("phase") == "completed"), None),
        },
        "render": render,
        "voice": voice,
        "reviews": {
            "rounds": len(reviews),
            "final_mean": reviews[-1].get("mean") if reviews else None,
            "verdicts": [r.get("verdict") for r in reviews],
        },
        "guardrails": guardrail_summary(guardrail_events, guardrail_logs),
        "trace": {"spans": len(trace), "by_scope": scopes, "trace_id": next((s.get("trace_id") for s in trace), None)},
        "cost": {
            "free_tier_usd": round(voice_usd + translate_usd, 6),
            "paid_tier_llm_usd": llm["paid_tier_usd"],
            "voice_usd": round(voice_usd + translate_usd, 6),
            "hosting_usd": round(compute_usd, 6),
            "paid_tier_total_usd": round(llm["paid_tier_usd"] + voice_usd + translate_usd + compute_usd, 6),
            "prices_as_of": PRICES_AS_OF,
            "assumptions": [
                "Gemini free tier: $0; paid projection at Standard-tier list prices",
                "replay runs: tokens estimated at 4 characters per token" if llm["estimated"] else "tokens from the API's usage metadata",
                f"Sarvam prices converted at Rs {INR_PER_USD:.0f} per USD",
                f"hosting: Cloud Run {CLOUD_RUN['vcpu']} vCPU / {CLOUD_RUN['gib']} GiB, instance-based billing, for the wall time",
            ],
        },
    }


def write(job_dir: Path, settings: Settings | None = None, status: str | None = None, manifest: dict | None = None) -> dict[str, Any]:
    data = compute(job_dir, settings, status, manifest)
    (job_dir / "metrics.json").write_text(json.dumps(data, indent=2), encoding="utf-8")
    return data
