from __future__ import annotations

from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import quote_plus

import feedparser

from ..models import NewsArticle


def fetch_google_news_rss(query: str, *, limit: int = 10) -> list[NewsArticle]:
    if not query.strip():
        return []
    url = (
        "https://news.google.com/rss/search?q="
        f"{quote_plus(query)}&hl=en-US&gl=US&ceid=US:en"
    )
    feed = feedparser.parse(url)
    articles: list[NewsArticle] = []
    for entry in feed.entries[:limit]:
        title = str(getattr(entry, "title", "")).strip()
        if not title:
            continue
        link = str(getattr(entry, "link", "")).strip()
        published = _parse_published(getattr(entry, "published", None))
        articles.append(
            NewsArticle(
                source="google_rss",
                title=title,
                url=link,
                published_at=published,
                keywords=tuple(query.lower().split()),
                query=query,
            )
        )
    return articles


def _parse_published(value: object) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    try:
        parsed = parsedate_to_datetime(str(value))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except (TypeError, ValueError, OverflowError):
        return datetime.now(timezone.utc)
