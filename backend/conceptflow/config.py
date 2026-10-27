"""Settings, read once from the environment (and ``.env`` at the project root)."""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
load_dotenv(ROOT / ".env", override=False)

# CrewAI's own usage telemetry and hosted tracing stay off; the studio's
# OpenTelemetry tracing (observability.py) is separate and stays on.
os.environ.setdefault("CREWAI_DISABLE_TELEMETRY", "true")
os.environ.setdefault("CREWAI_TRACING_ENABLED", "false")

ROLES = (
    "planner",
    "coder",
    "fixer",
    "visual_critic",
    "pedagogy_critic",
    "reader",
)

_DEFAULT_LADDERS = {
    "planner": "gemini-3.5-flash-lite,gemini-3.1-flash-lite,gemini-2.5-flash-lite",
    "coder": "gemini-3.8-flash,gemini-3.6-flash,gemini-3-flash-preview,gemini-2.5-flash",
    "fixer": "gemini-3.6-flash,gemini-3.8-flash,gemini-3-flash-preview,gemini-2.5-flash",
    "visual_critic": "gemini-3-flash-preview,gemini-3.6-flash,gemini-3.5-flash-lite",
    "pedagogy_critic": "gemini-3.5-flash-lite,gemini-3.1-flash-lite,gemini-3.6-flash",
    "reader": "gemini-3.5-flash-lite,gemini-3.1-flash-lite,gemini-3.6-flash",
}


def _env(name: str, default: str = "") -> str:
    return (os.environ.get(name) or default).strip()


def _int(name: str, default: int) -> int:
    try:
        return int(_env(name, str(default)))
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    google_api_key: str
    llm_mode: str  # "live" | "replay"
    ladders: dict[str, list[str]]

    elevenlabs_key: str
    elevenlabs_voice: str
    elevenlabs_model: str
    sarvam_key: str
    sarvam_tts_model: str
    sarvam_speaker: str
    sarvam_translate_mode: str
    voice_fallback: str

    default_quality: str
    max_revisions: int
    render_workers: int
    render_timeout: int

    host: str
    port: int

    # Observability (optional): Langfuse over OpenTelemetry.
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""
    langfuse_host: str = "https://cloud.langfuse.com"

    # Where the studio is reachable. The A2A agent card and the MCP endpoint
    # advertise this address; behind a proxy or on Cloud Run set PUBLIC_URL.
    public_url: str = ""
    # Let LLM agents call the MCP tools themselves (live mode only).
    llm_tools: bool = True

    data_dir: Path = field(default=ROOT / "data")

    @property
    def base_url(self) -> str:
        return (self.public_url or f"http://127.0.0.1:{self.port}").rstrip("/")

    @property
    def local_url(self) -> str:
        # In-process calls (A2A client, MCP toolsets) always use loopback.
        return f"http://127.0.0.1:{self.port}"

    @property
    def lessons_dir(self) -> Path:
        return self.data_dir / "lessons"

    @property
    def cache_dir(self) -> Path:
        return self.data_dir / "cache"

    @property
    def has_latex(self) -> bool:
        return shutil.which("latex") is not None and shutil.which("dvisvgm") is not None

    def providers(self) -> dict[str, bool]:
        return {
            "gemini": bool(self.google_api_key),
            "elevenlabs": bool(self.elevenlabs_key),
            "sarvam": bool(self.sarvam_key),
            "latex": self.has_latex,
            "replay": self.llm_mode == "replay",
            "langfuse": bool(self.langfuse_public_key and self.langfuse_secret_key),
        }


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    key = _env("GOOGLE_API_KEY") or _env("GEMINI_API_KEY")
    mode = _env("LLM_MODE", "auto").lower()
    if mode not in ("live", "replay"):
        mode = "live" if key else "replay"

    ladders = {}
    for role in ROLES:
        raw = _env(f"MODELS_{role.upper()}", _DEFAULT_LADDERS[role])
        ladders[role] = [m.strip() for m in raw.split(",") if m.strip()]

    workers = _int("RENDER_WORKERS", 0)
    if workers <= 0:
        workers = max(2, min(6, (os.cpu_count() or 4) // 4))

    settings = Settings(
        google_api_key=key,
        llm_mode=mode,
        ladders=ladders,
        elevenlabs_key=_env("ELEVENLABS_API_KEY"),
        elevenlabs_voice=_env("ELEVENLABS_VOICE_ID"),
        elevenlabs_model=_env("ELEVENLABS_MODEL", "eleven_multilingual_v2"),
        sarvam_key=_env("SARVAM_API_KEY"),
        sarvam_tts_model=_env("SARVAM_TTS_MODEL", "bulbul:v3"),
        sarvam_speaker=_env("SARVAM_SPEAKER"),
        sarvam_translate_mode=_env("SARVAM_TRANSLATE_MODE", "code-mixed"),
        voice_fallback=_env("VOICE_FALLBACK", "edge").lower(),
        default_quality=_env("DEFAULT_QUALITY", "hd"),
        max_revisions=max(0, _int("MAX_REVISIONS", 3)),
        render_workers=workers,
        render_timeout=max(60, _int("RENDER_TIMEOUT", 240)),
        host=_env("HOST", "127.0.0.1"),
        port=_int("PORT", 8010),
        langfuse_public_key=_env("LANGFUSE_PUBLIC_KEY"),
        langfuse_secret_key=_env("LANGFUSE_SECRET_KEY"),
        langfuse_host=_env("LANGFUSE_HOST") or _env("LANGFUSE_BASE_URL", "https://cloud.langfuse.com"),
        public_url=_env("PUBLIC_URL"),
        llm_tools=_env("LLM_TOOLS", "true").lower() not in ("0", "false", "no"),
        data_dir=Path(_env("DATA_DIR")) if _env("DATA_DIR") else ROOT / "data",
    )
    if key:
        # ADK's Gemini model reads the key from the environment.
        os.environ.setdefault("GOOGLE_API_KEY", key)
        os.environ.setdefault("GOOGLE_GENAI_USE_VERTEXAI", "false")
    settings.lessons_dir.mkdir(parents=True, exist_ok=True)
    settings.cache_dir.mkdir(parents=True, exist_ok=True)
    return settings
