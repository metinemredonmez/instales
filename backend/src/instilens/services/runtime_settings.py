"""Admin-editable runtime settings.

A small allow-list of NON-secret settings can be overridden from the admin UI; overrides live in `app_settings`
and are applied onto the process-wide `settings` object. Every process calls `apply()` — the API at startup, after
every change and on the provider-facing routes (/quotes, /admin/providers: with two uvicorn workers the PUT lands
on only one of them), the scheduler at the start of every job, the feed on every iteration — so a change takes
effect within one cycle without a restart. Everything else (keys, hosts, database, the Matriks credentials)
stays in `.env` and needs `pm2 restart instilens-api instilens-scheduler instilens-feed --update-env`.
"""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from instilens.config import settings
from instilens.domain.models import AppSetting

log = logging.getLogger("instilens.settings")

# key → (type, group, min, max). Types: bool | int | str | list[str] | choice:<a|b>
EDITABLE: dict[str, dict[str, Any]] = {
    "allow_registration": {"type": "bool", "group": "access"},
    "account_lockout_attempts": {"type": "int", "group": "access", "min": 3, "max": 50},
    "account_lockout_minutes": {"type": "int", "group": "access", "min": 1, "max": 1440},
    "auth_rate_limit_per_minute": {"type": "int", "group": "access", "min": 3, "max": 100},
    "ai_requests_per_hour": {"type": "int", "group": "ai", "min": 1, "max": 1000},
    "ai_model": {"type": "str", "group": "ai"},
    "ai_news_enabled": {"type": "bool", "group": "ai"},
    "ai_news_model": {"type": "str", "group": "ai"},
    "ai_extract_model": {"type": "str", "group": "ai"},
    "elevenlabs_model": {"type": "choice:eleven_multilingual_v2|eleven_v3|eleven_turbo_v2_5|eleven_flash_v2_5", "group": "ai"},
    "elevenlabs_speed": {"type": "float", "group": "ai", "min": 0.7, "max": 1.2},
    "elevenlabs_paragraph_pause_s": {"type": "float", "group": "ai", "min": 0.0, "max": 3.0},
    "elevenlabs_stability": {"type": "float", "group": "ai", "min": 0.0, "max": 1.0},
    "elevenlabs_style": {"type": "float", "group": "ai", "min": 0.0, "max": 1.0},
    "elevenlabs_speaker_boost": {"type": "bool", "group": "ai"},
    "elevenlabs_extra_voices": {"type": "text", "group": "ai"},
    "breached_password_check": {"type": "bool", "group": "access"},
    "news_enabled": {"type": "bool", "group": "data"},
    "live_tv_channels": {"type": "text", "group": "data"},
    "kap_adapter": {"type": "choice:public|api|fixture", "group": "data"},
    "kap_public_days_back": {"type": "int", "group": "data", "min": 1, "max": 60},
    "kap_public_max_details": {"type": "int", "group": "data", "min": 1, "max": 500},
    "kap_public_max_reports": {"type": "int", "group": "data", "min": 0, "max": 500},
    "kap_public_fund_codes": {"type": "list", "group": "data"},
    "sec_ciks": {"type": "list", "group": "data"},
    "market_holidays_tr": {"type": "list", "group": "data", "item": "date"},  # ISO dates BIST is closed
    "price_provider": {"type": "choice:yahoo|matriks", "group": "data"},  # an unconfigured choice falls back to yahoo
    "quotes_interval_s": {"type": "int", "group": "data", "min": 15, "max": 600},
    "fundamentals_enabled": {"type": "bool", "group": "data"},  # gates the weekly statements/metrics job (services/fundamentals)
    "fundamentals_max_instruments": {"type": "int", "group": "data", "min": 10, "max": 5000},  # per market and run, stalest first
    "sec_form4_enabled": {"type": "bool", "group": "data"},  # gates the daily Form 4 / issuer filings job (services/insiders)
    "sec_form4_max_issuers": {"type": "int", "group": "data", "min": 10, "max": 2000},  # issuers per run, stalest first
}
_DEFAULTS: dict[str, Any] = {}

# Rows under these keys are process state, not settings: never listed, never editable, never applied onto
# `settings`. `_feed_status` is the quote feed's heartbeat (services/feed).
RESERVED_PREFIX = "_"


class SettingError(ValueError):
    pass


def defaults() -> dict[str, Any]:
    """Values from .env/code before any override, captured once at import."""
    if not _DEFAULTS:
        for k in EDITABLE:
            _DEFAULTS[k] = getattr(settings, k)
    return _DEFAULTS


