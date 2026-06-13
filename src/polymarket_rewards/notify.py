from __future__ import annotations

import os
import sys
from pathlib import Path

import requests


def send_discord_message(webhook_url: str, content: str) -> None:
    # Discord limit is 2000 chars per message.
    chunks = [content[i : i + 1900] for i in range(0, len(content), 1900)] or [content]
    for chunk in chunks:
        response = requests.post(webhook_url, json={"content": chunk}, timeout=30)
        response.raise_for_status()


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    if not argv:
        print("Usage: python -m polymarket_rewards.notify <markdown-file>", file=sys.stderr)
        return 1

    webhook_url = os.getenv("DISCORD_WEBHOOK_URL", "").strip()
    if not webhook_url:
        print("DISCORD_WEBHOOK_URL is not set", file=sys.stderr)
        return 1

    content = Path(argv[0]).read_text(encoding="utf-8").strip()
    if not content:
        print("Scan file is empty", file=sys.stderr)
        return 1

    send_discord_message(webhook_url, f"**Polymarket Rewards Scan**\n\n{content}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
