"""Recorded agent responses, for running the whole studio without a key.

A showcase is a directory of the responses each role gave on a real run -
analysis, plan, storyboard, code, reviews, fix. In replay mode the model
adapters of *both* frameworks read from it instead of calling Gemini, and
everything else - the CrewAI crew, the ADK workflow, narration, rendering,
review routing, assembly - runs for real. That makes replay useful twice:
as an offline demo that cannot hit a quota mid-presentation, and as the
integration test for the pipeline.
"""

from __future__ import annotations

import contextvars
import json
import random
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

SHOWCASE_DIR = Path(__file__).resolve().parents[1] / "showcase"


@dataclass
class ReplayScript:
    slug: str
    title: str
    responses: dict[str, list[str]]
    delay: tuple[float, float] = (0.9, 2.2)
    _cursor: dict[str, int] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def next(self, role: str) -> str:
        with self._lock:
            items = self.responses.get(role) or []
            if not items:
                raise RuntimeError(f"the '{self.slug}' showcase has no recorded response for {role}")
            i = min(self._cursor.get(role, 0), len(items) - 1)
            self._cursor[role] = i + 1
            return items[i]

    def pause(self) -> float:
        return random.uniform(*self.delay)

    def sleep(self) -> None:
        time.sleep(self.pause())


current: contextvars.ContextVar[ReplayScript | None] = contextvars.ContextVar("replay_script", default=None)


def showcases() -> list[dict]:
    items = []
    for meta_path in sorted(SHOWCASE_DIR.glob("*/meta.json")):
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        meta["slug"] = meta_path.parent.name
        items.append(meta)
    return items


def choose(prompt: str) -> str | None:
    """Pick the showcase closest to what was asked for."""
    words = set(re.findall(r"[a-z]+", prompt.lower()))
    best, best_score = None, 0
    for meta in showcases():
        score = len(words & set(meta.get("keywords", [])))
        if score > best_score:
            best, best_score = meta["slug"], score
    if best is None:
        items = showcases()
        best = items[0]["slug"] if items else None
    return best


def load(slug: str) -> ReplayScript:
    root = SHOWCASE_DIR / slug
    meta = json.loads((root / "meta.json").read_text(encoding="utf-8"))
    responses: dict[str, list[str]] = {}
    for role, files in meta["responses"].items():
        responses[role] = [(root / name).read_text(encoding="utf-8") for name in files]
    return ReplayScript(slug=slug, title=meta["title"], responses=responses)
