"""The Narrator: turns the storyboard's lines into timed speech.

Runs in parallel with the Manim coder. The coder only needs to know *that*
each beat exists; the narrator decides *how long* each beat lasts. The two
meet at render time through ``timing.json``, which the scene base class reads
so every beat lasts exactly as long as its line of speech.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import httpx
from pydantic import BaseModel

from ..config import Settings
from ..llm import replay
from ..llm.router import QuotaExhausted, get_router
from ..schemas import LANGUAGES, LessonRequest, Storyboard
from .audio import Speech, Word, loudness_normalise, read_wav, restore_punctuation, trim, write_wav
from .providers import Edge, ElevenLabs, Sarvam, Silent, VoiceUnavailable

BREATH = 0.38  # seconds of air after each line
MIN_BEAT = 1.6
ELEVEN_LANGS = {"en", "hi", "ta"}  # languages eleven_multilingual_v2 speaks


class _Lines(BaseModel):
    lines: list[str]


@dataclass
class NarrationResult:
    provider: str
    voice: str
    language: str
    translated: bool
    beats: dict[str, dict]

    def as_state(self) -> dict:
        return {
            "provider": self.provider,
            "voice": self.voice,
            "language": self.language,
            "translated": self.translated,
            "beats": self.beats,
        }


class Narrator:
    def __init__(self, settings: Settings, job_dir: Path, emit: Callable[[dict], None]) -> None:
        self.settings = settings
        self.dir = job_dir
        self.emit = emit
        self.cache = settings.cache_dir / "tts"
        self.cache.mkdir(parents=True, exist_ok=True)
        (job_dir / "audio").mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------ choose

    def chain(self, request: LessonRequest) -> list:
        s, lang = self.settings, request.language
        makers: dict[str, Callable[[], object]] = {
            "elevenlabs": lambda: ElevenLabs(s.elevenlabs_key, s.elevenlabs_voice, s.elevenlabs_model),
            "sarvam": lambda: Sarvam(s.sarvam_key, s.sarvam_tts_model, s.sarvam_speaker, s.sarvam_translate_mode),
            "edge": Edge,
            "silent": Silent,
        }
        if request.voice == "silent":
            return [Silent()]  # asked for captions only: never fall back to a voice
        if request.voice != "auto":
            order = [request.voice]
        elif lang == "en":
            order = ["elevenlabs", "sarvam"]
        else:
            order = ["sarvam"] + (["elevenlabs"] if lang in ELEVEN_LANGS else [])
        if s.voice_fallback == "edge":
            order.append("edge")
        order.append("silent")

        providers, seen = [], set()
        for name in order:
            if name in seen:
                continue
            seen.add(name)
            try:
                providers.append(makers[name]())
            except VoiceUnavailable:
                continue
        return providers

    # ---------------------------------------------------------- translate

    async def translate(self, client: httpx.AsyncClient, lines: list[str], lang: str) -> tuple[list[str], bool]:
        if lang == "en":
            return lines, False
        name = LANGUAGES[lang][0]
        if self.settings.sarvam_key:
            self.emit({"type": "log", "agent": "narrator", "text": f"Translating the narration into {name} with Sarvam Mayura ({self.settings.sarvam_translate_mode})."})
            sarvam = Sarvam(self.settings.sarvam_key, translate_mode=self.settings.sarvam_translate_mode)
            sem = asyncio.Semaphore(3)

            async def one(line: str) -> str:
                async with sem:
                    return await sarvam.translate(client, line, lang)

            try:
                return list(await asyncio.gather(*[one(line) for line in lines])), True
            except (VoiceUnavailable, httpx.HTTPError) as exc:
                self.emit({"type": "log", "agent": "narrator", "text": f"Sarvam translation unavailable ({exc}); asking Gemini instead."})
        if replay.current.get() is None and self.settings.google_api_key:
            self.emit({"type": "log", "agent": "narrator", "text": f"Translating the narration into {name} with Gemini."})
            prompt = (
                f"Translate each line of this lesson narration into natural spoken {name}. "
                "Keep mathematical terms that students usually say in English (derivative, matrix, vector...) in English. "
                "Return exactly one output line per input line, in order.\n\n"
                + json.dumps(lines, ensure_ascii=False)
            )
            try:
                text, _ = await get_router().generate("planner", prompt, schema=_Lines, framework="adk")
                out = _Lines.model_validate_json(text).lines
                if len(out) == len(lines):
                    return out, True
            except (QuotaExhausted, ValueError) as exc:
                self.emit({"type": "log", "agent": "narrator", "text": f"Translation failed ({exc}); narrating in English."})
        else:
            self.emit({"type": "log", "agent": "narrator", "text": f"No translator available; narrating in English instead of {name}."})
        return lines, False

    # ------------------------------------------------------------- speak

    async def narrate(self, storyboard: Storyboard, request: LessonRequest) -> NarrationResult:
        beats = storyboard.beats()
        async with httpx.AsyncClient(timeout=90) as client:
            spoken, translated = await self.translate(client, [b.narration for b in beats], request.language)
            language = request.language if translated or request.language == "en" else "en"
            for provider in self.chain(request):
                try:
                    return await self._with(provider, client, beats, spoken, language, translated)
                except VoiceUnavailable as exc:
                    self.emit({"type": "log", "agent": "narrator", "text": f"{provider.name} unavailable: {exc}. Trying the next voice."})
        raise RuntimeError("no voice provider could narrate this lesson")

    async def _with(self, provider, client, beats, spoken, language, translated) -> NarrationResult:
        self.emit({"type": "log", "agent": "narrator", "text": f"Voicing {len(beats)} lines with {provider.name}."})
        sem = asyncio.Semaphore(2 if provider.name == "elevenlabs" else 3)
        done = 0
        results: dict[str, dict] = {}

        async def one(i: int) -> None:
            nonlocal done
            beat, text = beats[i], spoken[i]
            prev = spoken[i - 1] if i > 0 else ""
            nxt = spoken[i + 1] if i + 1 < len(spoken) else ""
            async with sem:
                speech = await self._cached(provider, client, text, language, prev, nxt)
            path = self.dir / "audio" / f"{beat.id}.wav"
            write_wav(path, speech.samples)
            duration = max(MIN_BEAT, speech.duration + BREATH)
            results[beat.id] = {
                "duration": round(duration, 3),
                "speech": round(speech.duration, 3),
                "audio": str(path),
                "text": text,
                "words": [[w.text, round(w.start, 3), round(w.end, 3)] for w in speech.words],
            }
            done += 1
            self.emit({"type": "progress", "agent": "narrator", "done": done, "total": len(beats), "text": f"Voiced line {done} of {len(beats)}"})

        await asyncio.gather(*[one(i) for i in range(len(beats))])
        timing = {"beats": {k: {"duration": v["duration"], "text": v["text"]} for k, v in results.items()}}
        (self.dir / "timing.json").write_text(json.dumps(timing, indent=2, ensure_ascii=False), encoding="utf-8")
        voice = getattr(provider, "voice_name", "") or getattr(provider, "speaker", "") or ""
        if provider.name == "edge":
            from .providers import EDGE_VOICES

            voice = EDGE_VOICES.get(language, "")
        total = sum(v["duration"] for v in results.values())
        self.emit({"type": "log", "agent": "narrator", "text": f"Narration ready: {total:.0f}s of speech ({provider.name})."})
        return NarrationResult(provider.name, voice, language, translated, results)

    async def _cached(self, provider, client, text, language, prev, nxt) -> Speech:
        key = hashlib.sha1(f"{provider.cache_key()}|{language}|{text}".encode("utf-8")).hexdigest()[:20]
        wav, meta = self.cache / f"{key}.wav", self.cache / f"{key}.json"
        if wav.exists() and meta.exists():
            words = [Word(*w) for w in json.loads(meta.read_text(encoding="utf-8"))["words"]]
            return Speech(read_wav(wav), restore_punctuation(text, words), provider.name)
        speech = await provider.speak(client, text, language, prev, nxt)
        if provider.name != "silent":
            speech = trim(speech)
            speech = Speech(loudness_normalise(speech.samples), speech.words, speech.provider, speech.voice)
        write_wav(wav, speech.samples)
        meta.write_text(json.dumps({"words": [[w.text, w.start, w.end] for w in speech.words]}), encoding="utf-8")
        return speech


def silent_duration(text: str) -> float:
    return max(MIN_BEAT, len(text.split()) / 2.5 + BREATH)

