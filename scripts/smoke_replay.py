"""Run one lesson through the whole studio in replay mode and print the event stream.

Exercises the real ADK workflow, the real CrewAI crew (over A2A), the MCP
tools, narration, rendering, the correction cycle, review routing and
assembly - with recorded model replies instead of Gemini, so it needs no key
and costs no quota.

    python scripts/smoke_replay.py "Explain the derivative" --quality preview
"""

from __future__ import annotations

import argparse
import asyncio
import os
import socket
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
os.environ["LLM_MODE"] = "replay"

from conceptflow import observability  # noqa: E402
from conceptflow.agents.writers_room_a2a import serve_in_thread  # noqa: E402
from conceptflow.config import get_settings  # noqa: E402
from conceptflow.jobs import JobManager  # noqa: E402
from conceptflow.schemas import LessonRequest  # noqa: E402


def show(event: dict) -> str | None:
    kind = event["type"]
    t = f"{event.get('t', 0):6.1f}s"
    if kind == "agent":
        return f"{t}  [{event.get('framework', '?'):6}] {event['agent']:<20} {event['status']}"
    if kind == "message":
        return f"{t}  -> {event['from']} to {event['to']}: {event['text'][:150]}"
    if kind == "scene":
        err = f"  ({event['error'][:90]})" if event.get("error") else ""
        return f"{t}     scene {event['scene']} {event['status']}{err}"
    if kind == "route":
        return f"{t}     route {event['node']} => {event['route']}"
    if kind == "review":
        return f"{t}     review round {event['round']}: mean {event['mean']} verdict {event['verdict']}"
    if kind == "model":
        return None if event.get("status") == "calling" else f"{t}     model {event['role']}: {event['model']} {event['status']}"
    if kind in ("done", "failed", "started", "queued"):
        return f"{t}  == {kind} {event.get('message', '')}"
    if kind == "artifact":
        return f"{t}     artifact {event['name']}"
    if kind == "a2a":
        return f"{t}     a2a {event['phase']} {event.get('agent', '')}"
    if kind == "tool":
        return f"{t}     mcp tool {event['tool']} ({event['seconds']}s)"
    return None


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("prompt", nargs="?", default="Explain the derivative")
    parser.add_argument("--quality", default="preview")
    parser.add_argument("--language", default="en")
    args = parser.parse_args()

    # The CrewAI writers' room is an A2A agent; serve it on a spare port for this run.
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    os.environ["A2A_WRITERS_ROOM_URL"] = serve_in_thread(port)
    observability.setup(get_settings())
    manager = JobManager(get_settings())
    manager.start()
    job = await manager.submit(LessonRequest(prompt=args.prompt, quality=args.quality, language=args.language), None)
    started = time.time()
    async for event in job.stream():
        line = show(event)
        if line:
            print(line, flush=True)
        if event["type"] in ("done", "failed"):
            break
    print(f"\nstatus={job.status} in {time.time() - started:.1f}s  dir={job.dir}")
    if job.manifest:
        print("video:", job.manifest["video"], f"{job.manifest['duration']}s", "chapters:", len(job.manifest["chapters"]))


if __name__ == "__main__":
    asyncio.run(main())
