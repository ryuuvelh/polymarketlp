from __future__ import annotations

from datetime import datetime, timedelta, timezone

from polymarket_rewards.client import RewardsMarket, RewardsToken
from polymarket_rewards.risk import RiskConfig, RiskDecision, RiskEngine
from polymarket_rewards.scorer import score_market


def _score(end_date: str):
    market = RewardsMarket(
        condition_id="cond",
        market_id="123",
        market_slug="slug",
        question="Q?",
        event_slug="event",
        rate_per_day=100.0,
        rewards_max_spread=3.0,
        rewards_min_size=100.0,
        market_competitiveness=2.0,
        spread=0.02,
        volume_24hr=10000.0,
        tokens=(
            RewardsToken("yes", "Yes", 0.5),
            RewardsToken("no", "No", 0.5),
        ),
        end_date=end_date,
    )
    return score_market(market)


def test_evaluate_tick_blocks_over_budget_notional() -> None:
    engine = RiskEngine(RiskConfig(total_capital_usd=100.0, tier_active_pct=0.15, tier_buffer_pct=0.25))
    score = _score("2026-06-20T00:00:00Z")
    decision = engine.evaluate_tick(score, planned_notional_usd=500.0)
    assert decision == RiskDecision.BLOCK


def test_evaluate_tick_allows_safe_market() -> None:
    engine = RiskEngine(RiskConfig(total_capital_usd=1000.0))
    score = _score("2026-06-20T00:00:00Z")
    decision = engine.evaluate_tick(score, planned_notional_usd=100.0)
    assert decision == RiskDecision.ALLOW


def test_overweight_side_detects_yes_inventory() -> None:
    engine = RiskEngine(RiskConfig(total_capital_usd=100.0, inventory_imbalance_pct=0.30))
    assert engine.overweight_side(700.0, 300.0) == "yes"
    assert engine.overweight_side(100.0, 900.0) == "no"
    assert engine.overweight_side(500.0, 500.0) is None


def test_cooldown_blocks_after_kill_switch() -> None:
    engine = RiskEngine(RiskConfig(total_capital_usd=100.0, kill_cooldown_sec=60.0))
    now = datetime(2026, 6, 13, 12, 0, tzinfo=timezone.utc)
    engine.trigger_kill_switch(now=now)
    assert engine.in_cooldown(now=now + timedelta(seconds=30))
    assert engine.check_volatility(now=now + timedelta(seconds=30)) == RiskDecision.BLOCK
