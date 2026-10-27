"""The CrewAI writers' room, published as an A2A agent.

The crew is served over the Agent2Agent protocol (a2a-sdk): an Agent Card at
``/a2a/writers-room/.well-known/agent-card.json`` describes it, and JSON-RPC
at ``/a2a/writers-room/`` accepts tasks. Any A2A client can ask it to design a
lesson - the ADK workflow does exactly that (``a2a_client.py``).

Protocol, end to end:
* request  - one A2A ``Message`` with a ``DataPart`` holding a
             ``LessonDesignRequest`` (validated here, at the boundary), and
             message metadata carrying the job id, the W3C trace context and
             the replay showcase if any;
* progress - while the crew works, ``TaskStatusUpdateEvent``s stream back
             (state *working*), each with a human-readable ``TextPart`` and a
             machine-readable ``DataPart`` - which agent started or finished,
             the hand-off note between agents, which model served a call;
* result   - one ``Artifact`` named ``lesson_design`` holding a
             ``LessonDesign`` (analysis + plan + storyboard), then *completed*;
             or *failed* with the reason.
"""

from __future__ import annotations

import asyncio
import re
import threading
import uuid
from typing import Any

import uvicorn
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.apps import A2AStarletteApplication
from a2a.server.events import EventQueue
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.tasks import InMemoryTaskStore, TaskUpdater
from a2a.types import (
    AgentCapabilities,
    AgentCard,
    AgentSkill,
    DataPart,
    Message,
    Part,
    Role,
    TaskState,
    TextPart,
    UnsupportedOperationError,
)
from a2a.utils import new_agent_text_message, new_task
from a2a.utils.errors import ServerError
from pydantic import BaseModel

from .. import monitoring, observability
from ..config import Settings, get_settings
from ..llm import replay
from ..llm import router as router_module
from ..schemas import ConceptAnalysis, ConceptBrief, LessonPlan, LessonRequest, Storyboard
from ..tools.models import ConceptLookup, DocumentDigest
from .writers_room import analysis_note, build_crew, plan_note, storyboard_note

MOUNT = "/a2a/writers-room"


class LessonDesignRequest(BaseModel):
    """What the writers' room needs to design a lesson."""

    request: LessonRequest
    brief: ConceptBrief | None = None
    document: DocumentDigest | None = None
    grounding: ConceptLookup | None = None


class LessonDesign(BaseModel):
    """What the writers' room returns."""

    analysis: ConceptAnalysis
    plan: LessonPlan
    storyboard: Storyboard


def agent_card(settings: Settings) -> AgentCard:
    return AgentCard(
        name="ConceptFlow Writers' Room",
        description=(
            "A CrewAI crew of three agents - Concept Analyst, Lesson Planner and Storyboard Director - that turns a "
            "mathematical concept into a validated lesson design: concept analysis, lesson plan with quiz, and a "
            "shot-by-shot storyboard for a narrated Manim animation."
        ),
        url=f"{settings.base_url}{MOUNT}/",
        version="1.1.0",
        default_input_modes=["application/json"],
        default_output_modes=["application/json"],
        capabilities=AgentCapabilities(streaming=True, push_notifications=False, state_transition_history=True),
        skills=[
            AgentSkill(
                id="design_lesson",
                name="Design a lesson",
                description="Analysis, lesson plan and storyboard for one mathematical concept, at a chosen level and length.",
                tags=["crewai", "pedagogy", "storyboard", "mathematics"],
                examples=['{"request": {"prompt": "Explain the derivative", "audience": "school", "length": "brief"}}'],
                input_modes=["application/json"],
                output_modes=["application/json"],
            )
        ],
    )


_ROSTER = [
    ("concept_analyst", "lesson_planner", "analysis", analysis_note),
    ("lesson_planner", "storyboard_director", "plan", plan_note),
    ("storyboard_director", "manim_coder", "storyboard", storyboard_note),
]


