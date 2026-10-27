"""Voice providers against mocked HTTP, and the narrator's choice of voice."""

import asyncio
import base64
import io
import json
import wave
from dataclasses import replace

import httpx
import numpy as np

from conceptflow.config import get_settings
from conceptflow.schemas import LessonRequest
from conceptflow.voice.audio import Word, estimate_words, restore_punctuation
from conceptflow.voice.narrator import Narrator
from conceptflow.voice.providers import ElevenLabs, Sarvam, VoiceUnavailable


def pcm(seconds: float, rate: int = 24_000) -> bytes:
    t = np.linspace(0, seconds, int(seconds * rate), endpoint=False)
    return (np.sin(2 * np.pi * 220 * t) * 12000).astype("<i2").tobytes()


def wav_b64(seconds: float) -> str:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(24_000)
        w.writeframes(pcm(seconds))
    return base64.b64encode(buffer.getvalue()).decode()


def test_elevenlabs_request_and_word_alignment():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/voices"):
            return httpx.Response(200, json={"voices": [{"voice_id": "v1", "name": "George", "category": "premade"}]})
        seen["url"] = str(request.url)
        seen["body"] = json.loads(request.content)
        seen["key"] = request.headers["xi-api-key"]
        text = "Hi, there."
        return httpx.Response(
            200,
            json={
                "audio_base64": base64.b64encode(pcm(1.0)).decode(),
                "alignment": {
                    "characters": list(text),
                    "character_start_times_seconds": [0.1 * i for i in range(len(text))],
                    "character_end_times_seconds": [0.1 * i + 0.08 for i in range(len(text))],
                },
            },
        )

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await ElevenLabs("secret").speak(client, "Hi, there.", "en", prev="Before.", nxt="After.")

    speech = asyncio.run(run())
    assert "/text-to-speech/v1/with-timestamps" in seen["url"] and "output_format=pcm_24000" in seen["url"]
    assert seen["key"] == "secret" and seen["body"]["previous_text"] == "Before." and seen["body"]["next_text"] == "After."
    assert [w.text for w in speech.words] == ["Hi,", "there."]
    assert abs(speech.duration - 1.0) < 0.01


def test_elevenlabs_out_of_credits_falls_back():
    def handler(request):
        return httpx.Response(401, json={"detail": {"status": "quota_exceeded"}})

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            await ElevenLabs("k", voice_id="v1").speak(client, "x", "en")

    try:
        asyncio.run(run())
        raise AssertionError("expected VoiceUnavailable")
    except VoiceUnavailable:
        pass


def test_sarvam_tts_and_translate_requests():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        seen.append((request.url.path, body, request.headers["api-subscription-key"]))
        if request.url.path == "/translate":
            return httpx.Response(200, json={"translated_text": "नमस्ते दुनिया"})
        return httpx.Response(200, json={"audios": [wav_b64(0.8)]})

    async def run():
        sarvam = Sarvam("key", model="bulbul:v3", translate_mode="code-mixed")
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            hindi = await sarvam.translate(client, "Hello world", "hi")
            speech = await sarvam.speak(client, hindi, "hi")
        return hindi, speech

    hindi, speech = asyncio.run(run())
    assert hindi == "नमस्ते दुनिया"
    translate, tts = seen
    assert translate[1]["target_language_code"] == "hi-IN" and translate[1]["mode"] == "code-mixed"
    assert tts[1]["language_code"] == "hi-IN" and tts[1]["model"] == "bulbul:v3" and tts[1]["speaker"] == "shubh"
    assert abs(speech.duration - 0.8) < 0.02 and [w.text for w in speech.words] == ["नमस्ते", "दुनिया"]


def test_sarvam_falls_back_to_the_older_field_name():
    bodies = []

    def handler(request):
        body = json.loads(request.content)
        bodies.append(body)
        if "language_code" in body:
            return httpx.Response(422, json={"error": {"message": "target_language_code: field required (language_code not permitted)"}})
        return httpx.Response(200, json={"audios": [wav_b64(0.5)]})

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await Sarvam("k").speak(client, "hello", "en")

    asyncio.run(run())
    assert "target_language_code" in bodies[-1]


def test_restore_punctuation_and_estimates():
    text = "So the derivative is a new function: at every point, it gives the slope."
    bare = [Word(t.strip(":,."), i, i + 0.5) for i, t in enumerate(text.split())]
    assert " ".join(w.text for w in restore_punctuation(text, bare)) == text
    words = estimate_words("one two three", 3.0)
    assert words[0].start < words[1].start < words[2].start and words[-1].end <= 3.0


def test_narrator_voice_chain_follows_keys_and_language(tmp_path):
    base = replace(get_settings(), elevenlabs_key="", sarvam_key="", voice_fallback="edge", data_dir=tmp_path)
    (tmp_path / "cache").mkdir()
    names = lambda s, **kw: [p.name for p in Narrator(s, tmp_path, lambda e: None).chain(LessonRequest(prompt="x", **kw))]
    assert names(base) == ["edge", "silent"]
    both = replace(base, elevenlabs_key="e", sarvam_key="s")
    assert names(both) == ["elevenlabs", "sarvam", "edge", "silent"]
    assert names(both, language="te") == ["sarvam", "edge", "silent"]  # multilingual v2 has no Telugu
    assert names(both, language="hi") == ["sarvam", "elevenlabs", "edge", "silent"]
    assert names(both, voice="silent") == ["silent"]
