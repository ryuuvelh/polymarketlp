from __future__ import annotations

import re

from ..client import RewardsMarket

STOPWORDS = {
    "a", "an", "the", "and", "or", "by", "on", "in", "at", "to", "for", "of", "is", "will",
    "be", "this", "that", "with", "from", "before", "after", "?", "us", "x", "vs",
}


def tokenize(text: str) -> set[str]:
    tokens = re.findall(r"[a-z0-9]+", text.lower())
    return {token for token in tokens if token not in STOPWORDS and len(token) > 2}


def query_from_market(market: RewardsMarket, *, max_terms: int = 5) -> str:
    raw = " ".join(filter(None, [market.question, market.event_slug.replace("-", " ")]))
    tokens = sorted(tokenize(raw), key=len, reverse=True)
    return " ".join(tokens[:max_terms])


def slugify_event(label: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-")
    return slug[:80] or "event"
