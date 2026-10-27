"""Names from older Manim that models still reach for.

Language models learnt Manim from years of tutorials written against the
3b1b fork and early Community releases. Most of what they misremember is a
rename, so rather than spend a correction round on ``ShowCreation``, the old
names are simply made to work.
"""

from __future__ import annotations

from manim import (
    DOWN,
    UP,
    Create,
    CoordinateSystem,
    FadeIn,
    FadeOut,
    MathTex,
    Tex,
    Uncreate,
)

__all__ = [
    "ShowCreation",
    "Uncreate",
    "TexMobject",
    "TextMobject",
    "FadeInFromDown",
    "FadeInFrom",
    "FadeOutAndShift",
    "FadeOutAndShiftDown",
]

ShowCreation = Create
TexMobject = MathTex
TextMobject = Tex


def FadeInFromDown(mobject, **kwargs):
    return FadeIn(mobject, shift=UP, **kwargs)


def FadeInFrom(mobject, direction=DOWN, **kwargs):
    return FadeIn(mobject, shift=-direction, **kwargs)


def FadeOutAndShift(mobject, direction=DOWN, **kwargs):
    return FadeOut(mobject, shift=direction, **kwargs)


def FadeOutAndShiftDown(mobject, **kwargs):
    return FadeOut(mobject, shift=DOWN, **kwargs)


# Axes.get_graph was renamed to plot.
if not hasattr(CoordinateSystem, "get_graph"):
    CoordinateSystem.get_graph = CoordinateSystem.plot  # type: ignore[attr-defined]


# Manim 0.21's LaggedStartMap passes each submobject to the animation as
# ``animation_class(*submob)`` with an identity default, which unpacks the
# submobject's own children into positional arguments: LaggedStartMap(Create,
# arrows) calls Create(*arrow) and the arrow tip lands in ``lag_ratio``. The
# documented behaviour - one animation per submobject - needs the default to wrap.
def _patch_lagged_start_map() -> None:
    import inspect

    from manim import LaggedStartMap

    original = LaggedStartMap.__init__
    if getattr(original, "_cfkit_patched", False) or "identity" not in inspect.getsource(original):
        return

    def __init__(self, animation_class, mobject, arg_creator=None, run_time=2, **kwargs):
        if arg_creator is None:
            arg_creator = lambda mob: (mob,)  # noqa: E731
        original(self, animation_class, mobject, arg_creator=arg_creator, run_time=run_time, **kwargs)

    __init__._cfkit_patched = True  # type: ignore[attr-defined]
    LaggedStartMap.__init__ = __init__


_patch_lagged_start_map()
