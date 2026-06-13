#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from rich.console import Console
from rich.table import Table

from polymarket_rewards.engine import EngineConfig, RewardsEngine
from polymarket_rewards.news.service import get_cached_events, refresh_news
from polymarket_rewards.risk import RiskConfig
from polymarket_rewards.scanner import RewardsScanner, ScanFilters
from polymarket_rewards.scorer import TierConfig
from polymarket_rewards.trader import RewardsTrader, TraderConfig


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Find and quote Polymarket liquidity reward opportunities.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    scan = subparsers.add_parser("scan", help="Rank markets by reward opportunity")
    scan.add_argument("--top", type=int, default=20, help="Number of markets to show")
    scan.add_argument("--min-rate", type=float, default=5.0, help="Minimum $/day reward pool")
    scan.add_argument("--min-volume", type=float, default=1000.0, help="Minimum 24h volume in USDC")
    scan.add_argument(
        "--max-competitiveness",
        type=float,
        default=5.0,
        help="Skip markets above this competitiveness score",
    )
    scan.add_argument(
        "--min-hours-to-expiry",
        type=float,
        default=3.0,
        help="Exclude markets resolving within this many hours",
    )
    scan.add_argument(
        "--include-near-expiry",
        action="store_true",
        help="Include markets close to expiry (soft penalty only)",
    )
    scan.add_argument(
        "--max-capital",
        type=float,
        default=None,
        help="Skip markets needing more capital than this budget (USD)",
    )
    scan.add_argument("--query", type=str, default=None, help="Text search on market question")
    scan.add_argument("--tag", type=str, default=None, help="Filter by tag slug")
    scan.add_argument(
        "--with-news",
        action="store_true",
        help="Enrich scan with multi-feed news risk scores and regimes",
    )
    scan.add_argument(
        "--sort",
        choices=["efficiency", "reward", "pool", "risk_adjusted", "combined"],
        default="risk_adjusted",
        help="Sort by capital efficiency, estimated reward, raw pool, risk-adjusted, or news-combined score",
    )
    scan.add_argument(
        "--format",
        choices=["table", "json", "markdown"],
        default="table",
        help="Output format (markdown/json useful for GitHub Actions)",
    )
    scan.add_argument("--output", type=str, default=None, help="Write results to a file")

    news_scan = subparsers.add_parser("news-scan", help="Rank news events linked to reward markets")
    news_scan.add_argument("--top", type=int, default=20, help="Number of events to show")
    news_scan.add_argument("--min-rate", type=float, default=5.0, help="Minimum $/day reward pool for market scan")
    news_scan.add_argument("--min-volume", type=float, default=1000.0, help="Minimum 24h volume in USDC")
    news_scan.add_argument(
        "--format",
        choices=["table", "json", "markdown"],
        default="table",
        help="Output format",
    )
    news_scan.add_argument("--output", type=str, default=None, help="Write results to a file")

    quote = subparsers.add_parser("quote", help="Place reward-qualifying quotes for one market")
    quote.add_argument("--market-id", required=True, help="Polymarket market_id from scan output")
    quote.add_argument("--live", action="store_true", help="Submit real orders (default is dry-run)")
    quote.add_argument(
        "--tiered",
        action="store_true",
        help="Use tiered capital deployment from TOTAL_CAPITAL_USD in .env",
    )

    run = subparsers.add_parser("run", help="Run the defensive LP engine loop")
    run.add_argument("--market-id", type=str, default=None, help="Market to quote continuously")
    run.add_argument(
        "--auto-pick-top",
        type=int,
        default=0,
        help="Pick Nth best risk-adjusted market when --market-id is omitted",
    )
    run.add_argument("--live", action="store_true", help="Submit real orders (default is dry-run)")
    run.add_argument("--tick-seconds", type=float, default=3.0, help="Seconds between engine ticks")

    return parser


def _format_expiry(hours: float | None) -> str:
    if hours is None:
        return "unknown"
    if hours < 0:
        return "expired"
    if hours < 24:
        return f"{hours:.1f}h"
    return f"{hours / 24:.1f}d"


