"""ConceptScene: a Manim scene whose clock is the narration.

A lesson is written as a sequence of *beats*. Each beat is one line of
narration, already synthesised by the time the scene renders, so its length in
seconds is known. The scene code says what should happen on screen during that
line; the base class makes the picture last exactly as long as the voice:

    with self.beat("s2b1") as b:
        self.play(Create(curve), run_time=b.t(0.6))   # 60% of the line
        self.play(Write(label))                       # whatever is left is held

If the animations run long, the beat simply overruns and the next line of
narration waits for it - the voice never talks over the wrong picture.

While it renders, the scene also keeps notes for the agents that review it: a
keyframe at the end of every beat, and a layout report of anything off the
frame, overlapping text, text too small to read, or an empty stage. Those
notes are what the Evaluation agent looks at before a person ever does.
"""

from __future__ import annotations

import json
import os
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

import numpy as np
from manim import (
    DOWN,
    UL,
    UP,
    CoordinateSystem,
    DecimalNumber,
    FadeIn,
    FadeOut,
    ImageMobject,
    MarkupText,
    MathTex,
    Mobject,
    MovingCameraScene,
    NumberLine,
    NumberPlane,
    Paragraph,
    Tex,
    Text,
    VGroup,
    VMobject,
    Write,
    config,
)

from . import theme
from .theme import DISPLAY, INK, MUTED, SANS, SERIF

# Narration pace used when a beat has no synthesised audio (silent mode, or a
# beat the code invented): ~150 words a minute, the pace of a calm lecture.
WORDS_PER_SECOND = 2.5
FALLBACK_BEAT = 3.0

# The beat currently being rendered, read by the render runner when a scene
# raises, so an error report can say where in the lesson it happened.
STATE: dict[str, str | None] = {"beat": None}


def _load_timing() -> dict[str, dict]:
    path = os.environ.get("CF_TIMING")
    if not path or not Path(path).exists():
        return {}
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return data.get("beats", data)


class Beat:
    """Handle for one line of narration inside ``with self.beat(...)``."""

    def __init__(self, scene: "ConceptScene", beat_id: str, duration: float) -> None:
        self.scene = scene
        self.id = beat_id
        self.dur = float(duration)
        self.start = scene.time

    def t(self, share: float) -> float:
        """A run_time that is ``share`` of this line of narration."""
        return max(0.2, float(share) * self.dur)

    @property
    def elapsed(self) -> float:
        return self.scene.time - self.start

    @property
    def remaining(self) -> float:
        return max(0.0, self.dur - self.elapsed)


