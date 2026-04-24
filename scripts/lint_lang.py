#!/usr/bin/env python3
"""
MasterClaw language lint — scans prose for foreign-language leakage.

Use case: when writing Russian narrative, catch English words that slipped in
("grew around them", "weapon shop", "phase: approaching Krækhol").

Reads text from stdin. Prints a JSON report to stdout. Exits 0 if clean, 1 if
warnings found. Designed to be called from post_narrative.py (see --lint-lang).

Usage:
  cat text.md | python3 lint_lang.py --lang ru
  python3 lint_lang.py --lang ru --allow allow.txt < text.md

The scanner is a heuristic, not a translator. It flags candidate words; the
caller decides whether to rewrite or to add proper nouns to the allow-list.

Output (stdout, JSON):
  {
    "lang": "ru",
    "count": N,
    "warnings": [ {"word": "...", "context": "..."}, ... ]
  }

Exit codes:
  0 — clean (no suspects)
  1 — suspects found (caller decides to block / continue)
  2 — argument error
"""

import sys
import json
import re
import os


# Builtin allow-list of technical tokens that commonly appear in prose without
# being a leak (script names, schema keys, units, acronyms). Extend per-project
# via --allow <file>.
DEFAULT_ALLOW = {
    # File paths / script names mentioned in GM instructions
    "post_narrative", "lint_lang", "session_snapshot", "roll", "manage_models", "manage_channels",
    # Schema keys
    "player", "traits", "reserve", "aspects", "flags",
    # Acronyms
    "GM", "NPC", "YAML", "JSON", "API", "URL", "ID", "XP", "HP",
    # Common technical nouns
    "Discord", "webhook",
}

# Languages we can lint. The alphabet is the "native" script of the language;
# words in other scripts with 3+ letters are candidates for flagging.
LANG_NATIVE_RANGES = {
    # Russian / Ukrainian / etc — Cyrillic
    "ru": [(0x0400, 0x04FF)],
    "uk": [(0x0400, 0x04FF)],
    "be": [(0x0400, 0x04FF)],
}


def load_allow_file(path: str) -> set[str]:
    """Allow-list format: one token per line, # comments allowed, blank lines ignored."""
    allow = set()
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                allow.add(line)
    except FileNotFoundError:
        print(f"Warning: allow file {path} not found, using defaults only", file=sys.stderr)
    return allow


def is_native_script(ch: str, lang: str) -> bool:
    ranges = LANG_NATIVE_RANGES.get(lang)
    if not ranges:
        return False
    code = ord(ch)
    for lo, hi in ranges:
        if lo <= code <= hi:
            return True
    return False


def scan(text: str, lang: str, allow: set[str]) -> list[dict]:
    """Return list of {word, context} suspects.

    Heuristic for `ru` (and similar):
    - Language is Cyrillic-native.
    - Any ASCII word of 3+ letters embedded in prose is a SUSPECT.
    - Skip:
      * tokens inside code fences or inline backticks
      * tokens that look like URLs or paths
      * tokens in the allow-list
      * tokens adjacent to digits (config keys like `size:64`)

    Lines ENTIRELY made of ASCII (no Cyrillic at all) are considered "code-like"
    and skipped — this covers YAML headers, bash commands, etc. which may appear
    in internal reasoning but not in the narrative block the agent posts.
    """
    if lang not in LANG_NATIVE_RANGES:
        # Unknown language — can't lint; return no warnings.
        return []

    warnings = []

    # Strip fenced code blocks
    text_clean = re.sub(r"```.*?```", "", text, flags=re.DOTALL)
    # Strip inline backticks
    text_clean = re.sub(r"`[^`]*`", "", text_clean)

    ascii_word_re = re.compile(r"[A-Za-z]{3,}")

    for line in text_clean.splitlines():
        # If the line contains NO native-script characters, treat as non-prose and skip.
        if not any(is_native_script(c, lang) for c in line):
            continue

        for match in ascii_word_re.finditer(line):
            word = match.group(0)

            # Allow-list (case-insensitive match, preserving case in report)
            if word in allow or word.lower() in {a.lower() for a in allow}:
                continue

            # Skip if word is part of a URL or path (has / or . adjacent)
            start, end = match.span()
            before = line[max(0, start - 1):start]
            after = line[end:end + 1]
            if before in ("/", ".", ":", "@", "-", "_") or after in ("/", ".", ":", "_", "-"):
                continue

            # Skip if adjacent to a digit (e.g. "size64", "day2")
            if before.isdigit() or after.isdigit():
                continue

            # Capture 40-char window for context
            ctx_start = max(0, start - 20)
            ctx_end = min(len(line), end + 20)
            context = line[ctx_start:ctx_end].strip()

            warnings.append({"word": word, "context": context})

    return warnings


def parse_args(argv: list[str]) -> dict:
    opts = {"lang": None, "allow_file": None}
    i = 1
    while i < len(argv):
        flag = argv[i]
        if flag == "--lang":
            if i + 1 >= len(argv):
                print("Error: --lang requires a value", file=sys.stderr)
                sys.exit(2)
            opts["lang"] = argv[i + 1]
            i += 2
        elif flag == "--allow":
            if i + 1 >= len(argv):
                print("Error: --allow requires a file path", file=sys.stderr)
                sys.exit(2)
            opts["allow_file"] = argv[i + 1]
            i += 2
        elif flag in ("-h", "--help"):
            print(__doc__)
            sys.exit(0)
        else:
            print(f"Error: unknown flag {flag}", file=sys.stderr)
            sys.exit(2)
    return opts


def main():
    opts = parse_args(sys.argv)
    lang = opts["lang"]
    if not lang:
        print(__doc__)
        sys.exit(2)

    allow = set(DEFAULT_ALLOW)
    if opts["allow_file"]:
        allow |= load_allow_file(opts["allow_file"])

    text = sys.stdin.read()
    warnings = scan(text, lang, allow)

    report = {
        "lang": lang,
        "count": len(warnings),
        "warnings": warnings,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))

    sys.exit(0 if len(warnings) == 0 else 1)


if __name__ == "__main__":
    main()