def render_scan_results(results, top: int, console: Console, sort: str, *, with_news: bool = False) -> None:
    title = f"Top Polymarket Liquidity Reward Opportunities (sorted by {sort})"
    table = Table(title=title)
    table.add_column("#", justify="right")
    table.add_column("Market", max_width=36)
    table.add_column("$/day", justify="right")
    table.add_column("Comp", justify="right")
    table.add_column("Est $/day", justify="right")
    table.add_column("Capital", justify="right")
    table.add_column("Risk", justify="right")
    if with_news:
        table.add_column("News", justify="right")
        table.add_column("Regime", max_width=12)
        table.add_column("Headline", max_width=28)
    table.add_column("Expires", justify="right")
    table.add_column("Notes", max_width=20)

    for index, item in enumerate(results[:top], start=1):
        market = item.market
        flags = ",".join(item.risk_flags[:2]) if item.risk_flags else "-"
        row = [
            str(index),
            market.question[:36],
            f"{market.rate_per_day:,.0f}",
            f"{market.market_competitiveness:.2f}",
            f"{item.estimated_daily_reward:.2f}",
            f"${item.capital_required_usd:,.0f}",
            f"{item.risk_score:.0f}",
        ]
        if with_news:
            headline = item.news_headlines[0][:28] if item.news_headlines else "-"
            row.extend([
                f"{item.news_risk_score:.0f}",
                item.news_regime,
                headline,
            ])
        row.extend([
            _format_expiry(item.hours_to_expiry),
            flags,
        ])
        table.add_row(*row)

    console.print(table)
    console.print()
    console.print("Use [bold]market_id[/bold] from scan details with [bold]quote --market-id[/bold] or [bold]run --market-id[/bold].")


def render_scan_details(results, top: int, console: Console) -> None:
    detail = Table(title="Market IDs and Links")
    detail.add_column("market_id")
    detail.add_column("risk_adj")
    detail.add_column("slug")
    detail.add_column("url")

    for item in results[:top]:
        market = item.market
        detail.add_row(
            market.market_id,
            f"{item.risk_adjusted_score:.2f}",
            market.market_slug,
            market.polymarket_url,
        )

    console.print(detail)


def score_to_dict(item) -> dict:
    market = item.market
    payload = {
        "market_id": market.market_id,
        "question": market.question,
        "market_slug": market.market_slug,
        "url": market.polymarket_url,
        "rate_per_day": market.rate_per_day,
        "competitiveness": market.market_competitiveness,
        "estimated_daily_reward": item.estimated_daily_reward,
        "capital_required_usd": item.capital_required_usd,
        "reward_per_100_usd": item.reward_per_100_usd,
        "requires_two_sided": item.requires_two_sided,
        "midpoint": item.midpoint,
        "risk_score": item.risk_score,
        "risk_flags": list(item.risk_flags),
        "risk_adjusted_score": item.risk_adjusted_score,
        "combined_risk_adjusted_score": item.combined_risk_adjusted_score or item.risk_adjusted_score,
        "hours_to_expiry": item.hours_to_expiry,
        "notes": list(item.notes),
        "news_risk_score": item.news_risk_score,
        "news_regime": item.news_regime,
        "news_sentiment_lean": item.news_sentiment_lean,
        "news_headlines": list(item.news_headlines),
    }
    return payload


def _sort_key(item, sort: str, *, with_news: bool = False):
    if sort == "reward":
        return item.estimated_daily_reward
    if sort == "pool":
        return item.market.rate_per_day
    if sort == "efficiency":
        return item.opportunity_score
    if sort == "combined" or (sort == "risk_adjusted" and with_news):
        return item.combined_risk_adjusted_score or item.risk_adjusted_score
    return item.risk_adjusted_score


def render_scan_markdown(results, top: int, sort: str, *, events=None) -> str:
    with_news = any(item.news_risk_score > 0 or item.news_headlines for item in results[:top])
    lines = [
        "# Polymarket Liquidity Reward Opportunities",
        "",
        f"Sorted by **{sort}** · showing top {min(top, len(results))} markets",
        "",
    ]
    if with_news:
        lines.extend([
            "| # | Market | $/day | Comp | Est $/day | Capital | Risk | News | Regime | Headline | Expires | Flags | market_id |",
            "|---:|---|---:|---:|---:|---:|---:|---:|---|---|---:|---|---|",
        ])
    else:
        lines.extend([
            "| # | Market | $/day | Comp | Est $/day | Capital | Risk | Expires | Flags | market_id |",
            "|---:|---|---:|---:|---:|---:|---:|---:|---|---|",
        ])
    for index, item in enumerate(results[:top], start=1):
        market = item.market
        question = market.question.replace("|", "/")[:50]
        flags = ",".join(item.risk_flags[:3]) if item.risk_flags else "-"
        if with_news:
            headline = (item.news_headlines[0] if item.news_headlines else "-").replace("|", "/")[:40]
            lines.append(
                f"| {index} | {question} | {market.rate_per_day:,.0f} | "
                f"{market.market_competitiveness:.2f} | {item.estimated_daily_reward:.2f} | "
                f"${item.capital_required_usd:,.0f} | {item.risk_score:.0f} | "
                f"{item.news_risk_score:.0f} | {item.news_regime} | {headline} | "
                f"{_format_expiry(item.hours_to_expiry)} | {flags} | {market.market_id} |"
            )
        else:
            lines.append(
                f"| {index} | {question} | {market.rate_per_day:,.0f} | "
                f"{market.market_competitiveness:.2f} | {item.estimated_daily_reward:.2f} | "
                f"${item.capital_required_usd:,.0f} | {item.risk_score:.0f} | "
                f"{_format_expiry(item.hours_to_expiry)} | {flags} | {market.market_id} |"
            )

    lines.extend(["", "## Links", ""])
    for item in results[:top]:
        market = item.market
        lines.append(f"- [{market.question[:80]}]({market.polymarket_url}) (`{market.market_id}`)")

    if events:
        lines.extend(["", "## Ranked News Events", ""])
        lines.append("| # | Event | Risk | Regime | Sources | Headlines | Markets |")
        lines.append("|---:|---|---:|---|---:|---|---|")
        for index, event in enumerate(events[:top], start=1):
            label = event.label.replace("|", "/")[:40]
            headlines = "; ".join(event.top_headlines[:2]).replace("|", "/")[:60]
            markets = ", ".join(event.matched_market_ids[:3]) or "-"
            lines.append(
                f"| {index} | {label} | {event.news_risk_score:.0f} | {event.regime} | "
                f"{event.source_count} | {headlines} | {markets} |"
            )

    return "\n".join(lines) + "\n"


