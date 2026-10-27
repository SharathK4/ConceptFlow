"""Safety-net scenes for when correction runs out.

If a scene still fails after the last correction round, the lesson should
not have a hole in it. The storyboard already says what that scene is about,
so a plain but dignified version is generated from it deterministically:
the scene title, each line of narration set as text, and the section's key
formula if the plan has one. No model call, and no API that could fail.
"""

from __future__ import annotations

import ast
import json

from ..render.sandbox import scene_class
from ..schemas import LessonPlan, StoryScene


def fallback_class(scene: StoryScene, index: int, formula: str = "") -> str:
    cls = scene_class(scene.id)
    lines = [f"class {cls}(ConceptScene):", "    def construct(self):"]
    for j, beat in enumerate(scene.beats):
        text = json.dumps(_plain(beat.narration))
        lines.append(f"        with self.beat({json.dumps(beat.id)}) as b:")
        if j == 0:
            if index == 1:
                lines.append(f"            self.title_card({json.dumps(scene.title)}, run_time=b.t(0.6))")
                lines.append("            self.clear_stage()")
            else:
                lines.append(f"            self.heading({json.dumps(scene.title)}, index={index})")
            if formula:
                lines.append(f"            eq = safe_eq({json.dumps(formula)}, size=56).move_to(UP * 1.4)")
                lines.append("            self.play(Write(eq), run_time=b.t(0.3))")
        else:
            lines.append("            self.play(FadeOut(line), run_time=0.4)")
        lines.append(f"            line = Note({text}, width=9.5, size=32).move_to(DOWN * 0.8)")
        lines.append("            self.play(FadeIn(line, shift=0.2 * UP), run_time=b.t(0.25))")
    return "\n".join(lines) + "\n"


def _plain(text: str) -> str:
    return " ".join(text.split())[:400]


def with_fallbacks(code: str, scenes: list[StoryScene], broken: set[str], plan: LessonPlan | None) -> str:
    """Replace the classes of ``broken`` scenes with fallback versions."""
    names = {scene_class(sid) for sid in broken}
    try:
        tree = ast.parse(code)
        source = code.splitlines()
        kept = []
        for node in tree.body:
            if isinstance(node, ast.ClassDef) and node.name in names:
                continue
            kept.append("\n".join(source[node.lineno - 1 : node.end_lineno]))
        base = "\n\n".join(kept)
    except SyntaxError:
        # Unparseable: keep nothing of it, every scene becomes a fallback.
        base = "from manim import *\nfrom cfkit import *\n"
        broken = {s.id for s in scenes}
    parts = [base]
    for i, scene in enumerate(scenes, 1):
        if scene.id not in broken:
            continue
        formula = ""
        if plan and i - 1 < len(plan.sections):
            formula = plan.sections[i - 1].key_formula
        parts.append(fallback_class(scene, i, formula))
    return "\n\n\n".join(parts) + "\n"

