"""Monitoring callbacks, metrics and the structured report."""

import asyncio
import json
from types import SimpleNamespace

from conceptflow import metrics, monitoring, report
from conceptflow.config import get_settings


def _tool(name: str):
    return SimpleNamespace(name=name)


def _tool_context():
    return SimpleNamespace(agent_name="pedagogy_critic", invocation_id="inv-1")


def test_run_log_records_and_surfaces_warnings(tmp_path):
    seen = []
    run_log = monitoring.RunLog(tmp_path, seen.append)
    token = monitoring.bind(run_log)
    try:
        monitoring.log("adk", "agent_start", agent="manim_coder")
        monitoring.log("mcp", "tool_error", "warning", tool="check_math", error="timed out")
        monitoring.record_llm_call("crewai", "planner", "gemini-3.5-flash-lite", 1.2, 900, 300)
    finally:
        monitoring.unbind(token)
    rows = [json.loads(line) for line in (tmp_path / "monitor.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [r["kind"] for r in rows] == ["agent_start", "tool_error", "call"]
    assert rows[2]["tokens_in"] == 900 and rows[2]["framework"] == "crewai"
    assert [e["level"] for e in seen] == ["warning"]  # only warnings and errors reach the page
    assert monitoring.current() is None


def test_guardrail_plugin_blocks_bad_tool_arguments_and_malformed_results():
    plugin = monitoring.GuardrailPlugin()
    blocked = asyncio.run(plugin.before_tool_callback(tool=_tool("check_math"), tool_args={"latex": r"\input{/etc/passwd}"}, tool_context=_tool_context()))
    assert blocked and "input" in blocked["error"]
    assert asyncio.run(plugin.before_tool_callback(tool=_tool("check_math"), tool_args={"latex": "x^2"}, tool_context=_tool_context())) is None
    outside = asyncio.run(plugin.before_tool_callback(tool=_tool("read_document"), tool_args={"path": "/etc/passwd"}, tool_context=_tool_context()))
    assert outside and "blocked by guardrail" in outside["error"]

    good = {"structuredContent": {"ok": True, "operation": "verify", "input": "1=1", "is_identity": True}, "content": [], "isError": False}
    bad = {"structuredContent": {"ok": "perhaps"}, "content": [], "isError": False}
    assert asyncio.run(plugin.after_tool_callback(tool=_tool("check_math"), tool_args={}, tool_context=_tool_context(), result=good)) is None
    rejected = asyncio.run(plugin.after_tool_callback(tool=_tool("check_math"), tool_args={}, tool_context=_tool_context(), result=bad))
    assert rejected and "malformed" in rejected["error"]


def _write_jsonl(path, rows):
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")


def _fake_lesson(folder):
    _write_jsonl(
        folder / "events.jsonl",
        [
            {"type": "started", "t": 1.0, "mode": "replay"},
            {"type": "agent", "agent": "intake", "status": "start", "framework": "adk", "t": 1.0},
            {"type": "agent", "agent": "intake", "status": "done", "framework": "adk", "t": 1.5},
            {"type": "a2a", "phase": "completed", "seconds": 6.0, "updates": 12, "t": 7.5},
            {"type": "artifact", "name": "narration", "data": {"provider": "elevenlabs", "language": "en", "seconds": 60, "lines": {"s1b1": "x" * 1000}}, "t": 9.0},
            {"type": "agent", "agent": "renderer", "status": "start", "framework": "adk", "round": 1, "t": 10.0},
            {"type": "scene", "scene": "s1", "status": "rendered", "seconds": 20.0, "t": 30.0},
            {"type": "agent", "agent": "renderer", "status": "done", "framework": "adk", "round": 1, "t": 30.0},
            {"type": "review", "round": 1, "mean": 8.1, "verdict": "approve", "t": 40.0},
            {"type": "guardrail", "stage": "prompt", "action": "sanitize", "reasons": ["removed email address"], "t": 1.1},
            {"type": "done", "t": 101.0, "manifest": {"duration": 50.0}},
        ],
    )
    _write_jsonl(
        folder / "monitor.jsonl",
        [
            {"source": "llm", "kind": "call", "framework": "crewai", "role": "planner", "model": "gemini-3.5-flash-lite", "seconds": 2.0, "tokens_in": 1_000_000, "tokens_out": 100_000},
            {"source": "llm", "kind": "call", "framework": "adk", "role": "coder", "model": "replay:derivative", "seconds": 1.0, "tokens_in": 10_000, "tokens_out": 20_000, "estimated": True},
            {"source": "llm", "kind": "call_failed", "framework": "adk", "role": "coder", "model": "gemini-3.8-flash", "seconds": 0.5},
            {"source": "mcp", "kind": "tool_call", "tool": "check_math", "seconds": 0.2},
            {"source": "crewai", "kind": "tool_result", "tool": "lookup_concept", "seconds": 0.4},
        ],
    )


def test_metrics_price_tokens_per_model_and_time_each_stage(tmp_path):
    _fake_lesson(tmp_path)
    data = metrics.compute(tmp_path, get_settings())
    assert data["status"] == "done" and data["wall_seconds"] == 100.0 and data["realtime_factor"] == 2.0
    llm = data["llm"]
    assert llm["calls"] == 2 and llm["failed_calls"] == 1 and llm["estimated"]
    # 1M in at $0.30 + 0.1M out at $2.50, plus the replayed coder call priced as gemini-3.8-flash
    expected = 0.30 + 0.25 + (10_000 * 0.75 + 20_000 * 3.75) / 1e6
    assert abs(llm["paid_tier_usd"] - expected) < 1e-9
    assert set(llm["by_model"]) == {"gemini-3.5-flash-lite", "gemini-3.8-flash"}
    assert data["tools"]["by_tool"]["check_math"]["callers"] == ["workflow"]
    assert data["tools"]["by_tool"]["lookup_concept"]["callers"] == ["crewai agent"]
    assert data["voice"]["characters"] == 1000 and abs(data["voice"]["usd"] - 0.10) < 1e-9
    assert data["a2a"] == {"exchanges": 1, "seconds": 6.0, "stream_updates": 12}
    assert data["stages"][0]["agent"] == "intake" and data["stages"][0]["seconds"] == 0.5
    assert data["cost"]["free_tier_usd"] == data["voice"]["usd"]  # Gemini free tier costs nothing


def test_report_is_validated_and_rendered_as_markdown_tables(tmp_path):
    _fake_lesson(tmp_path)
    _write_jsonl(tmp_path / "provenance.jsonl", [{"artifact": "storyboard", "producer": "storyboard_director", "framework": "crewai (via A2A)", "model": "m", "sha": "abc123", "inputs": {"plan": "def456"}}])
    data = metrics.compute(tmp_path, get_settings())
    events = [json.loads(line) for line in (tmp_path / "events.jsonl").read_text(encoding="utf-8").splitlines()]
    job = SimpleNamespace(id="lesson-1", dir=tmp_path, title="Slopes", created=1_790_000_000.0, mode="replay", manifest=None, events=events,
                          request={"prompt": "the derivative", "audience": "school", "length": "brief", "language": "en"})
    state = {
        "final": {"title": "Slopes", "logline": "From secant to tangent", "video": "/media/x.mp4", "duration": 50.0, "language": "en", "quality": "preview",
                  "chapters": [{"scene": "s1", "title": "Secants", "start": 0, "end": 25}], "quiz": [{"question": "q", "choices": ["a", "b", "c", "d"], "answer": 2, "why": "w"}]},
        "analysis": {"core_insight": "A derivative is a limit of slopes."},
        "plan": {"objectives": ["see it"], "takeaway": "zoom in"},
        "reviews": [{"round": 1, "mean": 8.1, "verdict": "approve", "scores": {"pedagogy": 8, "pacing": 8}, "issues": []}],
        "math_checks": [{"latex": "(x+h)^2 - x^2 = 2xh + h^2", "status": "holds", "detail": "identity holds"}],
        "grounding": {"ok": True, "title": "Derivative", "url": "https://en.wikipedia.org/wiki/Derivative"},
    }
    final = report.build(job, state, data)
    assert final.performance.llm_calls == 2 and final.formula_checks[0].status == "holds"
    assert [s.kind for s in final.sources] == ["request", "reference"]
    assert final.provenance[0].inputs == {"plan": "def456"}
    markdown = report.to_markdown(final)
    assert "| Scene | Title | Starts | Ends |" in markdown and "## Provenance" in markdown and "**c**" in markdown
    assert "| prompt | sanitize | 1 |" in markdown
