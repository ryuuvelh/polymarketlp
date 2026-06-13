from __future__ import annotations

from polymarket_rewards.client import RewardsMarket, RewardsToken
from polymarket_rewards.position_regime import (
    PositionRegime,
    is_flat_regime,
    quote_mode_for_regime,
    regime_for_market,
)
from polymarket_rewards.scorer import TierConfig, build_quote_plan


def test_regime_for_market_tiers() -> None:
    assert regime_for_market(0, None) == PositionRegime.FULL_LP.value
    assert regime_for_market(24, None) == PositionRegime.FULL_LP.value
    assert regime_for_market(25, None) == PositionRegime.BUFFER_ONLY.value
    assert regime_for_market(49, None) == PositionRegime.BUFFER_ONLY.value
    assert regime_for_market(50, None) == PositionRegime.MINIMAL.value
    assert regime_for_market(74, None) == PositionRegime.MINIMAL.value
    assert regime_for_market(75, None) == PositionRegime.FLAT.value
    assert regime_for_market(89, None) == PositionRegime.FLAT.value
    assert regime_for_market(90, None) == PositionRegime.FLAT_EXTENDED.value


def test_is_flat_regime() -> None:
    assert is_flat_regime("flat")
    assert is_flat_regime("flat_extended")
    assert not is_flat_regime("full_lp")
    assert not is_flat_regime("buffer_only")


def test_quote_mode_buffer_only_skips_active_tier() -> None:
    market = RewardsMarket(
        condition_id="c",
        market_id="1",
        market_slug="slug",
        question="Test?",
        event_slug="evt",
        rate_per_day=50.0,
        rewards_max_spread=3.0,
        rewards_min_size=10.0,
        market_competitiveness=2.0,
        spread=0.02,
        volume_24hr=10000.0,
        tokens=(
            RewardsToken("y", "Yes", 0.5),
            RewardsToken("n", "No", 0.5),
        ),
    )
    tier_config = TierConfig(total_capital_usd=1000.0)
    full = build_quote_plan(market, tier_config=tier_config, quote_mode=quote_mode_for_regime("full_lp"))
    buffer_only = build_quote_plan(
        market,
        tier_config=tier_config,
        quote_mode=quote_mode_for_regime("buffer_only"),
    )
    assert any(plan.tier == "active" for plan in full)
    assert all(plan.tier == "buffer" for plan in buffer_only)


def test_quote_mode_flat_returns_no_plans() -> None:
    market = RewardsMarket(
        condition_id="c",
        market_id="1",
        market_slug="slug",
        question="Test?",
        event_slug="evt",
        rate_per_day=50.0,
        rewards_max_spread=3.0,
        rewards_min_size=10.0,
        market_competitiveness=2.0,
        spread=0.02,
        volume_24hr=10000.0,
        tokens=(
            RewardsToken("y", "Yes", 0.5),
            RewardsToken("n", "No", 0.5),
        ),
    )
    tier_config = TierConfig(total_capital_usd=1000.0)
    plans = build_quote_plan(
        market,
        tier_config=tier_config,
        quote_mode=quote_mode_for_regime("flat"),
    )
    assert plans == []
