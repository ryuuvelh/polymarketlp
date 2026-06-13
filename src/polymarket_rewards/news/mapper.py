from __future__ import annotations

from ..client import RewardsMarket
from ..scorer import MarketScore
from .keywords import query_from_market, slugify_event, tokenize
from .models import RankedEvent


def build_query_map(markets: list[RewardsMarket]) -> dict[str, list[str]]:
    mapping: dict[str, list[str]] = {}
    for market in markets:
        query = query_from_market(market)
        mapping.setdefault(query, []).append(market.market_id)
    return mapping


def market_hours_map(scores: list[MarketScore]) -> dict[str, float | None]:
    return {item.market.market_id: item.hours_to_expiry for item in scores}


def match_event_to_market(event: RankedEvent, market: RewardsMarket) -> bool:
    if market.market_id in event.matched_market_ids:
        return True
    query = query_from_market(market)
    if slugify_event(query) == event.event_id:
        return True
    market_tokens = tokenize(" ".join([market.question, market.event_slug, market.market_slug]))
    event_tokens = tokenize(event.label)
    if not market_tokens or not event_tokens:
        return False
    overlap = len(market_tokens & event_tokens) / max(len(market_tokens), 1)
    return overlap >= 0.3


def best_event_for_market(events: list[RankedEvent], market: RewardsMarket) -> RankedEvent | None:
    matches = [event for event in events if match_event_to_market(event, market)]
    if not matches:
        return None
    return max(matches, key=lambda item: item.news_risk_score)


def enrich_market_score(score: MarketScore, event: RankedEvent | None) -> MarketScore:
    if event is None:
        return score
    combined = score.risk_adjusted_score * (1.0 - event.news_risk_score / 200.0)
    headlines = event.top_headlines
    notes = score.notes + (f"news_risk={event.news_risk_score:.0f}", f"regime={event.regime}")
    return MarketScore(
        market=score.market,
        opportunity_score=score.opportunity_score,
        estimated_daily_reward=score.estimated_daily_reward,
        capital_required_usd=score.capital_required_usd,
        reward_per_100_usd=score.reward_per_100_usd,
        requires_two_sided=score.requires_two_sided,
        midpoint=score.midpoint,
        notes=notes,
        risk_score=score.risk_score,
        risk_flags=score.risk_flags,
        risk_adjusted_score=score.risk_adjusted_score,
        hours_to_expiry=score.hours_to_expiry,
        news_risk_score=event.news_risk_score,
        news_regime=event.regime,
        news_sentiment_lean=event.sentiment_lean,
        news_headlines=headlines,
        combined_risk_adjusted_score=combined,
    )


def enrich_scores(scores: list[MarketScore], events: list[RankedEvent]) -> list[MarketScore]:
    enriched: list[MarketScore] = []
    for score in scores:
        event = best_event_for_market(events, score.market)
        enriched.append(enrich_market_score(score, event))
    return enriched
