# Polymarket Liquidity Rewards Bot

Find the best Polymarket markets for [liquidity rewards](https://help.polymarket.com/en/articles/13364466-liquidity-rewards) and optionally place qualifying limit orders.

Rewards pay makers who post resting limit orders near the midpoint. Payouts are daily (~midnight UTC), with a **$1 minimum**. See the [official methodology](https://docs.polymarket.com/market-makers/liquidity-rewards) for scoring details.

## What this bot does

1. **Scan** — Pulls all active reward markets from the [CLOB rewards API](https://docs.polymarket.com/api-reference/rewards/get-multiple-markets-with-rewards).
2. **Rank** — Scores each market by:
   - Daily reward pool (`rate_per_day`)
   - Competition (`market_competitiveness` — lower is better)
   - Capital needed (`rewards_min_size` × token prices)
   - Whether two-sided quoting is required (midpoint &lt; $0.10 or &gt; $0.90)
3. **Quote** (optional) — Suggests or submits tight two-sided bids inside the reward spread band.

## Quick start (local)

```bash
cd /Users/aashishd/Products/polymarket
python3 -m venv .venv
source .venv/bin/activate
pip install -e .

# Find best opportunities (default: $5+/day pool, competitiveness ≤ 5)
python -m polymarket_rewards.cli scan

# Looser filters for more results
python -m polymarket_rewards.cli scan --min-rate 50 --max-competitiveness 50 --top 30

# Search sports / esports
python -m polymarket_rewards.cli scan --query "Counter-Strike" --min-rate 100

# Dry-run quote plan for a market
python -m polymarket_rewards.cli quote --market-id 2523674
```

## Live trading setup

Copy `.env.example` to `.env` and fill in:

- `PRIVATE_KEY` — wallet that controls your Polymarket account
- `FUNDER_ADDRESS` — proxy wallet if using email/browser wallet login
- `SIGNATURE_TYPE` — `1` email, `2` browser wallet

```bash
cp .env.example .env
python -m polymarket_rewards.cli quote --market-id 2523674 --live
```

**Use `--live` carefully.** Resting orders can be filled; reward farming is not risk-free.

## Run on GitHub (no local machine)

Use GitHub Actions to scan markets on a schedule and view results in the Actions tab.

### Step 1 — Create a GitHub repo

```bash
cd /Users/aashishd/Products/polymarket
git init
git add .
git commit -m "Add Polymarket rewards scanner"
```

Create a new repo on GitHub (e.g. `polymarket-rewards-bot`), then:

```bash
git remote add origin https://github.com/YOUR_USERNAME/polymarket-rewards-bot.git
git branch -M main
git push -u origin main
```

### Step 2 — Enable Actions

1. Open your repo on GitHub
2. Go to **Actions**
3. If prompted, click **I understand my workflows, go ahead and enable them**
4. Open **Polymarket Rewards Scan** in the left sidebar

### Step 3 — Run manually (first test)

1. Click **Run workflow**
2. Leave defaults (`sort: reward`, `top: 15`)
3. Click the green **Run workflow** button
4. Wait ~1–2 minutes, then open the run
5. View results in the **Summary** tab (markdown table with market links)

Results are also saved as a downloadable artifact for 7 days.

### Step 4 — Automatic schedule

The workflow runs **every hour** (UTC) via cron. No laptop needed.

Edit schedule in `.github/workflows/rewards-scan.yml`:

```yaml
schedule:
  - cron: "0 * * * *"   # hourly
  # - cron: "0 */6 * * *"  # every 6 hours
```

### Step 5 — Optional Discord alerts

1. In Discord: **Server Settings → Integrations → Webhooks → New Webhook**
2. Copy the webhook URL
3. In GitHub: **Settings → Secrets and variables → Actions → New repository secret**
4. Name: `DISCORD_WEBHOOK_URL`, value: your webhook URL

Each scan will post the top markets to your Discord channel.

### What runs in the cloud

| Runs on GitHub | Does NOT run on GitHub |
|----------------|------------------------|
| Market scanning | Live order placement (`--live`) |
| Ranking / scoring | Storing your private key |
| Markdown reports | 24/7 auto-quoting |

For live trading you still need a VPS or always-on server with secrets — GitHub Actions is scan-only.

## Scoring intuition

| Signal | Why it matters |
|--------|----------------|
| High `rate_per_day` | Bigger daily pie to split |
| Low `market_competitiveness` | Fewer makers competing for share |
| Low `rewards_min_size` | Less capital locked per market |
| Wider `rewards_max_spread` | Easier to place qualifying orders |
| Midpoint in 0.10–0.90 | Single-sided quotes still score (at 1/3 rate) |

High-reward markets like World Cup winner pools ($3,000/day) often have **very high competitiveness** — most of the pool goes to professional market makers. Lower-tier esports matches with $2,000+/day and near-zero competitiveness are often better **capital efficiency** plays.

## Project layout

```
src/polymarket_rewards/
  client.py    # CLOB API client
  scorer.py    # Reward scoring + quote planning
  scanner.py   # Market discovery + filters
  trader.py    # Optional live order placement
  cli.py       # Command-line interface
```

## Disclaimer

This is educational tooling, not financial advice. Liquidity rewards do not guarantee profit — fills, resolution risk, and competition all affect outcomes. Minimum payout is $1/day per address.
