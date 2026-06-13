from __future__ import annotations

import os
from datetime import datetime, timezone

import requests

from ..models import NewsArticle

NEWSAPI_URL = "https://newsapi.org/v2/everything"


def fetch_newsapi(query: str, *, limit: int = 10) -> list[NewsArticle]:
    api_key = os.getenv("NEWSAPI_KEY", "").strip()
    if not api_key or not query.strip():
        return []
    response = requests.get(
        NEWSAPI_URL,
        params={
            "q": query,
            "language": "en",
            "sortBy": "publishedAt",
            "pageSize": min(limit, 20),
            "apiKey": api_key,
        },
        timeout=20,
    )
    response.raise_for_status()
    payload = response.json()
    articles: list[NewsArticle] = []
    for item in payload.get("articles", [])[:limit]:
        title = str(item.get("title", "")).strip()
        if not title:
            continue
        published = _parse_iso(item.get("publishedAt"))
        articles.append(
            NewsArticle(
                source="newsapi",
                title=title,
                url=str(item.get("url", "")),
                published_at=published,
                keywords=tuple(query.lower().split()),
                query=query,
            )
        )
    return articles


def _parse_iso(value: object) -> datetime:
    if not value:
        return datetime.now(timezone.utc)
    text = str(value).replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return datetime.now(timezone.utc)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)
