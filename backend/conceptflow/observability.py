"""Observability: distributed tracing (OpenTelemetry) and provenance.

Tracing
    One OpenTelemetry tracer provider for the whole process. Google ADK already
    emits spans for every agent invocation, LLM call and tool call through the
    global provider; the studio adds spans for each lesson, each CrewAI task
    and LLM call, each MCP tool call, each A2A exchange, each scene render and
    each voice synthesis. The W3C trace context travels inside A2A requests, so
    the CrewAI writers' room's spans join the same trace as the ADK workflow.

    Exporters:
    * always - ``data/lessons/<id>/trace.jsonl`` (the production log's Trace view)
    * when LANGFUSE_PUBLIC_KEY/SECRET_KEY are set - Langfuse, over OTLP/HTTP
    * when OTEL_EXPORTER_OTLP_ENDPOINT is set - any OTLP collector (Jaeger, Phoenix...)

Provenance
    ``data/lessons/<id>/provenance.jsonl`` records every artifact the agents
    hand to one another: what it is, which agent/framework/model produced it,
    a content hash, the hashes of the artifacts it was made from, and the
    trace/span it was produced in. Any output can be followed back to its inputs.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from opentelemetry import context as otel_context
from opentelemetry import propagate, trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SimpleSpanProcessor, SpanExporter, SpanExportResult
from opentelemetry.trace import Status, StatusCode

from .config import Settings

TRACER = "conceptflow"
_MAX_ATTR = 1500
_lock = threading.Lock()
_trace_dirs: dict[int, Path] = {}
_configured = False


def _short(value: Any) -> Any:
    if isinstance(value, str) and len(value) > _MAX_ATTR:
        return value[:_MAX_ATTR] + f"... [{len(value)} chars]"
    if isinstance(value, (list, tuple)):
        return [_short(v) for v in value][:40]
    return value


class LessonFileExporter(SpanExporter):
    """Writes each finished span of a lesson to that lesson's trace.jsonl."""

    def export(self, spans: list[ReadableSpan]) -> SpanExportResult:
        for span in spans:
            folder = _trace_dirs.get(span.context.trace_id)
            if folder is None:
                continue
            record = {
                "name": span.name,
                "trace_id": f"{span.context.trace_id:032x}",
                "span_id": f"{span.context.span_id:016x}",
                "parent_id": f"{span.parent.span_id:016x}" if span.parent else None,
                "start": span.start_time / 1e9 if span.start_time else None,
                "end": span.end_time / 1e9 if span.end_time else None,
                "status": span.status.status_code.name,
                "scope": span.instrumentation_scope.name if span.instrumentation_scope else "",
                "attributes": {k: _short(v) for k, v in (span.attributes or {}).items()},
                "events": [{"name": e.name, "attributes": {k: _short(v) for k, v in (e.attributes or {}).items()}} for e in span.events][:20],
            }
            try:
                with _lock, (folder / "trace.jsonl").open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps(record, default=str) + "\n")
            except OSError:
                pass
        return SpanExportResult.SUCCESS

    def shutdown(self) -> None:
        return None


def setup(settings: Settings) -> list[str]:
    """Install the tracer provider once. Returns the active exporter names."""
    global _configured
    if _configured:
        return []
    _configured = True
    provider = TracerProvider(resource=Resource.create({"service.name": "conceptflow", "service.version": "1.1.0"}))
    provider.add_span_processor(SimpleSpanProcessor(LessonFileExporter()))
    active = ["lesson-files"]
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

    if settings.langfuse_public_key and settings.langfuse_secret_key:
        token = base64.b64encode(f"{settings.langfuse_public_key}:{settings.langfuse_secret_key}".encode()).decode()
        endpoint = settings.langfuse_host.rstrip("/") + "/api/public/otel/v1/traces"
        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint, headers={"Authorization": f"Basic {token}"})))
        active.append("langfuse")
    if os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT"):
        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
        active.append("otlp")
    trace.set_tracer_provider(provider)
    return active


def tracer() -> trace.Tracer:
    return trace.get_tracer(TRACER)


def bind_trace(job_dir: Path) -> None:
    """Route the current trace's spans to this lesson's trace file."""
    span = trace.get_current_span()
    if span.get_span_context().is_valid:
        _trace_dirs[span.get_span_context().trace_id] = job_dir


@contextmanager
def span(name: str, **attributes: Any) -> Iterator[trace.Span]:
    with tracer().start_as_current_span(name) as current:
        for key, value in attributes.items():
            if value is not None:
                current.set_attribute(key, _short(value) if isinstance(value, str) else value)
        try:
            yield current
        except Exception as exc:
            current.record_exception(exc)
            current.set_status(Status(StatusCode.ERROR, str(exc)[:300]))
            raise


def carrier() -> dict[str, str]:
    """The current trace context as W3C headers, for A2A requests."""
    headers: dict[str, str] = {}
    propagate.inject(headers)
    return headers


def attach_carrier(headers: dict[str, str] | None):
    """Continue a remote trace (A2A server side). Returns a token for detach()."""
    return otel_context.attach(propagate.extract(headers or {}))


def detach(token) -> None:
    otel_context.detach(token)


def ids() -> tuple[str, str]:
    ctx = trace.get_current_span().get_span_context()
    if not ctx.is_valid:
        return "", ""
    return f"{ctx.trace_id:032x}", f"{ctx.span_id:016x}"


# ---------------------------------------------------------------- provenance


def digest(data: Any) -> str:
    blob = data if isinstance(data, str) else json.dumps(data, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode("utf-8", "replace")).hexdigest()[:16]


def file_digest(path: Path) -> str:
    sha = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            sha.update(block)
    return sha.hexdigest()[:16]


def record_artifact(
    job_dir: Path,
    artifact: str,
    data: Any,
    producer: str,
    framework: str,
    model: str | None = None,
    inputs: dict[str, str] | None = None,
    note: str = "",
) -> str:
    """Append a provenance record; returns the artifact's content hash."""
    sha = digest(data)
    trace_id, span_id = ids()
    record = {
        "at": round(time.time(), 3),
        "artifact": artifact,
        "sha": sha,
        "producer": producer,
        "framework": framework,
        "model": model,
        "inputs": inputs or {},
        "trace_id": trace_id,
        "span_id": span_id,
        "note": note,
    }
    try:
        with _lock, (job_dir / "provenance.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record) + "\n")
    except OSError:
        pass
    return sha
