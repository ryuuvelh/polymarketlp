from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from .client import RewardsMarket, RewardsToken

# Polymarket uses a scaling factor of 3 for single-sided quoting in the [0.10, 0.90] band.
SINGLE_SIDED_PENALTY = 3.0
MIDPOINT_TWO_SIDED_LOW = 0.10
MIDPOINT_TWO_SIDED_HIGH = 0.90


@dataclass(frozen=True)
class QuotePlan:
    token: RewardsToken
    side: str
    price: float
    size: float


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
    """
    Proxy for expected reward share.

    API `market_competitiveness` is higher when more makers are fighting for rewards.
    We invert it with a floor so brand-new markets do not look infinitely good.
    """
    competitiveness = max(market.market_competitiveness, 0.0)
    if competitiveness <= 0:
        return 0.35
    return 1.0 / (1.0 + competitiveness)


def estimate_daily_reward(market: RewardsMarket, two_sided: bool) -> float:
    max_spread_cents = market.rewards_max_spread
    if max_spread_cents <= 0:
        return 0.0

    # Assume we quote 1 cent inside the reward band on each required side.
    target_spread_cents = min(1.0, max_spread_cents)
    per_side_score = order_score(max_spread_cents, target_spread_cents) * market.rewards_min_size

    if two_sided:
        q_min = per_side_score
    else:
        q_min = max(per_side_score / SINGLE_SIDED_PENALTY, per_side_score)

    share = estimate_competition_adjusted_share(market)
    estimated = market.rate_per_day * share * min(1.0, q_min / (q_min + competitiveness_floor(market)))
    return estimated


def competitiveness_floor(market: RewardsMarket) -> float:
    # Keeps very competitive markets from looking deceptively cheap.
    return max(50.0, market.rewards_min_size * 2.0)


def build_quote_plan(market: RewardsMarket) -> list[QuotePlan]:
    """Suggest tight two-sided quotes that should qualify for rewards."""
    if len(market.tokens) < 2:
        return []

    midpoint = midpoint_from_tokens(market.tokens)
    two_sided = requires_two_sided_quoting(midpoint)
    max_spread = market.rewards_max_spread / 100.0
    min_size = market.rewards_min_size
    tick = 0.01 if midpoint >= 0.1 else 0.001

    yes_token, no_token = market.tokens[0], market.tokens[1]
    bid_price = round(max(tick, midpoint - max_spread + tick), 3)
    ask_price = round(min(1.0 - tick, midpoint + max_spread - tick), 3)

    plans = [
        QuotePlan(token=yes_token, side="BUY", price=bid_price, size=min_size),
    ]
    if two_sided:
        plans.append(
            QuotePlan(token=no_token, side="BUY", price=round(1.0 - ask_price, 3), size=min_size)
        )
    return plans


def score_market(market: RewardsMarket) -> MarketScore:
    midpoint = midpoint_from_tokens(market.tokens)
    two_sided = requires_two_sided_quoting(midpoint)
    capital = estimate_capital_required(market, two_sided=two_sided)
    estimated_reward = estimate_daily_reward(market, two_sided=two_sided)

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

    return MarketScore(
        market=market,
        opportunity_score=opportunity,
        estimated_daily_reward=estimated_reward,
        capital_required_usd=capital,
        reward_per_100_usd=reward_per_100,
        requires_two_sided=two_sided,
        midpoint=midpoint,
        notes=tuple(notes),
    )


def rank_markets(markets: list[RewardsMarket]) -> list[MarketScore]:
    scored = [score_market(market) for market in markets]
    scored.sort(key=lambda item: item.opportunity_score, reverse=True)
    return scored
