"""Agent to agent: the CrewAI writers' room served over the A2A protocol.

These tests talk to the crew exactly as the ADK workflow does - Agent Card
discovery, one JSON DataPart message, streamed task updates, a typed artifact -
with the crew running on recorded replies (replay), so no key is needed.
"""

import asyncio
import socket
import uuid

import httpx
import pytest
from a2a.client import A2ACardResolver, ClientConfig, ClientFactory
from a2a.types import DataPart, Message, Part, Role, TaskArtifactUpdateEvent, TaskState, TaskStatusUpdateEvent

from conceptflow.agents.writers_room_a2a import LessonDesign, LessonDesignRequest, serve_in_thread
from conceptflow.schemas import LessonRequest


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture(scope="module")
def room_url() -> str:
    return serve_in_thread(_free_port())


async def _send(url: str, data: dict, metadata: dict):
    items = []
    async with httpx.AsyncClient(timeout=120) as http:
        card = await A2ACardResolver(http, base_url=url).get_agent_card()
        card.url = url + "/"
        client = ClientFactory(ClientConfig(httpx_client=http, streaming=True)).create(card)
        message = Message(role=Role.user, message_id=uuid.uuid4().hex, parts=[Part(root=DataPart(data=data))], metadata=metadata)
        async for item in client.send_message(message):
            if isinstance(item, tuple):
                items.append(item)
    return items


def test_agent_card_describes_the_crew(room_url):
    async def fetch():
        async with httpx.AsyncClient() as http:
            return await A2ACardResolver(http, base_url=room_url).get_agent_card()

    card = asyncio.run(fetch())
    assert card.name == "ConceptFlow Writers' Room"
    assert card.capabilities.streaming
    assert [s.id for s in card.skills] == ["design_lesson"]
    assert "application/json" in card.default_input_modes


def test_the_crew_designs_a_lesson_over_a2a_and_streams_its_progress(room_url):
    request = LessonDesignRequest(request=LessonRequest(prompt="the derivative", length="standard"))
    items = asyncio.run(_send(room_url, request.model_dump(mode="json"), {"replay": "derivative", "job_id": "test-a2a"}))

    task, _ = items[-1]
    assert task.status.state == TaskState.completed
    updates = [u for _, u in items if isinstance(u, TaskStatusUpdateEvent) and u.status.message]
    data = [p.root.data for u in updates for p in u.status.message.parts if isinstance(p.root, DataPart)]
    started = [d["agent"] for d in data if d.get("type") == "agent" and d.get("status") == "start"]
    assert started[:3] == ["concept_analyst", "lesson_planner", "storyboard_director"]
    handoffs = [(d["from"], d["to"]) for d in data if d.get("type") == "message"]
    assert ("concept_analyst", "lesson_planner") in handoffs and ("lesson_planner", "storyboard_director") in handoffs

    artifacts = [u.artifact for _, u in items if isinstance(u, TaskArtifactUpdateEvent)]
    assert [a.name for a in artifacts] == ["lesson_design"]
    design = LessonDesign.model_validate(artifacts[0].parts[0].root.data)
    assert len(design.storyboard.scenes) == 4 and len(design.plan.quiz) == 3
    assert [s.id for s in design.storyboard.scenes] == ["s1", "s2", "s3", "s4"]


def test_a_malformed_request_fails_the_task_with_a_reason(room_url):
    items = asyncio.run(_send(room_url, {"request": {"audience": "toddlers"}}, {}))
    task, _ = items[-1]
    assert task.status.state == TaskState.failed
    reason = " ".join(p.root.text for p in task.status.message.parts if hasattr(p.root, "text"))
    assert "ValidationError" in reason
