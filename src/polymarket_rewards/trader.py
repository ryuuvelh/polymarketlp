from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

from .client import ClobClient
from .scorer import MarketScore, build_quote_plan


@dataclass(frozen=True)
class TraderConfig:
    private_key: str
    funder_address: str | None = None
    signature_type: int = 2
    dry_run: bool = True


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

    def quote_market(self, score: MarketScore) -> list[dict[str, object]]:
        plans = build_quote_plan(score.market)
        results: list[dict[str, object]] = []

        for plan in plans:
            payload = {
                "token_id": plan.token.token_id,
                "outcome": plan.token.outcome,
                "side": plan.side,
                "price": plan.price,
                "size": plan.size,
                "market": score.market.question,
            }
            if self.config.dry_run:
                payload["status"] = "dry_run"
                results.append(payload)
                continue

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
            results.append(payload)

        return results
