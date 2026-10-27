"""End-to-end evaluation: real lessons through the real API, timed and costed.

Every lesson goes in through ``POST /api/lessons`` and is followed over the
same Server-Sent Events stream the page uses, so the measurement covers the
whole system - HTTP, the ADK workflow, the A2A hop to the CrewAI crew, the MCP
tools, narration, rendering, review and assembly. Afterwards it reads each
lesson's ``/metrics`` and ``/report``, runs the guardrail red-team set against
the API, and projects cost and free-tier capacity.

    python scripts/evaluate.py                         # no key: the recorded showcases (replay)
    python scripts/evaluate.py --prompts "Explain the chain rule" "Why is e^(i*pi) = -1?"
                                                       # with GOOGLE_API_KEY in .env: live lessons
    python scripts/evaluate.py --url https://<your-service>.run.app --prompts "..."
                                                       # evaluate a deployed studio

Writes docs/evaluation.md and docs/evaluation.json.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import os
import platform
import socket
import statistics
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

SHOWCASE_PROMPTS = ["Explain the derivative", "What does the determinant of a matrix mean?"]
# Free-tier requests per day, per model. Google shows the exact numbers for
# your key in AI Studio (Dashboard -> Rate limits); these are typical values.
FREE_TIER_RPD = {
    "gemini-3.8-flash": 20,
    "gemini-3.6-flash": 20,
    "gemini-3-flash-preview": 20,
    "gemini-2.5-flash": 20,
    "gemini-3.5-flash-lite": 500,
    "gemini-3.1-flash-lite": 500,
    "gemini-2.5-flash-lite": 500,
}
# For projecting live running time from a replay run: time to first token and
# output speed per model class (thinking models also think before answering).
LIVE_LATENCY = {"flash": {"first_token": 4.0, "tokens_per_second": 180.0, "thinking_factor": 1.5}, "lite": {"first_token": 1.5, "tokens_per_second": 300.0, "thinking_factor": 1.0}}


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def start_local() -> str:
    os.environ["PORT"] = str(free_port())
    # Evaluation lessons get their own folder, so they do not fill the studio's library.
    os.environ.setdefault("DATA_DIR", str(ROOT / "data" / "evaluation"))
    from conceptflow import server

    url = server.serve_in_thread()
    print(f"studio running at {url} ({server.settings.llm_mode} mode)", flush=True)
    return url


def follow(client: httpx.Client, base: str, job_id: str) -> tuple[str, str | None]:
    """Follow a lesson's event stream to the end, printing what the agents do."""
    with client.stream("GET", f"{base}/api/lessons/{job_id}/events", timeout=httpx.Timeout(None, connect=10)) as stream:
        for line in stream.iter_lines():
            if not line.startswith("data: "):
                continue
            event = json.loads(line[6:])
            kind = event.get("type")
            if kind == "agent" and event.get("status") == "start":
                print(f"   {event.get('t', 0):7.1f}s  {event.get('framework', ''):6} {event.get('agent')}", flush=True)
            elif kind == "a2a":
                print(f"   {event.get('t', 0):7.1f}s  a2a    {event.get('phase')} {event.get('agent', '')}", flush=True)
            elif kind in ("done", "failed"):
                return kind, event.get("message")
    return "failed", "the event stream ended early"


def run_lesson(client: httpx.Client, base: str, prompt: str, options: dict) -> dict:
    print(f"\n>> {prompt}", flush=True)
    started = time.perf_counter()
    response = client.post(f"{base}/api/lessons", data={"prompt": prompt, **options})
    response.raise_for_status()
    job = response.json()
    status, error = follow(client, base, job["id"])
    seconds = time.perf_counter() - started
    metrics = client.get(f"{base}/api/lessons/{job['id']}/metrics").json()
    report = client.get(f"{base}/api/lessons/{job['id']}/report").json() if status == "done" else None
    print(f"   {status} in {seconds:.0f}s" + (f": {error}" if error else ""), flush=True)
    return {"id": job["id"], "prompt": prompt, "mode": job.get("mode"), "status": status, "error": error, "client_seconds": round(seconds, 1), "metrics": metrics, "report": report}


