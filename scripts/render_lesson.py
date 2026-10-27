"""Render a lesson from a storyboard and a Manim module, without any LLM.

Runs the physical half of the studio - narration, parallel scene renders,
layout report, contact sheet, assembly - on files you provide. Useful for
developing cfkit, checking a showcase, or re-cutting a lesson by hand.

    python scripts/render_lesson.py backend/conceptflow/showcase/derivative --quality preview
    python scripts/render_lesson.py path/to/dir --code lesson.py --quality hd --voice edge
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from conceptflow.config import get_settings  # noqa: E402
from conceptflow.render import contact  # noqa: E402
from conceptflow.render.assemble import Clip, assemble  # noqa: E402
from conceptflow.render.runner import SceneRenderer  # noqa: E402
from conceptflow.render.sandbox import check, extract_code  # noqa: E402
from conceptflow.schemas import LessonRequest, Storyboard  # noqa: E402
from conceptflow.voice.narrator import Narrator  # noqa: E402


def log(event: dict) -> None:
    kind = event.get("type")
    if kind == "scene":
        extra = f" ({event.get('seconds')}s)" if event.get("seconds") else ""
        err = f"  !! {event['error']}" if event.get("error") else ""
        print(f"  scene {event['scene']}: {event['status']}{extra}{err}")
    elif kind in ("log", "progress") and kind == "log":
        print(f"  {event.get('agent')}: {event.get('text')}")


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("folder", type=Path)
    parser.add_argument("--code", default="lesson.py")
    parser.add_argument("--quality", default="preview", choices=["preview", "draft", "hd", "hd60"])
    parser.add_argument("--voice", default="auto")
    parser.add_argument("--language", default="en")
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()

    settings = get_settings()
    board = Storyboard.model_validate_json((args.folder / "storyboard.json").read_text(encoding="utf-8"))
    code = extract_code((args.folder / args.code).read_text(encoding="utf-8"))
    out = args.out or settings.data_dir / "dev" / args.folder.name
    out.mkdir(parents=True, exist_ok=True)

    scene_ids = [s.id for s in board.scenes]
    beat_ids = {s.id: [b.id for b in s.beats] for s in board.scenes}
    shape = check(code, scene_ids, beat_ids)
    for problem in shape.problems:
        print("  sandbox:", problem)

    started = time.time()
    request = LessonRequest(prompt=board.title, voice=args.voice, language=args.language, quality=args.quality)
    narration = await Narrator(settings, out, log).narrate(board, request)
    print(f"narration: {narration.provider} {narration.voice} in {time.time() - started:.1f}s")

    started = time.time()
    results = await SceneRenderer(settings, out, log).render_all(code, scene_ids, beat_ids, args.quality, "dev")
    print(f"render: {time.time() - started:.1f}s")

    frames, clips = [], []
    for scene in board.scenes:
        r = results[scene.id]
        if not r.ok:
            print(f"  {scene.id} FAILED: {json.dumps(r.error, indent=1)[:1500]}")
            continue
        for beat in r.timeline["beats"]:
            issues = ", ".join(f"{i['kind']}:{i.get('what', i.get('detail', ''))}" for i in beat["issues"])
            flag = f"  overrun {beat['overrun']}s" if beat["overrun"] > 0.3 else ""
            if issues or flag:
                print(f"  {beat['id']}: {issues}{flag}")
            if beat.get("keyframe"):
                frames.append((beat["id"], Path(r.out_dir) / "keyframes" / beat["keyframe"], ""))
        clips.append(Clip(scene.id, scene.title, Path(r.video), r.timeline))
    sheets = contact.build(frames, out / "review", "contact")
    print("contact sheets:", *sheets, sep="\n  ")
    if clips:
        manifest = assemble(clips, narration.beats, out / "final")
        print(f"lesson: {out / 'final' / manifest['video']}  ({manifest['duration']:.1f}s)")


if __name__ == "__main__":
    asyncio.run(main())
