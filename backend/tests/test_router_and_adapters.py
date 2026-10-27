"""The quota-aware router and both framework adapters, with Gemini faked."""

import asyncio
import json
from dataclasses import replace
from types import SimpleNamespace

import pytest
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.genai import types
from google.genai.errors import ClientError, ServerError
from pydantic import BaseModel

from conceptflow.config import get_settings
from conceptflow.llm import adk_model, crew_llm, router as router_module
from conceptflow.llm.router import ModelRouter, QuotaExhausted, classify


def quota_error(per: str = "Day", delay: str = "41s") -> ClientError:
    return ClientError(
        429,
        {
            "error": {
                "code": 429,
                "message": "Resource has been exhausted (e.g. check quota).",
                "status": "RESOURCE_EXHAUSTED",
                "details": [
                    {"@type": "type.googleapis.com/google.rpc.QuotaFailure", "violations": [{"quotaId": f"GenerateRequestsPer{per}PerProjectPerModel-FreeTier"}]},
                    {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": delay},
                ],
            }
        },
    )


def test_classify_quota_kinds():
    assert classify(quota_error("Day")).kind == "daily"
    minute = classify(quota_error("Minute", "7s"))
    assert minute.kind == "minute" and minute.wait == 7.0
    assert classify(quota_error("Minute", "300s")).kind == "daily"  # too long to wait for
    assert classify(ClientError(404, {"error": {"code": 404, "status": "NOT_FOUND", "message": "models/x is not found"}})).kind == "missing"
    assert classify(ServerError(503, {"error": {"code": 503, "status": "UNAVAILABLE", "message": "overloaded"}})).kind == "busy"
    assert classify(ValueError("bad request shape")).kind == "fatal"


@pytest.fixture
def router(tmp_path, monkeypatch):
    settings = replace(
        get_settings(),
        google_api_key="test-key",
        ladders={"planner": ["m-a", "m-b", "m-c"], "coder": ["m-a", "m-b"]},
        data_dir=tmp_path,
    )
    (tmp_path / "cache").mkdir()
    r = ModelRouter(settings)
    monkeypatch.setattr(router_module, "_router", r)
    return r


class FakeModels:
    def __init__(self, script):
        self.script = script  # model -> list of exceptions / texts, consumed in order
        self.calls = []

    def generate_content(self, model, contents, config):
        self.calls.append(model)
        outcome = self.script[model].pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return SimpleNamespace(text=outcome, usage_metadata=SimpleNamespace(total_token_count=12))


def test_daily_quota_parks_the_model_for_every_role(router):
    fake = FakeModels({"m-a": [quota_error("Day")], "m-b": ["hello", "again"]})
    router._client = SimpleNamespace(models=fake)
    text, model = router.generate_sync("planner", "hi")
    assert (text, model) == ("hello", "m-b")
    # m-a is parked until the quota resets, so the coder skips it too.
    assert router.ladder("coder") == ["m-b"]
    text, model = router.generate_sync("coder", "hi")
    assert model == "m-b" and fake.calls == ["m-a", "m-b", "m-b"]
    assert router.usage()["calls"] == {"m-b": 2}


def test_minute_quota_waits_then_retries_same_model(router, monkeypatch):
    slept = []
    monkeypatch.setattr(router_module.time, "sleep", lambda s: slept.append(s))
    fake = FakeModels({"m-a": [quota_error("Minute", "3s"), "ok"]})
    router._client = SimpleNamespace(models=fake)
    assert router.generate_sync("planner", "hi") == ("ok", "m-a")
    assert slept == [3.0]


def test_all_models_exhausted_raises_without_rate_limit_wording(router):
    fake = FakeModels({m: [quota_error("Day")] for m in ("m-a", "m-b", "m-c")})
    router._client = SimpleNamespace(models=fake)
    with pytest.raises(QuotaExhausted) as info:
        router.generate_sync("planner", "hi")
    # CrewAI retries anything that looks like a rate limit; this must not.
    assert "rate limit" not in str(info.value).lower() and info.value.__cause__ is None


# ------------------------------------------------------------------ CrewAI


class Out(BaseModel):
    formula: str
    steps: list[str]


def test_crew_llm_sends_system_and_schema_and_parses_fenced_json(router, monkeypatch):
    seen = {}

    def fake_generate(role, contents, *, system=None, schema=None, temperature=None, framework=None):
        seen.update(role=role, contents=contents, system=system, schema=schema, framework=framework)
        return '```json\n{"formula": "\\\\frac{a}{b}", "steps": ["one"]}\n```', "m-a"

    monkeypatch.setattr(router, "generate_sync", fake_generate)
    llm = crew_llm.RoutedCrewLLM(model="conceptflow/planner", role="planner")
    messages = [
        {"role": "system", "content": "You are the Concept Analyst."},
        {"role": "user", "content": "Analyse the derivative."},
    ]
    result = llm.call(messages, response_model=Out)
    assert isinstance(result, Out) and result.formula == r"\frac{a}{b}"
    assert seen["system"] == "You are the Concept Analyst." and seen["schema"] is Out
    assert seen["contents"][0].role == "user"
    assert seen["framework"] == "crewai"  # every call is attributed to its framework in the monitoring log


# --------------------------------------------------------------------- ADK


class FakeDelegate:
    def __init__(self, outcome):
        self.outcome = outcome

    async def generate_content_async(self, request, stream=False):
        if isinstance(self.outcome, Exception):
            raise self.outcome
        yield LlmResponse(content=types.Content(role="model", parts=[types.Part(text=self.outcome)]))


def test_adk_model_falls_down_the_ladder_and_restores_the_request(router, monkeypatch):
    delegates = {"m-a": FakeDelegate(quota_error("Day")), "m-b": FakeDelegate("class S1: pass")}
    llm = adk_model.RoutedGemini(role="coder")
    monkeypatch.setattr(llm, "_delegate", lambda name: delegates[name])
    request = LlmRequest(contents=[types.Content(role="user", parts=[types.Part(text="write code")])])

    async def run():
        return [r async for r in llm.generate_content_async(request)]

    responses = asyncio.run(run())
    assert responses[0].content.parts[0].text == "class S1: pass"
    assert request.model == "m-b" and len(request.contents) == 1
    assert json.dumps(router.usage()["calls"]) == '{"m-b": 1}'