def write_scan_output(results, args: argparse.Namespace, *, events=None) -> None:
    if args.format == "json":
        payload = {
            "sort": args.sort,
            "count": len(results[: args.top]),
            "markets": [score_to_dict(item) for item in results[: args.top]],
        }
        if events is not None:
            payload["news_events"] = [
                {
                    "event_id": event.event_id,
                    "label": event.label,
                    "news_risk_score": event.news_risk_score,
                    "regime": event.regime,
                    "sentiment_lean": event.sentiment_lean,
                    "source_count": event.source_count,
                    "headline_count": event.headline_count,
                    "matched_market_ids": list(event.matched_market_ids),
                    "top_headlines": list(event.top_headlines),
                }
                for event in events[: args.top]
            ]
        text = json.dumps(payload, indent=2) + "\n"
    elif args.format == "markdown":
        text = render_scan_markdown(results, args.top, args.sort, events=events)
    else:
        return

    if args.output:
        Path(args.output).write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)


def cmd_scan(args: argparse.Namespace) -> int:
    console = Console() if args.format == "table" else None
    scanner = RewardsScanner()
    tier_config = TierConfig.from_env()
    max_capital = args.max_capital
    if max_capital is None and tier_config is not None:
        max_capital = tier_config.deployable_usd

    filters = ScanFilters(
        min_rate_per_day=args.min_rate,
        min_volume_24hr=args.min_volume,
        max_competitiveness=args.max_competitiveness,
        min_hours_to_expiry=args.min_hours_to_expiry,
        exclude_near_expiry=not args.include_near_expiry,
        max_capital_usd=max_capital,
        query=args.query,
        tag_slug=args.tag,
    )

    if args.format == "table":
        console.print("Fetching reward markets from Polymarket CLOB API...")
    results = scanner.scan(filters, with_news=args.with_news)
    events = get_cached_events() if args.with_news else None
    results.sort(key=lambda item: _sort_key(item, args.sort, with_news=args.with_news), reverse=True)

    if not results:
        message = "No markets matched your filters. Try lowering --min-rate or --max-competitiveness."
        if args.format == "table":
            console.print(f"[yellow]{message}[/yellow]")
        else:
            print(message, file=sys.stderr)
        return 1

    if args.format != "table":
        write_scan_output(results, args, events=events)
        return 0

    render_scan_results(results, args.top, console, args.sort, with_news=args.with_news)
    render_scan_details(results, args.top, console)
    return 0


def render_news_events(events, top: int, console: Console) -> None:
    table = Table(title="Ranked News Events")
    table.add_column("#", justify="right")
    table.add_column("Event", max_width=32)
    table.add_column("Risk", justify="right")
    table.add_column("Regime", max_width=14)
    table.add_column("Sources", justify="right")
    table.add_column("Headlines", max_width=40)
    table.add_column("Markets", max_width=16)

    for index, event in enumerate(events[:top], start=1):
        headlines = "; ".join(event.top_headlines[:2])[:40]
        markets = ", ".join(event.matched_market_ids[:2]) or "-"
        table.add_row(
            str(index),
            event.label[:32],
            f"{event.news_risk_score:.0f}",
            event.regime,
            str(event.source_count),
            headlines,
            markets,
        )
    console.print(table)


