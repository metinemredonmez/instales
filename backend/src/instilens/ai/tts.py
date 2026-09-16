"""Text-to-speech for AI notes. ElevenLabs when a key is set (best Turkish); OpenAI TTS as an alternative;
otherwise the frontend falls back to the browser's own voice. Audio is cached per note on disk."""

from __future__ import annotations

import hashlib
from pathlib import Path

import httpx

from instilens.config import BACKEND_ROOT, settings

CACHE = BACKEND_ROOT / "media" / "tts"


def _path(text: str, provider: str) -> Path:
    CACHE.mkdir(parents=True, exist_ok=True)
    return CACHE / f"{provider}-{hashlib.sha1(text.encode()).hexdigest()[:20]}.mp3"


def provider() -> str | None:
    if settings.elevenlabs_api_key:
        return "elevenlabs"
    if settings.openai_api_key:
        return "openai"
    return None


def synthesize(text: str) -> Path | None:
    p = provider()
    if p is None:
        return None
    out = _path(text, p)
    if out.exists():
        return out
    if p == "elevenlabs":
        r = httpx.post(
            f"https://api.elevenlabs.io/v1/text-to-speech/{settings.elevenlabs_voice_id}",
            headers={"xi-api-key": settings.elevenlabs_api_key, "accept": "audio/mpeg"},
            json={"text": text, "model_id": "eleven_multilingual_v2", "voice_settings": {"stability": 0.5, "similarity_boost": 0.75}},
            timeout=120,
        )
    else:
        r = httpx.post(
            "https://api.openai.com/v1/audio/speech",
            headers={"Authorization": f"Bearer {settings.openai_api_key}"},
            json={"model": "gpt-4o-mini-tts", "voice": settings.openai_tts_voice, "input": text, "response_format": "mp3"},
            timeout=120,
        )
    r.raise_for_status()
    out.write_bytes(r.content)
    return out
