"""LLM çağrı sınırları: mesai penceresi, eşzamanlılık, çağrı arası boşluk, 429 geri çekilme."""

from __future__ import annotations

from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from zoneinfo import ZoneInfo

from ..config import Settings

BACKOFF_BASE_S = 30.0
BACKOFF_MAX_S = 900.0


def is_busy(now: datetime, settings: Settings) -> bool:
    """Mesai penceresi içinde mi? (TZ'ye göre, ISO gün: Pazartesi=1)"""
    local = now.astimezone(ZoneInfo(settings.tz))
    if local.isoweekday() not in settings.busy_days:
        return False
    start, end = settings.busy_window
    return start <= local.time() < end


def concurrency(now: datetime, settings: Settings) -> int:
    return settings.llm_busy_concurrency if is_busy(now, settings) else settings.llm_idle_concurrency


def gap_s(now: datetime, settings: Settings) -> float:
    return settings.llm_busy_gap_s if is_busy(now, settings) else 0.0


def retry_after_s(value: str | None, now: datetime | None = None) -> float | None:
    """Retry-After başlığı: saniye ya da HTTP tarihi."""
    if not value:
        return None
    value = value.strip()
    try:
        return max(0.0, float(value))
    except ValueError:
        pass
    try:
        when = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    return max(0.0, (when - (now or datetime.now(UTC))).total_seconds())


def backoff_s(n: int) -> float:
    """n. ardışık hata için üstel geri çekilme (30 sn → 15 dk tavan)."""
    return min(BACKOFF_MAX_S, BACKOFF_BASE_S * (2 ** max(0, n - 1)))