def cmd_news_scan(args: argparse.Namespace) -> int:
    console = Console() if args.format == "table" else None
    scanner = RewardsScanner()
    filters = ScanFilters(
        min_rate_per_day=args.min_rate,
        min_volume_24hr=args.min_volume,
    )
    if args.format == "table":
        console.print("Scanning markets and fetching news feeds...")
    results = scanner.scan(filters)
    events, enriched = refresh_news(results)

    if not events:
        message = "No news events matched scanned markets (RSS-only mode may have fewer matches)."
        if args.format == "table":
            console.print(f"[yellow]{message}[/yellow]")
        else:
            print(message, file=sys.stderr)
        return 1

    if args.format == "json":
        payload = {
            "count": len(events[: args.top]),
            "events": [
                {
                    "event_id": event.event_id,
                    "label": event.label,
                    "news_risk_score": event.news_risk_score,
                    "regime": event.regime,
                    "sentiment_lean": event.sentiment_lean,
                    "source_count": event.source_count,
                    "headline_count": event.headline_count,
                    "matched_market_ids": list(event.matched_market_ids),
                    "top_headlines": list(event.top_headlines),
                }
                for event in events[: args.top]
            ],
        }
        text = json.dumps(payload, indent=2) + "\n"
        if args.output:
            Path(args.output).write_text(text, encoding="utf-8")
        else:
            sys.stdout.write(text)
        return 0

    if args.format == "markdown":
        text = render_scan_markdown(enriched, args.top, "news_risk", events=events)
        if args.output:
            Path(args.output).write_text(text, encoding="utf-8")
        else:
            sys.stdout.write(text)
        return 0

    render_news_events(events, args.top, console)
    return 0


def cmd_quote(args: argparse.Namespace) -> int:
    console = Console()
    scanner = RewardsScanner()
    tier_config = TierConfig.from_env() if args.tiered else None
    if args.tiered and tier_config is None:
        console.print("[red]--tiered requires TOTAL_CAPITAL_USD in .env[/red]")
        return 1

    match = scanner.get_market_score(args.market_id, tier_config=tier_config)
    if match is None:
        console.print(f"[red]Market {args.market_id} not found among active reward markets.[/red]")
        return 1

    from dotenv import load_dotenv

    load_dotenv()
    config = TraderConfig(
        private_key=os.getenv("PRIVATE_KEY", ""),
        funder_address=os.getenv("FUNDER_ADDRESS") or None,
        signature_type=int(os.getenv("SIGNATURE_TYPE", "2")),
        dry_run=not args.live,
    )
    trader = RewardsTrader(config=config)

    orders = trader.quote_market(match, tier_config=tier_config, tiered=args.tiered)
    table = Table(title=f"Quote plan: {match.market.question}")
    table.add_column("Tier")
    table.add_column("Outcome")
    table.add_column("Side")
    table.add_column("Price", justify="right")
    table.add_column("Size", justify="right")
    table.add_column("Status")

    for order in orders:
        table.add_row(
            str(order.get("tier", "")),
            str(order["outcome"]),
            str(order["side"]),
            f"{order['price']:.3f}",
            f"{order['size']:.0f}",
            str(order["status"]),
        )

    console.print(table)
    console.print(f"Risk score: {match.risk_score:.0f} · flags: {', '.join(match.risk_flags) or 'none'}")
    if not args.live:
        console.print("[cyan]Dry run only. Re-run with --live to submit orders.[/cyan]")
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    from dotenv import load_dotenv

    load_dotenv()
    if not args.market_id and args.auto_pick_top <= 0:
        print("Provide --market-id or --auto-pick-top N", file=sys.stderr)
        return 1

    risk_config = RiskConfig.from_env(require_capital=True)
    trader_config = TraderConfig(
        private_key=os.getenv("PRIVATE_KEY", ""),
        funder_address=os.getenv("FUNDER_ADDRESS") or None,
        signature_type=int(os.getenv("SIGNATURE_TYPE", "2")),
        dry_run=not args.live,
    )
    engine = RewardsEngine(
        engine_config=EngineConfig(
            market_id=args.market_id,
            auto_pick_top=args.auto_pick_top,
            tick_seconds=args.tick_seconds,
            dry_run=not args.live,
        ),
        risk_config=risk_config,
        trader=RewardsTrader(config=trader_config),
    )
    return engine.run()


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "scan":
        return cmd_scan(args)
    if args.command == "news-scan":
        return cmd_news_scan(args)
    if args.command == "quote":
        return cmd_quote(args)
    if args.command == "run":
        return cmd_run(args)
    return 1


if __name__ == "__main__":
    sys.exit(main())
