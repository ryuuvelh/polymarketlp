from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

from ..scorer import MarketScore
from .aggregator import dedupe_articles
from .events import cluster_articles
from .fetcher import fetch_for_queries
from .keywords import query_from_market, tokenize
from .mapper import build_query_map, enrich_scores, market_hours_map
from .models import NewsCache, RankedEvent
from .ranker import rank_clusters

_CACHE = NewsCache()
_CACHE_TTL_SEC = float(os.getenv("NEWS_CACHE_TTL_SEC", "300"))


def _cache_stale(now: datetime) -> bool:
    if _CACHE.fetched_at is None:
        return True
    return (now - _CACHE.fetched_at).total_seconds() >= _CACHE_TTL_SEC


def refresh_news(scores: list[MarketScore], *, force: bool = False) -> tuple[list[RankedEvent], list[MarketScore]]:
    now = datetime.now(timezone.utc)
    if not force and not _cache_stale(now):
        return _CACHE.events, enrich_scores(scores, _CACHE.events)

    queries = list(dict.fromkeys(query_from_market(item.market) for item in scores[:25]))
    articles = fetch_for_queries(queries)
    query_keywords = {query: tuple(tokenize(query)) for query in queries}
    clusters = cluster_articles(articles, query_keywords=query_keywords)
    query_map = build_query_map([item.market for item in scores])
    events = rank_clusters(
        clusters,
        market_hours=market_hours_map(scores),
        market_ids_by_query=query_map,
    )
    _CACHE.fetched_at = now
    _CACHE.articles = articles
    _CACHE.events = events
    return events, enrich_scores(scores, events)


def get_cached_events() -> list[RankedEvent]:
    return list(_CACHE.events)


def news_for_market(market_id: str) -> RankedEvent | None:
    for event in _CACHE.events:
        if market_id in event.matched_market_ids:
            return event
    return None
