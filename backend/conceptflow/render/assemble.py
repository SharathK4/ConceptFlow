"""The Editor's machinery: one finished lesson from many rendered scenes.

* the scene videos are joined without re-encoding (their packets are copied
  and re-timed), so assembly takes seconds rather than minutes;
* every line of narration is laid onto one audio track at the moment its beat
  actually started on screen - read from the timeline the scene wrote while
  rendering, not predicted - so voice and picture cannot drift;
* captions come from the voice provider's word timings, and chapters from the
  scene boundaries.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path

import av
import numpy as np

from ..voice.audio import RATE, read_wav, resample


@dataclass
class Clip:
    scene: str
    title: str
    video: Path
    timeline: dict


def _probe(path: Path) -> tuple[int, int, int, Fraction]:
    """(first_dts, first_pts, end_pts, time_base) of the video stream, in ticks."""
    with av.open(str(path)) as container:
        stream = container.streams.video[0]
        first_dts = first_pts = None
        end = 0
        for packet in container.demux(stream):
            if packet.dts is None:
                continue
            if first_dts is None:
                first_dts = packet.dts
            if packet.pts is not None:
                first_pts = packet.pts if first_pts is None else min(first_pts, packet.pts)
                end = max(end, packet.pts + (packet.duration or 0))
        return first_dts or 0, first_pts or 0, end, stream.time_base


def _plan_offsets(videos: list[Path]) -> tuple[list[int], list[float], float]:
    """Shift (in each file's ticks) so dts stays monotonic; start time of each file in seconds."""
    shifts, starts = [], []
    cursor = 0.0  # seconds: where the next file should start
    last_dts_sec = -1.0
    for path in videos:
        first_dts, first_pts, end, tb = _probe(path)
        shift = int(round(cursor / tb)) - first_pts
        if (first_dts + shift) * tb <= last_dts_sec:
            shift = int(np.ceil(last_dts_sec / tb)) + 1 - first_dts
        shifts.append(shift)
        starts.append(float((first_pts + shift) * tb))
        cursor = float((end + shift) * tb)
        last_dts_sec = float((end + shift) * tb) - float(tb)
    return shifts, starts, cursor


def build_audio(clips: list[Clip], starts: list[float], total: float, narration: dict) -> np.ndarray:
    track = np.zeros(int(total * RATE) + RATE, dtype=np.float32)
    for clip, start in zip(clips, starts):
        for beat in clip.timeline.get("beats", []):
            info = narration.get(beat["id"])
            if not info or not info.get("audio") or not Path(info["audio"]).exists():
                continue
            samples = read_wav(Path(info["audio"]))
            at = int((start + float(beat["start"])) * RATE)
            end = min(len(track), at + len(samples))
            if end > at:
                track[at:end] += samples[: end - at]
    peak = float(np.max(np.abs(track))) if len(track) else 0.0
    if peak > 0.98:
        track *= 0.98 / peak
    return track[: int(total * RATE)]


def _mux(videos: list[Path], shifts: list[int], audio: np.ndarray | None, out: Path) -> None:
    with av.open(str(videos[0])) as first:
        template = first.streams.video[0]
        output = av.open(str(out), mode="w", options={"movflags": "+faststart"})
        video_out = output.add_stream_from_template(template)

    audio_packets: list = []
    if audio is not None and len(audio):
        audio_out = output.add_stream("aac", rate=48_000, layout="mono")
        audio_out.bit_rate = 128_000
        samples = resample(audio, RATE, 48_000).astype(np.float32)
        step = 48_000  # one second per frame; the encoder re-chunks internally
        for i in range(0, len(samples), step):
            chunk = samples[i : i + step].reshape(1, -1)
            frame = av.AudioFrame.from_ndarray(chunk, format="flt", layout="mono")
            frame.sample_rate = 48_000
            frame.pts = i
            frame.time_base = Fraction(1, 48_000)
            audio_packets.extend(audio_out.encode(frame))
        audio_packets.extend(audio_out.encode(None))

    queue = [p for p in audio_packets if p.dts is not None]
    qi = 0
    try:
        for path, shift in zip(videos, shifts):
            with av.open(str(path)) as source:
                stream = source.streams.video[0]
                for packet in source.demux(stream):
                    if packet.dts is None:
                        continue
                    packet.dts += shift
                    if packet.pts is not None:
                        packet.pts += shift
                    now = float(packet.dts * packet.time_base)
                    while qi < len(queue) and float(queue[qi].dts * queue[qi].time_base) <= now:
                        output.mux(queue[qi])
                        qi += 1
                    packet.stream = video_out
                    output.mux(packet)
        while qi < len(queue):
            output.mux(queue[qi])
            qi += 1
    finally:
        output.close()


# ------------------------------------------------------------------ captions

_SENTENCE_END = re.compile(r"[.!?;:]$|[।॥]$")


def build_captions(clips: list[Clip], starts: list[float], narration: dict) -> list[dict]:
    cues: list[dict] = []
    for clip, start in zip(clips, starts):
        for beat in clip.timeline.get("beats", []):
            info = narration.get(beat["id"])
            if not info:
                continue
            base = start + float(beat["start"])
            words = info.get("words") or []
            if not words:
                cues.append({"start": base, "end": base + float(info.get("speech", 2)), "text": info.get("text", ""), "words": []})
                continue
            chunk: list = []
            for w in words:
                chunk.append(w)
                if len(chunk) >= 10 or (len(chunk) >= 4 and _SENTENCE_END.search(w[0])):
                    cues.append(_cue(chunk, base))
                    chunk = []
            if chunk:
                cues.append(_cue(chunk, base))
    # Never let a cue overlap the next one.
    for a, b in zip(cues, cues[1:]):
        a["end"] = min(a["end"], b["start"] - 0.02)
    return cues


def _cue(words: list, base: float) -> dict:
    start = base + float(words[0][1])
    end = base + float(words[-1][2]) + 0.25
    return {
        "start": round(start, 3),
        "end": round(max(end, start + 0.8), 3),
        "text": " ".join(w[0] for w in words),
        "words": [[w[0], round(base + float(w[1]), 3), round(base + float(w[2]), 3)] for w in words],
    }


def _vtt_time(t: float) -> str:
    h, rem = divmod(max(0.0, t), 3600)
    m, s = divmod(rem, 60)
    return f"{int(h):02d}:{int(m):02d}:{s:06.3f}"


def write_vtt(cues: list[dict], path: Path) -> None:
    lines = ["WEBVTT", ""]
    for i, cue in enumerate(cues, 1):
        lines += [str(i), f"{_vtt_time(cue['start'])} --> {_vtt_time(cue['end'])}", cue["text"], ""]
    path.write_text("\n".join(lines), encoding="utf-8")


def grab_frame(video: Path, at: float, out: Path, width: int = 1280) -> bool:
    try:
        with av.open(str(video)) as container:
            stream = container.streams.video[0]
            container.seek(int(max(0.0, at - 1.0) / stream.time_base), stream=stream, backward=True)
            chosen = None
            for frame in container.decode(stream):
                chosen = frame
                if frame.time is not None and frame.time >= at:
                    break
            if chosen is None:
                return False
            image = chosen.to_image()
            if image.width > width:
                image = image.resize((width, int(image.height * width / image.width)))
            image.save(out, quality=90)
            return True
    except (av.error.FFmpegError, OSError, ValueError):
        return False


# --------------------------------------------------------------------- cut


def assemble(clips: list[Clip], narration: dict, out_dir: Path) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    videos = [c.video for c in clips]
    shifts, starts, total = _plan_offsets(videos)
    audio = build_audio(clips, starts, total, narration) if narration else None
    film = out_dir / "lesson.mp4"
    _mux(videos, shifts, audio, film)

    cues = build_captions(clips, starts, narration)
    write_vtt(cues, out_dir / "captions.vtt")
    (out_dir / "captions.json").write_text(json.dumps(cues, ensure_ascii=False), encoding="utf-8")

    chapters = []
    for i, (clip, start) in enumerate(zip(clips, starts)):
        end = starts[i + 1] if i + 1 < len(starts) else total
        chapters.append({"scene": clip.scene, "title": clip.title, "start": round(start, 3), "end": round(end, 3)})

    poster = out_dir / "poster.jpg"
    first_beats = clips[0].timeline.get("beats", []) if clips else []
    moment = float(first_beats[0]["end"]) - 0.25 if first_beats else 1.0
    grab_frame(film, max(0.5, moment), poster)
    for chapter in chapters:
        thumb = out_dir / f"chapter_{chapter['scene']}.jpg"
        mid = chapter["start"] + 0.72 * (chapter["end"] - chapter["start"])
        if grab_frame(film, mid, thumb, width=480):
            chapter["thumb"] = thumb.name
    return {"video": film.name, "duration": round(total, 3), "captions": "captions.vtt", "captions_json": "captions.json", "chapters": chapters, "poster": poster.name if poster.exists() else None}
