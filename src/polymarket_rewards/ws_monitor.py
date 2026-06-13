from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Callable

try:
    import websocket
except ImportError:  # pragma: no cover - optional at import time
    websocket = None  # type: ignore[assignment]

WS_MARKET_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"


class MonitorEvent(str, Enum):
    PRICE_SPIKE = "price_spike"
    VOLUME_SPIKE = "volume_spike"
    MIDPOINT_UPDATE = "midpoint_update"


@dataclass
class MarketMonitorState:
    token_ids: list[str]
    last_midpoint: float | None = None
    last_trade_volume: float = 0.0
    updated_at: datetime | None = None


@dataclass
class WsMonitor:
    token_ids: list[str]
    on_event: Callable[[MonitorEvent, dict], None] | None = None
    price_spike_threshold: float = 0.04
    price_window_sec: float = 5.0
    volume_spike_multiplier: float = 3.0
    _thread: threading.Thread | None = field(default=None, init=False, repr=False)
    _stop: threading.Event = field(default_factory=threading.Event, init=False, repr=False)
    _price_samples: list[tuple[float, float]] = field(default_factory=list, init=False, repr=False)
    _volume_samples: list[tuple[float, float]] = field(default_factory=list, init=False, repr=False)
    state: MarketMonitorState = field(init=False)

    def __post_init__(self) -> None:
        self.state = MarketMonitorState(token_ids=list(self.token_ids))

    def _emit(self, event: MonitorEvent, payload: dict) -> None:
        if self.on_event is not None:
            self.on_event(event, payload)

    def _record_price(self, price: float) -> None:
        now = time.time()
        self._price_samples.append((now, price))
        cutoff = now - 300
        self._price_samples = [(ts, value) for ts, value in self._price_samples if ts >= cutoff]
        window = [value for ts, value in self._price_samples if ts >= now - self.price_window_sec]
        if len(window) >= 2:
            delta = max(window) - min(window)
            if delta >= self.price_spike_threshold:
                self._emit(MonitorEvent.PRICE_SPIKE, {"delta": delta, "price": price})
        self.state.last_midpoint = price
        self.state.updated_at = datetime.now(timezone.utc)
        self._emit(MonitorEvent.MIDPOINT_UPDATE, {"midpoint": price})

    def _record_volume(self, volume: float) -> None:
        now = time.time()
        self._volume_samples.append((now, volume))
        cutoff = now - 300
        self._volume_samples = [(ts, value) for ts, value in self._volume_samples if ts >= cutoff]
        recent = [value for ts, value in self._volume_samples if ts >= now - 5]
        baseline = [value for ts, value in self._volume_samples if now - 300 <= ts < now - 5]
        if recent and baseline:
            recent_avg = sum(recent) / len(recent)
            baseline_avg = sum(baseline) / len(baseline)
            if baseline_avg > 0 and recent_avg >= baseline_avg * self.volume_spike_multiplier:
                self._emit(MonitorEvent.VOLUME_SPIKE, {"recent_avg": recent_avg, "baseline_avg": baseline_avg})
        self.state.last_trade_volume = volume

    def _handle_message(self, message: str) -> None:
        try:
            payload = json.loads(message)
        except json.JSONDecodeError:
            return

        events = payload if isinstance(payload, list) else [payload]
        for event in events:
            if not isinstance(event, dict):
                continue
            asset_id = str(event.get("asset_id") or event.get("token_id") or "")
            if asset_id and asset_id not in self.token_ids:
                continue
            if event.get("event_type") == "price_change":
                changes = event.get("price_changes") or []
                for change in changes:
                    price = change.get("price")
                    if price is not None:
                        self._record_price(float(price))
            price = event.get("price")
            if price is not None:
                self._record_price(float(price))
            size = event.get("size")
            if size is not None:
                self._record_volume(float(size))

    def _run(self) -> None:
        if websocket is None:
            return

        def on_message(_ws, message: str) -> None:
            self._handle_message(message)

        def on_open(ws) -> None:
            subscribe = {
                "assets_ids": self.token_ids,
                "type": "market",
            }
            ws.send(json.dumps(subscribe))

        while not self._stop.is_set():
            ws_app = websocket.WebSocketApp(
                WS_MARKET_URL,
                on_message=on_message,
                on_open=on_open,
            )
            ws_app.run_forever(ping_interval=20, ping_timeout=10)
            if self._stop.is_set():
                break
            time.sleep(2)

    def start(self) -> None:
        if websocket is None or self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def poll_midpoint_rest(self, fetch_price: Callable[[], float]) -> float:
        price = fetch_price()
        self._record_price(price)
        return price