def redteam(client: httpx.Client, base: str) -> dict:
    """Attacks go through the API (a refused request starts nothing); benign prompts through the same screen."""
    from conceptflow.guardrails import screen_prompt
    from conceptflow.redteam import ATTACKS, BENIGN

    attacks = []
    for category, prompt in ATTACKS:
        response = client.post(f"{base}/api/lessons", data={"prompt": prompt})
        attacks.append({"category": category, "blocked": response.status_code == 422, "status": response.status_code})
        if response.status_code == 200:  # should never happen; do not leave a lesson running
            print(f"   !! not blocked: {category}", flush=True)
    benign = [{"prompt": p, "allowed": screen_prompt(p).action == "allow"} for p in BENIGN]
    return {
        "attacks": attacks,
        "blocked": sum(a["blocked"] for a in attacks),
        "attack_total": len(attacks),
        "benign_allowed": sum(b["allowed"] for b in benign),
        "benign_total": len(benign),
        "benign": benign,
    }


def model_class(model: str) -> str:
    return "lite" if "lite" in model else "flash"


def live_projection(metrics: dict, ladders: dict) -> float:
    """Wall time a replay run would take with real model calls (assumption-based)."""
    llm = metrics.get("llm", {})
    replay_llm = llm.get("seconds", 0.0)
    projected = 0.0
    for role, row in llm.get("by_role", {}).items():
        model = (ladders.get(role) or ["gemini-3.8-flash"])[0]
        profile = LIVE_LATENCY[model_class(model)]
        out_per_call = row["tokens_out"] / max(1, row["calls"])
        projected += row["calls"] * (profile["first_token"] + out_per_call * profile["thinking_factor"] / profile["tokens_per_second"])
    return metrics.get("wall_seconds", 0.0) - replay_llm + projected


def free_tier_lessons_per_day(calls_by_role: dict[str, int], ladders: dict[str, list[str]]) -> int:
    """Simulate the router's ladders against daily quotas: how many lessons fit in one day?"""
    quota = {m: FREE_TIER_RPD.get(m, 20) for ladder in ladders.values() for m in ladder}
    lessons = 0
    while lessons < 10_000:
        for role, calls in calls_by_role.items():
            for _ in range(calls):
                model = next((m for m in ladders.get(role, []) if quota.get(m, 0) > 0), None)
                if model is None:
                    return lessons
                quota[model] -= 1
        lessons += 1
    return lessons


def mean(values: list[float]) -> float:
    values = [v for v in values if v is not None]
    return statistics.fmean(values) if values else 0.0


