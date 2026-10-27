"""Render one scene of a generated lesson, in its own process.

    python -m cfkit.render lesson.py S2 --quality preview --out DIR --result S2.json

Each scene renders separately so a bug in scene 3 cannot take scenes 1, 2, 4
and 5 down with it, so scenes render in parallel, and so an unchanged scene can
be reused across correction rounds. The result file is what the render agent
reads: whether it worked, where the video is, and - if it did not work - an
error written for the agent that has to fix it: the exception, the line of the
lesson it came from, the beat that was playing, and for LaTeX failures the
TeX error itself rather than "see the log".
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import linecache
import re
import sys
import time
import traceback
from pathlib import Path

QUALITIES = {
    "preview": (854, 480, 15),
    "draft": (1280, 720, 30),
    "hd": (1920, 1080, 30),
    "hd60": (1920, 1080, 60),
}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("module")
    parser.add_argument("scene")
    parser.add_argument("--quality", default="preview", choices=sorted(QUALITIES))
    parser.add_argument("--out", required=True)
    parser.add_argument("--result", required=True)
    parser.add_argument("--tex-cache", default=None, help="shared LaTeX cache directory")
    args = parser.parse_args()

    module_path = Path(args.module).resolve()
    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    width, height, fps = QUALITIES[args.quality]

    result: dict = {"scene": args.scene, "quality": args.quality, "ok": False}
    started = time.time()
    try:
        import cfkit  # noqa: F401 - fonts and fontconfig before any text is set
        from cfkit import speed
        from manim import tempconfig

        speed.install("veryfast" if args.quality in ("preview", "draft") else "faster")
        settings = {
            "pixel_width": width,
            "pixel_height": height,
            "frame_rate": fps,
            "media_dir": str(out / "media"),
            "video_dir": str(out),
            "partial_movie_dir": str(out / "partial" / args.scene),
            "images_dir": str(out / "images"),
            "tex_dir": str(Path(args.tex_cache).resolve() if args.tex_cache else out / "tex"),
            "text_dir": str(out / "text" / args.scene),
            "output_file": args.scene,
            # Aux files stay: other renders share this directory, and the .log
            # is what turns a LaTeX failure into a readable error.
            "no_latex_cleanup": True,
            # Encode the previous animation while the next one renders.
            "max_inflight_encoders": 4,
            "disable_caching": True,
            "verbosity": "WARNING",
            "progress_bar": "none",
            "write_to_movie": True,
        }
        with tempconfig(settings):
            spec = importlib.util.spec_from_file_location("lesson", module_path)
            if spec is None or spec.loader is None:
                raise ImportError(f"cannot load {module_path}")
            module = importlib.util.module_from_spec(spec)
            sys.modules["lesson"] = module
            spec.loader.exec_module(module)
            scene_cls = getattr(module, args.scene, None)
            if scene_cls is None:
                raise AttributeError(f"the lesson has no scene class named {args.scene!r}")
            scene = scene_cls()
            scene.render()
            result["video"] = str(Path(scene.renderer.file_writer.movie_file_path))
            result["duration"] = round(float(scene.time), 3)
            result["ok"] = True
    except BaseException as exc:  # noqa: BLE001 - a render must always report
        result["error"] = describe(exc, module_path)
    result["seconds"] = round(time.time() - started, 2)
    Path(args.result).write_text(json.dumps(result, indent=2), encoding="utf-8")
    return 0 if result["ok"] else 1


def describe(exc: BaseException, module_path: Path) -> dict:
    """Turn an exception into something a code-fixing agent can act on."""
    from cfkit.scene import STATE

    frames = traceback.extract_tb(exc.__traceback__)
    lesson_frames = [f for f in frames if Path(f.filename).resolve() == module_path]
    where = None
    if lesson_frames:
        last = lesson_frames[-1]
        where = {
            "line": last.lineno,
            "code": (last.line or "").strip(),
            "context": _context(module_path, last.lineno),
        }

    message = f"{type(exc).__name__}: {exc}"
    report = {
        "type": type(exc).__name__,
        "message": str(exc)[:1200],
        "beat": STATE.get("beat"),
        "where": where,
        "trace_tail": "".join(traceback.format_exception(exc)[-6:])[-2500:],
    }
    latex = _latex_error(message)
    if latex:
        report["latex"] = latex
    return report


def _context(path: Path, line: int, radius: int = 3) -> str:
    rows = []
    for n in range(max(1, line - radius), line + radius + 1):
        text = linecache.getline(str(path), n)
        if not text:
            continue
        marker = ">>" if n == line else "  "
        rows.append(f"{marker}{n:4d} | {text.rstrip()}")
    return "\n".join(rows)


def _latex_error(message: str) -> dict | None:
    match = re.search(r"log file: (.+\.log)", message)
    if not match:
        return None
    log = Path(match.group(1).strip())
    tex = log.with_suffix(".tex")
    info: dict = {}
    if log.exists():
        lines = log.read_text(encoding="utf-8", errors="replace").splitlines()
        errors = []
        for i, line in enumerate(lines):
            if line.startswith("!"):
                errors.append("\n".join(lines[i : i + 3]))
        info["errors"] = errors[:3]
    if tex.exists():
        body = tex.read_text(encoding="utf-8", errors="replace")
        m = re.search(r"\\begin\{center\}(.*?)\\end\{center\}", body, re.S)
        info["source"] = (m.group(1) if m else body[-600:]).strip()
    return info or None


if __name__ == "__main__":
    sys.exit(main())
