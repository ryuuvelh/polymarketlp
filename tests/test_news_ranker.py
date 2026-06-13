from __future__ import annotations

from datetime import datetime, timezone

from polymarket_rewards.news.aggregator import dedupe_articles
from polymarket_rewards.news.models import NewsArticle, NewsEventCluster
from polymarket_rewards.news.ranker import score_cluster
from polymarket_rewards.position_regime import regime_for_market


def _article(source: str, title: str) -> NewsArticle:
    return NewsArticle(
        source=source,
        title=title,
        url=f"https://example.com/{source}",
        published_at=datetime.now(timezone.utc),
        keywords=("iran", "diplomacy"),
    )


def test_dedupe_identical_headlines_across_feeds() -> None:
    articles = [
        _article("rss", "US and Iran hold diplomatic talks"),
        _article("newsapi", "US and Iran hold diplomatic talks"),
        _article("gnews", "Different headline on Iran"),
    ]
    unique = dedupe_articles(articles)
    assert len(unique) == 2


def test_multi_source_agreement_raises_score() -> None:
    single = NewsEventCluster(
        event_id="iran-talks",
        label="Iran diplomacy",
        keywords=("iran", "diplomacy"),
        articles=(_article("rss", "Iran talks continue"),),
    )
    multi = NewsEventCluster(
        event_id="iran-talks",
        label="Iran diplomacy",
        keywords=("iran", "diplomacy"),
        articles=(
            _article("rss", "Breaking: Iran talks continue"),
            _article("newsapi", "Iran diplomacy update"),
            _article("gnews", "US Iran meeting confirmed"),
            _article("finnhub", "Iran crisis diplomacy"),
        ),
    )
    single_score = score_cluster(single, matched_market_ids=("123",)).news_risk_score
    multi_score = score_cluster(multi, matched_market_ids=("123",)).news_risk_score
    assert multi_score > single_score


def test_resolution_proximity_escalates_regime() -> None:
    cluster = NewsEventCluster(
        event_id="vote",
        label="Senate vote",
        keywords=("senate", "vote"),
        articles=(
            _article("rss", "Breaking: Senate vote confirmed"),
            _article("newsapi", "Senate vote deadline today"),
        ),
    )
    far = score_cluster(cluster, matched_market_ids=("1",), hours_to_expiry=168.0)
    near = score_cluster(cluster, matched_market_ids=("1",), hours_to_expiry=2.0)
    assert near.news_risk_score > far.news_risk_score
    assert near.regime in {"flat", "flat_extended", "minimal"}
    if near.news_risk_score > 50:
        assert near.regime == "flat_extended"


def test_regime_mapping_thresholds() -> None:
    assert regime_for_market(10, 100) == "full_lp"
    assert regime_for_market(30, 100) == "buffer_only"
    assert regime_for_market(60, 100) == "minimal"
    assert regime_for_market(80, 100) == "flat"
    assert regime_for_market(95, 100) == "flat_extended"
    assert regime_for_market(55, 2.0, near_expiry_hours=3.0) == "flat_extended"