def write_report(results: list[dict], red: dict, health: dict, base: str, local: bool) -> str:
    from conceptflow.metrics import CLOUD_RUN, GEMINI_PRICES, INR_PER_USD, PRICES_AS_OF, VOICE_USD_PER_CHAR

    done = [r for r in results if r["status"] == "done"]
    ladders = health.get("ladders", {})
    mode = health.get("mode", "?")
    md: list[str] = []
    md += [
        "# ConceptFlow AI - end-to-end evaluation",
        "",
        f"Run on {dt.datetime.now().astimezone().isoformat(timespec='minutes')} against {'a local studio' if local else base} "
        f"in **{mode}** mode, {platform.system()} {platform.release()}, {os.cpu_count()} logical CPUs.",
        "Each lesson went through `POST /api/lessons` and was followed over the same event stream the page uses, so the timings cover the "
        "whole system: the ADK workflow, the A2A hop to the CrewAI crew, the MCP tools, narration, rendering, review and assembly.",
        "",
    ]
    if mode == "replay":
        md += [
            "> **Replay mode.** No Gemini key was set, so the model replies were the recorded showcase replies, given after a short",
            "> simulated pause. Everything else ran for real. Token counts are estimated (4 characters per token), and live running",
            "> times are projected below. Run this script again with `GOOGLE_API_KEY` set to replace the estimates with measurements.",
            "",
        ]

    md += ["## Lessons", "", "| Lesson | Status | Wall time | Video | Time / video | LLM calls | Tokens in / out | Tool calls | A2A | Render | Rounds | Review mean | Cost (free tier) | Cost (paid tier) |", "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in results:
        m = r["metrics"]
        llm, cost = m.get("llm", {}), m.get("cost", {})
        md.append(
            f"| {r['prompt'][:48]} | {r['status']} | {m.get('wall_seconds', 0):.0f} s | {m.get('video_seconds') or 0:.0f} s | "
            f"{m.get('realtime_factor') or 0:.2f} | {llm.get('calls', 0)} | {llm.get('tokens_in', 0):,} / {llm.get('tokens_out', 0):,}"
            f"{' (est.)' if llm.get('estimated') else ''} | {m.get('tools', {}).get('calls', 0)} | {m.get('a2a', {}).get('seconds') or 0:.1f} s | "
            f"{m.get('render', {}).get('render_seconds', 0):.0f} s | {m.get('render', {}).get('rounds', 0)} | {m.get('reviews', {}).get('final_mean') or '-'} | "
            f"${cost.get('free_tier_usd', 0):.4f} | ${cost.get('paid_tier_total_usd', 0):.4f} |"
        )

    # where the time goes
    stages: dict[str, list[float]] = {}
    frameworks: dict[str, str] = {}
    for r in done:
        for s in r["metrics"].get("stages", []):
            stages.setdefault(s["agent"], []).append(s["seconds"])
            frameworks[s["agent"]] = s.get("framework") or "?"
    if stages:
        total = sum(mean(v) for v in stages.values()) or 1
        md += ["", "## Where the time goes (mean per lesson)", "", "| Agent | Framework | Seconds | Share |", "|---|---|---|---|"]
        for agent, values in sorted(stages.items(), key=lambda kv: -mean(kv[1])):
            md.append(f"| {agent} | {frameworks[agent]} | {mean(values):.1f} | {100 * mean(values) / total:.0f}% |")
        md += ["", "Agents running in parallel (narrator with coder, visual critic with the formula check) overlap, so shares can add up to more than the wall time."]

    # model usage
    roles: dict[str, dict] = {}
    for r in done:
        for role, row in r["metrics"].get("llm", {}).get("by_role", {}).items():
            agg = roles.setdefault(role, {"calls": [], "tin": [], "tout": [], "usd": []})
            agg["calls"].append(row["calls"])
            agg["tin"].append(row["tokens_in"])
            agg["tout"].append(row["tokens_out"])
            agg["usd"].append(row["usd"])
    if roles:
        md += ["", "## Model calls by role (mean per lesson)", "", "| Role | First-choice model | Calls | Tokens in | Tokens out | Paid-tier cost |", "|---|---|---|---|---|---|"]
        for role, agg in roles.items():
            md.append(f"| {role} | {(ladders.get(role) or ['?'])[0]} | {mean(agg['calls']):.1f} | {mean(agg['tin']):,.0f} | {mean(agg['tout']):,.0f} | ${mean(agg['usd']):.4f} |")

    # cost projection
    if done:
        per_lesson_llm = mean([r["metrics"]["cost"]["paid_tier_llm_usd"] for r in done])
        per_lesson_host = mean([r["metrics"]["cost"]["hosting_usd"] for r in done])
        chars = mean([r["metrics"]["voice"]["characters"] for r in done])
        eleven = chars * VOICE_USD_PER_CHAR["elevenlabs"]
        sarvam = chars * VOICE_USD_PER_CHAR["sarvam"]
        md += [
            "",
            "## Cost per lesson",
            "",
            "| Item | Free tier (as built) | Paid tier, per lesson | Per 1,000 lessons |",
            "|---|---|---|---|",
            f"| Gemini (both frameworks) | $0 - within daily quotas | ${per_lesson_llm:.4f} | ${1000 * per_lesson_llm:,.2f} |",
            f"| Voice, ElevenLabs ({chars:,.0f} characters) | $0 - 10k credits/month (~{10_000 / max(chars, 1):.0f} lessons) | ${eleven:.4f} | ${1000 * eleven:,.2f} |",
            f"| Voice, Sarvam Bulbul v3 (Indian languages) | $0 - Rs 1,000 sign-up credit | ${sarvam:.4f} | ${1000 * sarvam:,.2f} |",
            "| Voice, Edge neural voices (fallback) | $0 | $0 | $0 |",
            f"| Hosting, Cloud Run {CLOUD_RUN['vcpu']} vCPU / {CLOUD_RUN['gib']} GiB for the wall time | $0 - monthly free allowance | ${per_lesson_host:.4f} | ${1000 * per_lesson_host:,.2f} |",
            "",
            f"Prices as of {PRICES_AS_OF}: Gemini Standard tier per 1M tokens (input / output): "
            + ", ".join(f"{m} ${a:.2f} / ${b:.2f}" for m, (a, b) in GEMINI_PRICES.items() if any(m in ladder for ladder in ladders.values()))
            + f". ElevenLabs Multilingual v2 $0.10 per 1k characters; Sarvam Bulbul v3 Rs 30 and Mayura Rs 20 per 10k characters (Rs {INR_PER_USD:.0f} = $1). "
            "Gemini 3.6/3.8 Flash prices double on 1 Jan 2027.",
        ]
        calls_by_role = {role: math.ceil(mean(agg["calls"])) for role, agg in roles.items()}
        capacity = free_tier_lessons_per_day(calls_by_role, ladders)
        md += [
            "",
            "## Free-tier capacity",
            "",
            f"Simulating the router's model ladders against typical free-tier daily quotas ({', '.join(f'{m} {n}' for m, n in FREE_TIER_RPD.items() if any(m in ladder for ladder in ladders.values()))} requests/day), "
            f"one key can make about **{capacity} lessons a day** at {sum(calls_by_role.values())} model calls per lesson. The exact quotas for a key are in AI Studio "
            "(Dashboard -> Rate limits); a revision round adds a refiner call and a second review.",
        ]
        if mode == "replay":
            projected = mean([live_projection(r["metrics"], ladders) for r in done])
            md += [
                "",
                "## Projected live running time",
                "",
                f"Replacing the replayed replies with real calls (first token after ~{LIVE_LATENCY['flash']['first_token']:.0f} s for Flash and "
                f"~{LIVE_LATENCY['lite']['first_token']:.1f} s for Flash-Lite; {LIVE_LATENCY['flash']['tokens_per_second']:.0f} and "
                f"{LIVE_LATENCY['lite']['tokens_per_second']:.0f} output tokens/s; Flash thinking adds ~50% output) gives about "
                f"**{projected / 60:.1f} minutes per lesson** on this machine at preview quality, before quota waits. Rendering at HD roughly doubles the render share.",
            ]

    md += [
        "",
        "## Guardrail red-team",
        "",
        f"- Attacks refused by the API: **{red['blocked']} / {red['attack_total']}**",
        f"- Ordinary maths requests passed untouched: **{red['benign_allowed']} / {red['benign_total']}** (false-positive rate {100 * (1 - red['benign_allowed'] / max(1, red['benign_total'])):.0f}%)",
        "",
        "| Attack | Refused |",
        "|---|---|",
    ] + [f"| {a['category']} | {'yes' if a['blocked'] else '**no**'} |" for a in red["attacks"]]

    md += [
        "",
        "## Reproduce",
        "",
        "```bash",
        "python scripts/evaluate.py" + ("" if mode == "replay" else ' --prompts "your prompt"'),
        "```",
    ]
    return "\n".join(md) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--url", help="evaluate a running studio instead of starting one")
    parser.add_argument("--prompts", nargs="*", help="lessons to make (default: the showcases in replay mode)")
    parser.add_argument("--quality", default="preview")
    parser.add_argument("--voice", default="auto")
    parser.add_argument("--audience", default="undergraduate")
    parser.add_argument("--length", default="standard")
    parser.add_argument("--out", default=str(ROOT / "docs" / "evaluation.md"))
    args = parser.parse_args()

    base = args.url.rstrip("/") if args.url else start_local()
    with httpx.Client(timeout=60) as client:
        health = client.get(f"{base}/api/health").json()
        prompts = args.prompts or SHOWCASE_PROMPTS
        options = {"quality": args.quality, "voice": args.voice, "audience": args.audience, "length": args.length}
        results = [run_lesson(client, base, prompt, options) for prompt in prompts]
        print("\nred-team ...", flush=True)
        red = redteam(client, base)
    markdown = write_report(results, red, health, base, local=not args.url)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(markdown, encoding="utf-8")
    out.with_suffix(".json").write_text(json.dumps({"health": {k: health.get(k) for k in ("mode", "providers", "ladders")}, "lessons": results, "redteam": red}, indent=1, default=str), encoding="utf-8")
    print(f"\nwrote {out} and {out.with_suffix('.json')}")


if __name__ == "__main__":
    main()
