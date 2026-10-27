"""Zoom-invariant stroke widths for the moving camera.

Manim's Cairo camera means to keep lines the same thickness when the camera
zooms ("This ensures lines have constant width as you zoom in on them"), but
multiplies by ``frame_width / frame_width``, which is always 1 - so a 10x zoom
draws every curve ten times fatter. 3Blue1Brown-style zooms depend on lines
staying hairline-thin while the world grows, so the intended behaviour is
restored here for MovingCamera: stroke widths are scaled by the current frame
width over the default one.
"""

from __future__ import annotations

from manim import config
from manim.camera.camera import CAP_STYLE_MAP, LINE_JOIN_MAP
from manim.camera.moving_camera import MovingCamera
from manim.constants import CapStyleType, LineJointType


def _apply_stroke(self, ctx, vmobject, background: bool = False):
    width = vmobject.get_stroke_width(background)
    if width == 0:
        return self
    self.set_cairo_context_color(ctx, self.get_stroke_rgbas(vmobject, background=background), vmobject)
    zoom = self.frame_width / config.frame_width
    ctx.set_line_width(width * self.cairo_line_width_multiple * zoom)
    if vmobject.joint_type != LineJointType.AUTO:
        ctx.set_line_join(LINE_JOIN_MAP[vmobject.joint_type])
    if vmobject.cap_style != CapStyleType.AUTO:
        ctx.set_line_cap(CAP_STYLE_MAP[vmobject.cap_style])
    ctx.stroke_preserve()
    return self


def install() -> None:
    MovingCamera.apply_stroke = _apply_stroke
