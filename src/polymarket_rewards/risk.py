from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum

from dotenv import load_dotenv

from .market_time import parse_end_date
from .scorer import MarketScore


class RiskDecision(str, Enum):
    ALLOW = "allow"
    BLOCK = "block"
    CANCEL_ALL = "cancel_all"


@dataclass(frozen=True)
class RiskConfig:
    total_capital_usd: float
    near_expiry_hours: float = 3.0
    kill_price_delta: float = 0.04
    kill_window_sec: float = 5.0
    volume_spike_multiplier: float = 3.0
    inventory_imbalance_pct: float = 0.30
    max_open_markets: int = 1
    kill_cooldown_sec: float = 60.0
    tier_active_pct: float = 0.15
    tier_buffer_pct: float = 0.25

    @property
    def deployable_usd(self) -> float:
        return self.total_capital_usd * (self.tier_active_pct + self.tier_buffer_pct)

    @classmethod
    def from_env(cls, *, require_capital: bool = True) -> RiskConfig:
        load_dotenv()
        raw_capital = os.getenv("TOTAL_CAPITAL_USD", "").strip()
        if not raw_capital:
            if require_capital:
                raise RuntimeError("TOTAL_CAPITAL_USD is required in .env for run/tiered quoting")
            return cls(total_capital_usd=0.0)
        return cls(
            total_capital_usd=float(raw_capital),
            near_expiry_hours=float(os.getenv("NEAR_EXPIRY_HOURS", "3")),
            kill_price_delta=float(os.getenv("KILL_SWITCH_PRICE_DELTA", "0.04")),
            kill_window_sec=float(os.getenv("KILL_SWITCH_WINDOW_SEC", "5")),
            volume_spike_multiplier=float(os.getenv("VOLUME_SPIKE_MULTIPLIER", "3.0")),
            inventory_imbalance_pct=float(os.getenv("INVENTORY_IMBALANCE_PCT", "0.30")),
            max_open_markets=int(os.getenv("MAX_OPEN_MARKETS", "1")),
            kill_cooldown_sec=float(os.getenv("KILL_COOLDOWN_SEC", "60")),
            tier_active_pct=float(os.getenv("TIER_ACTIVE_PCT", "0.15")),
            tier_buffer_pct=float(os.getenv("TIER_BUFFER_PCT", "0.25")),
        )


@dataclass
class PriceWindow:
    samples: list[tuple[datetime, float]]

    def add(self, price: float, *, now: datetime | None = None) -> None:
        now = now or datetime.now(timezone.utc)
        self.samples.append((now, price))
        cutoff = now - timedelta(seconds=300)
        self.samples = [(ts, value) for ts, value in self.samples if ts >= cutoff]

    def delta_within(self, seconds: float, *, now: datetime | None = None) -> float | None:
        if not self.samples:
            return None
        now = now or datetime.now(timezone.utc)
        cutoff = now - timedelta(seconds=seconds)
        window = [value for ts, value in self.samples if ts >= cutoff]
        if len(window) < 2:
            return None
        return max(window) - min(window)


@dataclass
class VolumeWindow:
    samples: list[tuple[datetime, float]]

    def add(self, volume: float, *, now: datetime | None = None) -> None:
        now = now or datetime.now(timezone.utc)
        self.samples.append((now, volume))
        cutoff = now - timedelta(seconds=300)
        self.samples = [(ts, value) for ts, value in self.samples if ts >= cutoff]

    def spike_detected(self, multiplier: float, *, now: datetime | None = None) -> bool:
        if len(self.samples) < 3:
            return False
        now = now or datetime.now(timezone.utc)
        recent_cutoff = now - timedelta(seconds=5)
        baseline_cutoff = now - timedelta(seconds=300)
        recent = [value for ts, value in self.samples if ts >= recent_cutoff]
        baseline = [value for ts, value in self.samples if baseline_cutoff <= ts < recent_cutoff]
        if not recent or not baseline:
            return False
        recent_avg = sum(recent) / len(recent)
        baseline_avg = sum(baseline) / len(baseline)
        if baseline_avg <= 0:
            return False
        return recent_avg >= baseline_avg * multiplier


class RiskEngine:
    def __init__(self, config: RiskConfig) -> None:
        self.config = config
        self.price_window = PriceWindow(samples=[])
        self.volume_window = VolumeWindow(samples=[])
        self.kill_until: datetime | None = None

    def record_price(self, price: float, *, now: datetime | None = None) -> None:
        self.price_window.add(price, now=now)

    def record_volume(self, volume: float, *, now: datetime | None = None) -> None:
        self.volume_window.add(volume, now=now)

    def trigger_kill_switch(self, *, now: datetime | None = None) -> None:
        now = now or datetime.now(timezone.utc)
        self.kill_until = now + timedelta(seconds=self.config.kill_cooldown_sec)

    def in_cooldown(self, *, now: datetime | None = None) -> bool:
        if self.kill_until is None:
            return False
        now = now or datetime.now(timezone.utc)
        return now < self.kill_until

    def check_expiry(self, score: MarketScore, *, now: datetime | None = None) -> RiskDecision:
        now = now or datetime.now(timezone.utc)
        end = parse_end_date(score.market.end_date)
        if end is None:
            return RiskDecision.ALLOW
        if now >= end - timedelta(hours=self.config.near_expiry_hours):
            return RiskDecision.CANCEL_ALL
        return RiskDecision.ALLOW

    def check_volatility(self, *, now: datetime | None = None) -> RiskDecision:
        now = now or datetime.now(timezone.utc)
        if self.in_cooldown(now=now):
            return RiskDecision.BLOCK
        delta = self.price_window.delta_within(self.config.kill_window_sec, now=now)
        if delta is not None and delta >= self.config.kill_price_delta:
            self.trigger_kill_switch(now=now)
            return RiskDecision.CANCEL_ALL
        if self.volume_window.spike_detected(self.config.volume_spike_multiplier, now=now):
            self.trigger_kill_switch(now=now)
            return RiskDecision.CANCEL_ALL
        return RiskDecision.ALLOW

    def check_capital(self, planned_notional_usd: float) -> RiskDecision:
        if planned_notional_usd > self.config.deployable_usd:
            return RiskDecision.BLOCK
        return RiskDecision.ALLOW

    def overweight_side(
        self,
        yes_balance: float,
        no_balance: float,
    ) -> str | None:
        total = yes_balance + no_balance
        if total <= 0:
            return None
        yes_share = yes_balance / total
        threshold = self.config.inventory_imbalance_pct / 2
        if yes_share > 0.5 + threshold:
            return "yes"
        if yes_share < 0.5 - threshold:
            return "no"
        return None

    def evaluate_tick(
        self,
        score: MarketScore,
        *,
        planned_notional_usd: float,
        now: datetime | None = None,
    ) -> RiskDecision:
        expiry = self.check_expiry(score, now=now)
        if expiry == RiskDecision.CANCEL_ALL:
            return expiry
        vol = self.check_volatility(now=now)
        if vol != RiskDecision.ALLOW:
            return vol
        capital = self.check_capital(planned_notional_usd)
        if capital != RiskDecision.ALLOW:
            return capital
        if score.hours_to_expiry is not None and score.hours_to_expiry < self.config.near_expiry_hours:
            return RiskDecision.CANCEL_ALL
        return RiskDecision.ALLOW
