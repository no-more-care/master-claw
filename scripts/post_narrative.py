#!/usr/bin/env python3
"""
MasterClaw Narrative Webhook Poster

Posts narrative text to a Discord channel via webhook URL (no bot token needed).
Webhooks bypass microClaw's chat permission model entirely — direct HTTP POST to Discord.

Usage:
  python3 post_narrative.py <webhook_url> <text>
  cat text.md | python3 post_narrative.py <webhook_url> -

The webhook URL is created in Discord: right-click channel → Integrations → Webhooks → New.
Store the URL in game.md as `narrative_webhook` for the current game.

Exit codes:
  0 — posted successfully
  1 — argument error
  2 — HTTP error
"""

import sys
import json
import urllib.request
import urllib.error


DISCORD_MAX_MESSAGE = 2000  # Discord per-message character limit


def post_to_webhook(webhook_url: str, text: str) -> tuple[bool, str]:
    """POST a message to Discord webhook. Returns (ok, message)."""
    if not text.strip():
        return False, "Empty text, nothing to post"

    # Split long messages at paragraph or sentence boundaries
    chunks = []
    remaining = text
    while len(remaining) > DISCORD_MAX_MESSAGE:
        # Find last paragraph break before limit
        split_at = remaining.rfind("\n\n", 0, DISCORD_MAX_MESSAGE)
        if split_at < DISCORD_MAX_MESSAGE // 2:
            # No good paragraph break, try single newline
            split_at = remaining.rfind("\n", 0, DISCORD_MAX_MESSAGE)
        if split_at < DISCORD_MAX_MESSAGE // 2:
            # No newline, split at last space
            split_at = remaining.rfind(" ", 0, DISCORD_MAX_MESSAGE)
        if split_at < DISCORD_MAX_MESSAGE // 2:
            # Hard split
            split_at = DISCORD_MAX_MESSAGE
        chunks.append(remaining[:split_at].rstrip())
        remaining = remaining[split_at:].lstrip()
    chunks.append(remaining)

    for i, chunk in enumerate(chunks):
        payload = {"content": chunk}
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            webhook_url,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                if resp.status not in (200, 204):
                    return False, f"Part {i+1}: HTTP {resp.status}"
        except urllib.error.HTTPError as e:
            return False, f"Part {i+1}: HTTPError {e.code}: {e.reason}"
        except urllib.error.URLError as e:
            return False, f"Part {i+1}: URLError: {e.reason}"

    return True, f"Posted {len(chunks)} message(s)"


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)

    webhook_url = sys.argv[1]
    text_arg = sys.argv[2]

    if not webhook_url.startswith("https://discord.com/api/webhooks/") and not webhook_url.startswith("https://discordapp.com/api/webhooks/"):
        print("Error: webhook URL must start with https://discord.com/api/webhooks/")
        sys.exit(1)

    if text_arg == "-":
        text = sys.stdin.read()
    else:
        text = text_arg

    ok, message = post_to_webhook(webhook_url, text)
    if ok:
        print(message)
        sys.exit(0)
    else:
        print(f"Error: {message}", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
