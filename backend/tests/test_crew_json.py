"""CrewAI's text-based tool loop parses JSON answers itself; LaTeX must survive it."""

import json

from conceptflow.guardrails import guard_plan
from conceptflow.llm.crew_llm import loads_lenient, normalise_json


def test_unescaped_latex_in_a_final_answer_is_re_serialised():
    reply = 'Thought: I have it.\nFinal Answer: {"formula": "\\sqrt{T}(R - \\Pi) \\Rightarrow L", "half": "\\frac{1}{2}"}'
    fixed = normalise_json(reply)
    data = json.loads(fixed.split("Final Answer:", 1)[1])
    assert data == {"formula": r"\sqrt{T}(R - \Pi) \Rightarrow L", "half": r"\frac{1}{2}"}
    assert fixed.startswith("Thought: I have it.\nFinal Answer: ")


def test_tool_arguments_are_fixed_too():
    reply = 'Action: check_math\nAction Input: {"latex": "\\lambda^2 = \\lambda \\cdot \\lambda", "operation": "verify"}'
    data = json.loads(normalise_json(reply).split("Action Input:", 1)[1])
    assert data["latex"] == r"\lambda^2 = \lambda \cdot \lambda"


def test_prose_is_left_alone():
    assert normalise_json(r"Thought: the \sqrt grows") == r"Thought: the \sqrt grows"
    assert loads_lenient('{"a": "\\alpha"}') == {"a": r"\alpha"}


def test_the_plan_guardrail_reads_fenced_answers_with_latex():
    plan = {
        "title": "T",
        "hook": "h",
        "objectives": ["o"],
        "sections": [
            {"title": "a", "goal": "g", "explanation": "e", "key_formula": "\\sqrt{T}", "visual_idea": "v"},
            {"title": "b", "goal": "g", "explanation": "e", "key_formula": "", "visual_idea": "v"},
        ],
        "takeaway": "t",
        "quiz": [],
    }
    raw = "```json\n" + json.dumps(plan).replace("\\\\sqrt", "\\sqrt") + "\n```"
    assert guard_plan(raw)[0]
    assert not guard_plan("not json at all")[0]
