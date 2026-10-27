"""The house style for every ConceptFlow lesson.

One dark stage, one family of colours, one typographic voice. The palette is
Manim's own 3Blue1Brown set with two additions: a soft ink for text (pure
white glares on a dark stage) and the terracotta accent the web page uses, lifted
a little so it survives on the dark ground.

Nothing here is clever. It exists so a scene written by a model and a scene
written by a person come out looking like they belong to the same film.
"""

from __future__ import annotations

import os
from pathlib import Path

import manimpango
import numpy as np
from manim import (
    BLUE,
    GREEN,
    MathTex,
    ManimColor,
    MarkupText,
    Paragraph,
    RED,
    TEAL,
    Tex,
    Text,
    YELLOW,
)
from PIL import Image

__all__ = [
    "INK",
    "MUTED",
    "FAINT",
    "ACCENT",
    "HIGHLIGHT",
    "PRIMARY",
    "SECONDARY",
    "POSITIVE",
    "NEGATIVE",
    "STAGE_BG",
    "SERIF",
    "DISPLAY",
    "SANS",
    "SAFE_LEFT",
    "SAFE_RIGHT",
    "SAFE_TOP",
    "SAFE_BOTTOM",
]

# --------------------------------------------------------------------- colour

INK = ManimColor("#ECEEF4")  # body text and equations
MUTED = ManimColor("#9AA0B4")  # annotations, axis labels, secondary text
FAINT = ManimColor("#4A5063")  # grids, guides, anything that should recede
ACCENT = ManimColor("#E8845C")  # the house accent: the result, the punchline
HIGHLIGHT = YELLOW  # the thing currently being explained (#F7D96F)
PRIMARY = BLUE  # the main object on stage (#58C4DD)
SECONDARY = TEAL  # its counterpart (#5CD0B3)
POSITIVE = GREEN
NEGATIVE = RED

STAGE_BG = "#0E1016"

# ----------------------------------------------------------------------- type

SERIF = "CMU Serif"  # the text face matches the LaTeX maths exactly
DISPLAY = "Instrument Serif"  # title cards only
SANS = "Inter"  # tiny labels (tick values, tags)

# ----------------------------------------------------------------- the stage
#
# The frame is 14.22 x 8 units. The web player overlays captions across the
# bottom ~11%, so the safe area stops short of it.

SAFE_LEFT, SAFE_RIGHT = -6.55, 6.55
SAFE_TOP, SAFE_BOTTOM = 3.55, -2.95

_ROOT = Path(__file__).resolve().parents[2]
FONT_DIR = Path(os.environ.get("CF_FONT_DIR", _ROOT / "assets" / "fonts"))
CACHE_DIR = Path(os.environ.get("CF_CACHE_DIR", _ROOT / "data" / "cache"))

_FONT_FILES = (
    "cmunrm.otf",
    "cmunti.otf",
    "cmunbx.otf",
    "cmunsl.otf",
    "InstrumentSerif-Regular.ttf",
    "InstrumentSerif-Italic.ttf",
    "Inter-Variable.ttf",
)

_applied = False


def use_fontconfig() -> None:
    """Route Pango through fontconfig, reading the bundled fonts directly.

    On Windows, Pango's native backend slows to ~4 s per text object once any
    private font is registered; through fontconfig the same text takes ~0.1 s.
    Must run before Pango builds its font map, i.e. before the first ``Text``.
    The Windows font folder is included only as a fallback for glyphs the
    bundled faces do not have.
    """
    if os.environ.get("CF_NATIVE_PANGO"):
        return
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    dirs = [FONT_DIR]
    windows_fonts = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
    if os.name == "nt" and windows_fonts.exists():
        dirs.append(windows_fonts)
    conf = CACHE_DIR / "fonts.conf"
    body = "\n".join(f"  <dir>{d.as_posix()}</dir>" for d in dirs)
    text = (
        '<?xml version="1.0"?>\n<!DOCTYPE fontconfig SYSTEM "urn:fontconfig:fonts.dtd">\n'
        f"<fontconfig>\n{body}\n  <cachedir>{(CACHE_DIR / 'fontconfig').as_posix()}</cachedir>\n"
        "</fontconfig>\n"
    )
    if not conf.exists() or conf.read_text(encoding="utf-8") != text:
        conf.write_text(text, encoding="utf-8")
    os.environ.setdefault("FONTCONFIG_FILE", str(conf))
    os.environ.setdefault("PANGOCAIRO_BACKEND", "fc")


def register_fonts() -> None:
    fontconfig = os.environ.get("PANGOCAIRO_BACKEND") == "fc" or os.name != "nt"
    register = manimpango.fc_register_font if fontconfig else manimpango.register_font
    for name in _FONT_FILES:
        path = FONT_DIR / name
        if path.exists():
            register(str(path))


def apply_defaults() -> None:
    """Point Manim's text classes at the house faces and ink. Idempotent."""
    global _applied
    if _applied:
        return
    use_fontconfig()
    register_fonts()
    Text.set_default(font=SERIF, color=INK)
    MarkupText.set_default(font=SERIF, color=INK)
    Paragraph.set_default(font=SERIF, color=INK)
    MathTex.set_default(color=INK)
    Tex.set_default(color=INK)
    _applied = True


def backdrop(width: int, height: int) -> Path:
    """A barely-there radial falloff with fine grain, cached per resolution.

    Flat #000 reads as a void; a flat grey bands under H.264. A shallow
    vignette brighter just above centre - where the eye lands - plus a fixed
    dither gives the stage some depth without ever reading as a gradient.
    """
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = CACHE_DIR / f"stage_{width}x{height}_v2.png"
    if path.exists():
        return path

    y, x = np.mgrid[0:height, 0:width].astype(np.float32)
    cx, cy = width * 0.5, height * 0.44
    r = np.sqrt(((x - cx) / (width * 0.78)) ** 2 + ((y - cy) / (height * 0.86)) ** 2)
    t = np.clip(r, 0.0, 1.0) ** 1.5
    centre = np.array([23, 26, 35], dtype=np.float32)
    edge = np.array([9, 10, 14], dtype=np.float32)
    img = centre * (1 - t)[..., None] + edge * t[..., None]
    img += np.random.default_rng(7).normal(0.0, 0.9, img.shape).astype(np.float32)
    rgba = np.concatenate(
        [np.clip(img, 0, 255).astype(np.uint8), np.full((height, width, 1), 255, np.uint8)],
        axis=2,
    )
    # Scenes render in parallel processes: write aside, then swap in atomically
    # so no process ever reads a half-written file.
    scratch = path.with_name(f"{path.stem}.{os.getpid()}.tmp.png")
    Image.fromarray(rgba, "RGBA").save(scratch)
    try:
        os.replace(scratch, path)
    except OSError:
        scratch.unlink(missing_ok=True)  # another process won the race; its file is identical
    return path
