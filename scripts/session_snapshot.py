#!/usr/bin/env python3
"""
MasterClaw session snapshot — compact briefing for the game master.

Reads the live files of a game and prints a ≤80-line briefing to stdout. Use at
session continuation, every 10-15 messages for re-sync, or before a major beat.

Sections printed (in order):
  1. Game header — name, language, narrative_style, narrator_rights_level, webhook state
  2. Current scene — 3-5 lines from state.md
  3. Party — one line per character (location, reserve, conditions)
  4. Last resolved rolls — up to 3 entries scraped from log.md tail
  5. Active plot threads — "Plot progress" section of state.md
  6. NPCs present — tags from npcs_adhoc/<id>.md for NPCs mentioned in current scene
  7. Known sub-scenes — from scenes/_index.md for the current top-level location

The script is READ-ONLY. It never writes.

Usage:
  python3 session_snapshot.py <game_name>
  python3 session_snapshot.py <game_name> --base /path/to/GameMaster

Exit codes:
  0 — snapshot printed (may be partial if some files are missing)
  1 — argument error or game directory not found
"""

import sys
import os
import re
import glob


DEFAULT_BASE = "/root/.microclaw/working_dir/shared/GameMaster"


def read_file(path: str) -> str | None:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except (FileNotFoundError, IsADirectoryError):
        return None


def extract_yaml_field(text: str, field: str) -> str | None:
    """Extract `field: value` from a markdown file's frontmatter-ish lines."""
    pattern = re.compile(rf"^{re.escape(field)}\s*:\s*(.+?)\s*$", re.MULTILINE)
    m = pattern.search(text or "")
    return m.group(1).strip() if m else None


def extract_section(text: str, heading: str, max_lines: int = 20) -> list[str]:
    """Return the lines of the `## heading` section (excluding the heading itself)."""
    if not text:
        return []
    pattern = re.compile(rf"^##\s+{re.escape(heading)}\s*$", re.MULTILINE | re.IGNORECASE)
    m = pattern.search(text)
    if not m:
        return []
    start = m.end()
    # Find next ## heading
    next_m = re.search(r"^##\s+", text[start:], re.MULTILINE)
    end = start + next_m.start() if next_m else len(text)
    body = text[start:end].strip("\n")
    lines = [ln for ln in body.splitlines() if ln.strip()]
    return lines[:max_lines]


def summarize_character(path: str) -> str:
    """One-line summary of a character .md file."""
    text = read_file(path)
    if not text:
        return f"- {os.path.basename(path)}: (unreadable)"

    name = extract_yaml_field(text, "name") or os.path.basename(path).rsplit(".", 1)[0]
    player = extract_yaml_field(text, "player") or "?"

    # Reserve — look for a `current:` inside a `reserve_dice:` block
    reserve = "?"
    rm = re.search(r"reserve_dice:\s*\n(?:.*\n)*?\s*current:\s*(\d+)", text)
    if rm:
        reserve = rm.group(1) + "/7"

    # Conditions — first 3 items under `conditions:`
    conditions = []
    cm = re.search(r"^conditions:\s*\n((?:\s*-\s*.+\n)+)", text, re.MULTILINE)
    if cm:
        for line in cm.group(1).splitlines()[:3]:
            item = line.strip().lstrip("-").strip()
            if item:
                conditions.append(item)

    cond_str = ", ".join(conditions) if conditions else "none"
    return f"- {name} ({player}): reserve {reserve}, conditions {cond_str}"