class ConceptScene(MovingCameraScene):
    """Base class for every generated scene. See module docstring."""

    def setup(self) -> None:
        super().setup()
        theme.apply_defaults()

        stage = theme.backdrop(self.camera.pixel_width, self.camera.pixel_height)
        self.camera.background_image = str(stage)
        self.camera.init_background()

        self._timing = _load_timing()
        self._scene_id = os.environ.get("CF_SCENE_ID") or type(self).__name__
        out = os.environ.get("CF_OUT")
        self._out_dir = Path(out) if out else None
        if self._out_dir:
            (self._out_dir / "keyframes").mkdir(parents=True, exist_ok=True)

        self._records: list[dict] = []
        self._used: list[str] = []
        self._current: str | None = None
        self._persistent: list[Mobject] = []

    # ---------------------------------------------------------------- beats

    def beat_duration(self, beat_id: str) -> float:
        info = self._timing.get(beat_id)
        if isinstance(info, dict) and info.get("duration"):
            return float(info["duration"])
        if isinstance(info, (int, float)):
            return float(info)
        return FALLBACK_BEAT

    @contextmanager
    def beat(self, beat_id: str) -> Iterator[Beat]:
        """Run the enclosed animations for exactly one line of narration."""
        known = beat_id in self._timing
        b = Beat(self, beat_id, self.beat_duration(beat_id))
        self._current = beat_id
        STATE["beat"] = beat_id
        try:
            yield b
        except Exception:
            # The render agent reads this marker to say *where* it broke.
            print(f"CFKIT_BEAT={beat_id}", file=sys.stderr, flush=True)
            raise
        elapsed = self.time - b.start
        hold = b.dur - elapsed
        if hold > 1.0 / config.frame_rate:
            self.wait(hold)
        record = {
            "id": beat_id,
            "start": round(b.start, 3),
            "end": round(self.time, 3),
            "narration": round(b.dur, 3),
            "overrun": round(max(0.0, elapsed - b.dur), 3),
            "known": known,
            "repeat": beat_id in self._used,
        }
        record["issues"] = self._layout_issues()
        record["keyframe"] = self._keyframe(beat_id)
        self._records.append(record)
        self._used.append(beat_id)
        self._current = None
        STATE["beat"] = None

    # -------------------------------------------------------- stage helpers

    def heading(self, text: str, index: int | None = None) -> VGroup:
        """A quiet chapter label in the top-left that stays for the scene."""
        parts = []
        if index is not None:
            parts.append(Text(f"{index:02d}", font=SANS, font_size=18, color=MUTED))
        parts.append(Text(text, font=SERIF, font_size=28, color=MUTED))
        label = VGroup(*parts).arrange(np.array([1.0, 0.0, 0.0]), buff=0.25)
        label.to_corner(UL, buff=0.45)
        # A soft backing keeps the label legible over grids and curves.
        label.add_background_rectangle(color=theme.STAGE_BG, opacity=0.6, buff=0.12)
        self.play(FadeIn(label, shift=0.15 * DOWN), run_time=0.6)
        self._persistent.append(label)
        return label

    def title_card(self, title: str, subtitle: str | None = None, run_time: float = 2.0) -> VGroup:
        """A large display-serif title, optionally with an italic subtitle."""
        head = Text(title, font=DISPLAY, font_size=86, color=INK)
        if head.width > 12.2:
            head.scale_to_fit_width(12.2)
        group = VGroup(head)
        if subtitle:
            sub = Text(subtitle, font=SERIF, slant="ITALIC", font_size=32, color=MUTED)
            if sub.width > 11:
                sub.scale_to_fit_width(11)
            group.add(sub)
        group.arrange(DOWN, buff=0.45).move_to(0.2 * UP)
        self.play(Write(head), run_time=run_time * 0.65)
        if subtitle:
            self.play(FadeIn(group[1], shift=0.2 * UP), run_time=run_time * 0.35)
        return group

    def clear_stage(self, *keep: Mobject, run_time: float = 0.6) -> None:
        """Fade out everything except the chapter label and ``keep``."""
        spare = set(map(id, self._persistent)) | set(map(id, keep))
        leaving = [m for m in self.mobjects if id(m) not in spare and m is not self.camera.frame]
        if leaving:
            self.play(*[FadeOut(m) for m in leaving], run_time=run_time)

    def reveal(self, *mobjects: Mobject, run_time: float = 0.8, lag: float = 0.15) -> None:
        """Fade things in with a small rise, one after another."""
        from manim import LaggedStart

        self.play(
            LaggedStart(*[FadeIn(m, shift=0.2 * UP) for m in mobjects], lag_ratio=lag),
            run_time=run_time,
        )

    def fit(self, mob: Mobject, margin: float = 0.0) -> Mobject:
        """Shrink ``mob`` (never grow it) until it sits inside the safe area."""
        max_w = (theme.SAFE_RIGHT - theme.SAFE_LEFT) - 2 * margin
        max_h = (theme.SAFE_TOP - theme.SAFE_BOTTOM) - 2 * margin
        if mob.width > max_w:
            mob.scale_to_fit_width(max_w)
        if mob.height > max_h:
            mob.scale_to_fit_height(max_h)
        return mob

    # ---------------------------------------------------------- tear down

    def tear_down(self) -> None:
        # Every scene leaves an empty stage, so scenes cut together cleanly.
        leaving = [m for m in self.mobjects if m is not self.camera.frame]
        if leaving and not self.renderer.skip_animations:
            self.play(*[FadeOut(m) for m in leaving], run_time=0.6)
        self._write_timeline()
        super().tear_down()

    def _write_timeline(self) -> None:
        if not self._out_dir:
            return
        report = {
            "scene": self._scene_id,
            "duration": round(self.time, 3),
            "beats": self._records,
        }
        path = self._out_dir / f"{self._scene_id}.timeline.json"
        path.write_text(json.dumps(report, indent=2), encoding="utf-8")

    # ------------------------------------------------------- review notes

    def _keyframe(self, beat_id: str) -> str | None:
        if not self._out_dir or self.renderer.skip_animations:
            return None
        try:
            image = self.camera.get_image().convert("RGB")
            image.thumbnail((640, 360))
            path = self._out_dir / "keyframes" / f"{self._scene_id}__{beat_id}.jpg"
            image.save(path, quality=86)
            return path.name
        except Exception:  # a missing keyframe must never fail a render
            return None

    def _layout_issues(self) -> list[dict]:
        frame = self.camera.frame
        zoom = frame.height / config.frame_height
        cx, cy = frame.get_center()[:2]
        left, right = cx - frame.width / 2, cx + frame.width / 2
        bottom, top = cy - frame.height / 2, cy + frame.height / 2

        issues: list[dict] = []
        visible = [m for m in self.mobjects if m is not frame and _visible(m)]
        if not visible:
            issues.append({"kind": "blank", "detail": "nothing on stage at the end of this beat"})
            return issues

        for m in visible:
            if isinstance(m, NumberPlane):
                continue  # background grids are meant to run off the edges
            x0, y0, x1, y1 = _bbox(m)
            over = max(left - x0, x1 - right, bottom - y0, y1 - top)
            if over > 0.12 * zoom:
                issues.append({"kind": "off_frame", "what": _describe(m), "by": round(float(over), 2)})

        texts = _text_leaves(visible)
        for i in range(len(texts)):
            for j in range(i + 1, len(texts)):
                share = _overlap_share(texts[i], texts[j])
                if share > 0.18:
                    issues.append(
                        {
                            "kind": "text_overlap",
                            "what": f"{_describe(texts[i])} / {_describe(texts[j])}",
                            "share": round(share, 2),
                        }
                    )
        for t in texts:
            if t.height < 0.15 * zoom and len(_text_of(t)) > 1:
                issues.append({"kind": "tiny_text", "what": _describe(t), "height": round(float(t.height), 3)})
        if len(texts) > 10:
            issues.append({"kind": "crowded", "detail": f"{len(texts)} separate text objects on stage"})
        for t in texts:
            x0, y0, x1, y1 = _bbox(t)
            if y0 < bottom + 0.95 * zoom and y1 > bottom:
                issues.append({"kind": "caption_zone", "what": _describe(t)})
        return issues


