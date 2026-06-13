from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .scorer import QuotePlan
from .trader import LiveOrder, RewardsTrader


@dataclass
class OrderManager:
    trader: RewardsTrader
    tracked: dict[str, LiveOrder] = field(default_factory=dict)

    def sync_orders(self) -> list[dict[str, Any]]:
        open_orders = self.trader.list_open_orders()
        self.tracked.clear()
        for order in open_orders:
            order_id = str(order.get("id") or order.get("order_id") or "")
            if not order_id:
                continue
            tier = str(order.get("tier") or order.get("client_tag") or "")
            live = LiveOrder(
                order_id=order_id,
                token_id=str(order.get("asset_id") or order.get("token_id") or ""),
                side=str(order.get("side") or ""),
                price=float(order.get("price") or 0),
                size=float(order.get("original_size") or order.get("size") or 0),
                tier=tier,
            )
            self.tracked[order_id] = live
        return open_orders

    def cancel_all(self, *, market_id: str | None = None) -> list[dict[str, Any]]:
        results = self.trader.cancel_all_orders(market_id=market_id)
        if market_id is None:
            self.tracked.clear()
        else:
            self.tracked = {
                order_id: order
                for order_id, order in self.tracked.items()
                if order_id not in {str(item.get("order_id", "")) for item in results}
            }
        return results

    def _plan_key(self, plan: QuotePlan) -> str:
        return f"{plan.token.token_id}:{plan.side}:{plan.tier}:{plan.price:.4f}"

    def replace_quotes(
        self,
        plans: list[QuotePlan],
        *,
        market_question: str = "",
        cancel_first: bool = True,
    ) -> list[dict[str, object]]:
        if cancel_first:
            self.sync_orders()

        desired = {self._plan_key(plan): plan for plan in plans}
        current_keys = {
            self._plan_key_from_order(order): order
            for order in self.tracked.values()
        }

        results: list[dict[str, object]] = []
        if cancel_first:
            for key, order in list(current_keys.items()):
                if key not in desired:
                    results.append(self.trader.cancel_order(order.order_id))
                    self.tracked.pop(order.order_id, None)
        else:
            for key, order in list(current_keys.items()):
                if key not in desired:
                    results.append(self.trader.cancel_order(order.order_id))
                    self.tracked.pop(order.order_id, None)

        for key, plan in desired.items():
            if key in current_keys:
                continue
            payload = self.trader.submit_plan(plan, market_question=market_question)
            results.append(payload)
            order_id = str(payload.get("order_id") or "")
            if order_id:
                self.tracked[order_id] = LiveOrder(
                    order_id=order_id,
                    token_id=plan.token.token_id,
                    side=plan.side,
                    price=plan.price,
                    size=plan.size,
                    tier=plan.tier,
                )
        return results

    @staticmethod
    def _plan_key_from_order(order: LiveOrder) -> str:
        return f"{order.token_id}:{order.side}:{order.tier}:{order.price:.4f}"
