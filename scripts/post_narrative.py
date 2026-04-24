#!/usr/bin/env python3
"""
MasterClaw Narrative Webhook Poster

Posts narrative text to a Discord channel via webhook URL (no bot token needed).
Webhooks bypass microClaw's chat permission model entirely — direct HTTP POST to Discord.

Usage:
  python3 post_narrative.py <webhook_url> <text> [flags]
  cat text.md | python3 post_narrative.py <webhook_url> - [flags]

Optional style budget check (soft — warns, does not block by default):
  --style <preset>      Expected narrative_style preset (documentary|concise|gamemaster|narrative|noir|horror|custom)
  --max-words <N>       Soft word budget for the whole text. Overrides preset default if both given.
                        If text exceeds the budget, prints a warning to stderr. Posts anyway.

Optional language lint (blocks on warnings unless --force):
  --lint-lang <code>    Expected game language code (e.g. ru). Runs scripts/lint_lang.py on the draft.
                        If warnings found, prints them to stderr and exits 2 WITHOUT posting.
  --force               Bypass --lint-lang refusal (use when suspect tokens are known proper nouns).

The webhook URL is created in Discord: right-click channel → Integrations → Webhooks → New.
Store the URL in game.md as `narrative_webhook` for the current game.

Exit codes:
  0 — posted successfully
  1 — argument error
  2 — HTTP error, lint refusal, or blocking pre-flight failure
"""

import sys
import json
import os
import subprocess
import urllib.request
import urllib.error


DISCORD_MAX_MESSAGE = 2000  # Discord per-message character limit

# Per-preset soft word budgets for the ENTIRE narrative block (sum across chunks).
# These are lenient — they sum a per-action and a scene-change allowance together
# so a narrative post that does both in one chunk still fits. Tighter per-block
# enforcement is the agent's job (see skills/narrator/SKILL.md section 9).
PRESET_BUDGETS = {
    "documentary": 30,
    "concise":     60,
    "gamemaster":  150,
    "narrative":   300,
    "noir":        150,
    "horror":      150,
    "custom":      300,  # permissive default; custom styles override
}


def count_words(text: str) -> int:
    """Whitespace-separated token count. Good enough for a soft budget check."""
    return len([t for t in text.split() if t.strip()])


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
            headers={
                "Content-Type": "application/json",
                # Discord requires a proper User-Agent; default Python-urllib is blocked by Cloudflare
                "User-Agent": "MasterClaw-Narrator/1.0 (+https://github.com/no-more-care/master-claw)",
            },
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


def parse_args(argv: list[str]) -> dict:
    """Positional: webhook_url, text_arg. Remaining: --flag value pairs."""
    if len(argv) < 3:
        return {}
    opts = {
        "webhook_url": argv[1],
        "text_arg":    argv[2],
        "style":       None,
        "max_words":   None,
        "lint_lang":   None,
        "force":       False,
    }
    i = 3
    while i < len(argv):
        flag = argv[i]
        if flag == "--force":
            opts["force"] = True
            i += 1
        elif flag in ("--style", "--max-words", "--lint-lang"):
            if i + 1 >= len(argv):
                print(f"Error: {flag} requires a value", file=sys.stderr)
                sys.exit(1)
            key = flag[2:].replace("-", "_")
            opts[key] = argv[i + 1]
            i += 2
        else:
            print(f"Error: unknown flag {flag}", file=sys.stderr)
            sys.exit(1)
    return opts


def check_style_budget(text: str, style: str | None, max_words: str | None) -> None:
    """Print a stderr warning if the text exceeds its soft budget. Never blocks."""
    limit = None
    if max_words is not None:
        try:
            limit = int(max_words)
        except ValueError:
            print(f"Warning: --max-words '{max_words}' is not an integer; ignoring", file=sys.stderr)
            return
    elif style is not None:
        limit = PRESET_BUDGETS.get(style)
        if limit is None:
            print(f"Warning: unknown --style '{style}'; skipping budget check", file=sys.stderr)
            return
    else:
        return

    words = count_words(text)
    if words > limit:
        print(
            f"Warning: narrative is {words} words; soft budget for "
            f"{style or 'custom'} is {limit}. Consider tightening. (posting anyway — agent's call)",
            file=sys.stderr,
        )


def run_lang_lint(text: str, lang: str, force: bool) -> bool:
    """Invoke scripts/lint_lang.py. Return True if OK to post, False if blocked."""
    lint_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "lint_lang.py")
    if not os.path.exists(lint_path):
        print(f"Warning: --lint-lang set but {lint_path} missing; skipping lint", file=sys.stderr)
        return True
    try:
        result = subprocess.run(
            [sys.executable, lint_path, "--lang", lang],
            input=text,
            text=True,
            capture_output=True,
            timeout=10,
        )
    except subprocess.TimeoutExpired:
        print("Warning: lint_lang.py timed out; skipping lint", file=sys.stderr)
        return True

    if result.returncode == 0:
        return True

    # lint_lang returned non-zero → warnings found
    print("Language lint warnings:", file=sys.stderr)
    print(result.stdout, file=sys.stderr)
    if result.stderr:
        print(result.stderr, file=sys.stderr)
    if force:
        print("(proceeding due to --force)", file=sys.stderr)
        return True
    print("Refusing to post. Rewrite the text, or re-run with --force if tokens are proper nouns.", file=sys.stderr)
    return False


def main():
    opts = parse_args(sys.argv)
    if not opts:
        print(__doc__)
        sys.exit(1)

    webhook_url = opts["webhook_url"]
    text_arg = opts["text_arg"]

    if not webhook_url.startswith("https://discord.com/api/webhooks/") and not webhook_url.startswith("https://discordapp.com/api/webhooks/"):
        print("Error: webhook URL must start with https://discord.com/api/webhooks/", file=sys.stderr)
        sys.exit(1)

    if text_arg == "-":
        text = sys.stdin.read()
    else:
        text = text_arg

    # Pre-flight: soft style budget check (never blocks)
    check_style_budget(text, opts["style"], opts["max_words"])

    # Pre-flight: language lint (blocks unless --force)
    if opts["lint_lang"]:
        if not run_lang_lint(text, opts["lint_lang"], opts["force"]):
            sys.exit(2)

    ok, message = post_to_webhook(webhook_url, text)
    if ok:
        print(message)
        sys.exit(0)
    else:
        print(f"Error: {message}", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
