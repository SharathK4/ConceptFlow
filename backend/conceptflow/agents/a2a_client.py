"""ADK -> CrewAI over A2A: the workflow node that delegates lesson design.

``WritersRoomClient`` is a node of the Google ADK workflow. It does not run
CrewAI itself: it is an A2A *client* (a2a-sdk) that

1. grounds the concept first with the MCP ``lookup_concept`` tool,
2. discovers the writers' room from its Agent Card,
3. sends one A2A message - a ``DataPart`` holding the request, the
   distilled brief, the uploaded document's digest and the grounding - with
   the W3C trace context in the message metadata,
4. relays the streamed task updates (which CrewAI agent is working, their
   hand-off notes, the models they use) to the page as they arrive,
5. validates the returned ``lesson_design`` artifact against its Pydantic
   schema and writes analysis, plan and storyboard into ADK session state,
   where every later agent reads them.

Set ``A2A_WRITERS_ROOM_URL`` to call a writers' room deployed elsewhere.
"""

from __future__ import annotations

import os
import time
import uuid
from typing import Any

import httpx
from a2a.client import A2ACardResolver, ClientConfig, ClientFactory
from a2a.types import (
    DataPart,
    Message,
    Part,
    Role,
    TaskArtifactUpdateEvent,
    TaskState,
    TaskStatusUpdateEvent,
    TextPart,
)
from google.adk.agents.context import Context
from google.adk.events.event import Event
from google.adk.workflow import BaseNode

from .. import monitoring, observability
from ..config import get_settings
from ..llm import replay
from ..schemas import ConceptBrief, LessonRequest
from ..tools.client import ToolError, call_tool
from ..tools.models import ConceptLookup, DocumentDigest
from .bus import current_job
from .writers_room_a2a import MOUNT, LessonDesign, LessonDesignRequest


def writers_room_url() -> str:
    return os.environ.get("A2A_WRITERS_ROOM_URL") or f"{get_settings().local_url}{MOUNT}"


def _topic(request: LessonRequest, brief: ConceptBrief | None, document: DocumentDigest | None) -> str:
    if brief is not None and brief.concept:
        return brief.concept
    if request.prompt:
        return request.prompt
    return document.title if document is not None else ""