class WritersRoomExecutor(AgentExecutor):
    """Runs the crew for one A2A task, streaming its progress."""

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        message = context.message
        task = context.current_task or new_task(message)
        await event_queue.enqueue_event(task)
        updater = TaskUpdater(event_queue, task.id, task.context_id)
        loop = asyncio.get_running_loop()
        meta: dict[str, Any] = dict(message.metadata or {})

        def status(event: dict, text: str) -> None:
            update = Message(
                role=Role.agent,
                message_id=uuid.uuid4().hex,
                task_id=task.id,
                context_id=task.context_id,
                parts=[Part(root=TextPart(text=text)), Part(root=DataPart(data=event))],
            )
            future = asyncio.run_coroutine_threadsafe(updater.update_status(TaskState.working, message=update), loop)
            future.result(timeout=30)

        try:
            data = next((p.root.data for p in message.parts if isinstance(p.root, DataPart)), None)
            if data is None:
                raise ValueError("send the lesson request as a JSON DataPart")
            payload = LessonDesignRequest.model_validate(data)
            await updater.start_work(new_agent_text_message("The writers' room is on it.", task.context_id, task.id))
            design = await asyncio.to_thread(self._run_crew, payload, meta, status)
            await updater.add_artifact([Part(root=DataPart(data=design.model_dump()))], name="lesson_design")
            await updater.complete(new_agent_text_message(storyboard_note(design.storyboard), task.context_id, task.id))
        except Exception as exc:  # noqa: BLE001 - reported to the caller as a failed task
            monitoring.log("a2a", "task_failed", "error", agent="writers_room", error=f"{type(exc).__name__}: {exc}"[:500])
            await updater.failed(new_agent_text_message(f"{type(exc).__name__}: {exc}"[:1000], task.context_id, task.id))

    def _run_crew(self, payload: LessonDesignRequest, meta: dict, status) -> LessonDesign:
        settings = get_settings()
        token = observability.attach_carrier(meta.get("trace"))
        script = replay.load(meta["replay"]) if meta.get("replay") else None
        replay.current.set(script)
        job_id = str(meta.get("job_id", ""))
        job_dir = settings.lessons_dir / job_id if re.fullmatch(r"[\w-]{6,64}", job_id or "") else None
        log_token = monitoring.bind(monitoring.RunLog(job_dir) if job_dir and job_dir.exists() else None)
        router_module.reporter.set(lambda e: status(e, f"{e.get('role')}: {e.get('model')} {e.get('status')}"))

        mcps = None
        if script is None and settings.llm_tools and meta.get("tools"):
            from crewai.mcp import MCPServerHTTP
            from crewai.mcp.filters import create_static_tool_filter

            mcps = [
                MCPServerHTTP(
                    url=f"{settings.local_url}/mcp/",
                    streamable=True,
                    tool_filter=create_static_tool_filter(allowed_tool_names=["lookup_concept", "check_math"]),
                )
            ]

        outputs: dict[int, BaseModel] = {}

        def on_task_done(index: int, output) -> None:
            agent_id, next_id, artifact, note = _ROSTER[index]
            model = output.pydantic
            outputs[index] = model
            status({"type": "agent", "agent": agent_id, "status": "done", "framework": "crewai"}, f"{agent_id} finished")
            if model is not None:
                status({"type": "artifact", "name": artifact, "data": model.model_dump()}, f"{artifact} ready")
                status({"type": "message", "from": agent_id, "to": next_id, "text": note(model)}, note(model))
            if index < 2:
                status({"type": "agent", "agent": _ROSTER[index + 1][0], "status": "start", "framework": "crewai"}, f"{_ROSTER[index + 1][0]} started")

        try:
            with observability.span("crewai.writers_room", **{"crewai.crew": "writers_room", "conceptflow.job_id": job_id, "a2a.server": True}):
                crew, tasks = build_crew(payload.request, payload.brief, payload.document, payload.grounding, mcps, on_task_done)
                status({"type": "agent", "agent": "concept_analyst", "status": "start", "framework": "crewai"}, "concept_analyst started")
                try:
                    crew.kickoff()
                except Exception as exc:  # noqa: BLE001 - one retry, below
                    if not mcps:
                        raise
                    # Tool use runs through CrewAI's text-based loop, the least robust
                    # path; a failed crew is run once more without tools.
                    monitoring.log("crewai", "retry_without_tools", "warning", agent="concept_analyst", error=f"{type(exc).__name__}: {exc}"[:300])
                    status({"type": "log", "agent": "concept_analyst", "text": "Tool use failed; the crew is starting again without tools.", "level": "warning"}, "retrying without tools")
                    outputs.clear()
                    crew, tasks = build_crew(payload.request, payload.brief, payload.document, payload.grounding, None, on_task_done)
                    status({"type": "agent", "agent": "concept_analyst", "status": "start", "framework": "crewai"}, "concept_analyst started")
                    crew.kickoff()
            analysis, plan, board = (_structured(t) for t in tasks)
            if not isinstance(board, Storyboard):
                raise RuntimeError("the Storyboard Director did not return a storyboard")
            board.normalise_ids()
            return LessonDesign(analysis=analysis, plan=plan, storyboard=board)
        finally:
            monitoring.unbind(log_token)
            observability.detach(token)

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        raise ServerError(error=UnsupportedOperationError())


def _structured(task) -> BaseModel | None:
    """A task's Pydantic output. When an agent used tools, CrewAI keeps only the
    raw text of its final answer, so it is parsed here against the task's model."""
    output = task.output
    if output is None:
        return None
    if output.pydantic is not None:
        return output.pydantic
    from ..llm.crew_llm import _parse

    return _parse(output.raw, task.output_pydantic)


def build_app(settings: Settings | None = None):
    settings = settings or get_settings()
    handler = DefaultRequestHandler(agent_executor=WritersRoomExecutor(), task_store=InMemoryTaskStore())
    return A2AStarletteApplication(agent_card=agent_card(settings), http_handler=handler).build()


def serve_in_thread(port: int) -> str:
    """Run the writers' room alone on a local port (tests and command-line runs)."""
    from starlette.applications import Starlette
    from starlette.routing import Mount

    app = Starlette(routes=[Mount(MOUNT, app=build_app())])
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    import time

    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    return f"http://127.0.0.1:{port}{MOUNT}"
