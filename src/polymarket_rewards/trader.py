from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from dotenv import load_dotenv

from .client import ClobClient
from .scorer import MarketScore, QuotePlan, TierConfig, build_quote_plan


@dataclass(frozen=True)
class TraderConfig:
    private_key: str
    funder_address: str | None = None
    signature_type: int = 2
    dry_run: bool = True


@dataclass
class LiveOrder:
    order_id: str
    token_id: str
    side: str
    price: float
    size: float
    tier: str = ""


class RewardsTrader:
    def __init__(self, client: ClobClient | None = None, config: TraderConfig | None = None) -> None:
        load_dotenv()
        self.public_client = client or ClobClient()
        self.config = config or self._config_from_env()
        self._trading_client = None

    @staticmethod
    def _config_from_env() -> TraderConfig:
        private_key = os.getenv("PRIVATE_KEY", "").strip()
        return TraderConfig(
            private_key=private_key,
            funder_address=os.getenv("FUNDER_ADDRESS") or None,
            signature_type=int(os.getenv("SIGNATURE_TYPE", "2")),
            dry_run=True,
        )

    def _get_trading_client(self):
        if self._trading_client is not None:
            return self._trading_client

        if not self.config.private_key:
            raise RuntimeError("PRIVATE_KEY is required for live quoting. Set it in .env")

        try:
            from py_clob_client_v2 import ApiCreds, ClobClient as TradingClobClient
        except ImportError as exc:
            raise RuntimeError("Install py-clob-client-v2 to place live orders") from exc

        creds = None
        if os.getenv("CLOB_API_KEY") and os.getenv("CLOB_SECRET") and os.getenv("CLOB_PASS_PHRASE"):
            creds = ApiCreds(
                api_key=os.environ["CLOB_API_KEY"],
                api_secret=os.environ["CLOB_SECRET"],
                api_passphrase=os.environ["CLOB_PASS_PHRASE"],
            )

        kwargs: dict[str, object] = {
            "host": "https://clob.polymarket.com",
            "chain_id": 137,
            "key": self.config.private_key,
        }
        if creds is not None:
            kwargs["creds"] = creds
        if self.config.funder_address:
            kwargs["funder"] = self.config.funder_address
            kwargs["signature_type"] = self.config.signature_type

        client = TradingClobClient(**kwargs)
        if creds is None:
            creds = client.create_or_derive_api_key()
            client = TradingClobClient(**kwargs, creds=creds)

        self._trading_client = client
        return client

    def list_open_orders(self) -> list[dict[str, Any]]:
        if self.config.dry_run:
            return []
        client = self._get_trading_client()
        orders = client.get_orders()
        if isinstance(orders, dict):
            orders = orders.get("data", [])
        return [order for order in orders if str(order.get("status", "")).lower() in {"live", "open", "active"}]

    def cancel_order(self, order_id: str) -> dict[str, Any]:
        if self.config.dry_run:
            return {"order_id": order_id, "status": "dry_run_cancelled"}
        client = self._get_trading_client()
        return client.cancel(order_id)

    def cancel_all_orders(self, *, market_id: str | None = None) -> list[dict[str, Any]]:
        open_orders = self.list_open_orders()
        if market_id is not None:
            open_orders = [order for order in open_orders if str(order.get("market", "")) == market_id]
        results: list[dict[str, Any]] = []
        for order in open_orders:
            order_id = str(order.get("id") or order.get("order_id") or "")
            if order_id:
                results.append(self.cancel_order(order_id))
        return results

    def get_balances(self) -> dict[str, float]:
        if self.config.dry_run:
            return {}
        client = self._get_trading_client()
        if hasattr(client, "get_balance_allowance"):
            payload = client.get_balance_allowance()
            if isinstance(payload, dict):
                return {str(k): float(v) for k, v in payload.items() if isinstance(v, (int, float))}
        return {}

    def get_token_balances(self, token_ids: list[str]) -> dict[str, float]:
        balances = self.get_balances()
        if not balances:
            return {token_id: 0.0 for token_id in token_ids}
        return {token_id: float(balances.get(token_id, 0.0)) for token_id in token_ids}

    def submit_plan(self, plan: QuotePlan, *, market_question: str = "") -> dict[str, object]:
        payload: dict[str, object] = {
            "token_id": plan.token.token_id,
            "outcome": plan.token.outcome,
            "side": plan.side,
            "price": plan.price,
            "size": plan.size,
            "tier": plan.tier,
            "market": market_question,
        }
        if self.config.dry_run:
            payload["status"] = "dry_run"
            return payload

        from py_clob_client_v2 import OrderArgs, OrderType, PartialCreateOrderOptions, Side

        client = self._get_trading_client()
        book = self.public_client.fetch_order_book(plan.token.token_id)
        tick_size = str(book.get("tick_size", "0.01"))
        response = client.create_and_post_order(
            order_args=OrderArgs(
                token_id=plan.token.token_id,
                price=plan.price,
                size=plan.size,
                side=Side.BUY if plan.side == "BUY" else Side.SELL,
            ),
            options=PartialCreateOrderOptions(tick_size=tick_size),
            order_type=OrderType.GTC,
        )
        payload["status"] = "submitted"
        payload["response"] = response
        order_id = ""
        if isinstance(response, dict):
            order_id = str(response.get("orderID") or response.get("id") or "")
        payload["order_id"] = order_id
        return payload

    def quote_market(
        self,
        score: MarketScore,
        *,
        tier_config: TierConfig | None = None,
        momentum: float | None = None,
        yes_balance: float = 0.0,
        no_balance: float = 0.0,
        tiered: bool = False,
    ) -> list[dict[str, object]]:
        config = tier_config if tiered else None
        plans = build_quote_plan(
            score.market,
            tier_config=config,
            momentum=momentum,
            yes_balance=yes_balance,
            no_balance=no_balance,
        )
        return [self.submit_plan(plan, market_question=score.market.question) for plan in plans]
