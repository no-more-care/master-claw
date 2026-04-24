#!/usr/bin/env python3
"""
MasterClaw dice-pool builder — validated against the real character file.

Addresses the "trait level = dice count" class of bugs by computing the pool
from the character sheet on disk, not from the agent's memory. Each trait gives
exactly +1 die; each aspect on those traits gives +1; at most 1 flag gives +1;
the player adds reserve dice on top.

Usage:
  python3 build_pool.py <game> <character> \\
    --trait "Trait A" --trait "Trait B" \\
    --aspect "aspect 1" --aspect "aspect 2" \\
    [--flag "flag text"] \\
    [--reserve-spent N] \\
    --difficulty D \\
    [--lang ru] \\
    [--json]

Repeated --trait and --aspect flags accept names with commas (unlike CSV).
Legacy --traits / --aspects (CSV) still accepted when names contain no commas.

stdout (non-JSON): one-line display per `locales/<lang>/templates/dice_pool.md`
stdout (--json):   {"pool_size": N, "display": "...", "components": [...]}

Exit:
  0 — valid pool, display printed
  1 — validation error (missing trait, aspect on wrong trait, >1 flag, reserve out of bounds)
  2 — file / argument error
"""

from __future__ import annotations

import argparse
import json
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _mc_common import game_dir, game_meta, load_character, die  # noqa: E402


def parse_csv(value: str | None) -> list[str]:
    if not value:
        return []
    return [s.strip() for s in value.split(",") if s.strip()]


def validate_pool(
    char_data: dict,
    trait_names: list[str],
    aspect_names: list[str],
    flag_text: str | None,
    reserve_spent: int,
) -> tuple[list[dict], list[str]]:
    """Return (components, errors). components = [{source, kind, dice, parent}, ...]."""
    errors: list[str] = []
    components: list[dict] = []

    # Build lookup
    char_traits = {t.get("name", ""): t for t in (char_data.get("traits") or [])}
    char_flags = [f.get("text", "") for f in (char_data.get("flags") or [])]
    reserve = (char_data.get("reserve_dice") or {}).get("current", 0)

    # Traits
    for tname in trait_names:
        if tname not in char_traits:
            suggestion = _suggest(tname, list(char_traits.keys()))
            errors.append(f"trait '{tname}' not found on character" + (f"; did you mean '{suggestion}'?" if suggestion else ""))
            continue
        components.append({"source": tname, "kind": "trait", "dice": 1, "parent": None})

    # Aspects — must belong to one of the LISTED traits (otherwise the aspect
    # is not applicable to this action even if it exists on another trait)
    listed_trait_aspects: dict[str, str] = {}  # aspect -> parent trait name
    for tname in trait_names:
        trait = char_traits.get(tname)
        if not trait:
            continue
        for asp in trait.get("aspects") or []:
            listed_trait_aspects[asp] = tname

    for aname in aspect_names:
        parent = listed_trait_aspects.get(aname)
        if not parent:
            # Check if the aspect exists on a trait NOT in the listed set — gives a better error
            all_aspect_parents: dict[str, str] = {}
            for tname, trait in char_traits.items():
                for asp in trait.get("aspects") or []:
                    all_aspect_parents[asp] = tname
            existing_parent = all_aspect_parents.get(aname)
            if existing_parent:
                errors.append(
                    f"aspect '{aname}' belongs to trait '{existing_parent}' which was not included in --traits"
                )
            else:
                suggestion = _suggest(aname, list(all_aspect_parents.keys()))
                errors.append(f"aspect '{aname}' not found on character" + (f"; did you mean '{suggestion}'?" if suggestion else ""))
            continue
        components.append({"source": aname, "kind": "aspect", "dice": 1, "parent": parent})

    # Flag
    if flag_text:
        if flag_text not in char_flags:
            suggestion = _suggest(flag_text, char_flags)
            errors.append(f"flag '{flag_text}' not found on character" + (f"; did you mean '{suggestion}'?" if suggestion else ""))
        else:
            components.append({"source": flag_text, "kind": "flag", "dice": 1, "parent": None})

    # Reserve
    if reserve_spent < 0:
        errors.append(f"reserve-spent must be >= 0, got {reserve_spent}")
    elif reserve_spent > reserve:
        errors.append(f"reserve-spent {reserve_spent} exceeds current reserve {reserve}")
    elif reserve_spent > 0:
        components.append({"source": f"резерв ×{reserve_spent}" , "kind": "reserve", "dice": reserve_spent, "parent": None})

    return components, errors


