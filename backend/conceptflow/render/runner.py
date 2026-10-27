"""The Render agent's machinery: every scene in its own process, in parallel.

A scene is re-rendered only when something that affects it changed - its
class, the module-level helpers, the length of its narration, or the output
quality. Correction rounds usually touch one or two scenes, so the rest come
straight from the cache.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable

from ..config import BACKEND, Settings
from .sandbox import class_source, scene_class

def _toolkit_fingerprint() -> str:
    """Changes whenever cfkit changes, so a toolkit fix invalidates old renders."""
    digest = hashlib.sha1()
    for path in sorted((BACKEND / "cfkit").glob("*.py")):
        digest.update(path.read_bytes())
    return digest.hexdigest()[:12]


RENDER_VERSION = f"cf-render-4-{_toolkit_fingerprint()}"


@dataclass
class SceneResult:
    scene: str
    cls: str
    ok: bool
    quality: str
    out_dir: str
    video: str | None = None
    duration: float = 0.0
    error: dict | None = None
    timeline: dict | None = None
    cached: bool = False
    seconds: float = 0.0

    def to_dict(self) -> dict:
        return asdict(self)


class SceneRenderer:
    def __init__(self, settings: Settings, job_dir: Path, emit: Callable[[dict], None]) -> None:
        self.settings = settings
        self.dir = job_dir
        self.emit = emit
        self.sem = asyncio.Semaphore(settings.render_workers)

    def _key(self, code: str, cls: str, quality: str, beat_ids: list[str], timing: dict) -> str:
        durations = [timing.get(b, {}).get("duration") for b in beat_ids]
        blob = json.dumps([RENDER_VERSION, quality, class_source(code, cls), durations], sort_keys=True)
        return hashlib.sha1(blob.encode("utf-8")).hexdigest()[:16]

    async def render_all(
        self,
        code: str,
        scene_ids: list[str],
        beat_ids: dict[str, list[str]],
        quality: str,
        label: str,
    ) -> dict[str, SceneResult]:
        code_dir = self.dir / "code"
        code_dir.mkdir(parents=True, exist_ok=True)
        module = code_dir / f"lesson_{label}.py"
        module.write_text(code, encoding="utf-8")
        (code_dir / "lesson.py").write_text(code, encoding="utf-8")

        timing_path = self.dir / "timing.json"
        timing = json.loads(timing_path.read_text(encoding="utf-8")).get("beats", {}) if timing_path.exists() else {}

        async def one(sid: str) -> SceneResult:
            cls = scene_class(sid)
            key = self._key(code, cls, quality, beat_ids.get(sid, []), timing)
            out = self.dir / "renders" / quality / f"{cls}-{key}"
            result_file = out / "result.json"
            if result_file.exists():
                # Same code, same narration, same quality: the outcome cannot
                # differ - unless the failure was environmental (a timeout, a
                # crash, anything not raised from the lesson's own code).
                cached = self._load(sid, cls, quality, out, cached=True)
                error = cached.error or {}
                from_lesson = bool(error.get("where")) and error.get("type") not in ("Timeout", "Crash")
                if cached.ok or from_lesson:
                    status = "cached" if cached.ok else "failed"
                    self.emit({"type": "scene", "scene": sid, "status": status, "quality": quality, "error": _short(cached.error), **_urls(self.dir, cached)})
                    return cached
            async with self.sem:
                self.emit({"type": "scene", "scene": sid, "status": "rendering", "quality": quality})
                started = time.time()
                await asyncio.to_thread(self._run, module, cls, quality, out, timing_path)
                result = self._load(sid, cls, quality, out, cached=False)
                result.seconds = round(time.time() - started, 1)
            status = "rendered" if result.ok else "failed"
            self.emit({"type": "scene", "scene": sid, "status": status, "quality": quality, "seconds": result.seconds, "error": _short(result.error), **_urls(self.dir, result)})
            return result

        results = await asyncio.gather(*[one(s) for s in scene_ids])
        return {r.scene: r for r in results}

    def _run(self, module: Path, cls: str, quality: str, out: Path, timing: Path) -> None:
        out.mkdir(parents=True, exist_ok=True)
        env = dict(os.environ)
        env.update(
            {
                "PYTHONPATH": str(BACKEND) + os.pathsep + env.get("PYTHONPATH", ""),
                "CF_TIMING": str(timing),
                "CF_OUT": str(out),
                "CF_SCENE_ID": cls,
                "CF_CACHE_DIR": str(self.settings.cache_dir),
                "PYTHONIOENCODING": "utf-8",
            }
        )
        command = [
            sys.executable,
            "-m",
            "cfkit.render",
            str(module),
            cls,
            "--quality",
            quality,
            "--out",
            str(out),
            "--result",
            str(out / "result.json"),
            "--tex-cache",
            str(self.settings.cache_dir / "tex"),
        ]
        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        try:
            proc = subprocess.run(
                command,
                env=env,
                cwd=str(out),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self.settings.render_timeout,
                creationflags=flags,
            )
            (out / "stderr.txt").write_text(proc.stderr[-20000:], encoding="utf-8")
        except subprocess.TimeoutExpired:
            (out / "result.json").write_text(
                json.dumps(
                    {
                        "scene": cls,
                        "ok": False,
                        "error": {
                            "type": "Timeout",
                            "message": f"the scene took longer than {self.settings.render_timeout}s to render - simplify it (fewer updaters, fewer points, shorter always_redraw work)",
                        },
                    }
                ),
                encoding="utf-8",
            )

    def _load(self, sid: str, cls: str, quality: str, out: Path, cached: bool) -> SceneResult:
        result = SceneResult(sid, cls, False, quality, str(out), cached=cached)
        try:
            data = json.loads((out / "result.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            stderr = (out / "stderr.txt").read_text(encoding="utf-8") if (out / "stderr.txt").exists() else ""
            result.error = {"type": "Crash", "message": stderr[-1500:] or "the renderer exited without a result"}
            return result
        result.ok = bool(data.get("ok")) and bool(data.get("video")) and Path(data["video"]).exists()
        result.video = data.get("video")
        result.duration = float(data.get("duration") or 0)
        result.error = data.get("error")
        result.seconds = float(data.get("seconds") or 0)
        timeline = out / f"{cls}.timeline.json"
        if timeline.exists():
            result.timeline = json.loads(timeline.read_text(encoding="utf-8"))
        return result


def _short(error: dict | None) -> str | None:
    if not error:
        return None
    return f"{error.get('type')}: {str(error.get('message', ''))[:220]}"


def _urls(job_dir: Path, result: SceneResult) -> dict:
    frames = []
    if result.timeline:
        for beat in result.timeline.get("beats", []):
            if beat.get("keyframe"):
                path = Path(result.out_dir) / "keyframes" / beat["keyframe"]
                frames.append({"beat": beat["id"], "url": media_url(job_dir, path)})
    return {"keyframes": frames}


def media_url(job_dir: Path, path: Path) -> str:
    rel = Path(path).resolve().relative_to(job_dir.resolve()).as_posix()
    return f"/media/{job_dir.name}/{rel}"
