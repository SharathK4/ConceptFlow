"""Pre-styled building blocks.

Each of these is ordinary Manim underneath - a VGroup, an Axes, a MathTex - so
a scene can animate, transform and position them like anything else. They
exist to make the good-looking choice the short one: a model asked for "a
labelled graph" should reach for ``cf_axes`` and get thin muted axes with
quiet tick labels, not the default chunky white ones.
"""

from __future__ import annotations

import re
import textwrap
from typing import Iterable, Sequence

import numpy as np
from manim import (
    DOWN,
    LEFT,
    RIGHT,
    UP,
    Arrow,
    Axes,
    Dot,
    MathTex,
    Mobject,
    NumberPlane,
    Paragraph,
    RoundedRectangle,
    SurroundingRectangle,
    Tex,
    Text,
    Underline,
    VGroup,
)

from .theme import ACCENT, FAINT, HIGHLIGHT, INK, MUTED, SANS, SERIF, STAGE_BG

__all__ = [
    "Eq",
    "Label",
    "Note",
    "Pill",
    "Card",
    "Callout",
    "StepList",
    "highlight_box",
    "plate",
    "underline",
    "cf_axes",
    "cf_plane",
    "labeled_dot",
    "safe_eq",
]


def Eq(*tex: str, size: float = 52, color=INK, **kwargs) -> MathTex:
    """An equation in the house ink. Pass several strings to colour parts:

    ``Eq("f'(x)", "=", r"\\lim_{h\\to 0}", r"\\frac{f(x+h)-f(x)}{h}")``
    """
    return MathTex(*tex, font_size=size, color=color, **kwargs)


_MATH_TOKEN = re.compile(r"[_^\\]")
_TEX_SPECIAL = {"&": r"\&", "%": r"\%", "#": r"\#", "$": r"\$", "~": r"\textasciitilde{}"}


def Label(text: str, size: float = 30, color=MUTED, italic: bool = False) -> Mobject:
    """A short caption for something on stage.

    Captions often name a symbol - "Integrated series X_t", "Matrix R_{X,T}".
    Plain text would print the underscores, so a caption with maths in it is
    typeset with LaTeX instead: words as text, symbol tokens as inline maths.
    """
    plain = Text(text, font=SERIF, font_size=size, color=color, slant="ITALIC" if italic else "NORMAL")
    if "$" not in text and not _MATH_TOKEN.search(text):
        return plain
    source = text if "$" in text else _inline_maths(text, italic)
    try:
        return Tex(source, font_size=size * 1.18, color=color)
    except Exception:  # noqa: BLE001 - LaTeX refused it; a caption is better than a crash
        return plain


def _inline_maths(text: str, italic: bool) -> str:
    """Words as text, runs of symbol tokens as one inline-maths span."""
    out: list[str] = []
    run: list[str] = []

    def close_run() -> None:
        if run:
            out.append("$" + " ".join(run) + "$")
            run.clear()

    for token in text.split():
        if _MATH_TOKEN.search(token):
            core = token.rstrip(".,;:?!")
            if core.endswith("\\"):  # "\," is a thin space, not a comma
                core = token
            run.append(core)
            if core != token:  # trailing punctuation belongs to the sentence
                close_run()
                out[-1] += token[len(core):]
        else:
            close_run()
            word = "".join(_TEX_SPECIAL.get(ch, ch) for ch in token)
            out.append(rf"\textit{{{word}}}" if italic else word)
    close_run()
    return " ".join(out)


def Note(text: str, width: float = 5.6, size: float = 28, color=INK, align: str = "left") -> Paragraph:
    """A few lines of explanatory text, wrapped to roughly ``width`` units."""
    # CMU Serif at font_size 28 is ~0.2 units per character on average.
    chars = max(12, int(width / (0.0071 * size)))
    lines = textwrap.wrap(text, width=chars) or [text]
    para = Paragraph(*lines, font=SERIF, font_size=size, color=color, alignment=align, line_spacing=0.9)
    if para.width > width * 1.08:
        para.scale_to_fit_width(width)
    return para


def Pill(text: str, color=ACCENT, size: float = 22) -> VGroup:
    """A small rounded tag, for naming a region or a step."""
    label = Text(text, font=SANS, font_size=size, color=color, weight="MEDIUM")
    box = RoundedRectangle(
        width=label.width + 0.42,
        height=label.height + 0.26,
        corner_radius=min(0.18, (label.height + 0.26) / 2),
        stroke_color=color,
        stroke_width=1.6,
        fill_color=color,
        fill_opacity=0.10,
    )
    label.move_to(box)
    return VGroup(box, label)


def Card(*items: Mobject, width: float | None = None, pad: float = 0.35, color=FAINT) -> VGroup:
    """A quiet rounded panel holding ``items`` stacked vertically."""
    content = VGroup(*items).arrange(DOWN, buff=0.28, aligned_edge=LEFT)
    w = max(width or 0, content.width + 2 * pad)
    panel = RoundedRectangle(
        width=w,
        height=content.height + 2 * pad,
        corner_radius=0.18,
        stroke_color=color,
        stroke_width=1.2,
        fill_color="#161924",
        fill_opacity=0.92,
    )
    content.move_to(panel)
    return VGroup(panel, content)


