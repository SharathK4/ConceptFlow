"""How agents talk to the page.

Every node, callback and crew task can reach the job it is working for
through a context variable, set once when the job starts. Context variables
follow ``asyncio`` tasks and ``asyncio.to_thread``, so the ADK workflow's
parallel branches and the CrewAI crew's worker thread all see the same job
without it being threaded through every signature.
"""

from __future__ import annotations

import contextvars
from pathlib import Path
from typing import Protocol


class JobHandle(Protocol):
    id: str
    dir: Path

    def emit(self, event: dict) -> None: ...

    def update(self, **fields) -> None: ...


_current: contextvars.ContextVar[JobHandle | None] = contextvars.ContextVar("conceptflow_job", default=None)


def bind(job: JobHandle) -> contextvars.Token:
    return _current.set(job)


def current_job() -> JobHandle:
    job = _current.get()
    if job is None:
        raise RuntimeError("no job is bound to this context")
    return job
