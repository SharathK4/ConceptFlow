"""One LaTeX cache for every render, safe under parallel scenes.

Manim caches each compiled formula as an SVG named by a hash of its source, so
a digit on an axis is typeset once and reused. Scenes render in separate
processes, though, and two of them reaching for the same formula at the same
moment would run LaTeX over the same files at once. This wraps the compile in
a lock file per formula, so the second process waits a moment and then finds
the SVG already there.
"""

from __future__ import annotations

import hashlib
import os
import secrets
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

import manim.mobject.svg.svg_mobject as svg_mobject
import manim.mobject.text.tex_mobject as tex_mobject
import manim.utils.tex_file_writing as tex_file_writing
from manim import config

_original = tex_file_writing.tex_to_svg_file
_STALE_AFTER = 90.0


@contextmanager
def _lock(path: Path) -> Iterator[None]:
    fd = None
    deadline = time.monotonic() + _STALE_AFTER
    while fd is None:
        try:
            fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            try:
                if time.time() - path.stat().st_mtime > _STALE_AFTER:
                    path.unlink(missing_ok=True)  # a crashed render left it behind
                    continue
            except FileNotFoundError:
                continue
            if time.monotonic() > deadline:
                break  # proceed unlocked rather than hang a render
            time.sleep(0.05)
    try:
        yield
    finally:
        if fd is not None:
            os.close(fd)
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass


def _locked_tex_to_svg_file(expression, environment=None, tex_template=None):
    template = tex_template or config["tex_template"]
    key = hashlib.sha256(
        f"{template.body}|{environment}|{expression}".encode("utf-8", "replace")
    ).hexdigest()[:24]
    tex_dir = Path(config.get_dir("tex_dir"))
    tex_dir.mkdir(parents=True, exist_ok=True)
    with _lock(tex_dir / f"{key}.lock"):
        return _original(expression, environment, tex_template)


def _generate_mobject(self):
    """SVGMobject.generate_mobject with a private scratch file.

    Manim rewrites each SVG to ``<name>_.svg`` beside the original, parses it
    and deletes it. With a shared cache, two scenes typesetting the same
    formula at once delete each other's scratch file mid-parse; a per-process
    name removes the collision.
    """
    file_path = self.get_file_path()
    tree = svg_mobject.ET.parse(file_path)
    modified = self.modify_xml_tree(tree)
    scratch = file_path.with_name(f"{file_path.stem}_{os.getpid()}_{secrets.token_hex(3)}{file_path.suffix}")
    modified.write(scratch)
    try:
        svg = svg_mobject.se.SVG.parse(scratch)
    finally:
        scratch.unlink(missing_ok=True)
    mobjects, mobject_dict = self.get_mobjects_from(svg)
    self.add(*mobjects)
    self.id_to_vgroup_dict = mobject_dict
    self.flip(svg_mobject.RIGHT)
    return self


def install() -> None:
    tex_file_writing.tex_to_svg_file = _locked_tex_to_svg_file
    tex_mobject.tex_to_svg_file = _locked_tex_to_svg_file
    svg_mobject.SVGMobject.generate_mobject = _generate_mobject
