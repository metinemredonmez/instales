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
    tuning = f"{settings.elevenlabs_model}{settings.elevenlabs_speed}{settings.elevenlabs_stability}{settings.elevenlabs_style}{settings.elevenlabs_speaker_boost}{settings.elevenlabs_paragraph_pause_s}" if provider == "elevenlabs" else ""
    return CACHE / f"{provider}-{voice[:12]}-{hashlib.sha1((text + tuning).encode()).hexdigest()[:20]}.mp3"


def pick_voice(lang: str = "tr", gender: str = "female") -> str:
    lang, gender = ("en" if lang == "en" else "tr"), ("male" if gender == "male" else "female")
    chosen = getattr(settings, f"elevenlabs_voice_{lang}_{gender}", "") or getattr(settings, f"elevenlabs_voice_en_{gender}", "")
    return chosen or settings.elevenlabs_voice_id


_VOICES_CACHE: dict = {"at": 0.0, "rows": []}


def voices(lang: str) -> list[dict]:
    """Selectable voices for a language: the four configured, admin extras, plus ElevenLabs library voices that
    are verified for the language (when the key may read voices; otherwise silently just the configured ones)."""
    import time

    import httpx as _hx

    lang = "en" if lang == "en" else "tr"
    out: list[dict] = []
    seen: set[str] = set()

    def add(vid: str, name: str, gender: str, source: str) -> None:
        if vid and vid not in seen:
            seen.add(vid)
            out.append({"id": vid, "name": name, "gender": gender, "lang": lang, "source": source})

    names = {"female": "Kadın" if lang == "tr" else "Female", "male": "Erkek" if lang == "tr" else "Male"}
    for g in ("female", "male"):
        add(getattr(settings, f"elevenlabs_voice_{lang}_{g}", ""), f"{names[g]} (varsayılan)" if lang == "tr" else f"{names[g]} (default)", g, "config")
    for part in (settings.elevenlabs_extra_voices or "").split(","):
        bits = [b.strip() for b in part.split(":")]
        if len(bits) >= 3 and bits[0] == lang:
            add(bits[2], bits[3] if len(bits) > 3 and bits[3] else bits[2][:8], "male" if bits[1] == "male" else "female", "extra")
    if provider() == "elevenlabs":
        if time.time() - _VOICES_CACHE["at"] > 600:
            try:
                r = _hx.get("https://api.elevenlabs.io/v1/voices", headers={"xi-api-key": settings.elevenlabs_api_key}, timeout=8)
                _VOICES_CACHE["rows"] = r.json().get("voices", []) if r.status_code == 200 else []
            except Exception:  # noqa: BLE001 — optional enrichment
                _VOICES_CACHE["rows"] = []
            _VOICES_CACHE["at"] = time.time()
        for v in _VOICES_CACHE["rows"]:
            labels = v.get("labels") or {}
            langs = {x.get("language") for x in (v.get("verified_languages") or []) if x.get("language")} | ({labels.get("language")} if labels.get("language") else set())
            if lang in langs or (lang == "en" and v.get("category") == "premade"):
                add(v.get("voice_id", ""), v.get("name", "?"), "male" if str(labels.get("gender", "")).lower() == "male" else "female", "library")
    return out


def voice_allowed(lang: str, voice_id: str) -> bool:
    return any(v["id"] == voice_id for v in voices(lang))


OPENAI_VOICES = {"female": "nova", "male": "onyx"}


def _voice_settings() -> dict:
    return {"stability": settings.elevenlabs_stability, "similarity_boost": 0.75, "style": settings.elevenlabs_style,
            "use_speaker_boost": settings.elevenlabs_speaker_boost, "speed": settings.elevenlabs_speed}


def provider() -> str | None:
    if settings.elevenlabs_api_key:
        return "elevenlabs"
    if settings.openai_api_key:
        return "openai"
    return None


