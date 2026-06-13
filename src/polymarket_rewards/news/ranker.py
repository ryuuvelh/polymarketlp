from __future__ import annotations

import os
from datetime import datetime, timezone

from ..position_regime import regime_for_market
from .events import cluster_articles
from .keywords import query_from_market, slugify_event, tokenize
from .models import NewsArticle, NewsEventCluster, RankedEvent

URGENCY_WORDS = {
    "breaking", "urgent", "confirmed", "war", "attack", "ceasefire", "sanctions",
    "invasion", "strike", "missile", "emergency", "crisis", "deadline", "vote",
    "passed", "failed", "resign", "indicted", "default", "halt", "suspend",
}

YES_POSITIVE = {"agree", "deal", "peace", "ceasefire", "pass", "approved", "win", "wins", "success"}
YES_NEGATIVE = {"war", "attack", "fail", "failed", "reject", "crisis", "collapse", "deny", "no deal"}


def sentiment_from_titles(titles: list[str]) -> float:
    if not titles:
        return 0.0
    score = 0.0
    for title in titles:
        tokens = tokenize(title)
        score += sum(1 for token in tokens if token in YES_POSITIVE)
        score -= sum(1 for token in tokens if token in YES_NEGATIVE)
    return max(-1.0, min(1.0, score / max(len(titles), 1)))


def _recency_boost(articles: list[NewsArticle], *, now: datetime) -> float:
    if not articles:
        return 0.0
    recent = 0
    for article in articles:
        age_min = (now - article.published_at).total_seconds() / 60.0
        if age_min <= 30:
            recent += 1
    return min(25.0, recent * 5.0)


def _urgency_boost(titles: list[str]) -> float:
    boost = 0.0
    for title in titles:
        tokens = tokenize(title)
        if tokens & URGENCY_WORDS:
            boost += 8.0
    return min(30.0, boost)


def _source_agreement_boost(articles: list[NewsArticle]) -> float:
    sources = {article.source for article in articles}
    if len(sources) >= 4:
        return 25.0
    if len(sources) == 3:
        return 18.0
    if len(sources) == 2:
        return 10.0
    return 0.0


def _resolution_multiplier(hours_to_expiry: float | None) -> float:
    if hours_to_expiry is None:
        return 1.0
    if hours_to_expiry <= 3:
        return 2.0
    if hours_to_expiry <= 24:
        return 1.5
    if hours_to_expiry <= 72:
        return 1.2
    return 1.0


def score_cluster(
    cluster: NewsEventCluster,
    *,
    matched_market_ids: tuple[str, ...],
    hours_to_expiry: float | None = None,
    now: datetime | None = None,
) -> RankedEvent:
    now = now or datetime.now(timezone.utc)
    titles = [article.title for article in cluster.articles]
    base = 10.0 + len(cluster.articles) * 4.0
    base += _recency_boost(list(cluster.articles), now=now)
    base += _urgency_boost(titles)
    base += _source_agreement_boost(list(cluster.articles))
    base *= _resolution_multiplier(hours_to_expiry)
    news_risk = max(0.0, min(100.0, base))
    sentiment = sentiment_from_titles(titles)
    near_expiry_hours = float(os.getenv("NEAR_EXPIRY_HOURS", "3"))
    regime = regime_for_market(news_risk, hours_to_expiry, near_expiry_hours=near_expiry_hours)
    return RankedEvent(
        event_id=cluster.event_id,
        label=cluster.label,
        news_risk_score=news_risk,
        sentiment_lean=sentiment,
        source_count=len({article.source for article in cluster.articles}),
        headline_count=len(cluster.articles),
        matched_market_ids=matched_market_ids,
        top_headlines=tuple(titles[:5]),
        regime=regime,
    )


def rank_clusters(
    clusters: list[NewsEventCluster],
    *,
    market_hours: dict[str, float | None],
    market_ids_by_query: dict[str, list[str]],
) -> list[RankedEvent]:
    ranked: list[RankedEvent] = []
    for cluster in clusters:
        matched: list[str] = []
        hours_values: list[float] = []
        for query, ids in market_ids_by_query.items():
            if slugify_event(query) == cluster.event_id or set(cluster.keywords) & set(tokenize(query)):
                matched.extend(ids)
        matched = list(dict.fromkeys(matched))
        for market_id in matched:
            hours = market_hours.get(market_id)
            if hours is not None:
                hours_values.append(hours)
        hours_to_expiry = min(hours_values) if hours_values else None
        ranked.append(
            score_cluster(
                cluster,
                matched_market_ids=tuple(matched),
                hours_to_expiry=hours_to_expiry,
            )
        )
    ranked.sort(key=lambda item: item.news_risk_score, reverse=True)
    return ranked
