from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from polymarket_rewards.client import RewardsMarket, RewardsToken
from polymarket_rewards.market_time import hours_until_expiry, parse_end_date
from polymarket_rewards.risk import PriceWindow, RiskConfig, RiskDecision, RiskEngine, VolumeWindow
from polymarket_rewards.scorer import (
    TierConfig,
    build_quote_plan,
    estimate_daily_reward,
    score_market,
)


def _market(
    *,
    midpoint_yes: float = 0.5,
    end_date: str | None = None,
    spread: float = 0.02,
    competitiveness: float = 2.0,
    max_spread: float = 3.0,
    min_size: float = 100.0,
) -> RewardsMarket:
    return RewardsMarket(
        condition_id="cond",
        market_id="123",
        market_slug="test-market",
        question="Test market?",
        event_slug="test-event",
        rate_per_day=100.0,
        rewards_max_spread=max_spread,
        rewards_min_size=min_size,
        market_competitiveness=competitiveness,
        spread=spread,
        volume_24hr=50000.0,
        tokens=(
            RewardsToken("yes-token", "Yes", midpoint_yes),
            RewardsToken("no-token", "No", 1.0 - midpoint_yes),
        ),
        end_date=end_date,
    )


def test_parse_end_date_iso() -> None:
    parsed = parse_end_date("2026-06-13T18:00:00Z")
    assert parsed is not None
    assert parsed.tzinfo is not None


def test_hours_until_expiry() -> None:
    now = datetime(2026, 6, 13, 12, 0, tzinfo=timezone.utc)
    market = _market(end_date="2026-06-13T18:00:00Z")
    hours = hours_until_expiry(market, now=now)
    assert hours == pytest.approx(6.0)


def test_single_sided_penalty_applied() -> None:
    market = _market(midpoint_yes=0.50)
    two_sided = estimate_daily_reward(market, two_sided=True)
    single = estimate_daily_reward(market, two_sided=False)
    assert single < two_sided


def test_near_expiry_lowers_risk_score() -> None:
    now = datetime(2026, 6, 13, 12, 0, tzinfo=timezone.utc)
    safe = score_market(_market(end_date="2026-06-20T00:00:00Z"))
    risky = score_market(_market(end_date="2026-06-13T14:00:00Z"))
    assert risky.risk_score < safe.risk_score
    assert "near_expiry" in risky.risk_flags


def test_tiered_quote_plan_respects_capital() -> None:
    market = _market(min_size=10.0)
    tier_config = TierConfig(total_capital_usd=1000.0, active_pct=0.15, buffer_pct=0.25)
    plans = build_quote_plan(market, tier_config=tier_config)
    assert plans
    tiers = {plan.tier for plan in plans}
    assert "active" in tiers
    assert "buffer" in tiers
    total_notional = sum(plan.price * plan.size for plan in plans)
    assert total_notional <= tier_config.deployable_usd + 1.0


def test_asymmetric_quote_widens_vulnerable_side_on_momentum() -> None:
    market = _market(min_size=10.0)
    tier_config = TierConfig(total_capital_usd=1000.0)
    neutral = build_quote_plan(market, tier_config=tier_config, momentum=0.0)
    rising = build_quote_plan(market, tier_config=tier_config, momentum=0.02)
    neutral_no = next(plan for plan in neutral if plan.token.outcome == "No")
    rising_no = next(plan for plan in rising if plan.token.outcome == "No")
    assert rising_no.price <= neutral_no.price


def test_inventory_skew_pauses_active_yes_buys() -> None:
    market = _market(min_size=10.0)
    tier_config = TierConfig(total_capital_usd=1000.0)
    plans = build_quote_plan(
        market,
        tier_config=tier_config,
        yes_balance=900.0,
        no_balance=100.0,
        imbalance_threshold_pct=0.30,
    )
    active_yes = [plan for plan in plans if plan.tier == "active" and plan.token.outcome == "Yes"]
    assert not active_yes


def test_kill_switch_triggers_on_price_spike() -> None:
    engine = RiskEngine(RiskConfig(total_capital_usd=100.0, kill_price_delta=0.04, kill_window_sec=5.0))
    now = datetime(2026, 6, 13, 12, 0, tzinfo=timezone.utc)
    engine.record_price(0.50, now=now)
    engine.record_price(0.55, now=now + timedelta(seconds=2))
    decision = engine.check_volatility(now=now + timedelta(seconds=2))
    assert decision == RiskDecision.CANCEL_ALL


def test_expiry_guard_cancels_near_resolution() -> None:
    engine = RiskEngine(RiskConfig(total_capital_usd=100.0, near_expiry_hours=3.0))
    now = datetime(2026, 6, 13, 16, 0, tzinfo=timezone.utc)
    score = score_market(_market(end_date="2026-06-13T18:00:00Z"))
    assert engine.check_expiry(score, now=now) == RiskDecision.CANCEL_ALL


def test_volume_spike_detection() -> None:
    window = VolumeWindow(samples=[])
    now = datetime(2026, 6, 13, 12, 0, tzinfo=timezone.utc)
    for i in range(20):
        window.add(10.0, now=now - timedelta(seconds=120 - i))
    window.add(100.0, now=now - timedelta(seconds=2))
    window.add(120.0, now=now)
    assert window.spike_detected(3.0, now=now)