def coerce(key: str, value: Any) -> Any:
    meta = EDITABLE.get(key)
    if meta is None:
        raise SettingError(f"{key} is not editable")
    t = meta["type"]
    if t == "bool":
        if isinstance(value, bool):
            return value
        if isinstance(value, str) and value.lower() in ("true", "false", "1", "0", "on", "off"):
            return value.lower() in ("true", "1", "on")
        raise SettingError(f"{key}: expected true/false")
    if t == "int":
        try:
            v = int(value)
        except (TypeError, ValueError) as exc:
            raise SettingError(f"{key}: expected a whole number") from exc
        if not (meta.get("min", -10**9) <= v <= meta.get("max", 10**9)):
            raise SettingError(f"{key}: must be between {meta.get('min')} and {meta.get('max')}")
        return v
    if t == "str":
        v = str(value).strip()
        if not v or len(v) > 128:
            raise SettingError(f"{key}: 1-128 characters")
        return v
    if t == "text":
        v = str(value).strip()
        if len(v) > 4000:
            raise SettingError(f"{key}: too long")
        return v
    if t == "float":
        try:
            v = float(value)
        except (TypeError, ValueError) as exc:
            raise SettingError(f"{key}: expected a number") from exc
        if not (meta.get("min", -1e9) <= v <= meta.get("max", 1e9)):
            raise SettingError(f"{key}: must be between {meta.get('min')} and {meta.get('max')}")
        return v
    if t == "list":
        items = value if isinstance(value, list) else str(value).replace(";", ",").split(",")
        out = [str(x).strip().upper() for x in items if str(x).strip()]
        if len(out) > 200 or any(len(x) > 32 for x in out):
            raise SettingError(f"{key}: too many or too long entries")
        if meta.get("item") == "date":
            try:
                out = sorted({date.fromisoformat(x).isoformat() for x in out})
            except ValueError as exc:
                raise SettingError(f"{key}: entries must be ISO dates (YYYY-MM-DD)") from exc
        return out
    if t.startswith("choice:"):
        allowed = t.split(":", 1)[1].split("|")
        if str(value) not in allowed:
            raise SettingError(f"{key}: one of {', '.join(allowed)}")
        return str(value)
    raise SettingError(f"{key}: unsupported type")


def apply(session: Session) -> int:
    """Patch `settings` with stored overrides (and reset keys without an override to their defaults)."""
    defaults()
    rows = {r.key: r.value.get("v") for r in session.scalars(select(AppSetting))}
    n = 0
    for k in EDITABLE:
        target = rows[k] if k in rows else _DEFAULTS[k]
        if getattr(settings, k) != target:
            setattr(settings, k, target)
            n += 1
    return n


def snapshot(session: Session) -> list[dict]:
    defaults()
    rows = {r.key: r for r in session.scalars(select(AppSetting))}
    return [
        {"key": k, "group": m["group"], "type": m["type"], "item": m.get("item"), "min": m.get("min"), "max": m.get("max"),
         "value": rows[k].value.get("v") if k in rows else _DEFAULTS[k], "default": _DEFAULTS[k],
         "overridden": k in rows, "updated_at": rows[k].updated_at.isoformat() if k in rows else None, "updated_by": rows[k].updated_by if k in rows else None}
        for k, m in EDITABLE.items()
    ]


def load_json(session: Session, key: str) -> Any:
    """The stored value of a reserved key (None when absent). Editable keys go through snapshot()/apply()."""
    if not key.startswith(RESERVED_PREFIX):
        raise SettingError(f"{key} is not a reserved key")
    row = session.get(AppSetting, key)
    return None if row is None else row.value.get("v")


def store_json(session: Session, key: str, value: Any, actor: str) -> None:
    """Upsert a reserved key. Same `{"v": ...}` envelope as overrides so scalars round-trip through the JSON column."""
    if not key.startswith(RESERVED_PREFIX):
        raise SettingError(f"{key} is not a reserved key")
    row = session.get(AppSetting, key)
    if row is None:
        session.add(AppSetting(key=key, value={"v": value}, updated_by=actor))
    else:
        row.value, row.updated_at, row.updated_by = {"v": value}, datetime.now(UTC), actor
    session.flush()


def set_many(session: Session, values: dict[str, Any], actor: str) -> list[str]:
    """Validate and store overrides; a value equal to the default removes the override. Returns changed keys."""
    defaults()
    changed: list[str] = []
    for k, raw in values.items():
        v = coerce(k, raw)
        row = session.get(AppSetting, k)
        if v == _DEFAULTS[k]:
            if row is not None:
                session.delete(row)
                changed.append(k)
            continue
        if row is None:
            session.add(AppSetting(key=k, value={"v": v}, updated_by=actor))
            changed.append(k)
        elif row.value.get("v") != v:
            row.value, row.updated_at, row.updated_by = {"v": v}, datetime.now(UTC), actor
            changed.append(k)
    session.flush()
    apply(session)
    if changed:
        log.info("runtime settings changed by %s: %s", actor, ", ".join(changed))
    return changed