def _suggest(needle: str, haystack: list[str]) -> str | None:
    """Best-effort typo fix using simple substring / prefix matching."""
    if not haystack:
        return None
    nlow = needle.lower()
    for s in haystack:
        if s.lower() == nlow:
            return s
    for s in haystack:
        if nlow in s.lower() or s.lower() in nlow:
            return s
    # Prefix match on first few chars
    prefix = nlow[:4]
    for s in haystack:
        if s.lower().startswith(prefix):
            return s
    return None


def format_display(components: list[dict], difficulty: int, reserve_after: int, lang: str, reserve_spent: int) -> str:
    """Render the compact one-line format from dice_pool.md.

    dice_pool.md (ru) example:
      🎲 Пул (5 кубов): <trait A> +1, <aspect 1> +1, ... флаг «<F>» +1. Резерв?
      Сложность: N.

    The "Резерв?" prompt is for the pre-roll phase when agent asks the player
    how many to add. If reserve_spent > 0 (post-confirmation) we show the current
    after-reserve state instead.
    """
    total = sum(c["dice"] for c in components)
    parts = []
    for c in components:
        if c["kind"] == "flag":
            parts.append(f"флаг «{c['source']}» +{c['dice']}" if lang == "ru" else f"flag “{c['source']}” +{c['dice']}")
        elif c["kind"] == "reserve":
            parts.append(f"{c['source']}" if lang == "ru" else c["source"])
        else:
            parts.append(f"{c['source']} +{c['dice']}")

    if lang == "ru":
        header = f"🎲 Пул ({total} кубов): "
        tail_diff = f"Сложность: {difficulty}."
        tail_reserve = f"Резерв: {reserve_after}/7." if reserve_spent > 0 else "Резерв?"
    else:
        header = f"🎲 Pool ({total} dice): "
        tail_diff = f"Difficulty: {difficulty}."
        tail_reserve = f"Reserve: {reserve_after}/7." if reserve_spent > 0 else "Reserve?"

    body = header + ", ".join(parts) + ". " + tail_reserve + "\n" + tail_diff
    return body


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("game", help="Game name under GameMaster/games/")
    ap.add_argument("character", help="Character file basename without .md")
    ap.add_argument("--trait", action="append", default=[], help="Trait name (repeatable). Preferred when names contain commas.")
    ap.add_argument("--aspect", action="append", default=[], help="Aspect name (repeatable; must belong to --trait). Preferred when names contain commas.")
    ap.add_argument("--traits", default="", help='Legacy: comma-separated trait names (no commas inside names)')
    ap.add_argument("--aspects", default="", help='Legacy: comma-separated aspect names')
    ap.add_argument("--flag", default=None, help="At most one flag text")
    ap.add_argument("--reserve-spent", type=int, default=0, help="Reserve dice player chose to add")
    ap.add_argument("--difficulty", type=int, required=True)
    ap.add_argument("--lang", default=None, help="Language code; defaults to game.md")
    ap.add_argument("--base", default=None)
    ap.add_argument("--json", action="store_true", help="Emit JSON instead of rendered text")
    args = ap.parse_args()

    try:
        game_path = game_dir(args.game, args.base)
    except FileNotFoundError as e:
        die(str(e), 2)

    lang = args.lang or game_meta(game_path).get("language", "ru")
    char_path = os.path.join(game_path, "characters", f"{args.character}.md")

    try:
        _heading, char_data = load_character(char_path)
    except (FileNotFoundError, ValueError) as e:
        die(str(e), 2)

    traits = list(args.trait) + parse_csv(args.traits)
    aspects = list(args.aspect) + parse_csv(args.aspects)
    if not traits:
        die("at least one --trait (or --traits) entry required", 2)

    components, errors = validate_pool(char_data, traits, aspects, args.flag, args.reserve_spent)

    if errors:
        print("Pool validation failed:", file=sys.stderr)
        for err in errors:
            print(f"  - {err}", file=sys.stderr)
        sys.exit(1)

    reserve_before = (char_data.get("reserve_dice") or {}).get("current", 0)
    reserve_after = reserve_before - args.reserve_spent

    display = format_display(components, args.difficulty, reserve_after, lang, args.reserve_spent)
    pool_size = sum(c["dice"] for c in components)

    if args.json:
        out = {
            "pool_size": pool_size,
            "difficulty": args.difficulty,
            "reserve_before": reserve_before,
            "reserve_after": reserve_after,
            "components": components,
            "display": display,
        }
        print(json.dumps(out, ensure_ascii=False, indent=2))
    else:
        print(display)


if __name__ == "__main__":
    main()
