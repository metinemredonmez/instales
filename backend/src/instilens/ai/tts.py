"""Text-to-speech for AI notes. ElevenLabs when a key is set (best Turkish); OpenAI TTS as an alternative;
otherwise the frontend falls back to the browser's own voice. Audio is cached per note on disk."""

from __future__ import annotations

import hashlib
from pathlib import Path

import httpx

from instilens.config import BACKEND_ROOT, settings

CACHE = BACKEND_ROOT / "media" / "tts"


def _path(text: str, provider: str, voice: str) -> Path:
    CACHE.mkdir(parents=True, exist_ok=True)
    return CACHE / f"{provider}-{voice[:12]}-{hashlib.sha1(text.encode()).hexdigest()[:20]}.mp3"


def pick_voice(lang: str = "tr", gender: str = "female") -> str:
    lang, gender = ("en" if lang == "en" else "tr"), ("male" if gender == "male" else "female")
    chosen = getattr(settings, f"elevenlabs_voice_{lang}_{gender}", "") or getattr(settings, f"elevenlabs_voice_en_{gender}", "")
    return chosen or settings.elevenlabs_voice_id


OPENAI_VOICES = {"female": "nova", "male": "onyx"}


def provider() -> str | None:
    if settings.elevenlabs_api_key:
        return "elevenlabs"
    if settings.openai_api_key:
        return "openai"
    return None


def synthesize(text: str, lang: str = "tr", gender: str = "female") -> Path | None:
    p = provider()
    if p is None:
        return None
    voice = pick_voice(lang, gender) if p == "elevenlabs" else OPENAI_VOICES.get(gender, settings.openai_tts_voice)
    out = _path(text, p, voice)
    if out.exists():
        return out
    if p == "elevenlabs":
        r = httpx.post(
            f"https://api.elevenlabs.io/v1/text-to-speech/{voice}",
            headers={"xi-api-key": settings.elevenlabs_api_key, "accept": "audio/mpeg"},
            json={"text": text, "model_id": "eleven_multilingual_v2", "voice_settings": {"stability": 0.5, "similarity_boost": 0.75}},
            timeout=120,
        )
    else:
        r = httpx.post(
            "https://api.openai.com/v1/audio/speech",
            headers={"Authorization": f"Bearer {settings.openai_api_key}"},
            json={"model": "gpt-4o-mini-tts", "voice": voice, "input": text, "response_format": "mp3"},
            timeout=120,
        )
    r.raise_for_status()
    out.write_bytes(r.content)
    return out


def note_text(note) -> str:
    """The exact text the audio endpoint narrates — keep in sync with routes/v1.get_note_audio."""
    prefix = "Watch" if note.lang == "en" else "İzlenecek"
    watch = " ".join(f"{prefix}: {w}." for w in (note.data or {}).get("watch", []))
    return f"{note.content} {watch}"


def warm(note) -> int:
    """Pre-synthesise both voices for a note so the first 'Listen' click is instant. Returns files produced."""
    if provider() is None or note is None:
        return 0
    n = 0
    for gender in ("female", "male"):
        try:
            if synthesize(note_text(note), lang=note.lang, gender=gender):
                n += 1
        except Exception:  # noqa: BLE001 — cache warming must never break the job
            continue
    return n
