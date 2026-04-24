#!/usr/bin/env python3
"""
MasterClaw game-channel response renderer.

Takes a structured JSON payload and emits a Discord-ready text block per the
locale's game_response.md template. Does NOT touch the narrative channel (see
post_narrative.py for that). The renderer handles "layout" — wrapping tables
and multi-line mechanics in code blocks, ordering sections, ensuring the CTA
is present when required, showing reserve deltas. It does NOT dictate the
agent's prose: a free-form `flavor_prefix` and `flavor_suffix` are passed
through verbatim.

Usage:
  cat payload.json | python3 render_response.py [--lang ru] [--check]

Payload schema:
{
  "type": "action_roll" | "roll_result" | "auto_success" | "scene_description"
          | "narrator_handoff" | "reserve_update" | "simple",
  "lang": "ru",                 # optional, overrides --lang
  "flavor_prefix": "...",        # optional free text, printed above the block
  "flavor_suffix": "...",        # optional free text, printed below the block
  # type-specific fields below
}

# action_roll — announcing a pool before rolling
  "pool_line": "🎲 Пул (5 кубов): ...",     # one-line pool display (from build_pool.py)
  "difficulty": 3,                            # required
  "reserve_current": 2,                       # required
  "failure_stakes": "если провалишь...",     # optional — explain what's at risk
  "cta": "Сколько из резерва?"                # defaults to "Сколько из резерва?" / "How many from reserve?"

# roll_result — after roll.py ran
  "roll_block": "<verbatim player-facing output of roll.py>",   # required
  "interpretation": "...",                                      # optional — what it means mechanically
  "reserve_after": 0,                                           # required
  "reserve_delta": -2,                                          # optional — if non-zero, show
  "conditions_changed": [{"op":"+","text":"..."}, {"op":"-","text":"..."}],
  "narrator_rights_prompt": "...",                              # optional — rendered as-is
  "cta": "..."                                                  # optional

# auto_success — short resolved action
  "body": "1-3 lines of narration",   # required (prose)
  "reserve_delta": 0,                  # optional
  "reserve_after": null,               # optional
  "cta": "Что дальше?"                 # optional; auto "Что дальше?" / "What's next?" if omitted

# scene_description — narrator response, no mechanics
  "body": "description",               # required
  # intentionally no CTA

# narrator_handoff — giving narrator rights to the player
  "setup": "brief mechanical context",  # required
  "rights_level": "minor|significant|madness",  # required — affects the prompt
  "options_hint": "...",                # optional override

# reserve_update — pure reserve change outside of a roll
  "reserve_before": 4,
  "reserve_after": 5,
  "reason": "за отыгрыш",               # optional

# simple — free-form content with layout help
  "body": "...",                        # required
  "code_block": false,                  # optional — wrap body in ```...```
  "cta": "..."                          # optional

Output: rendered text to stdout. Trailing newline included.
Exit codes:
  0 — rendered OK
  1 — validation error (missing required field; printed to stderr)
  2 — argument / I/O error
"""

from __future__ import annotations

import argparse
import json
import sys


def L(lang: str, ru: str, en: str) -> str:
    return ru if lang == "ru" else en


def has_multiline(text: str) -> bool:
    return "\n" in (text or "").strip()


def in_code(text: str) -> str:
    """Wrap in triple-backtick code block for Discord monospace."""
    t = text.rstrip("\n")
    return f"```\n{t}\n```"


def assemble(flavor_prefix: str | None, core: str, flavor_suffix: str | None) -> str:
    parts = []
    if flavor_prefix and flavor_prefix.strip():
        parts.append(flavor_prefix.strip())
    parts.append(core)
    if flavor_suffix and flavor_suffix.strip():
        parts.append(flavor_suffix.strip())
    return "\n\n".join(parts) + "\n"


def require(d: dict, *keys: str) -> list[str]:
    return [k for k in keys if k not in d or d[k] in (None, "")]


# ---------------------------------------------------------------------------
# Renderers
# ---------------------------------------------------------------------------

