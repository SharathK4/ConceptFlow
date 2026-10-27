"""Small audio toolkit: decode anything, write WAV, trim, mix.

Everything is mono float32 at 24 kHz internally. PyAV (already a Manim
dependency) bundles FFmpeg's decoders, so MP3 from edge-tts, WAV from Sarvam
and raw PCM from ElevenLabs all come in through one path without a system
FFmpeg install.
"""

from __future__ import annotations

import io
import wave
from dataclasses import dataclass, field
from pathlib import Path

import av
import numpy as np

RATE = 24_000


@dataclass
class Word:
    text: str
    start: float
    end: float


@dataclass
class Speech:
    samples: np.ndarray
    words: list[Word] = field(default_factory=list)
    provider: str = ""
    voice: str = ""

    @property
    def duration(self) -> float:
        return len(self.samples) / RATE


def decode(data: bytes) -> np.ndarray:
    """Decode an encoded audio file (mp3, wav, ogg...) to mono float32 @ RATE."""
    resampler = av.AudioResampler(format="flt", layout="mono", rate=RATE)
    chunks: list[np.ndarray] = []
    with av.open(io.BytesIO(data)) as container:
        for frame in container.decode(audio=0):
            for out in resampler.resample(frame):
                chunks.append(out.to_ndarray().reshape(-1))
    for out in resampler.resample(None):
        chunks.append(out.to_ndarray().reshape(-1))
    return np.concatenate(chunks).astype(np.float32) if chunks else np.zeros(0, np.float32)


def from_pcm16(data: bytes, rate: int) -> np.ndarray:
    samples = np.frombuffer(data, dtype="<i2").astype(np.float32) / 32768.0
    if rate != RATE:
        samples = resample(samples, rate, RATE)
    return samples


def resample(samples: np.ndarray, src: int, dst: int) -> np.ndarray:
    if src == dst or len(samples) == 0:
        return samples
    n = int(round(len(samples) * dst / src))
    x_old = np.linspace(0.0, 1.0, num=len(samples), endpoint=False)
    x_new = np.linspace(0.0, 1.0, num=n, endpoint=False)
    return np.interp(x_new, x_old, samples).astype(np.float32)


def trim(speech: Speech, threshold: float = 0.012, keep: float = 0.06) -> Speech:
    """Cut leading/trailing silence, keeping a few ms; shift word times to match."""
    x = speech.samples
    if len(x) == 0:
        return speech
    loud = np.flatnonzero(np.abs(x) > threshold)
    if len(loud) == 0:
        return speech
    pad = int(keep * RATE)
    start = max(0, int(loud[0]) - pad)
    end = min(len(x), int(loud[-1]) + pad)
    offset = start / RATE
    words = [Word(w.text, max(0.0, w.start - offset), max(0.0, w.end - offset)) for w in speech.words]
    return Speech(x[start:end].copy(), words, speech.provider, speech.voice)


def write_wav(path: Path, samples: np.ndarray, rate: int = RATE) -> None:
    pcm = (np.clip(samples, -1.0, 1.0) * 32767.0).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm.tobytes())


def read_wav(path: Path) -> np.ndarray:
    with wave.open(str(path), "rb") as w:
        rate = w.getframerate()
        data = w.readframes(w.getnframes())
        channels = w.getnchannels()
    samples = np.frombuffer(data, dtype="<i2").astype(np.float32) / 32768.0
    if channels > 1:
        samples = samples.reshape(-1, channels).mean(axis=1)
    return resample(samples, rate, RATE)


def _bare(token: str) -> str:
    return "".join(ch for ch in token.lower() if ch.isalnum())


def restore_punctuation(text: str, words: list[Word]) -> list[Word]:
    """Give timed words back the punctuation of the sentence they came from.

    Some voices report word boundaries as bare words ("derivative" rather
    than "derivative:"). Walking the original text alongside the timed words
    recovers each word's written form, so captions read like the script.
    """
    tokens = text.split()
    out: list[Word] = []
    i = 0
    for w in words:
        target = _bare(w.text)
        j = i
        while j < len(tokens) and _bare(tokens[j]) != target and j < i + 3:
            j += 1
        if j < len(tokens) and _bare(tokens[j]) == target:
            # Words the voice skipped (a lone dash, say) ride along with the previous one.
            if out and j > i:
                prev = out[-1]
                out[-1] = Word(" ".join([prev.text, *tokens[i:j]]), prev.start, prev.end)
            out.append(Word(tokens[j], w.start, w.end))
            i = j + 1
        else:
            out.append(w)
    return out


def estimate_words(text: str, duration: float, lead: float = 0.05) -> list[Word]:
    """Spread words over a clip in proportion to their length (no timestamps)."""
    tokens = text.split()
    if not tokens or duration <= 0:
        return []
    weights = np.array([len(t) + 2 for t in tokens], dtype=float)
    span = max(0.1, duration - 2 * lead)
    edges = lead + np.concatenate([[0.0], np.cumsum(weights) / weights.sum() * span])
    return [Word(t, float(edges[i]), float(edges[i + 1])) for i, t in enumerate(tokens)]


def loudness_normalise(samples: np.ndarray, target_rms: float = 0.075) -> np.ndarray:
    voiced = samples[np.abs(samples) > 0.01]
    if len(voiced) == 0:
        return samples
    rms = float(np.sqrt(np.mean(voiced**2)))
    gain = min(4.0, target_rms / max(rms, 1e-6))
    out = samples * gain
    peak = float(np.max(np.abs(out))) if len(out) else 0.0
    return out / peak * 0.97 if peak > 0.97 else out
