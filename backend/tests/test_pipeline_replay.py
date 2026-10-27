"""End to end: the ADK workflow, the CrewAI crew over A2A, the MCP tools, on recorded replies.

Renders real video, so it is skipped unless pytest runs with --slow.
"""

import asyncio
import json
import socket
from dataclasses import replace

import pytest

from conceptflow.agents.writers_room_a2a import serve_in_thread
from conceptflow.config import get_settings
from conceptflow.jobs import JobManager
from conceptflow.schemas import LessonRequest


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.mark.slow
def test_replay_lesson_runs_the_whole_studio(monkeypatch):
    from conceptflow import observability

    observability.setup(get_settings())
    monkeypatch.setenv("A2A_WRITERS_ROOM_URL", serve_in_thread(_free_port()))
    settings = replace(get_settings(), llm_mode="replay", voice_fallback="silent")

    async def run():
        manager = JobManager(settings)
        manager.start()
        job = await manager.submit(LessonRequest(prompt="the derivative", quality="preview", voice="silent"), None)
        events = [e async for e in job.stream()]
        return job, events

    job, events = asyncio.run(run())
    kinds = [(e["type"], e.get("agent"), e.get("status")) for e in events]
    assert job.status == "done", job.error

    # The ADK workflow reached the CrewAI crew over A2A, and the crew ran in order.
    assert [e["phase"] for e in events if e["type"] == "a2a"] == ["discovered", "sent", "completed"]
    crew = [a for t, a, s in kinds if t == "agent" and s == "start" and a in ("concept_analyst", "lesson_planner", "storyboard_director")]
    assert crew == ["concept_analyst", "lesson_planner", "storyboard_director"]

    # The recorded first draft fails in S3, the refiner fixes it, the panel approves.
    routes = [e["route"] for e in events if e["type"] == "route"]
    assert routes == ["direct", "broken", "rendered", "approve"]
    assert job.manifest and len(job.manifest["chapters"]) == 4 and job.manifest["duration"] > 60

    # MCP tools: the library search, the formula checks.
    tools = {e["tool"] for e in events if e["type"] == "tool"}
    assert {"search_lessons", "check_math"} <= tools
    checks = next(e["data"] for e in events if e["type"] == "artifact" and e["name"] == "math_checks")
    assert checks["counts"]["holds"] >= 1

    # Monitoring, tracing, provenance, metrics and the structured report.
    for name in ("monitor.jsonl", "trace.jsonl", "provenance.jsonl", "metrics.json", "report.json", "report.md"):
        assert (job.dir / name).exists(), name
    spans = [json.loads(line) for line in (job.dir / "trace.jsonl").read_text(encoding="utf-8").splitlines()]
    names = {s["name"] for s in spans}
    assert {"lesson", "crewai.writers_room", "a2a.client writers_room", "mcp.check_math"} <= names
    assert len({s["trace_id"] for s in spans}) == 1  # one trace, across both frameworks
    artifacts = {json.loads(line)["artifact"] for line in (job.dir / "provenance.jsonl").read_text(encoding="utf-8").splitlines()}
    assert {"analysis", "plan", "storyboard", "narration", "code_round0", "video"} <= artifacts
    metrics = json.loads((job.dir / "metrics.json").read_text(encoding="utf-8"))
    assert metrics["llm"]["calls"] >= 7 and metrics["llm"]["estimated"] and metrics["a2a"]["exchanges"] == 1
    report = json.loads((job.dir / "report.json").read_text(encoding="utf-8"))
    assert report["title"] == job.manifest["title"] and report["reviews"][-1]["verdict"] == "approve"