def render_action_roll(p: dict, lang: str) -> str:
    missing = require(p, "pool_line", "difficulty", "reserve_current")
    if missing:
        raise ValueError(f"action_roll missing fields: {missing}")

    pool_line = p["pool_line"].strip()
    diff = p["difficulty"]
    reserve = p["reserve_current"]
    stakes = p.get("failure_stakes")
    cta = p.get("cta") or L(lang, "Сколько из резерва? (или «нет»)", "How many from reserve? (or “none”)")

    # pool_line from build_pool.py already has "🎲 Пул ..." + "Сложность: N." on two lines
    # but we also need reserve display if not already in the pool line
    lines = [pool_line]
    if f"{diff}" not in pool_line.split("\n")[-1]:
        lines.append(L(lang, f"Сложность: {diff}.", f"Difficulty: {diff}."))
    lines.append(L(lang, f"Запас: {reserve}/7.", f"Reserve: {reserve}/7."))
    if stakes:
        lines.append(L(lang, f"Провал: {stakes}", f"On failure: {stakes}"))
    lines.append(cta)

    core_raw = "\n".join(lines)
    # Wrap the mechanics in a code block if multi-line (which it always is here)
    core = in_code(core_raw)
    return assemble(p.get("flavor_prefix"), core, p.get("flavor_suffix"))


def render_roll_result(p: dict, lang: str) -> str:
    missing = require(p, "roll_block", "reserve_after")
    if missing:
        raise ValueError(f"roll_result missing fields: {missing}")

    roll_block = p["roll_block"].rstrip()
    reserve_after = p["reserve_after"]
    reserve_delta = p.get("reserve_delta", 0)
    interpretation = p.get("interpretation", "").strip()
    narrator_prompt = p.get("narrator_rights_prompt", "").strip()
    cta = p.get("cta", "").strip()
    cond_changes = p.get("conditions_changed") or []

    blocks = []
    # 1. Roll block from roll.py — always code-wrapped
    blocks.append(in_code(roll_block))

    # 2. Interpretation (plain prose)
    if interpretation:
        blocks.append(interpretation)

    # 3. Narrator prompt (plain prose — lets agent use bold/italics)
    if narrator_prompt:
        blocks.append(narrator_prompt)

    # 4. Reserve line if changed
    if reserve_delta != 0 or "reserve_after" in p:
        sign = "+" if reserve_delta > 0 else ""
        if reserve_delta != 0:
            blocks.append(L(lang,
                f"Запас: {reserve_after}/7 ({sign}{reserve_delta}).",
                f"Reserve: {reserve_after}/7 ({sign}{reserve_delta})."))
        else:
            blocks.append(L(lang, f"Запас: {reserve_after}/7.", f"Reserve: {reserve_after}/7."))

    # 5. Conditions delta
    if cond_changes:
        parts = []
        for c in cond_changes:
            if c.get("op") == "+":
                parts.append(L(lang, f"+ {c.get('text','')}", f"+ {c.get('text','')}"))
            elif c.get("op") == "-":
                parts.append(L(lang, f"− {c.get('text','')}", f"− {c.get('text','')}"))
        blocks.append(L(lang, "Состояния: " + ", ".join(parts), "Conditions: " + ", ".join(parts)))

    # 6. CTA
    if cta:
        blocks.append(cta)

    core = "\n\n".join(blocks)
    return assemble(p.get("flavor_prefix"), core, p.get("flavor_suffix"))


def render_auto_success(p: dict, lang: str) -> str:
    missing = require(p, "body")
    if missing:
        raise ValueError(f"auto_success missing fields: {missing}")

    body = p["body"].strip()
    reserve_after = p.get("reserve_after")
    reserve_delta = p.get("reserve_delta", 0)
    cta = p.get("cta") or L(lang, "Что дальше?", "What's next?")

    blocks = [body]
    if reserve_after is not None and reserve_delta != 0:
        sign = "+" if reserve_delta > 0 else ""
        blocks.append(L(lang,
            f"Запас: {reserve_after}/7 ({sign}{reserve_delta}).",
            f"Reserve: {reserve_after}/7 ({sign}{reserve_delta})."))
    blocks.append(cta)

    core = "\n\n".join(blocks)
    return assemble(p.get("flavor_prefix"), core, p.get("flavor_suffix"))


def render_scene_description(p: dict, lang: str) -> str:
    missing = require(p, "body")
    if missing:
        raise ValueError(f"scene_description missing fields: {missing}")
    core = p["body"].strip()
    # Scene description is intentionally pure prose — no CTA, no mechanics
    return assemble(p.get("flavor_prefix"), core, p.get("flavor_suffix"))


