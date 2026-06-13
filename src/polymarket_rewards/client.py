from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import requests

CLOB_BASE_URL = "https://clob.polymarket.com"
GAMMA_BASE_URL = "https://gamma-api.polymarket.com"
LAST_PAGE_CURSOR = "LTE="


@dataclass(frozen=True)
class RewardsToken:
    token_id: str
    outcome: str
    price: float


@dataclass(frozen=True)
class RewardsMarket:
    condition_id: str
    market_id: str
    market_slug: str
    question: str
    event_slug: str
    rate_per_day: float
    rewards_max_spread: float
    rewards_min_size: float
    market_competitiveness: float
    spread: float
    volume_24hr: float
    tokens: tuple[RewardsToken, ...]
    end_date: str | None = None
    image: str | None = None

    @property
    def polymarket_url(self) -> str:
        return f"https://polymarket.com/event/{self.event_slug}"

    @classmethod
    def from_api(cls, payload: dict[str, Any]) -> RewardsMarket | None:
        configs = payload.get("rewards_config") or []
        rate_per_day = sum(float(cfg.get("rate_per_day", 0) or 0) for cfg in configs)
        if rate_per_day <= 0:
            return None

        tokens: list[RewardsToken] = []
        for token in payload.get("tokens") or []:
            tokens.append(
                RewardsToken(
                    token_id=str(token["token_id"]),
                    outcome=str(token.get("outcome", "")),
                    price=float(token.get("price", 0) or 0),
                )
            )
        if len(tokens) < 2:
            return None

        return cls(
            condition_id=str(payload["condition_id"]),
            market_id=str(payload["market_id"]),
            market_slug=str(payload.get("market_slug", "")),
            question=str(payload.get("question", "")),
            event_slug=str(payload.get("event_slug", "")),
            rate_per_day=rate_per_day,
            rewards_max_spread=float(payload.get("rewards_max_spread", 0) or 0),
            rewards_min_size=float(payload.get("rewards_min_size", 0) or 0),
            market_competitiveness=float(payload.get("market_competitiveness", 0) or 0),
            spread=float(payload.get("spread", 0) or 0),
            volume_24hr=float(payload.get("volume_24hr", 0) or 0),
            tokens=tuple(tokens),
            end_date=payload.get("end_date"),
            image=payload.get("image"),
        )


class ClobClient:
    def __init__(self, base_url: str = CLOB_BASE_URL, timeout: float = 30.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "polymarket-rewards-bot/0.1"})

    def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        response = self.session.get(
            f"{self.base_url}{path}",
            params=params,
            timeout=self.timeout,
        )
        response.raise_for_status()
        return response.json()

    def fetch_reward_markets_page(
        self,
        *,
        cursor: str | None = None,
        page_size: int = 500,
        params: dict[str, Any] | None = None,
    ) -> tuple[list[RewardsMarket], str]:
        query: dict[str, Any] = {"page_size": page_size}
        if cursor:
            query["next_cursor"] = cursor
        if params:
            query.update(params)

        payload = self._get("/rewards/markets/multi", query)
        markets: list[RewardsMarket] = []
        for item in payload.get("data", []):
            market = RewardsMarket.from_api(item)
            if market is not None:
                markets.append(market)

        next_cursor = str(payload.get("next_cursor", LAST_PAGE_CURSOR))
        return markets, next_cursor

    def fetch_all_reward_markets(
        self,
        *,
        page_size: int = 500,
        params: dict[str, Any] | None = None,
    ) -> list[RewardsMarket]:
        markets: list[RewardsMarket] = []
        cursor: str | None = None

        while True:
            page, cursor = self.fetch_reward_markets_page(
                cursor=cursor,
                page_size=page_size,
                params=params,
            )
            markets.extend(page)
            if cursor == LAST_PAGE_CURSOR:
                break

        return markets

    def fetch_order_book(self, token_id: str) -> dict[str, Any]:
        return self._get("/book", {"token_id": token_id})

    def fetch_gamma_market(self, market_id: str) -> dict[str, Any]:
        response = self.session.get(
            f"{GAMMA_BASE_URL}/markets/{market_id}",
            timeout=self.timeout,
        )
        response.raise_for_status()
        return response.json()

    def fetch_reward_market_by_condition(self, condition_id: str) -> dict[str, Any] | None:
        payload = self._get(f"/rewards/markets/{condition_id}")
        data = payload.get("data") or []
        return data[0] if data else None

    def fetch_market_by_id(self, market_id: str) -> RewardsMarket | None:
        gamma = self.fetch_gamma_market(market_id)
        condition_id = gamma.get("conditionId")
        if not condition_id:
            return None

        reward_payload = self.fetch_reward_market_by_condition(str(condition_id))
        if reward_payload is None:
            return None

        reward_payload.setdefault("market_id", market_id)
        reward_payload.setdefault("volume_24hr", gamma.get("volume24hr", 0))
        reward_payload.setdefault("spread", gamma.get("spread", 0))
        reward_payload.setdefault(
            "market_competitiveness",
            gamma.get("marketCompetitiveness", reward_payload.get("market_competitiveness", 0)),
        )
        reward_payload.setdefault("end_date", gamma.get("endDate") or gamma.get("end_date"))
        return RewardsMarket.from_api(reward_payload)
