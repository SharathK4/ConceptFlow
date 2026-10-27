"""Speech and translation providers.

* ElevenLabs - the premium English narrator. The ``with-timestamps`` endpoint
  returns character-level alignment, which becomes word-exact captions.
* Sarvam AI  - Indian languages: Mayura translates the narration (optionally
  "code-mixed", keeping technical terms in English the way Indian classrooms
  actually speak) and Bulbul voices it.
* Edge       - Microsoft Edge's free neural voices; no key, word timestamps.
  The fallback when no keys are configured.
* Silent     - no audio at all; timings estimated so captions still flow.

Every provider raises ``VoiceUnavailable`` for problems that another
provider could get around (no key, quota, unsupported language) and lets
anything else propagate.
"""

from __future__ import annotations

import base64
import re

import httpx

from .audio import RATE, Speech, Word, decode, estimate_words, from_pcm16, restore_punctuation
from ..schemas import LANGUAGES


class VoiceUnavailable(RuntimeError):
    pass


# ---------------------------------------------------------------- ElevenLabs

_EL_PREFERRED = ("George", "Brian", "Daniel", "Eric", "Bill", "Chris", "Liam", "Will", "Callum", "Roger")


class ElevenLabs:
    name = "elevenlabs"
    base = "https://api.elevenlabs.io/v1"

    def __init__(self, key: str, voice_id: str = "", model: str = "eleven_multilingual_v2") -> None:
        if not key:
            raise VoiceUnavailable("ELEVENLABS_API_KEY is not set")
        self.key = key
        self.voice_id = voice_id
        self.voice_name = voice_id
        self.model = model

    @property
    def headers(self) -> dict:
        return {"xi-api-key": self.key, "Content-Type": "application/json"}

    async def _pick_voice(self, client: httpx.AsyncClient) -> None:
        if self.voice_id:
            return
        r = await client.get(f"{self.base}/voices", headers=self.headers)
        if r.status_code in (401, 403):
            raise VoiceUnavailable(f"ElevenLabs rejected the key ({r.status_code})")
        r.raise_for_status()
        voices = r.json().get("voices", [])
        premade = [v for v in voices if v.get("category") == "premade"] or voices
        if not premade:
            raise VoiceUnavailable("no ElevenLabs voices available to this key")
        by_name = {v.get("name", "").split(" ")[0]: v for v in premade}
        choice = next((by_name[n] for n in _EL_PREFERRED if n in by_name), premade[0])
        self.voice_id, self.voice_name = choice["voice_id"], choice.get("name", choice["voice_id"])

    def cache_key(self) -> str:
        return f"elevenlabs|{self.voice_id or 'auto'}|{self.model}"

    async def speak(self, client: httpx.AsyncClient, text: str, lang: str, prev: str = "", nxt: str = "") -> Speech:
        await self._pick_voice(client)
        body = {
            "text": text,
            "model_id": self.model,
            "voice_settings": {"stability": 0.52, "similarity_boost": 0.8, "style": 0.05, "use_speaker_boost": True},
        }
        if prev:
            body["previous_text"] = prev[-400:]
        if nxt:
            body["next_text"] = nxt[:400]
        url = f"{self.base}/text-to-speech/{self.voice_id}/with-timestamps"
        r = await client.post(url, params={"output_format": "pcm_24000"}, json=body, headers=self.headers, timeout=90)
        if r.status_code in (401, 402, 403, 429) or (r.status_code == 400 and "quota" in r.text.lower()):
            raise VoiceUnavailable(f"ElevenLabs: {r.status_code} {r.text[:200]}")
        r.raise_for_status()
        data = r.json()
        samples = from_pcm16(base64.b64decode(data["audio_base64"]), 24_000)
        words = _words_from_alignment(data.get("alignment") or data.get("normalized_alignment") or {})
        return Speech(samples, words, self.name, self.voice_name)


def _words_from_alignment(alignment: dict) -> list[Word]:
    chars = alignment.get("characters") or []
    starts = alignment.get("character_start_times_seconds") or []
    ends = alignment.get("character_end_times_seconds") or []
    words: list[Word] = []
    current, t0, t1 = "", None, 0.0
    for ch, s, e in zip(chars, starts, ends):
        if ch.isspace():
            if current:
                words.append(Word(current, t0 or 0.0, t1))
            current, t0 = "", None
            continue
        if t0 is None:
            t0 = s
        current += ch
        t1 = e
    if current:
        words.append(Word(current, t0 or 0.0, t1))
    return words


# -------------------------------------------------------------------- Sarvam

_SARVAM_DEFAULT_SPEAKER = {"bulbul:v3": "shubh", "bulbul:v2": "abhilash"}