def tail_log_entries(text: str, n: int) -> list[str]:
    """Return the last `n` action/event blocks from log.md as compact one-liners."""
    if not text:
        return []
    # Match `## <heading>` blocks; take the heading line and the first line with meaningful data.
    blocks = re.split(r"\n(?=##\s)", text)
    entries = []
    for block in blocks[-n * 3:]:  # sample more than n to filter
        lines = [ln for ln in block.splitlines() if ln.strip()]
        if not lines or not lines[0].startswith("##"):
            continue
        heading = lines[0].lstrip("#").strip()
        # Try to find a "Бросок:" / "Roll:" / "Результат:" / "Outcome:" line
        detail = ""
        for ln in lines[1:]:
            low = ln.lower()
            if any(k in low for k in ("бросок:", "roll:", "результат:", "outcome:", "последствия:", "consequences:", "result:")):
                detail = ln.strip("*- ").strip()
                break
        entries.append(f"- {heading}" + (f" — {detail}" if detail else ""))
    return entries[-n:]


def extract_npc_adhoc_summary(npc_dir: str, npc_ids: list[str]) -> list[str]:
    """For each npc_id, open the sheet and pull the one-line summary."""
    summaries = []
    for npc_id in npc_ids:
        path = os.path.join(npc_dir, f"{npc_id}.md")
        text = read_file(path)
        if not text:
            continue
        name = extract_yaml_field(text, "known_name") or npc_id
        # Collect first few appearance + voice tags
        app = re.search(r"^appearance:\s*\[(.*?)\]", text, re.MULTILINE)
        voice = re.search(r"^voice:\s*\[(.*?)\]", text, re.MULTILINE)
        parts = []
        if app:
            parts.append(app.group(1).strip())
        if voice:
            parts.append(voice.group(1).strip())
        tail = " | ".join(parts) if parts else ""
        summaries.append(f"- {name}" + (f": {tail}" if tail else ""))
    return summaries


def find_current_top_location(state_text: str, index_text: str) -> str | None:
    """Best-effort: match a scene_id from _index.md against state's `location:` / `Current party location`."""
    if not state_text or not index_text:
        return None
    # Pull candidate location tokens from state
    candidates = []
    m = re.search(r"^\s*-?\s*location:\s*(.+)$", state_text, re.MULTILINE)
    if m:
        candidates.append(m.group(1).strip())
    loc_section = extract_section(state_text, "Current party location", max_lines=5)
    candidates.extend(loc_section)

    # Top-level keys in _index.md (YAML-ish): match lines starting with `word:` at col 0
    tops = re.findall(r"^([a-z_]+):\s*$", index_text, re.MULTILINE)

    for c in candidates:
        low = c.lower()
        for t in tops:
            if t in low or low in t:
                return t
    return tops[0] if tops else None


def list_sub_scenes(index_text: str, top_id: str) -> list[str]:
    """Find `sub_scenes: [...]` under the given top_id key."""
    if not index_text or not top_id:
        return []
    block_re = re.compile(rf"^{re.escape(top_id)}:\s*\n((?:  .*\n)+)", re.MULTILINE)
    m = block_re.search(index_text)
    if not m:
        return []
    block = m.group(1)
    sub_m = re.search(r"sub_scenes:\s*\[([^\]]*)\]", block)
    if not sub_m:
        return []
    return [s.strip() for s in sub_m.group(1).split(",") if s.strip()]


def parse_args(argv: list[str]) -> tuple[str, str]:
    game = None
    base = DEFAULT_BASE
    i = 1
    while i < len(argv):
        if argv[i] == "--base":
            if i + 1 >= len(argv):
                print("Error: --base requires a path", file=sys.stderr)
                sys.exit(1)
            base = argv[i + 1]
            i += 2
        elif argv[i] in ("-h", "--help"):
            print(__doc__)
            sys.exit(0)
        elif not game:
            game = argv[i]
            i += 1
        else:
            print(f"Error: unexpected argument {argv[i]}", file=sys.stderr)
            sys.exit(1)
    if not game:
        print(__doc__)
        sys.exit(1)
    return game, base


