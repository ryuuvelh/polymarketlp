# Polymarket Liquidity Rewards Bot

Find the best Polymarket markets for [liquidity rewards](https://help.polymarket.com/en/articles/13364466-liquidity-rewards) and optionally run a **defensive** liquidity-providing engine that prioritizes loss minimization over raw yield.

Rewards pay makers who post resting limit orders near the midpoint. Payouts are daily (~midnight UTC), with a **$1 minimum**. See the [official methodology](https://docs.polymarket.com/market-makers/liquidity-rewards) for scoring details.

## What this bot does

1. **Scan** — Pulls active reward markets and ranks them with **risk-adjusted scoring** (expiry, midpoint, competition, capital fit). Use `--with-news` for multi-feed news risk and regime columns.
2. **News scan** — Clusters headlines from 5 free feeds, ranks events, and maps them to Polymarket markets.
3. **Quote** — Builds **tiered, asymmetric** quote plans inside the reward band.
4. **Run** — Continuous local daemon with news-aware position regimes, kill switch, expiry guard, inventory skew, and cancel-first execution.

**Design principle:** missing 30 minutes of rewards is better than a 100→0 wipeout on news-driven markets.

## News feeds and position regimes

Five free sources (RSS works without API keys):

| Source | Key env var | Free tier |
|--------|-------------|-----------|
| Google News RSS | — | Unmetered |
| Currents API | `CURRENTS_API_KEY` | ~600/day |
| NewsAPI.org | `NEWSAPI_KEY` | 100/day |
| GNews | `GNEWS_API_KEY` | 100/day |
| Finnhub | `FINNHUB_API_KEY` | 60/min |

Headlines are deduped, clustered by keyword overlap, and scored into a **news_risk_score (0–100)**. The engine picks a **position regime** automatically:

| News risk | Regime | Behavior |
|-----------|--------|----------|
| 0–25 | `full_lp` | Active + buffer tiers, balanced sizing |
| 25–50 | `buffer_only` | Buffer only, join-not-lead |
| 50–75 | `minimal` | Half buffer, widen news-vulnerable side |
| 75–90 | `flat` | Cancel all until news cools |
| 90–100 / near expiry + news | `flat_extended` | Flat + extended cooldown |

```bash
# Scan with news columns (News, Regime, Headline)
python -m polymarket_rewards.cli scan --with-news

# Ranked news events only
python -m polymarket_rewards.cli news-scan --top 20
```

**Rate limits:** Local `run` uses a shared 5-minute cache (`NEWS_CACHE_TTL_SEC`). GitHub Actions runs once per hour (~24 fetches/day), well within free tiers. Without API keys, RSS-only mode still works but multi-source agreement scores are lower.

**Limitations:** Free news APIs cannot reliably predict moves before price; sentiment is keyword-based (good for skew, not certainty).

## Quick start (local)

```bash
cd /Users/aashishd/Products/polymarket
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

# Risk-adjusted scan (excludes markets resolving within 3h by default)
python -m polymarket_rewards.cli scan

# Scan with multi-feed news risk and regimes
python -m polymarket_rewards.cli scan --with-news

# Ranked news events linked to reward markets
python -m polymarket_rewards.cli news-scan --top 20

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
| `NEWSAPI_KEY` / `GNEWS_API_KEY` / `CURRENTS_API_KEY` / `FINNHUB_API_KEY` | Optional news feed keys |
| `NEWS_CACHE_TTL_SEC` | News cache TTL in seconds (default 300) |

## Run on GitHub (scan only)

GitHub Actions scans hourly with `--with-news`, risk filters, and publishes markdown results (including News, Regime, Headline columns and a Ranked News Events section). Live trading and the `run` daemon require a local machine or VPS.

Optional repository secrets for richer news ranking: `NEWSAPI_KEY`, `GNEWS_API_KEY`, `CURRENTS_API_KEY`, `FINNHUB_API_KEY`. RSS-only mode works without any news secrets.

Optional Discord alerts: set `DISCORD_WEBHOOK_URL` repository secret.

## Project layout

```
src/polymarket_rewards/
  client.py         # CLOB + Gamma API
  market_time.py    # Expiry parsing
  scorer.py         # Risk-adjusted scoring + tiered quote plans
  scanner.py        # Discovery + filters
  news/             # Multi-feed aggregation, clustering, ranking
  position_regime.py # News-driven LP regimes
  risk.py           # Kill switch, expiry guard, capital/inventory checks
  trader.py         # Order placement, cancel, balances
  order_manager.py  # Cancel-first replace logic
  ws_monitor.py     # WebSocket price/volume monitor
  engine.py         # Defensive LP loop with news regimes
  cli.py            # scan | news-scan | quote | run
tests/
  test_scorer.py
  test_risk.py
  test_news_ranker.py
  test_position_regime.py
```

## Disclaimer

This is educational tooling, not financial advice. Liquidity rewards do not guarantee profit — fills, resolution risk, and competition all affect outcomes. Minimum payout is $1/day per address.
