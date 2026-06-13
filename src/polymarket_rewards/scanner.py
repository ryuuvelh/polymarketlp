from __future__ import annotations

from dataclasses import dataclass

from .client import ClobClient, RewardsMarket
from .scorer import MarketScore, TierConfig, rank_markets, score_market


@dataclass(frozen=True)
class ScanFilters:
    min_rate_per_day: float = 1.0
    min_volume_24hr: float = 0.0
    max_competitiveness: float | None = None
    min_reward_per_100_usd: float = 0.0
    min_hours_to_expiry: float | None = None
    exclude_near_expiry: bool = True
    max_capital_usd: float | None = None
    query: str | None = None
    tag_slug: str | None = None


class RewardsScanner:
    def __init__(self, client: ClobClient | None = None) -> None:
        self.client = client or ClobClient()

    def get_market_score(self, market_id: str, *, tier_config: TierConfig | None = None) -> MarketScore | None:
        market = self.client.fetch_market_by_id(market_id)
        if market is None:
            return None
        return score_market(market, tier_config=tier_config)

    def scan(self, filters: ScanFilters | None = None) -> list[MarketScore]:
        filters = filters or ScanFilters()
        tier_config = TierConfig.from_env() if filters.max_capital_usd is None else None
        if filters.max_capital_usd is not None and tier_config is None:
            tier_config = TierConfig(total_capital_usd=filters.max_capital_usd)

        params: dict[str, object] = {}
        if filters.query:
            params["q"] = filters.query
        if filters.tag_slug:
            params["tag_slug"] = filters.tag_slug
        if filters.min_volume_24hr > 0:
            params["min_volume_24hr"] = filters.min_volume_24hr

        markets = self.client.fetch_all_reward_markets(params=params or None)
        markets = self._apply_local_filters(markets, filters)
        ranked = rank_markets(markets, tier_config=tier_config)
        return self._apply_score_filters(ranked, filters)

    @staticmethod
    def _apply_local_filters(markets: list[RewardsMarket], filters: ScanFilters) -> list[RewardsMarket]:
        filtered: list[RewardsMarket] = []
        for market in markets:
            if market.rate_per_day < filters.min_rate_per_day:
                continue
            if filters.max_competitiveness is not None and market.market_competitiveness > filters.max_competitiveness:
                continue
            filtered.append(market)
        return filtered

    @staticmethod
    def _apply_score_filters(scored: list[MarketScore], filters: ScanFilters) -> list[MarketScore]:
        result: list[MarketScore] = []
        for item in scored:
            if filters.min_reward_per_100_usd > 0 and item.reward_per_100_usd < filters.min_reward_per_100_usd:
                continue
            if filters.exclude_near_expiry and filters.min_hours_to_expiry is not None:
                if item.hours_to_expiry is not None and item.hours_to_expiry < filters.min_hours_to_expiry:
                    continue
            if filters.max_capital_usd is not None and item.capital_required_usd > filters.max_capital_usd:
                continue
            result.append(item)
        return result