def main():
    game, base = parse_args(sys.argv)
    game_dir = os.path.join(base, "games", game)
    if not os.path.isdir(game_dir):
        print(f"Error: game directory not found: {game_dir}", file=sys.stderr)
        sys.exit(1)

    game_md = read_file(os.path.join(game_dir, "game.md")) or ""
    state_md = read_file(os.path.join(game_dir, "state.md")) or ""
    log_md = read_file(os.path.join(game_dir, "log.md")) or ""
    index_md = read_file(os.path.join(game_dir, "scenes", "_index.md")) or ""

    out = []

    # 1. Header
    out.append(f"=== Snapshot: {game} ===")
    lang = extract_yaml_field(game_md, "language") or "?"
    style = extract_yaml_field(game_md, "narrative_style") or "gamemaster"
    rights = extract_yaml_field(game_md, "narrator_rights_level") or "minor"
    webhook = extract_yaml_field(game_md, "narrative_webhook") or "none"
    webhook_state = "set" if webhook.startswith("http") else webhook
    world = extract_yaml_field(game_md, "world") or "?"
    out.append(f"world={world}  lang={lang}  style={style}  rights={rights}  webhook={webhook_state}")
    out.append("")

    # 2. Current scene
    out.append("--- Current scene ---")
    scene_lines = extract_section(state_md, "Current scene", max_lines=6)
    if scene_lines:
        out.extend(scene_lines)
    else:
        out.append("(empty — reconstruct from log.md)")
    out.append("")

    # 3. Party
    out.append("--- Party ---")
    chars = sorted(glob.glob(os.path.join(game_dir, "characters", "*.md")))
    if chars:
        for c in chars:
            out.append(summarize_character(c))
    else:
        out.append("(no characters)")
    out.append("")

    # 4. Last resolved rolls
    out.append("--- Last resolved (tail of log.md) ---")
    tail = tail_log_entries(log_md, 3)
    if tail:
        out.extend(tail)
    else:
        out.append("(empty)")
    out.append("")

    # 5. Active plot threads
    out.append("--- Active plot threads ---")
    plot_lines = extract_section(state_md, "Plot progress", max_lines=8)
    if not plot_lines:
        plot_lines = extract_section(state_md, "Active plot threads", max_lines=8)
    if plot_lines:
        out.extend(plot_lines)
    else:
        out.append("(none listed in state.md)")
    out.append("")

    # 6. NPCs in scene (from npcs_adhoc)
    out.append("--- Improvised NPCs present (from npcs_adhoc) ---")
    # Heuristic: scan Current scene for NPC ids that have a matching sheet
    scene_text = "\n".join(scene_lines).lower()
    npc_dir = os.path.join(game_dir, "npcs_adhoc")
    if os.path.isdir(npc_dir):
        present = []
        for path in sorted(glob.glob(os.path.join(npc_dir, "*.md"))):
            npc_id = os.path.basename(path)[:-3]
            # Match by id OR by known_name token
            text = read_file(path) or ""
            name = extract_yaml_field(text, "known_name")
            hay = scene_text
            if npc_id.lower() in hay or (name and name.strip('"\' ').lower() in hay):
                present.append(npc_id)
        if present:
            out.extend(extract_npc_adhoc_summary(npc_dir, present))
        else:
            out.append("(none match current scene)")
    else:
        out.append("(npcs_adhoc/ not initialised)")
    out.append("")

    # 7. Known sub-scenes of current top location
    out.append("--- Known sub-scenes (from scenes/_index.md) ---")
    top = find_current_top_location(state_md, index_md)
    if top:
        subs = list_sub_scenes(index_md, top)
        if subs:
            out.append(f"{top}: {', '.join(subs)}")
        else:
            out.append(f"{top}: (no sub-scenes recorded)")
    else:
        out.append("(no top-level location matched or _index.md empty)")

    # Trim to ≤80 lines
    trimmed = out[:80]
    print("\n".join(trimmed))
    if len(out) > 80:
        print(f"... ({len(out) - 80} more lines omitted)")


if __name__ == "__main__":
    main()
