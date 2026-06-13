from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed

from .aggregator import dedupe_articles
from .feeds import (
    fetch_currents,
    fetch_finnhub_for_query,
    fetch_gnews,
    fetch_google_news_rss,
    fetch_newsapi,
)
from .models import NewsArticle


def fetch_for_query(query: str, *, limit_per_source: int = 8) -> list[NewsArticle]:
    tasks = {
        "google_rss": lambda: fetch_google_news_rss(query, limit=limit_per_source),
        "currents": lambda: fetch_currents(query, limit=limit_per_source),
        "newsapi": lambda: fetch_newsapi(query, limit=limit_per_source),
        "gnews": lambda: fetch_gnews(query, limit=limit_per_source),
        "finnhub": lambda: fetch_finnhub_for_query(query, limit=limit_per_source),
    }
    articles: list[NewsArticle] = []
    with ThreadPoolExecutor(max_workers=len(tasks)) as pool:
        futures = {pool.submit(fn): name for name, fn in tasks.items()}
        for future in as_completed(futures):
            try:
                articles.extend(future.result())
            except Exception:
                continue
    return dedupe_articles(articles)


def fetch_for_queries(queries: list[str], *, limit_per_source: int = 6) -> list[NewsArticle]:
    all_articles: list[NewsArticle] = []
    seen_queries: set[str] = set()
    for query in queries:
        normalized = query.strip().lower()
        if not normalized or normalized in seen_queries:
            continue
        seen_queries.add(normalized)
        all_articles.extend(fetch_for_query(query, limit_per_source=limit_per_source))
    return dedupe_articles(all_articles)
