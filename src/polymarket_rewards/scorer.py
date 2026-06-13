from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Iterable

from .client import RewardsMarket, RewardsToken
from .market_time import hours_until_expiry

# Polymarket uses a scaling factor of 3 for single-sided quoting in the [0.10, 0.90] band.
SINGLE_SIDED_PENALTY = 3.0
MIDPOINT_TWO_SIDED_LOW = 0.10
MIDPOINT_TWO_SIDED_HIGH = 0.90
EXTREME_MIDPOINT_LOW = 0.15
EXTREME_MIDPOINT_HIGH = 0.85
DEFAULT_TIER_ACTIVE_PCT = 0.15
DEFAULT_TIER_BUFFER_PCT = 0.25
DEFAULT_MAX_SKEW_CENTS = 2.0


@dataclass(frozen=True)
class QuoteTierConfig:
    label: str
    spread_offset_cents: float
    capital_pct: float


@dataclass(frozen=True)
class QuotePlan:
    token: RewardsToken
    side: str
    price: float
    size: float
    tier: str = "active"


@dataclass(frozen=True)
class TierConfig:
    total_capital_usd: float
    active_pct: float = DEFAULT_TIER_ACTIVE_PCT
    buffer_pct: float = DEFAULT_TIER_BUFFER_PCT
    max_skew_cents: float = DEFAULT_MAX_SKEW_CENTS

    @property
    def deployable_pct(self) -> float:
        return self.active_pct + self.buffer_pct

    @property
    def deployable_usd(self) -> float:
        return self.total_capital_usd * self.deployable_pct

    @classmethod
    def from_env(cls) -> TierConfig | None:
        raw = os.getenv("TOTAL_CAPITAL_USD", "").strip()
        if not raw:
            return None
        return cls(
            total_capital_usd=float(raw),
            active_pct=float(os.getenv("TIER_ACTIVE_PCT", str(DEFAULT_TIER_ACTIVE_PCT))),
            buffer_pct=float(os.getenv("TIER_BUFFER_PCT", str(DEFAULT_TIER_BUFFER_PCT))),
            max_skew_cents=float(os.getenv("MAX_SKEW_CENTS", str(DEFAULT_MAX_SKEW_CENTS))),
        )


@dataclass(frozen=True)
class MarketScore:
    market: RewardsMarket
    opportunity_score: float
    estimated_daily_reward: float
    capital_required_usd: float
    reward_per_100_usd: float
    requires_two_sided: bool
    midpoint: float
    notes: tuple[str, ...]
    risk_score: float
    risk_flags: tuple[str, ...]
    risk_adjusted_score: float
    hours_to_expiry: float | None


def order_score(max_spread_cents: float, spread_cents: float) -> float:
    """Quadratic scoring rule from Polymarket liquidity rewards docs."""
    if max_spread_cents <= 0:
        return 0.0
    spread_cents = max(0.0, min(spread_cents, max_spread_cents))
    return ((max_spread_cents - spread_cents) / max_spread_cents) ** 2


def requires_two_sided_quoting(midpoint: float) -> bool:
    return midpoint < MIDPOINT_TWO_SIDED_LOW or midpoint > MIDPOINT_TWO_SIDED_HIGH


def midpoint_from_tokens(tokens: Iterable[RewardsToken]) -> float:
    tokens = list(tokens)
    if not tokens:
        return 0.5
    yes_like = next((t for t in tokens if t.outcome.lower() in {"yes", "y"}), tokens[0])
    return yes_like.price


def estimate_capital_required(market: RewardsMarket, two_sided: bool) -> float:
    """Rough USDC needed to post minimum qualifying resting orders."""
    min_size = market.rewards_min_size
    if min_size <= 0:
        return 0.0

    prices = [max(token.price, 0.001) for token in market.tokens[:2]]
    if two_sided:
        return min_size * sum(prices)

    return min_size * min(prices)


def estimate_competition_adjusted_share(market: RewardsMarket) -> float:
    competitiveness = max(market.market_competitiveness, 0.0)
    if competitiveness <= 0:
        return 0.35
    return 1.0 / (1.0 + competitiveness)


