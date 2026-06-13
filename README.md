# Polymarket Liquidity Rewards Bot

Find the best Polymarket markets for [liquidity rewards](https://help.polymarket.com/en/articles/13364466-liquidity-rewards) and optionally run a **defensive** liquidity-providing engine that prioritizes loss minimization over raw yield.

Rewards pay makers who post resting limit orders near the midpoint. Payouts are daily (~midnight UTC), with a **$1 minimum**. See the [official methodology](https://docs.polymarket.com/market-makers/liquidity-rewards) for scoring details.

## What this bot does

1. **Scan** — Pulls active reward markets and ranks them with **risk-adjusted scoring** (expiry, midpoint, competition, capital fit).
2. **Quote** — Builds **tiered, asymmetric** quote plans inside the reward band.
3. **Run** — Continuous local daemon with kill switch, expiry guard, inventory skew, and cancel-first execution.

**Design principle:** missing 30 minutes of rewards is better than a 100→0 wipeout on news-driven markets.

## Quick start (local)

```bash
cd /Users/aashishd/Products/polymarket
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

# Risk-adjusted scan (excludes markets resolving within 3h by default)
python -m polymarket_rewards.cli scan

# Tiered dry-run quote plan
cp .env.example .env   # set TOTAL_CAPITAL_USD
python -m polymarket_rewards.cli quote --market-id 2523674 --tiered

# Defensive engine (dry-run loop)
python -m polymarket_rewards.cli run --auto-pick-top 1

# Live engine (requires PRIVATE_KEY + TOTAL_CAPITAL_USD)
python -m polymarket_rewards.cli run --market-id 2523674 --live
```

## Defensive architecture

Each engine tick follows this sequence:

```
Check expiry (< 3h)     → cancel all, stop quoting
Check volatility spike  → emergency cancel + cooldown
Check inventory skew    → widen/pause buys on overweight side
Rebuild tiered quotes   → active (15%) + buffer (25%) + reserve (60%)
Replace orders          → cancel stale, place new
```

### Loss controls

| Layer | Mechanism |
|-------|-----------|
| Selection | Exclude near-expiry markets; penalize extreme midpoints |
| Sizing | Tiered capital — never deploy 100% at once |
| Quoting | Asymmetric spreads based on momentum + inventory |
| Monitoring | WebSocket/REST price & volume kill switch |
| Time | Hard stop before resolution |
| Execution | Cancel-first replace; cooldown after kill switch |

## Configuration

Copy `.env.example` to `.env`:

| Variable | Purpose |
|----------|---------|
| `TOTAL_CAPITAL_USD` | **Required** for `run` / `--tiered` (no default) |
| `PRIVATE_KEY` | Required for live orders |
| `NEAR_EXPIRY_HOURS` | Stop quoting N hours before resolution (default 3) |
| `KILL_SWITCH_PRICE_DELTA` | Cancel if price moves this much in window (default 0.04) |
| `TIER_ACTIVE_PCT` / `TIER_BUFFER_PCT` | Capital tiers (default 15% / 25%) |
| `INVENTORY_IMBALANCE_PCT` | Pause buys when one side exceeds this share |

## Run on GitHub (scan only)

GitHub Actions scans hourly with risk filters and publishes markdown results. Live trading and the `run` daemon require a local machine or VPS.

```bash
# Manual workflow run uses risk_adjusted sort and min-hours-to-expiry 3
```

Optional Discord alerts: set `DISCORD_WEBHOOK_URL` repository secret.

## Project layout

```
src/polymarket_rewards/
  client.py         # CLOB + Gamma API
  market_time.py    # Expiry parsing
  scorer.py         # Risk-adjusted scoring + tiered quote plans
  scanner.py        # Discovery + filters
  risk.py           # Kill switch, expiry guard, capital/inventory checks
  trader.py         # Order placement, cancel, balances
  order_manager.py  # Cancel-first replace logic
  ws_monitor.py     # WebSocket price/volume monitor
  engine.py         # Defensive LP loop
  cli.py            # scan | quote | run
tests/
  test_scorer.py
  test_risk.py
```

## Disclaimer

This is educational tooling, not financial advice. Liquidity rewards do not guarantee profit — fills, resolution risk, and competition all affect outcomes. Minimum payout is $1/day per address.
