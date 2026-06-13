from __future__ import annotations

from datetime import datetime, timezone

from .client import RewardsMarket


def parse_end_date(value: str | None) -> datetime | None:
    if not value:
        return None
    text = value.strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def hours_until_expiry(market: RewardsMarket, *, now: datetime | None = None) -> float | None:
    end = parse_end_date(market.end_date)
    if end is None:
        return None
    now = now or datetime.now(timezone.utc)
    return (end - now).total_seconds() / 3600.0