# ------------------------------------------------------------------ helpers

_TEXT_TYPES = (Text, MarkupText, Paragraph, MathTex, Tex, DecimalNumber)


def _visible(m: Mobject) -> bool:
    if isinstance(m, ImageMobject):
        return True
    for sub in m.get_family():
        if isinstance(sub, VMobject) and sub.has_points():
            if sub.get_fill_opacity() > 0.04:
                return True
            if sub.get_stroke_opacity() > 0.04 and sub.get_stroke_width() > 0:
                return True
    return False


def _bbox(m: Mobject) -> tuple[float, float, float, float]:
    dl = m.get_corner(np.array([-1.0, -1.0, 0.0]))
    ur = m.get_corner(np.array([1.0, 1.0, 0.0]))
    return float(dl[0]), float(dl[1]), float(ur[0]), float(ur[1])


def _text_leaves(mobjects: list[Mobject]) -> list[Mobject]:
    """Every separately-placed piece of text on stage.

    Axes and number lines are skipped: their tick labels are placed by Manim,
    not by the scene, and counting them would call every graph "crowded".
    """
    found: list[Mobject] = []

    def walk(m: Mobject) -> None:
        if isinstance(m, (NumberLine, CoordinateSystem)):
            return
        if isinstance(m, _TEXT_TYPES):
            if _visible(m):
                found.append(m)
            return
        for sub in m.submobjects:
            walk(sub)

    for m in mobjects:
        walk(m)
    return found


def _overlap_share(a: Mobject, b: Mobject) -> float:
    ax0, ay0, ax1, ay1 = _bbox(a)
    bx0, by0, bx1, by1 = _bbox(b)
    w = min(ax1, bx1) - max(ax0, bx0)
    h = min(ay1, by1) - max(ay0, by0)
    if w <= 0 or h <= 0:
        return 0.0
    smaller = min((ax1 - ax0) * (ay1 - ay0), (bx1 - bx0) * (by1 - by0))
    return float(w * h / smaller) if smaller > 1e-6 else 0.0


def _text_of(m: Mobject) -> str:
    for attr in ("text", "tex_string", "original_text"):
        value = getattr(m, attr, None)
        if isinstance(value, str):
            return value
    return ""


def _describe(m: Mobject) -> str:
    text = _text_of(m).strip().replace("\n", " ")
    if len(text) > 40:
        text = text[:37] + "..."
    return f'{type(m).__name__}("{text}")' if text else type(m).__name__