class Sarvam:
    name = "sarvam"
    base = "https://api.sarvam.ai"

    def __init__(self, key: str, model: str = "bulbul:v3", speaker: str = "", translate_mode: str = "code-mixed") -> None:
        if not key:
            raise VoiceUnavailable("SARVAM_API_KEY is not set")
        self.key = key
        self.model = model
        self.speaker = speaker or _SARVAM_DEFAULT_SPEAKER.get(model, "shubh")
        self.translate_mode = translate_mode

    @property
    def headers(self) -> dict:
        return {"api-subscription-key": self.key, "Content-Type": "application/json"}

    def cache_key(self) -> str:
        return f"sarvam|{self.model}|{self.speaker}"

    async def speak(self, client: httpx.AsyncClient, text: str, lang: str, prev: str = "", nxt: str = "") -> Speech:
        code = LANGUAGES.get(lang, LANGUAGES["en"])[1]
        body = {
            "text": text[:2400],
            "language_code": code,
            "model": self.model,
            "speaker": self.speaker,
            "speech_sample_rate": RATE,
        }
        r = await client.post(f"{self.base}/text-to-speech", json=body, headers=self.headers, timeout=90)
        if r.status_code in (400, 422) and "language_code" in r.text:
            # Older API revisions call the field target_language_code.
            body["target_language_code"] = body.pop("language_code")
            r = await client.post(f"{self.base}/text-to-speech", json=body, headers=self.headers, timeout=90)
        if r.status_code in (401, 402, 403, 429):
            raise VoiceUnavailable(f"Sarvam: {r.status_code} {r.text[:200]}")
        r.raise_for_status()
        audios = r.json().get("audios") or []
        if not audios:
            raise VoiceUnavailable("Sarvam returned no audio")
        samples = decode(base64.b64decode(audios[0]))
        return Speech(samples, estimate_words(text, len(samples) / RATE), self.name, self.speaker)

    async def translate(self, client: httpx.AsyncClient, text: str, lang: str) -> str:
        target = LANGUAGES[lang][1]
        body = {
            "input": text[:1000],
            "source_language_code": "en-IN",
            "target_language_code": target,
            "model": "mayura:v1",
            "mode": self.translate_mode,
            "numerals_format": "international",
        }
        r = await client.post(f"{self.base}/translate", json=body, headers=self.headers, timeout=60)
        if r.status_code in (401, 402, 403, 429):
            raise VoiceUnavailable(f"Sarvam translate: {r.status_code} {r.text[:200]}")
        r.raise_for_status()
        return r.json().get("translated_text", "").strip() or text


# ---------------------------------------------------------------------- Edge

EDGE_VOICES = {
    "en": "en-US-AndrewMultilingualNeural",
    "hi": "hi-IN-MadhurNeural",
    "te": "te-IN-MohanNeural",
    "ta": "ta-IN-ValluvarNeural",
    "kn": "kn-IN-GaganNeural",
    "ml": "ml-IN-MidhunNeural",
    "bn": "bn-IN-BashkarNeural",
    "mr": "mr-IN-ManoharNeural",
    "gu": "gu-IN-NiranjanNeural",
}


class Edge:
    name = "edge"

    def __init__(self) -> None:
        try:
            import edge_tts  # noqa: F401
        except ImportError as exc:  # pragma: no cover
            raise VoiceUnavailable("edge-tts is not installed") from exc

    def cache_key(self) -> str:
        return "edge|v1"

    async def speak(self, client: httpx.AsyncClient, text: str, lang: str, prev: str = "", nxt: str = "") -> Speech:
        import edge_tts

        voice = EDGE_VOICES.get(lang)
        if voice is None:
            raise VoiceUnavailable(f"no free Edge voice for {LANGUAGES[lang][0]}")
        audio = bytearray()
        words: list[Word] = []
        try:
            stream = edge_tts.Communicate(text, voice, rate="-4%", boundary="WordBoundary").stream()
            async for chunk in stream:
                if chunk["type"] == "audio":
                    audio += chunk["data"]
                elif chunk["type"] == "WordBoundary":
                    start = chunk["offset"] / 1e7
                    words.append(Word(chunk["text"], start, start + chunk["duration"] / 1e7))
        except Exception as exc:  # network, service change
            raise VoiceUnavailable(f"Edge voice failed: {exc}") from exc
        if not audio:
            raise VoiceUnavailable("Edge voice returned no audio")
        return Speech(decode(bytes(audio)), restore_punctuation(text, words), self.name, voice)


# -------------------------------------------------------------------- Silent


class Silent:
    name = "silent"

    def cache_key(self) -> str:
        return "silent"

    async def speak(self, client: httpx.AsyncClient, text: str, lang: str, prev: str = "", nxt: str = "") -> Speech:
        import numpy as np

        words = len(re.findall(r"\S+", text))
        duration = max(2.0, words / 2.5)
        return Speech(np.zeros(int(duration * RATE), np.float32), estimate_words(text, duration), self.name, "")
