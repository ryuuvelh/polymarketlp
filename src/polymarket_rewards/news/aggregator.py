from __future__ import annotations

import hashlib
import re

from .models import NewsArticle


def normalize_title(title: str) -> str:
    lowered = title.lower()
    lowered = re.sub(r"[^a-z0-9\s]", " ", lowered)
    return re.sub(r"\s+", " ", lowered).strip()


def title_hash(title: str) -> str:
    return hashlib.sha256(normalize_title(title).encode("utf-8")).hexdigest()


def dedupe_articles(articles: list[NewsArticle]) -> list[NewsArticle]:
    seen: set[str] = set()
    unique: list[NewsArticle] = []
    for article in articles:
        key = title_hash(article.title)
        if key in seen:
            continue
        seen.add(key)
        unique.append(article)
    return unique
