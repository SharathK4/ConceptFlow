"""Encoder settings for renders that should not keep a person waiting.

Manim encodes each animation with libx264 at its default ("medium") preset.
Lesson frames are mostly a still, dark stage with a few moving lines, which a
faster preset compresses nearly as well, so previews use "veryfast" and final
renders "faster".
"""

from __future__ import annotations

from manim.scene.scene_file_writer import SceneFileWriter


def install(preset: str | None) -> None:
    if not preset:
        return
    original = SceneFileWriter.open_partial_movie_stream
    if getattr(original, "_cf_preset", None) == preset:
        return
    base = getattr(original, "_cf_original", original)

    def open_partial_movie_stream(self, file_path=None):
        base(self, file_path)
        job = getattr(self, "_current_encode_job", None)
        stream = getattr(job, "stream", None)
        # The codec opens lazily on the first frame, so options set here apply.
        if stream is not None and stream.codec_context.name == "libx264":
            options = dict(stream.codec_context.options or {})
            options["preset"] = preset
            stream.codec_context.options = options

    open_partial_movie_stream._cf_preset = preset  # type: ignore[attr-defined]
    open_partial_movie_stream._cf_original = base  # type: ignore[attr-defined]
    SceneFileWriter.open_partial_movie_stream = open_partial_movie_stream