class WritersRoomClient(BaseNode):
    """Delegates analysis, planning and storyboarding to the CrewAI crew over A2A."""

    async def _run_impl(self, *, ctx: Context, node_input: Any):
        job = current_job()
        settings = get_settings()
        request = LessonRequest.model_validate(ctx.state["request"])
        brief = ConceptBrief.model_validate(ctx.state["brief"]) if ctx.state.get("brief") else None
        document = DocumentDigest.model_validate(ctx.state["document"]) if ctx.state.get("document") else None

        # 1. Ground the concept with an external tool, over MCP.
        grounding: ConceptLookup | None = None
        topic = _topic(request, brief, document)
        if topic:
            try:
                grounding = await call_tool("lookup_concept", emit=job.emit, query=topic[:150], sentences=4)
            except ToolError as exc:
                monitoring.log("mcp", "grounding_skipped", "warning", detail=str(exc)[:200])
        if grounding is not None and grounding.ok:
            observability.record_artifact(job.dir, "grounding", grounding.model_dump(), "lookup_concept", "mcp", note=grounding.url)
            job.emit({"type": "message", "from": "intake", "to": "concept_analyst", "text": f"Reference found with the lookup tool: {grounding.title} ({grounding.url})."})

        # Keep the document light for the crew: structure, not the full text.
        slim = document.model_copy(update={"excerpt": document.excerpt[:3000]}) if document is not None else None
        payload = LessonDesignRequest(request=request, brief=brief, document=slim, grounding=grounding)
        script = replay.current.get()
        url = writers_room_url()
        design_data: dict | None = None
        failure = ""
        started = time.perf_counter()

        with observability.span("a2a.client writers_room", **{"a2a.url": url, "a2a.skill": "design_lesson", "conceptflow.job_id": job.id}) as current:
            metadata = {"job_id": job.id, "trace": observability.carrier(), "replay": script.slug if script else None, "tools": settings.llm_tools}
            async with httpx.AsyncClient(timeout=httpx.Timeout(900.0, connect=15.0)) as http:
                card = await A2ACardResolver(http, base_url=url).get_agent_card()
                card.url = url.rstrip("/") + "/"  # call in-process even if the card advertises a public address
                current.set_attribute("a2a.agent", card.name)
                job.emit({"type": "a2a", "phase": "discovered", "agent": card.name, "url": url, "skills": [s.id for s in card.skills]})
                monitoring.log("a2a", "card_resolved", agent=card.name, url=url, protocol=card.protocol_version)
                client = ClientFactory(ClientConfig(httpx_client=http, streaming=True)).create(card)
                message = Message(
                    role=Role.user,
                    message_id=uuid.uuid4().hex,
                    parts=[Part(root=DataPart(data=payload.model_dump(mode="json")))],
                    metadata=metadata,
                )
                job.emit({"type": "a2a", "phase": "sent", "agent": card.name, "text": "lesson design request (DataPart)"})
                final_state = None
                updates = 0
                async for item in client.send_message(message):
                    if not isinstance(item, tuple):
                        continue
                    task, update = item
                    final_state = task.status.state
                    updates += 1
                    if isinstance(update, TaskStatusUpdateEvent) and update.status.message is not None:
                        for part in update.status.message.parts:
                            if isinstance(part.root, DataPart):
                                job.emit(dict(part.root.data))
                            elif isinstance(part.root, TextPart) and update.status.state == TaskState.failed:
                                failure = part.root.text
                    elif isinstance(update, TaskArtifactUpdateEvent):
                        for part in update.artifact.parts:
                            if isinstance(part.root, DataPart):
                                design_data = dict(part.root.data)
                    if update is None and task.artifacts:  # non-streaming servers
                        for artifact in task.artifacts:
                            for part in artifact.parts:
                                if isinstance(part.root, DataPart):
                                    design_data = dict(part.root.data)
                    if task.status.state == TaskState.failed and task.status.message is not None:
                        failure = " ".join(p.root.text for p in task.status.message.parts if isinstance(p.root, TextPart))
            current.set_attribute("a2a.final_state", str(final_state))

        seconds = time.perf_counter() - started
        if design_data is None:
            monitoring.log("a2a", "task_failed", "error", agent="writers_room", error=failure or str(final_state))
            raise RuntimeError(f"the writers' room (A2A) did not return a lesson design: {failure or final_state}")
        design = LessonDesign.model_validate(design_data)  # type-check at the protocol boundary
        monitoring.log("a2a", "task_completed", agent="writers_room", seconds=round(seconds, 2), scenes=len(design.storyboard.scenes))
        job.emit({"type": "a2a", "phase": "completed", "agent": "ConceptFlow Writers' Room", "seconds": round(seconds, 1), "updates": updates})

        board = design.storyboard.normalise_ids()
        inputs = {"request": observability.digest(request.model_dump())}
        if brief is not None:
            inputs["brief"] = observability.digest(brief.model_dump())
        if grounding is not None and grounding.ok:
            inputs["grounding"] = observability.digest(grounding.model_dump())
        model = f"replay:{script.slug}" if script else ",".join(settings.ladders["planner"][:1])
        sha_a = observability.record_artifact(job.dir, "analysis", design.analysis.model_dump(), "concept_analyst", "crewai (via A2A)", model, inputs)
        sha_p = observability.record_artifact(job.dir, "plan", design.plan.model_dump(), "lesson_planner", "crewai (via A2A)", model, {"analysis": sha_a})
        observability.record_artifact(job.dir, "storyboard", board.model_dump(), "storyboard_director", "crewai (via A2A)", model, {"analysis": sha_a, "plan": sha_p})

        state = {
            "analysis": design.analysis.model_dump(),
            "plan": design.plan.model_dump(),
            "storyboard": board.model_dump(),
            "grounding": grounding.model_dump() if grounding is not None else None,
            "title": board.title,
        }
        job.update(title=board.title)
        summary = {"title": board.title, "scenes": len(board.scenes), "beats": len(board.beats()), "via": "a2a"}
        yield Event(output=summary, state=state)
