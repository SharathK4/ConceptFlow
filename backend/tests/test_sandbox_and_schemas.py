import json

from conceptflow.agents.fallback import with_fallbacks
from conceptflow.render.sandbox import check, class_source, extract_code
from conceptflow.schemas import LessonPlan, Storyboard, repair_latex

BOARD = Storyboard.model_validate(
    {
        "title": "T",
        "logline": "L",
        "color_story": "blue is f",
        "scenes": [
            {"id": "x", "title": "One", "purpose": "p", "beats": [{"id": "a", "narration": "Hello there.", "visual": "v"}]},
            {"id": "y", "title": "Two", "purpose": "p", "beats": [{"id": "b", "narration": "First.", "visual": "v"}, {"id": "c", "narration": "Second.", "visual": "v"}]},
        ],
    }
).normalise_ids()
SCENES = [s.id for s in BOARD.scenes]
BEATS = {s.id: [b.id for b in s.beats] for s in BOARD.scenes}

GOOD = '''
from manim import *
from cfkit import *
import numpy as np


class S1(ConceptScene):
    def construct(self):
        with self.beat("s1b1") as b:
            self.play(Write(Eq("x^2")), run_time=b.t(0.5))


class S2(ConceptScene):
    def construct(self):
        with self.beat("s2b1") as b:
            pass
        with self.beat("s2b2") as b:
            pass
'''


def test_normalise_ids_rewrites_scene_and_beat_ids():
    assert SCENES == ["s1", "s2"]
    assert BEATS == {"s1": ["s1b1"], "s2": ["s2b1", "s2b2"]}


def test_good_module_passes():
    result = check(GOOD, SCENES, BEATS)
    assert result.ok, result.problems
    assert result.scenes == ["S1", "S2"]


def test_forbidden_import_and_names_are_rejected():
    bad = GOOD.replace("import numpy as np", "import os\nimport numpy as np") + "\nopen('x')\n"
    result = check(bad, SCENES, BEATS)
    assert not result.ok
    assert any("'os'" in p for p in result.problems)
    assert any("'open'" in p for p in result.problems)


def test_dunder_escape_is_rejected():
    bad = GOOD + "\nleak = ().__class__.__bases__\n"
    assert any("dunder" in p for p in check(bad, SCENES, BEATS).problems)


def test_missing_and_duplicate_beats_are_reported():
    bad = GOOD.replace('with self.beat("s2b2") as b:', 'with self.beat("s2b1") as b:')
    problems = check(bad, SCENES, BEATS).problems
    assert any("never plays beat(s) s2b2" in p for p in problems)
    assert any("more than once" in p for p in problems)


def test_syntax_error_is_reported_with_line():
    problems = check("class S1(ConceptScene)\n    pass\n", SCENES, BEATS).problems
    assert problems and problems[0].startswith("SyntaxError on line 1")


def test_extract_code_prefers_the_fenced_block():
    reply = "Here you go\n```python\nprint(1)\n```\nNOTES:\n- fine"
    assert extract_code(reply) == "print(1)\n"


def test_class_source_ignores_other_scenes():
    changed = GOOD.replace('Eq("x^2")', 'Eq("x^3")')
    assert class_source(GOOD, "S2") == class_source(changed, "S2")
    assert class_source(GOOD, "S1") != class_source(changed, "S1")


def test_repair_latex_restores_eaten_backslashes():
    # What json.loads makes of an unescaped "\frac", "\theta" and "\nabla":
    broken = json.loads('"\\frac{a}{b} + \\theta + \\nabla f"'.replace("\\\\", "\\"))
    assert broken == "\x0crac{a}{b} + \theta + \nabla f"
    assert repair_latex(broken) == r"\frac{a}{b} + \theta + \nabla f"
    assert repair_latex("plain text\nnext line") == "plain text\nnext line"


def test_fallback_scenes_replace_broken_classes_and_pass_the_sandbox():
    plan = LessonPlan.model_validate(
        {
            "title": "T",
            "hook": "h",
            "objectives": ["o"],
            "sections": [
                {"title": "One", "goal": "g", "explanation": "e", "key_formula": r"\frac{a}{b}", "visual_idea": "v"},
                {"title": "Two", "goal": "g", "explanation": "e", "key_formula": "", "visual_idea": "v"},
            ],
            "takeaway": "t",
            "quiz": [],
        }
    )
    broken = GOOD.replace("pass\n        with", "undefined_name()\n        with")
    code = with_fallbacks(broken, BOARD.scenes, {"s2"}, plan)
    result = check(code, SCENES, BEATS)
    assert result.ok, result.problems
    assert "undefined_name" not in code
    assert 'Write(Eq("x^2"))' in code  # the healthy scene is untouched
