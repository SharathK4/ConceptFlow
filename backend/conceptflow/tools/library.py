"""The lesson library index (SQLite + full-text search).

Backs the ``search_lessons`` MCP tool: a database lookup the intake step uses
to tell a learner they already have a lesson on something similar. Rebuilt
from the lesson folders at start-up, updated as lessons finish.
"""

from __future__ import annotations

import json
import re
import sqlite3
import threading
from pathlib import Path

from .models import LessonHit

_lock = threading.Lock()


class Library:
    def __init__(self, db_path: Path) -> None:
        self.path = db_path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.execute(
                "CREATE VIRTUAL TABLE IF NOT EXISTS lessons USING fts5("
                "id UNINDEXED, title, prompt, logline, created UNINDEXED, duration UNINDEXED)"
            )

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path, timeout=10)

    def upsert(self, lesson_id: str, title: str, prompt: str, logline: str, created: float, duration: float | None) -> None:
        with _lock, self._connect() as db:
            db.execute("DELETE FROM lessons WHERE id = ?", (lesson_id,))
            db.execute(
                "INSERT INTO lessons (id, title, prompt, logline, created, duration) VALUES (?, ?, ?, ?, ?, ?)",
                (lesson_id, title, prompt, logline, created, duration),
            )

    def remove(self, lesson_id: str) -> None:
        with _lock, self._connect() as db:
            db.execute("DELETE FROM lessons WHERE id = ?", (lesson_id,))

    def rebuild(self, lessons_dir: Path) -> int:
        count = 0
        for job_file in lessons_dir.glob("*/job.json"):
            try:
                job = json.loads(job_file.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if job.get("status") != "done":
                continue
            manifest = job.get("manifest") or {}
            self.upsert(
                job["id"],
                job.get("title") or manifest.get("title", ""),
                job.get("request", {}).get("prompt", ""),
                manifest.get("logline", ""),
                float(job.get("created", 0)),
                manifest.get("duration"),
            )
            count += 1
        return count

    def search(self, query: str, limit: int = 5) -> list[LessonHit]:
        terms = [t for t in re.findall(r"[A-Za-z0-9]+", query.lower()) if len(t) > 2][:8]
        if not terms:
            return []
        match = " OR ".join(f'"{t}"*' for t in terms)
        with _lock, self._connect() as db:
            rows = db.execute(
                "SELECT id, title, prompt, created, duration FROM lessons WHERE lessons MATCH ? ORDER BY rank LIMIT ?",
                (match, max(1, min(limit, 20))),
            ).fetchall()
        return [
            LessonHit(id=r[0], title=r[1], prompt=r[2], created=float(r[3] or 0), duration=float(r[4]) if r[4] else None)
            for r in rows
        ]
