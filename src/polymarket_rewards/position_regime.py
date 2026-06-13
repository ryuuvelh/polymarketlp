from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class PositionRegime(str, Enum):
    FULL_LP = "full_lp"
    BUFFER_ONLY = "buffer_only"
    MINIMAL = "minimal"
    FLAT = "flat"
    FLAT_EXTENDED = "flat_extended"


@dataclass(frozen=True)
class QuoteMode:
    use_active_tier: bool
    use_buffer_tier: bool
    buffer_size_multiplier: float
    join_not_lead_ticks: int
    extra_vulnerable_skew_cents: float
    sentiment_size_skew: float


def regime_for_market(
    news_risk: float,
    hours_to_expiry: float | None,
    *,
    near_expiry_hours: float = 3.0,
) -> str:
    if hours_to_expiry is not None and hours_to_expiry < near_expiry_hours and news_risk > 50:
        return PositionRegime.FLAT_EXTENDED.value
    if news_risk >= 90:
        return PositionRegime.FLAT_EXTENDED.value
    if news_risk >= 75:
        return PositionRegime.FLAT.value
    if news_risk >= 50:
        return PositionRegime.MINIMAL.value
    if news_risk >= 25:
        return PositionRegime.BUFFER_ONLY.value
    return PositionRegime.FULL_LP.value


def quote_mode_for_regime(regime: str) -> QuoteMode:
    if regime == PositionRegime.FULL_LP.value:
        return QuoteMode(
            use_active_tier=True,
            use_buffer_tier=True,
            buffer_size_multiplier=1.0,
            join_not_lead_ticks=0,
            extra_vulnerable_skew_cents=0.0,
            sentiment_size_skew=0.0,
        )
    if regime == PositionRegime.BUFFER_ONLY.value:
        return QuoteMode(
            use_active_tier=False,
            use_buffer_tier=True,
            buffer_size_multiplier=1.0,
            join_not_lead_ticks=1,
            extra_vulnerable_skew_cents=0.0,
            sentiment_size_skew=0.1,
        )
    if regime == PositionRegime.MINIMAL.value:
        return QuoteMode(
            use_active_tier=False,
            use_buffer_tier=True,
            buffer_size_multiplier=0.5,
            join_not_lead_ticks=1,
            extra_vulnerable_skew_cents=2.0,
            sentiment_size_skew=0.2,
        )
    return QuoteMode(
        use_active_tier=False,
        use_buffer_tier=False,
        buffer_size_multiplier=0.0,
        join_not_lead_ticks=0,
        extra_vulnerable_skew_cents=0.0,
        sentiment_size_skew=0.0,
    )


def is_flat_regime(regime: str) -> bool:
    return regime in {PositionRegime.FLAT.value, PositionRegime.FLAT_EXTENDED.value}