def synthesize(text: str, lang: str = "tr", gender: str = "female", voice_id: str | None = None) -> Path | None:
    p = provider()
    if p is None:
        return None
    voice = (voice_id or pick_voice(lang, gender)) if p == "elevenlabs" else OPENAI_VOICES.get(gender, settings.openai_tts_voice)
    out = _path(text, p, voice)
    if out.exists():
        return out
    if p == "elevenlabs":
        r = httpx.post(
            f"https://api.elevenlabs.io/v1/text-to-speech/{voice}",
            headers={"xi-api-key": settings.elevenlabs_api_key, "accept": "audio/mpeg"},
            json={"text": text, "model_id": settings.elevenlabs_model, "language_code": "en" if lang == "en" else "tr",
                  "voice_settings": _voice_settings()},
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


def cached_path(text: str, lang: str = "tr", gender: str = "female", voice_id: str | None = None) -> Path | None:
    """The cache file for this text/voice if it already exists (no network)."""
    p = provider()
    if p is None:
        return None
    voice = (voice_id or pick_voice(lang, gender)) if p == "elevenlabs" else OPENAI_VOICES.get(gender, settings.openai_tts_voice)
    out = _path(text, p, voice)
    return out if out.exists() else None


def stream(text: str, lang: str = "tr", gender: str = "female", voice_id: str | None = None):
    """Yield MP3 chunks as ElevenLabs produces them (first audio within ~1 s instead of after the whole text),
    while writing the same bytes to the cache so the next play is instant. OpenAI has no streaming here → whole file."""
    p = provider()
    if p is None:
        return
    if p != "elevenlabs":
        path = synthesize(text, lang, gender, voice_id)
        if path:
            yield path.read_bytes()
        return
    voice = voice_id or pick_voice(lang, gender)
    out = _path(text, p, voice)
    if out.exists():
        yield out.read_bytes()
        return
    part = out.with_suffix(".part")
    with httpx.stream(
        "POST",
        f"https://api.elevenlabs.io/v1/text-to-speech/{voice}/stream",
        headers={"xi-api-key": settings.elevenlabs_api_key, "accept": "audio/mpeg"},
        json={"text": text, "model_id": settings.elevenlabs_model, "language_code": "en" if lang == "en" else "tr",
              "voice_settings": _voice_settings()},
        timeout=httpx.Timeout(120, connect=15),
    ) as r:
        r.raise_for_status()
        with part.open("wb") as fh:
            for chunk in r.iter_bytes(chunk_size=8192):
                fh.write(chunk)
                yield chunk
    part.replace(out)


# ---------------------------------------------------------------- spoken script
_SIGNALS = {
    "tr": {"NEGATIVE_DIVERGENCE": "negatif ayrışma", "POSITIVE_DIVERGENCE": "pozitif ayrışma", "ACCUMULATION": "birikim",
           "DISTRIBUTION": "dağıtım", "NEW_POSITION_CLUSTER": "yeni pozisyon kümesi", "EXIT_CLUSTER": "çıkış kümesi",
           "INFERRED": "türetilmiş", "GROUPED": "gruplu", "EXACT": "kesin", "NEW": "yeni giriş", "ADD": "artırma", "REDUCE": "azaltma", "EXIT": "tam çıkış", "HOLD": "tutma"},
    "en": {"NEGATIVE_DIVERGENCE": "negative divergence", "POSITIVE_DIVERGENCE": "positive divergence", "ACCUMULATION": "accumulation",
           "DISTRIBUTION": "distribution", "NEW_POSITION_CLUSTER": "new-position cluster", "EXIT_CLUSTER": "exit cluster",
           "INFERRED": "inferred", "GROUPED": "grouped", "EXACT": "exact", "NEW": "new entry", "ADD": "increase", "REDUCE": "reduction", "EXIT": "full exit", "HOLD": "hold"},
}
_UNITS_TR = [(r"\bmn\s*TL\b", "milyon lira"), (r"\bmn\s*\$", "milyon dolar"), (r"\bmilyar\s*TL\b", "milyar lira"), (r"\bbn\b", "milyar"), (r"\bmn\b", "milyon"),
             (r"\bTL\b", "lira"), (r"\bUSD\b", "dolar"), (r"\$(\d)", r"\1 dolar "), (r"\b13F\b", "on üç F"), (r"\bKAP\b", "KAP"), (r"\bBoJ\b", "Japonya Merkez Bankası"),
             (r"\bTCMB\b", "Merkez Bankası"), (r"\bFed\b", "Fed")]
_UNITS_EN = [(r"\bmn\b", "million"), (r"\bbn\b", "billion"), (r"\bTL\b", "lira"), (r"\b13F\b", "thirteen F")]


# ---- numbers → words (TTS engines stumble on "344,6" and "10.150.110"; words are read cleanly)
_TR_ONES = ["", "bir", "iki", "üç", "dört", "beş", "altı", "yedi", "sekiz", "dokuz"]
_TR_TENS = ["", "on", "yirmi", "otuz", "kırk", "elli", "altmış", "yetmiş", "seksen", "doksan"]
_TR_SCALE = ["", "bin", "milyon", "milyar", "trilyon"]


def _tr_below_1000(n: int) -> str:
    parts = []
    h, r = divmod(n, 100)
    if h:
        parts.append("yüz" if h == 1 else f"{_TR_ONES[h]} yüz")
    t, o = divmod(r, 10)
    if t:
        parts.append(_TR_TENS[t])
    if o:
        parts.append(_TR_ONES[o])
    return " ".join(parts)


def tr_number_words(n: int) -> str:
    if n == 0:
        return "sıfır"
    groups, i, out = [], 0, []
    while n:
        n, g = divmod(n, 1000)
        groups.append(g)
    for i, g in enumerate(groups):
        if not g:
            continue
        words = "" if (g == 1 and i == 1) else _tr_below_1000(g)  # "bin", not "bir bin"
        out.append(f"{words} {_TR_SCALE[i]}".strip() if i < len(_TR_SCALE) else str(g))
    return " ".join(reversed(out))


def _en_number_words(n: int) -> str:
    from num2words import num2words

    return num2words(n, lang="en").replace(",", "").replace("-", " ")


def numbers_to_words(text: str, lang: str) -> str:
    import re

    if lang == "tr":
        pat = re.compile(r"(?<![\w,])(\d{1,3}(?:\.\d{3})+|\d+)(?:,(\d+))?(?![\w.])")

        def rep(m: re.Match) -> str:
            whole = int(m.group(1).replace(".", ""))
            words = tr_number_words(whole)
            if m.group(2):
                frac = m.group(2)
                words += " virgül " + (tr_number_words(int(frac)) if len(frac) <= 2 and not frac.startswith("0") else " ".join(_TR_ONES[int(c)] if c != "0" else "sıfır" for c in frac))
            return words
    else:
        pat = re.compile(r"(?<![\w.])(\d{1,3}(?:,\d{3})+|\d+)(?:\.(\d+))?(?![\w,])")

        def rep(m: re.Match) -> str:
            words = _en_number_words(int(m.group(1).replace(",", "")))
            if m.group(2):
                words += " point " + " ".join(_en_number_words(int(c)) for c in m.group(2))
            return words

    return pat.sub(rep, text)


def _spell(sym: str) -> str:
    """Tickers with no vowel are unreadable as words: spell them ('K C H O L')."""
    return " ".join(sym) if not any(c in "AEIOUÖÜİ" for c in sym) else sym.capitalize()


def spoken_text(note, names: dict[str, str] | None = None) -> str:
    """Turn the written note into something a TTS voice can read naturally in the note's language:
    tickers → company names (or spelled out), enum constants → words, citations dropped, symbols → words."""
    import re

    lang = "en" if note.lang == "en" else "tr"
    names = names or {}
    data = note.data or {}
    spoken = (data.get("spoken") or "").strip()
    if spoken:
        parts = [spoken]  # the anchor-style script the model wrote for the voice
    else:
        prefix = "Watch" if lang == "en" else "İzlenecek"
        parts = [data.get("headline", ""), note.content] + [f"{prefix}: {w}." for w in data.get("watch", [])]
    # Breathing room: a break tag between paragraphs (ElevenLabs honours <break time="…s" /> in v2/v3).
    blocks = [b.strip() for p in parts if p for b in p.split("\n\n") if b.strip()]
    t = " PARAGRAPHBREAK ".join(b if b.endswith((".", "!", "?", ":")) else b + "." for b in blocks)  # placeholder: tags go in last
    t = re.sub(r"\s*\[(kap|n):\d+\]", "", t)  # citations are for the screen
    for k, v in _SIGNALS[lang].items():
        t = re.sub(rf"\b{k}\b", v, t)
    for pat, rep in (_UNITS_TR if lang == "tr" else _UNITS_EN):
        t = re.sub(pat, rep, t)
    if lang == "tr":
        t = re.sub(r"[+]\s?%\s?([\d.,]+)", r"artı yüzde \1", t)
        t = re.sub(r"[-−]\s?%\s?([\d.,]+)", r"eksi yüzde \1", t)
        t = re.sub(r"%\s?([\d.,]+)", r"yüzde \1", t)
        t = re.sub(r"(?<![\w])[+]\s?(\d)", r"artı \1", t)
        t = re.sub(r"(?<![\w])[-−]\s?(\d)", r"eksi \1", t)
        t = t.replace("→", " ile ").replace("/", " bölü ").replace("&", " ve ")
    else:
        t = re.sub(r"\$\s?([\d.,]+)\s*(billion|million|thousand|bn|mn|k)?", lambda m: f"{m.group(1)} {({'bn': 'billion', 'mn': 'million', 'k': 'thousand'}.get(m.group(2) or '', m.group(2) or '')).strip()} dollars".replace("  ", " "), t)
        t = re.sub(r"([\d.,]+)\s?%", r"\1 percent", t)
        t = re.sub(r"(?<![\w])[+]\s?(\d)", r"plus \1", t)
        t = re.sub(r"(?<![\w])[-−]\s?(\d)", r"minus \1", t)
        t = t.replace("→", " to ").replace("&", " and ")

    t = numbers_to_words(t, lang)

    def ticker(m: re.Match) -> str:
        s = m.group(0)
        if s in names:
            return names[s]
        return _spell(s) if len(s) >= 3 and s.isupper() else s

    known = set(names) | set((note.data or {}).get("inputs", {}).get("_symbols", []))
    t = re.sub(r"\b[A-Z][A-Z0-9]{2,5}\b", lambda m: ticker(m) if (m.group(0) in known or not any(c in "AEIOU" for c in m.group(0))) else m.group(0), t)
    t = re.sub(r"\s+", " ", t).strip()
    pause = f'<break time="{settings.elevenlabs_paragraph_pause_s:.1f}s" />' if settings.elevenlabs_paragraph_pause_s > 0 else ""
    return t.replace("PARAGRAPHBREAK", pause).replace("  ", " ")


def note_text(note, session=None) -> str:
    """The exact text the audio endpoint narrates — keep in sync with routes/v1.get_note_audio."""
    names: dict[str, str] = {}
    if session is not None:
        from sqlalchemy import select

        from instilens.domain.models import Instrument

        market = getattr(note, "market_code", "TR")
        for sym, name in session.execute(select(Instrument.symbol, Instrument.name).where(Instrument.market_code == market)):
            if name and name != sym:
                names[sym] = name
    return spoken_text(note, names)


def warm(note, session=None) -> int:
    """Pre-synthesise both voices for a note so the first 'Listen' click is instant. Returns files produced."""
    if provider() is None or note is None:
        return 0
    n = 0
    text = note_text(note, session)
    for gender in ("female", "male"):
        try:
            if synthesize(text, lang=note.lang, gender=gender):
                n += 1
        except Exception:  # noqa: BLE001 — cache warming must never break the job
            continue
    return n