def Callout(target: Mobject, text: str, direction=UP + RIGHT, distance: float = 1.3, color=MUTED, size: float = 26) -> VGroup:
    """A label placed near ``target`` with a thin arrow pointing at it."""
    direction = np.array(direction, dtype=float)
    direction = direction / (np.linalg.norm(direction) or 1.0)
    label = Text(text, font=SERIF, font_size=size, color=color, slant="ITALIC")
    anchor = target.get_critical_point(direction)
    label.move_to(anchor + direction * distance)
    start = label.get_critical_point(-direction)
    arrow = Arrow(
        start,
        anchor + direction * 0.08,
        buff=0.08,
        stroke_width=2,
        color=color,
        max_tip_length_to_length_ratio=0.12,
        tip_length=0.14,
    )
    return VGroup(arrow, label)


def StepList(items: Sequence[str], size: float = 30, numbered: bool = True, color=INK) -> VGroup:
    """Rows of short statements. Reveal them one by one with ``reveal``."""
    rows = []
    for i, item in enumerate(items, 1):
        body = Text(item, font=SERIF, font_size=size, color=color)
        if numbered:
            num = Text(f"{i:02d}", font=SANS, font_size=size * 0.62, color=ACCENT)
            num.next_to(body, LEFT, buff=0.32).align_to(body, UP).shift(0.04 * DOWN)
            rows.append(VGroup(num, body))
        else:
            rows.append(body)
    return VGroup(*rows).arrange(DOWN, buff=0.34, aligned_edge=LEFT)


def plate(*mobjects: Mobject, buff: float = 0.22) -> SurroundingRectangle:
    """A dark rounded backing for text placed over a grid or a busy picture.

    Add it before (underneath) the text: ``self.play(FadeIn(plate(eq)), Write(eq))``.
    """
    return SurroundingRectangle(
        VGroup(*mobjects),
        color=FAINT,
        stroke_width=1,
        fill_color=STAGE_BG,
        fill_opacity=0.9,
        buff=buff,
        corner_radius=0.12,
    )


def highlight_box(mob: Mobject, color=HIGHLIGHT, buff: float = 0.14) -> SurroundingRectangle:
    """A rounded outline around ``mob``. Animate with Create(...)."""
    return SurroundingRectangle(mob, color=color, buff=buff, corner_radius=0.1, stroke_width=2.6)


def underline(mob: Mobject, color=ACCENT) -> Underline:
    return Underline(mob, color=color, stroke_width=3, buff=0.08)


def cf_axes(
    x_range: Iterable[float] = (-4, 4, 1),
    y_range: Iterable[float] = (-3, 3, 1),
    x_length: float = 9.0,
    y_length: float = 5.4,
    numbers: bool = True,
    **kwargs,
) -> Axes:
    """Thin, muted axes with small tick labels - the frame, not the subject."""
    axis_config = {
        "color": MUTED,
        "stroke_width": 2,
        "include_tip": True,
        "tip_width": 0.18,
        "tip_height": 0.18,
        "include_numbers": numbers,
        "font_size": 22,
        "decimal_number_config": {"color": MUTED, "num_decimal_places": 0},
    }
    axis_config.update(kwargs.pop("axis_config", {}))
    x_range, y_range = list(x_range), list(y_range)
    # Tick labels get as many decimals as the step needs: a 0.25 step reads
    # 0.25, 0.5, 0.75 - not 0, 0, 1.
    per_axis = {}
    for name, rng in (("x_axis_config", x_range), ("y_axis_config", y_range)):
        own = dict(kwargs.pop(name, {}))
        own.setdefault("decimal_number_config", {"color": MUTED, "num_decimal_places": _decimals(rng)})
        per_axis[name] = own
    return Axes(
        x_range=x_range,
        y_range=y_range,
        x_length=x_length,
        y_length=y_length,
        axis_config=axis_config,
        **per_axis,
        **kwargs,
    )


def _decimals(rng: list) -> int:
    """Decimal places needed to print the ticks of a (min, max, step) range."""
    step = float(rng[2]) if len(rng) > 2 else 1.0
    for places in range(3):
        if abs(round(step, places) - step) < 1e-9 and abs(round(float(rng[0]), places) - float(rng[0])) < 1e-9:
            return places
    return 2


def cf_plane(x_range=(-7, 7, 1), y_range=(-4, 4, 1), **kwargs) -> NumberPlane:
    """A faint coordinate grid for linear-algebra style scenes."""
    return NumberPlane(
        x_range=list(x_range),
        y_range=list(y_range),
        background_line_style={"stroke_color": FAINT, "stroke_width": 1, "stroke_opacity": 0.55},
        axis_config={"stroke_color": MUTED, "stroke_width": 2},
        faded_line_ratio=2,
        **kwargs,
    )


def labeled_dot(point, tex: str | None = None, color=HIGHLIGHT, direction=UP, size: float = 30) -> VGroup:
    """A dot with an optional MathTex label beside it."""
    dot = Dot(point, color=color, radius=0.07)
    group = VGroup(dot)
    if tex:
        label = MathTex(tex, font_size=size, color=color).next_to(dot, direction, buff=0.14)
        group.add(label)
    return group


def safe_eq(tex: str, size: float = 52, color=INK) -> Mobject:
    """MathTex that degrades to plain text if the LaTeX does not compile.

    Used by the studio's fallback scenes, which must never fail to render.
    """
    try:
        return MathTex(tex, font_size=size, color=color)
    except Exception:  # noqa: BLE001 - any LaTeX failure
        return Text(tex, font=SERIF, font_size=size * 0.6, color=color)
