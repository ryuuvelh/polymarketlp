from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass(frozen=True)
class NewsArticle:
    source: str
    title: str
    url: str
    published_at: datetime
    keywords: tuple[str, ...] = ()
    query: str = ""


@dataclass(frozen=True)
class NewsEventCluster:
    event_id: str
    label: str
    keywords: tuple[str, ...]
    articles: tuple[NewsArticle, ...]


@dataclass(frozen=True)
class RankedEvent:
    event_id: str
    label: str
    news_risk_score: float
    sentiment_lean: float
    source_count: int
    headline_count: int
    matched_market_ids: tuple[str, ...]
    top_headlines: tuple[str, ...]
    regime: str


@dataclass
class NewsCache:
    fetched_at: datetime | None = None
    articles: list[NewsArticle] = field(default_factory=list)
    events: list[RankedEvent] = field(default_factory=list)
