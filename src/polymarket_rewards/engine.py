from __future__ import annotations

import signal
import time
from dataclasses import dataclass
from datetime import datetime, timezone

from rich.console import Console

from .news.service import refresh_news
from .order_manager import OrderManager
from .position_regime import is_flat_regime, quote_mode_for_regime
from .risk import RiskDecision, RiskConfig, RiskEngine
from .scanner import RewardsScanner, ScanFilters
from .scorer import TierConfig, build_quote_plan, midpoint_from_tokens
from .trader import RewardsTrader
from .ws_monitor import MonitorEvent, WsMonitor


@dataclass
class EngineConfig:
    market_id: str | None = None
    auto_pick_top: int = 0
    tick_seconds: float = 3.0
    dry_run: bool = True


class RewardsEngine:
    def __init__(
        self,
        *,
        engine_config: EngineConfig,
        risk_config: RiskConfig,
        trader: RewardsTrader,
        scanner: RewardsScanner | None = None,
        console: Console | None = None,
    ) -> None:
        self.engine_config = engine_config
        self.risk_config = risk_config
        self.trader = trader
        self.scanner = scanner or RewardsScanner()
        self.console = console or Console()
        self.risk_engine = RiskEngine(risk_config)
        self.order_manager = OrderManager(trader=trader)
        self.tier_config = TierConfig(
            total_capital_usd=risk_config.total_capital_usd,
            active_pct=risk_config.tier_active_pct,
            buffer_pct=risk_config.tier_buffer_pct,
        )
        self._stop = False
        self._ws_monitor: WsMonitor | None = None
        self._previous_midpoint: float | None = None

    def _resolve_market_id(self) -> str:
        if self.engine_config.market_id:
            return self.engine_config.market_id

        filters = ScanFilters(
            min_rate_per_day=5.0,
            min_volume_24hr=1000.0,
            max_competitiveness=10.0,
            min_hours_to_expiry=self.risk_config.near_expiry_hours,
            exclude_near_expiry=True,
            max_capital_usd=self.risk_config.deployable_usd,
        )
        results = self.scanner.scan(filters, with_news=True)
        if not results:
            raise RuntimeError("No markets matched auto-pick risk filters")

        pick = max(1, self.engine_config.auto_pick_top)
        results.sort(key=lambda item: item.combined_risk_adjusted_score or item.risk_adjusted_score, reverse=True)
        return results[pick - 1].market.market_id

    def _on_ws_event(self, event: MonitorEvent, payload: dict) -> None:
        if event in {MonitorEvent.PRICE_SPIKE, MonitorEvent.VOLUME_SPIKE}:
            self.risk_engine.trigger_kill_switch()
            self.console.print(f"[red]Kill switch triggered via WebSocket: {event.value} {payload}[/red]")

    def _refresh_score(self, market_id: str):
        score = self.scanner.get_market_score(market_id, tier_config=self.tier_config)
        if score is None:
            raise RuntimeError(f"Market {market_id} not found among active reward markets")
        _, enriched = refresh_news([score])
        return enriched[0]

    def _best_bids(self, score) -> dict[str, float]:
        best: dict[str, float] = {}
        for token in score.market.tokens[:2]:
            try:
                book = self.trader.public_client.fetch_order_book(token.token_id)
                bids = book.get("bids") or []
                if bids:
                    best[token.token_id] = float(bids[0].get("price", 0))
            except Exception:
                continue
        return best

    def _planned_notional(self, plans) -> float:
        return sum(plan.price * plan.size for plan in plans)

    def _tick(self, market_id: str) -> None:
        score = self._refresh_score(market_id)

        if is_flat_regime(score.news_regime):
            self.console.print(
                f"[yellow]News regime {score.news_regime} (risk={score.news_risk_score:.0f}): flat[/yellow]"
            )
            self.order_manager.cancel_all()
            return

        midpoint = midpoint_from_tokens(score.market.tokens)
        momentum = None
        if self._previous_midpoint is not None:
            momentum = midpoint - self._previous_midpoint
        self._previous_midpoint = midpoint
        self.risk_engine.record_price(midpoint)
        self.risk_engine.record_volume(score.market.volume_24hr / 86400.0)

        token_ids = [token.token_id for token in score.market.tokens[:2]]
        balances = self.trader.get_token_balances(token_ids)
        yes_balance = balances.get(token_ids[0], 0.0)
        no_balance = balances.get(token_ids[1], 0.0) if len(token_ids) > 1 else 0.0

        quote_mode = quote_mode_for_regime(score.news_regime)
        plans = build_quote_plan(
            score.market,
            tier_config=self.tier_config,
            momentum=momentum,
            yes_balance=yes_balance,
            no_balance=no_balance,
            imbalance_threshold_pct=self.risk_config.inventory_imbalance_pct,
            quote_mode=quote_mode,
            news_sentiment_lean=score.news_sentiment_lean,
            best_bids=self._best_bids(score),
        )
        notional = self._planned_notional(plans)
        decision = self.risk_engine.evaluate_tick(score, planned_notional_usd=notional)

        if decision == RiskDecision.CANCEL_ALL:
            self.console.print("[yellow]Risk guard triggered: cancelling all orders[/yellow]")
            self.order_manager.cancel_all()
            return
        if decision == RiskDecision.BLOCK:
            self.console.print("[yellow]Risk guard blocked new quotes (cooldown or capital)[/yellow]")
            return

        results = self.order_manager.replace_quotes(
            plans,
            market_question=score.market.question,
            cancel_first=True,
        )
        headline = score.news_headlines[0][:60] if score.news_headlines else "-"
        self.console.print(
            f"[green]{datetime.now(timezone.utc).isoformat()}[/green] "
            f"mid={midpoint:.3f} news={score.news_risk_score:.0f} regime={score.news_regime} "
            f"orders={len(results)} headline={headline}"
        )

    def run(self) -> int:
        market_id = self._resolve_market_id()
        score = self._refresh_score(market_id)
        token_ids = [token.token_id for token in score.market.tokens[:2]]
        self._ws_monitor = WsMonitor(
            token_ids=token_ids,
            on_event=self._on_ws_event,
            price_spike_threshold=self.risk_config.kill_price_delta,
            price_window_sec=self.risk_config.kill_window_sec,
            volume_spike_multiplier=self.risk_config.volume_spike_multiplier,
        )
        self._ws_monitor.start()

        def _handle_stop(_signum, _frame) -> None:
            self._stop = True

        signal.signal(signal.SIGINT, _handle_stop)
        signal.signal(signal.SIGTERM, _handle_stop)

        self.console.print(
            f"[bold]Starting defensive LP engine[/bold] on market {market_id} "
            f"({'dry-run' if self.trader.config.dry_run else 'LIVE'})"
        )

        try:
            while not self._stop:
                try:
                    self._tick(market_id)
                except Exception as exc:  # pragma: no cover - runtime guard
                    self.console.print(f"[red]Tick error: {exc}[/red]")
                time.sleep(self.engine_config.tick_seconds)
        finally:
            self.console.print("[yellow]Shutting down: cancelling all orders[/yellow]")
            if self._ws_monitor is not None:
                self._ws_monitor.stop()
            self.order_manager.cancel_all()
        return 0