# Default narrator-rights options per level, localised
RIGHTS_PROMPTS = {
    "ru": {
        "disabled": "Рассказываю я.",
        "minor":    "Расскажи, что произошло. Хочешь +1 куб на следующий бросок, −1 сложности на одну проверку, или снять одно состояние?",
        "significant": "Расскажи, что произошло. Можешь ввести второстепенного NPC, мелкий триумф в сцене, сессионный бонус, или изменить деталь сцены — в пределах разумного.",
        "madness":  "Расскажи, что произошло. На этом уровне — любая крутая ерунда, если это делает игру интереснее.",
    },
    "en": {
        "disabled": "I'll narrate this one.",
        "minor":    "Tell me what happened. Want +1 die on the next roll, −1 difficulty on one check, or clear one condition?",
        "significant": "Tell me what happened. You can introduce a minor NPC, a scene-level triumph, a session bonus, or change a scene detail — within reason.",
        "madness":  "Tell me what happened. At this level — any cool nonsense, as long as it makes the game better.",
    },
}


def render_narrator_handoff(p: dict, lang: str) -> str:
    missing = require(p, "setup", "rights_level")
    if missing:
        raise ValueError(f"narrator_handoff missing fields: {missing}")

    setup = p["setup"].strip()
    level = p["rights_level"].strip().lower()
    options_hint = (p.get("options_hint") or "").strip()

    table = RIGHTS_PROMPTS.get(lang, RIGHTS_PROMPTS["en"])
    if level not in table:
        raise ValueError(f"unknown rights_level '{level}' (expected: {', '.join(table.keys())})")

    prompt = options_hint or table[level]

    blocks = [setup, prompt]
    core = "\n\n".join(blocks)
    return assemble(p.get("flavor_prefix"), core, p.get("flavor_suffix"))


def render_reserve_update(p: dict, lang: str) -> str:
    missing = require(p, "reserve_before", "reserve_after")
    if missing:
        raise ValueError(f"reserve_update missing fields: {missing}")
    before = p["reserve_before"]
    after = p["reserve_after"]
    delta = after - before
    sign = "+" if delta > 0 else ""
    reason = p.get("reason", "").strip()

    line = L(lang, f"Запас: {before} → {after} ({sign}{delta}).", f"Reserve: {before} → {after} ({sign}{delta}).")
    if reason:
        line += " " + (L(lang, f"Причина: {reason}.", f"Reason: {reason}."))

    return assemble(p.get("flavor_prefix"), line, p.get("flavor_suffix"))


def render_simple(p: dict, lang: str) -> str:
    missing = require(p, "body")
    if missing:
        raise ValueError(f"simple missing fields: {missing}")
    body = p["body"].strip()
    code = p.get("code_block", False) or has_multiline(body) and p.get("code_block") is not False and p.get("auto_code_block", True)
    # Only auto-code-block when body looks like structured/tabular output (has ≥2 lines with : or | )
    if code is True:
        core = in_code(body)
    elif code == "auto":
        # heuristic: ≥3 lines AND contains ':' or '|' → code block
        if body.count("\n") >= 2 and (":" in body or "|" in body):
            core = in_code(body)
        else:
            core = body
    else:
        core = body
    cta = (p.get("cta") or "").strip()
    if cta:
        core = core + "\n\n" + cta
    return assemble(p.get("flavor_prefix"), core, p.get("flavor_suffix"))


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

RENDERERS = {
    "action_roll":        render_action_roll,
    "roll_result":        render_roll_result,
    "auto_success":       render_auto_success,
    "scene_description":  render_scene_description,
    "narrator_handoff":   render_narrator_handoff,
    "reserve_update":     render_reserve_update,
    "simple":             render_simple,
}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--lang", default="ru")
    ap.add_argument("--check", action="store_true", help="Validate payload; do not render")
    args = ap.parse_args()

    try:
        payload = json.loads(sys.stdin.read())
    except json.JSONDecodeError as e:
        print(f"Error: invalid JSON on stdin: {e}", file=sys.stderr)
        sys.exit(2)

    lang = payload.get("lang", args.lang)
    type_ = payload.get("type")
    if not type_:
        print("Error: payload.type is required", file=sys.stderr)
        sys.exit(1)
    renderer = RENDERERS.get(type_)
    if not renderer:
        print(f"Error: unknown type '{type_}' (valid: {', '.join(RENDERERS.keys())})", file=sys.stderr)
        sys.exit(1)

    try:
        out = renderer(payload, lang)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    if args.check:
        print(json.dumps({"ok": True, "type": type_, "bytes": len(out)}, ensure_ascii=False))
        return

    sys.stdout.write(out)


if __name__ == "__main__":
    main()
