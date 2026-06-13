#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from rich.console import Console
from rich.table import Table

from polymarket_rewards.scanner import RewardsScanner, ScanFilters
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
    scan.add_argument("--query", type=str, default=None, help="Text search on market question")
    scan.add_argument("--tag", type=str, default=None, help="Filter by tag slug")
    scan.add_argument(
        "--sort",
        choices=["efficiency", "reward", "pool"],
        default="efficiency",
        help="Sort by capital efficiency, estimated reward, or raw pool size",
    )
    scan.add_argument(
        "--format",
        choices=["table", "json", "markdown"],
        default="table",
        help="Output format (markdown/json useful for GitHub Actions)",
    )
    scan.add_argument("--output", type=str, default=None, help="Write results to a file")

    quote = subparsers.add_parser("quote", help="Place reward-qualifying quotes for one market")
    quote.add_argument("--market-id", required=True, help="Polymarket market_id from scan output")
    quote.add_argument("--live", action="store_true", help="Submit real orders (default is dry-run)")

    return parser


def render_scan_results(results, top: int, console: Console, sort: str) -> None:
    table = Table(title=f"Top Polymarket Liquidity Reward Opportunities (sorted by {sort})")
    table.add_column("#", justify="right")
    table.add_column("Market", max_width=48)
    table.add_column("$/day", justify="right")
    table.add_column("Comp", justify="right")
    table.add_column("Est $/day", justify="right")
    table.add_column("Capital", justify="right")
    table.add_column("$ / $100", justify="right")
    table.add_column("Notes", max_width=28)

    for index, item in enumerate(results[:top], start=1):
        market = item.market
        table.add_row(
            str(index),
            market.question[:48],
            f"{market.rate_per_day:,.0f}",
            f"{market.market_competitiveness:.2f}",
            f"{item.estimated_daily_reward:.2f}",
            f"${item.capital_required_usd:,.0f}",
            f"{item.reward_per_100_usd:.2f}",
            "; ".join(item.notes),
        )

    console.print(table)
    console.print()
    console.print("Use [bold]market_id[/bold] from scan details with [bold]quote --market-id[/bold].")


def render_scan_details(results, top: int, console: Console) -> None:
    detail = Table(title="Market IDs and Links")
    detail.add_column("market_id")
    detail.add_column("slug")
    detail.add_column("url")

    for item in results[:top]:
        market = item.market
        detail.add_row(market.market_id, market.market_slug, market.polymarket_url)

    console.print(detail)


def score_to_dict(item) -> dict:
    market = item.market
    return {
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
        "notes": list(item.notes),
    }


def render_scan_markdown(results, top: int, sort: str) -> str:
    lines = [
        "# Polymarket Liquidity Reward Opportunities",
        "",
        f"Sorted by **{sort}** · showing top {min(top, len(results))} markets",
        "",
        "| # | Market | $/day | Comp | Est $/day | Capital | $/100 | market_id |",
        "|---:|---|---:|---:|---:|---:|---:|---|",
    ]
    for index, item in enumerate(results[:top], start=1):
        market = item.market
        question = market.question.replace("|", "/")[:60]
        lines.append(
            f"| {index} | {question} | {market.rate_per_day:,.0f} | "
            f"{market.market_competitiveness:.2f} | {item.estimated_daily_reward:.2f} | "
            f"${item.capital_required_usd:,.0f} | {item.reward_per_100_usd:.2f} | {market.market_id} |"
        )

    lines.extend(["", "## Links", ""])
    for item in results[:top]:
        market = item.market
        lines.append(f"- [{market.question[:80]}]({market.polymarket_url}) (`{market.market_id}`)")

    return "\n".join(lines) + "\n"


def write_scan_output(results, args: argparse.Namespace) -> None:
    if args.format == "json":
        payload = {
            "sort": args.sort,
            "count": len(results[: args.top]),
            "markets": [score_to_dict(item) for item in results[: args.top]],
        }
        text = json.dumps(payload, indent=2) + "\n"
    elif args.format == "markdown":
        text = render_scan_markdown(results, args.top, args.sort)
    else:
        return

    if args.output:
        Path(args.output).write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)


def cmd_scan(args: argparse.Namespace) -> int:
    console = Console() if args.format == "table" else None
    scanner = RewardsScanner()
    filters = ScanFilters(
        min_rate_per_day=args.min_rate,
        min_volume_24hr=args.min_volume,
        max_competitiveness=args.max_competitiveness,
        query=args.query,
        tag_slug=args.tag,
    )

    if args.format == "table":
        console.print("Fetching reward markets from Polymarket CLOB API...")
    results = scanner.scan(filters)
    if args.sort == "reward":
        results.sort(key=lambda item: item.estimated_daily_reward, reverse=True)
    elif args.sort == "pool":
        results.sort(key=lambda item: item.market.rate_per_day, reverse=True)
    if not results:
        message = "No markets matched your filters. Try lowering --min-rate or --max-competitiveness."
        if args.format == "table":
            console.print(f"[yellow]{message}[/yellow]")
        else:
            print(message, file=sys.stderr)
        return 1

    if args.format != "table":
        write_scan_output(results, args)
        return 0

    render_scan_results(results, args.top, console, args.sort)
    render_scan_details(results, args.top, console)
    return 0


def cmd_quote(args: argparse.Namespace) -> int:
    console = Console()
    scanner = RewardsScanner()
    match = scanner.get_market_score(args.market_id)
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

    orders = trader.quote_market(match)
    table = Table(title=f"Quote plan: {match.market.question}")
    table.add_column("Outcome")
    table.add_column("Side")
    table.add_column("Price", justify="right")
    table.add_column("Size", justify="right")
    table.add_column("Status")

    for order in orders:
        table.add_row(
            str(order["outcome"]),
            str(order["side"]),
            f"{order['price']:.3f}",
            f"{order['size']:.0f}",
            str(order["status"]),
        )

    console.print(table)
    if not args.live:
        console.print("[cyan]Dry run only. Re-run with --live to submit orders.[/cyan]")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "scan":
        return cmd_scan(args)
    if args.command == "quote":
        return cmd_quote(args)
    return 1


if __name__ == "__main__":
    sys.exit(main())
