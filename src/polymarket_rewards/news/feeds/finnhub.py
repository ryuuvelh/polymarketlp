from __future__ import annotations

import os
from datetime import datetime, timezone

import requests

from ..models import NewsArticle

FINNHUB_URL = "https://finnhub.io/api/v1/news"


def fetch_finnhub_general(*, limit: int = 20) -> list[NewsArticle]:
    api_key = os.getenv("FINNHUB_API_KEY", "").strip()
    if not api_key:
        return []
    response = requests.get(
        FINNHUB_URL,
        params={"category": "general", "token": api_key},
        timeout=20,
    )
    response.raise_for_status()
    payload = response.json()
    articles: list[NewsArticle] = []
    for item in payload[:limit]:
        title = str(item.get("headline", "")).strip()
        if not title:
            continue
        published = datetime.fromtimestamp(int(item.get("datetime", 0)), tz=timezone.utc)
        articles.append(
            NewsArticle(
                source="finnhub",
                title=title,
                url=str(item.get("url", "")),
                published_at=published,
                keywords=(),
                query="general",
            )
        )
    return articles


def fetch_finnhub_for_query(query: str, *, limit: int = 10) -> list[NewsArticle]:
    query_tokens = set(query.lower().split())
    matched: list[NewsArticle] = []
    for article in fetch_finnhub_general(limit=50):
        title_tokens = set(article.title.lower().split())
        if query_tokens & title_tokens:
            matched.append(
                NewsArticle(
                    source=article.source,
                    title=article.title,
                    url=article.url,
                    published_at=article.published_at,
                    keywords=tuple(query.lower().split()),
                    query=query,
                )
            )
        if len(matched) >= limit:
            break
    return matched