def estimate_daily_reward(market: RewardsMarket, two_sided: bool) -> float:
    max_spread_cents = market.rewards_max_spread
    if max_spread_cents <= 0:
        return 0.0

    target_spread_cents = min(1.0, max_spread_cents)
    per_side_score = order_score(max_spread_cents, target_spread_cents) * market.rewards_min_size

    if two_sided:
        q_min = per_side_score
    else:
        q_min = per_side_score / SINGLE_SIDED_PENALTY

    share = estimate_competition_adjusted_share(market)
    estimated = market.rate_per_day * share * min(1.0, q_min / (q_min + competitiveness_floor(market)))
    return estimated


def competitiveness_floor(market: RewardsMarket) -> float:
    return max(50.0, market.rewards_min_size * 2.0)


def compute_risk_score(
    market: RewardsMarket,
    *,
    midpoint: float,
    capital_required: float,
    tier_config: TierConfig | None = None,
) -> tuple[float, tuple[str, ...]]:
    score = 100.0
    flags: list[str] = []

    hours_left = hours_until_expiry(market)
    if hours_left is not None:
        if hours_left <= 3:
            score -= 60
            flags.append("near_expiry")
        elif hours_left <= 24:
            penalty = min(40.0, (24 - hours_left) / 24 * 40)
            score -= penalty
            flags.append("expiry_within_24h")
    else:
        score -= 5
        flags.append("unknown_expiry")

    if midpoint < EXTREME_MIDPOINT_LOW or midpoint > EXTREME_MIDPOINT_HIGH:
        score -= 20
        flags.append("extreme_midpoint")

    if market.spread > (market.rewards_max_spread / 100.0):
        score -= 15
        flags.append("wide_book")

    if market.market_competitiveness > 10:
        score -= 10
        flags.append("high_competition")

    if tier_config is not None and capital_required > tier_config.deployable_usd:
        score -= 25
        flags.append("capital_exceeds_budget")

    return max(0.0, min(100.0, score)), tuple(flags)


def default_tier_configs(tier_config: TierConfig) -> tuple[QuoteTierConfig, QuoteTierConfig]:
    return (
        QuoteTierConfig("active", 0.0, tier_config.active_pct),
        QuoteTierConfig("buffer", 2.0, tier_config.buffer_pct),
    )


def _momentum_skew_cents(momentum: float | None, max_skew_cents: float) -> tuple[float, float]:
    """Return (yes_skew_cents, no_skew_cents) where positive widens that side."""
    if momentum is None or abs(momentum) < 0.001:
        return 0.0, 0.0
    skew = min(max_skew_cents, abs(momentum) * 100)
    if momentum > 0:
        return 0.0, skew
    return skew, 0.0


def _inventory_skew_cents(
    yes_balance: float,
    no_balance: float,
    *,
    imbalance_threshold_pct: float,
    max_skew_cents: float,
) -> tuple[float, float]:
    total = yes_balance + no_balance
    if total <= 0:
        return 0.0, 0.0
    yes_share = yes_balance / total
    threshold = imbalance_threshold_pct / 2
    if yes_share > 0.5 + threshold:
        return max_skew_cents, 0.0
    if yes_share < 0.5 - threshold:
        return 0.0, max_skew_cents
    return 0.0, 0.0


def _tier_size_usd(tier: QuoteTierConfig, tier_config: TierConfig) -> float:
    return tier_config.total_capital_usd * tier.capital_pct


def _size_from_usd(usd: float, price: float, min_size: float) -> float:
    if price <= 0 or usd <= 0:
        return 0.0
    size = usd / price
    if size < min_size:
        return 0.0
    return round(size, 2)


