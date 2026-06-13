from __future__ import annotations

from .keywords import slugify_event, tokenize
from .models import NewsArticle, NewsEventCluster


def cluster_articles(
    articles: list[NewsArticle],
    *,
    query_keywords: dict[str, tuple[str, ...]] | None = None,
) -> list[NewsEventCluster]:
    if not articles:
        return []

    buckets: dict[str, list[NewsArticle]] = {}
    labels: dict[str, str] = {}
    keywords_map: dict[str, tuple[str, ...]] = {}

    if query_keywords:
        for query, keywords in query_keywords.items():
            event_id = slugify_event(query)
            buckets[event_id] = []
            labels[event_id] = query[:80]
            keywords_map[event_id] = keywords

    for article in articles:
        assigned = False
        title_tokens = tokenize(article.title)
        for event_id, keywords in keywords_map.items():
            keyword_set = set(keywords)
            if not keyword_set:
                continue
            overlap = len(title_tokens & keyword_set) / max(len(keyword_set), 1)
            if overlap >= 0.25 or (article.query and slugify_event(article.query) == event_id):
                buckets.setdefault(event_id, []).append(article)
                assigned = True
                break
        if not assigned:
            fallback_id = slugify_event(article.query or article.title[:40])
            buckets.setdefault(fallback_id, []).append(article)
            labels.setdefault(fallback_id, (article.query or article.title)[:80])
            keywords_map.setdefault(fallback_id, tuple(title_tokens))

    clusters: list[NewsEventCluster] = []
    for event_id, items in buckets.items():
        if not items:
            continue
        clusters.append(
            NewsEventCluster(
                event_id=event_id,
                label=labels.get(event_id, event_id),
                keywords=keywords_map.get(event_id, ()),
                articles=tuple(items),
            )
        )
    return clusters