def build_quote_plan(
    market: RewardsMarket,
    *,
    tier_config: TierConfig | None = None,
    momentum: float | None = None,
    yes_balance: float = 0.0,
    no_balance: float = 0.0,
    imbalance_threshold_pct: float = 0.30,
) -> list[QuotePlan]:
    """Build tiered asymmetric quote plans inside the reward band."""
    if len(market.tokens) < 2:
        return []

    midpoint = midpoint_from_tokens(market.tokens)
    two_sided = requires_two_sided_quoting(midpoint)
    max_spread = market.rewards_max_spread / 100.0
    min_size = market.rewards_min_size
    tick = 0.01 if midpoint >= 0.1 else 0.001

    yes_token, no_token = market.tokens[0], market.tokens[1]
    tiers = default_tier_configs(tier_config) if tier_config else (
        QuoteTierConfig("active", 0.0, 1.0),
    )
    max_skew = tier_config.max_skew_cents if tier_config else DEFAULT_MAX_SKEW_CENTS
    mom_yes_skew, mom_no_skew = _momentum_skew_cents(momentum, max_skew)
    inv_yes_skew, inv_no_skew = _inventory_skew_cents(
        yes_balance,
        no_balance,
        imbalance_threshold_pct=imbalance_threshold_pct,
        max_skew_cents=max_skew,
    )

    plans: list[QuotePlan] = []
    include_no = two_sided or tier_config is not None
    for tier in tiers:
        spread_offset = tier.spread_offset_cents / 100.0
        yes_widen = (mom_yes_skew + inv_yes_skew) / 100.0
        no_widen = (mom_no_skew + inv_no_skew) / 100.0

        yes_price = round(max(tick, midpoint - max_spread + tick - spread_offset - yes_widen), 3)
        ask_price = round(min(1.0 - tick, midpoint + max_spread - tick + spread_offset), 3)
        no_price = round(max(tick, 1.0 - ask_price - no_widen), 3)

        if tier_config is not None:
            tier_usd = _tier_size_usd(tier, tier_config) / (2 if include_no else 1)
            yes_size = _size_from_usd(tier_usd, yes_price, min_size)
            no_size = _size_from_usd(tier_usd, no_price, min_size)
        else:
            yes_size = min_size
            no_size = min_size

        if inv_yes_skew >= max_skew and tier.label == "active":
            yes_size = 0.0
        if inv_no_skew >= max_skew and tier.label == "active":
            no_size = 0.0

        if yes_size > 0:
            plans.append(QuotePlan(token=yes_token, side="BUY", price=yes_price, size=yes_size, tier=tier.label))
        if include_no and no_size > 0:
            plans.append(QuotePlan(token=no_token, side="BUY", price=no_price, size=no_size, tier=tier.label))

    return plans


def score_market(
    market: RewardsMarket,
    *,
    tier_config: TierConfig | None = None,
) -> MarketScore:
    midpoint = midpoint_from_tokens(market.tokens)
    two_sided = requires_two_sided_quoting(midpoint)
    capital = estimate_capital_required(market, two_sided=two_sided)
    estimated_reward = estimate_daily_reward(market, two_sided=two_sided)
    hours_left = hours_until_expiry(market)

    notes: list[str] = []
    if two_sided:
        notes.append("needs two-sided quotes")
    else:
        notes.append("single-sided ok (1/3 score)")

    if market.rewards_max_spread <= 0 or market.rewards_min_size <= 0:
        notes.append("missing reward parameters")

    if estimated_reward < 1.0:
        notes.append("likely below $1 daily payout")

    if market.spread > (market.rewards_max_spread / 100.0):
        notes.append("book wider than reward band")

    reward_per_100 = (estimated_reward / capital * 100.0) if capital > 0 else 0.0
    competitiveness = max(market.market_competitiveness, 0.01)
    opportunity = (estimated_reward / capital * 10_000.0) if capital > 0 else 0.0
    opportunity *= market.rate_per_day / (market.rate_per_day + competitiveness)

    risk_score, risk_flags = compute_risk_score(
        market,
        midpoint=midpoint,
        capital_required=capital,
        tier_config=tier_config,
    )
    risk_adjusted = opportunity * (risk_score / 100.0)

    return MarketScore(
        market=market,
        opportunity_score=opportunity,
        estimated_daily_reward=estimated_reward,
        capital_required_usd=capital,
        reward_per_100_usd=reward_per_100,
        requires_two_sided=two_sided,
        midpoint=midpoint,
        notes=tuple(notes),
        risk_score=risk_score,
        risk_flags=risk_flags,
        risk_adjusted_score=risk_adjusted,
        hours_to_expiry=hours_left,
    )


def rank_markets(
    markets: list[RewardsMarket],
    *,
    tier_config: TierConfig | None = None,
) -> list[MarketScore]:
    scored = [score_market(market, tier_config=tier_config) for market in markets]
    scored.sort(key=lambda item: item.risk_adjusted_score, reverse=True)
    return scored
