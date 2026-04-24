#!/usr/bin/env python3
"""
MasterClaw turn commit — atomic "after action" writes.

Replaces the manual Step 8 sequence of read+edit for log.md, character file,
state.md, and scene/npc sheets with a single JSON-in, all-or-nothing call.

Usage:
  cat payload.json | python3 turn_commit.py <game> [--base <path>] [--dry-run]

Payload schema (all sections optional except where noted):
{
  "log_entry": {                       # writes to games/<game>/log.md
    "kind": "roll" | "event",          # default: "roll"
    "heading": "Human-readable scene/moment",   # REQUIRED
    # roll fields:
    "action": "<actor> — <what they tried>",
    "roll": "Xd6 [n,n,n] → K успехов vs сложность D → итог",
    "narrator": "Мастер | Игрок",
    "conditions": "нет" | "...",
    "reserve": "X → Y",
    "consequences": "что изменилось в мире",
    # event fields:
    "description": "...",
    "participants": "...",
    "flags_triggered": "..."
  },
  "character_update": {                # writes to games/<game>/characters/<name>.md
    "name": "<character file basename>",        # REQUIRED
    "reserve": 0,                      # optional absolute value
    "add_conditions": [{"text":"...", "source":"..."}],
    "remove_conditions": ["<text>"],   # matched on text field
    "change_log_entry": "short line"   # appended as string
  },
  "state_update": {                    # writes to games/<game>/state.md
    "current_scene": "3-5 lines",
    "sections": {"Current party location": "...", "Key NPC status": "..."}
  },
  "scene_sheet_append": {              # writes to games/<game>/scenes/<id>.md
    "scene_id": "ivren_tavern_bearded_fish",
    "state_change": "d2 midday: описание изменения",
    "tags": {"props.hearth": ["cold"]}    # optional: append tags to existing keys
  },
  "npc_sheet_append": {                # writes to games/<game>/npcs_adhoc/<id>.md
    "npc_id": "edwin_stranger",
    "recent_interaction": "d2 midday: краткое описание"
  }
}

Exit codes:
  0 — all writes succeeded (or dry-run validated)
  1 — validation error; nothing written
  2 — I/O or unexpected error; rollback attempted if partial writes happened
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import shutil
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _mc_common import (  # noqa: E402
    DEFAULT_BASE,
    game_dir,
    game_meta,
    load_character,
    dump_character,
    read_text,
    write_text,
    replace_md_section,
    die,
)


# ---------------------------------------------------------------------------
# Backup / transaction primitives
# ---------------------------------------------------------------------------

class Transaction:
    """Stages file changes in a temp dir, then moves them into place on commit.
    Keeps original backups; rollback restores originals if commit fails halfway."""

    def __init__(self, workspace: str):
        self.workspace = workspace
        self.staged: list[tuple[str, str]] = []  # (target_path, temp_content_path)
        self.backups: list[tuple[str, str]] = []  # (target_path, backup_content_path)
        self._tmpdir = tempfile.mkdtemp(prefix="mc_turn_")

    def stage_write(self, target: str, content: str) -> None:
        tmp = os.path.join(self._tmpdir, f"stage_{len(self.staged):03d}")
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(content)
        self.staged.append((target, tmp))

    def commit(self) -> list[str]:
        """Apply all staged writes. Creates backups; on any failure, rolls back."""
        applied: list[str] = []
        try:
            for target, tmp in self.staged:
                if os.path.exists(target):
                    bk = os.path.join(self._tmpdir, f"bk_{len(self.backups):03d}")
                    shutil.copy2(target, bk)
                    self.backups.append((target, bk))
                os.makedirs(os.path.dirname(target) or ".", exist_ok=True)
                shutil.move(tmp, target)
                applied.append(target)
            return applied
        except Exception as e:
            for target, bk in self.backups:
                try:
                    shutil.copy2(bk, target)
                except Exception:
                    pass
            raise RuntimeError(f"commit failed, attempted rollback: {e}") from e

    def cleanup(self) -> None:
        shutil.rmtree(self._tmpdir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Section renderers
# ---------------------------------------------------------------------------

def render_log_entry(entry: dict, lang: str) -> str:
    """Render a log entry block per locale template."""
    kind = entry.get("kind", "roll")
    heading = entry.get("heading")
    if not heading:
        raise ValueError("log_entry.heading is required")

    if lang == "ru":
        labels = {
            "action": "Действие",
            "roll": "Бросок",
            "narrator": "Рассказчик",
            "conditions": "Состояния",
            "reserve": "Резерв",
            "consequences": "Последствия",
            "event": "Событие",
            "description": "Описание",
            "participants": "Участники",
            "flags": "Затронутые флаги",
        }
    else:
        labels = {
            "action": "Action",
            "roll": "Roll",
            "narrator": "Narrator",
            "conditions": "Conditions",
            "reserve": "Reserve",
            "consequences": "Consequences",
            "event": "Event",
            "description": "Description",
            "participants": "Participants",
            "flags": "Flags triggered",
        }

    lines = [f"## {heading}"]
    if kind == "event":
        fields = ["event", "description", "participants", "consequences", "flags"]
        field_map = {
            "event": entry.get("event") or heading,
            "description": entry.get("description", ""),
            "participants": entry.get("participants", ""),
            "consequences": entry.get("consequences", ""),
            "flags": entry.get("flags_triggered", ""),
        }
    else:
        fields = ["action", "roll", "narrator", "conditions", "reserve", "consequences"]
        field_map = {
            "action": entry.get("action", ""),
            "roll": entry.get("roll", ""),
            "narrator": entry.get("narrator", ""),
            "conditions": entry.get("conditions", "нет" if lang == "ru" else "none"),
            "reserve": entry.get("reserve", ""),
            "consequences": entry.get("consequences", ""),
        }

    for f in fields:
        val = field_map.get(f, "")
        if not val and f in ("flags", "participants"):
            continue  # optional fields
        lines.append(f"**{labels[f]}:** {val}")
    return "\n".join(lines) + "\n"


def apply_character_update(game_path: str, upd: dict) -> tuple[str, str]:
    """Return (char_path, new_content)."""
    name = upd.get("name")
    if not name:
        raise ValueError("character_update.name is required")
    char_path = os.path.join(game_path, "characters", f"{name}.md")
    heading, data = load_character(char_path)

    if "reserve" in upd:
        data.setdefault("reserve_dice", {"current": 0, "maximum": 7})
        val = int(upd["reserve"])
        if not 0 <= val <= data["reserve_dice"].get("maximum", 7):
            raise ValueError(f"character_update.reserve {val} out of [0..{data['reserve_dice'].get('maximum', 7)}]")
        data["reserve_dice"]["current"] = val

    conditions = list(data.get("conditions") or [])
    # Normalize legacy string conditions into dicts
    conditions = [c if isinstance(c, dict) else {"text": str(c), "source": "legacy"} for c in conditions]

    for rm in upd.get("remove_conditions") or []:
        conditions = [c for c in conditions if c.get("text") != rm]
    for add in upd.get("add_conditions") or []:
        if isinstance(add, str):
            conditions.append({"text": add, "source": "turn_commit"})
        elif isinstance(add, dict) and add.get("text"):
            conditions.append({"text": add["text"], "source": add.get("source", "turn_commit")})
    data["conditions"] = conditions

    change_log = list(data.get("change_log") or [])
    new_change = upd.get("change_log_entry")
    if new_change:
        change_log.append(str(new_change))
    data["change_log"] = change_log

    return char_path, dump_character(heading, data)


def apply_state_update(game_path: str, upd: dict) -> tuple[str, str]:
    """Return (state_path, new_content)."""
    state_path = os.path.join(game_path, "state.md")
    text = read_text(state_path) or ""

    if "current_scene" in upd:
        scene = upd["current_scene"].strip()
        # Try both localized headings
        for heading in ("Current scene", "Текущая сцена"):
            if re.search(rf"^##\s+{re.escape(heading)}\s*$", text, re.MULTILINE):
                text = replace_md_section(text, heading, scene)
                break
        else:
            # Append fresh with English heading as default
            text = replace_md_section(text, "Current scene", scene)

    for hd, body in (upd.get("sections") or {}).items():
        text = replace_md_section(text, hd, body.strip())

    return state_path, text


def apply_scene_sheet_append(game_path: str, upd: dict) -> tuple[str, str]:
    scene_id = upd.get("scene_id")
    if not scene_id:
        raise ValueError("scene_sheet_append.scene_id is required")
    path = os.path.join(game_path, "scenes", f"{scene_id}.md")
    text = read_text(path)
    if text is None:
        raise ValueError(f"scene sheet not found: {path}. Create it first via write, not append.")

    new_text = text
    change = upd.get("state_change")
    if change:
        # Find the state_changes: block
        m = re.search(r"^state_changes:\s*$", new_text, re.MULTILINE)
        if m:
            insertion = f'  - "{change}"\n'
            # Insert after last item of the block
            block_start = m.end()
            # Scan lines after block_start to find end of the list
            rest = new_text[block_start:]
            end_idx = len(rest)
            line_start = 0
            for ln in rest.splitlines(keepends=True):
                stripped = ln.strip()
                if stripped.startswith("-") or stripped == "":
                    line_start += len(ln)
                    continue
                end_idx = line_start
                break
            new_text = new_text[:block_start] + rest[:end_idx] + insertion + rest[end_idx:]
        else:
            # Append a fresh block before closing code fence if present, else at EOF
            if new_text.rstrip().endswith("```"):
                # within a fenced YAML block in the markdown file
                idx = new_text.rstrip().rfind("```")
                insertion = f'\nstate_changes:\n  - "{change}"\n'
                new_text = new_text[:idx] + insertion + new_text[idx:]
            else:
                if not new_text.endswith("\n"):
                    new_text += "\n"
                new_text += f'\nstate_changes:\n  - "{change}"\n'

    return path, new_text


def apply_npc_sheet_append(game_path: str, upd: dict) -> tuple[str, str]:
    npc_id = upd.get("npc_id")
    if not npc_id:
        raise ValueError("npc_sheet_append.npc_id is required")
    path = os.path.join(game_path, "npcs_adhoc", f"{npc_id}.md")
    text = read_text(path)
    if text is None:
        raise ValueError(f"npc sheet not found: {path}. Create it first via write, not append.")

    new_text = text
    inter = upd.get("recent_interaction")
    if inter:
        m = re.search(r"^recent_interactions:\s*$", new_text, re.MULTILINE)
        if m:
            block_start = m.end()
            rest = new_text[block_start:]
            line_start = 0
            end_idx = len(rest)
            for ln in rest.splitlines(keepends=True):
                stripped = ln.strip()
                if stripped.startswith("-") or stripped == "":
                    line_start += len(ln)
                    continue
                end_idx = line_start
                break
            insertion = f'  - "{inter}"\n'
            new_text = new_text[:block_start] + rest[:end_idx] + insertion + rest[end_idx:]
        else:
            if not new_text.endswith("\n"):
                new_text += "\n"
            new_text += f'\nrecent_interactions:\n  - "{inter}"\n'
    return path, new_text


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("game")
    ap.add_argument("--base", default=None)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    try:
        game_path = game_dir(args.game, args.base)
    except FileNotFoundError as e:
        die(str(e), 2)

    try:
        payload = json.loads(sys.stdin.read())
    except json.JSONDecodeError as e:
        die(f"invalid JSON on stdin: {e}", 2)

    lang = game_meta(game_path).get("language", "ru")

    changes: list[tuple[str, str, str]] = []  # (action, path, new_content)
    errors: list[str] = []

    # 1. log.md append
    if "log_entry" in payload:
        try:
            entry_md = render_log_entry(payload["log_entry"], lang)
            log_path = os.path.join(game_path, "log.md")
            existing = read_text(log_path) or ""
            if existing and not existing.endswith("\n"):
                existing += "\n"
            new_log = existing + "\n" + entry_md
            changes.append(("log_append", log_path, new_log))
        except Exception as e:
            errors.append(f"log_entry: {e}")

    # 2. character file update
    if "character_update" in payload:
        try:
            p, c = apply_character_update(game_path, payload["character_update"])
            changes.append(("character_update", p, c))
        except Exception as e:
            errors.append(f"character_update: {e}")

    # 3. state.md update
    if "state_update" in payload:
        try:
            p, c = apply_state_update(game_path, payload["state_update"])
            changes.append(("state_update", p, c))
        except Exception as e:
            errors.append(f"state_update: {e}")

    # 4. scene sheet append
    if "scene_sheet_append" in payload:
        try:
            p, c = apply_scene_sheet_append(game_path, payload["scene_sheet_append"])
            changes.append(("scene_sheet_append", p, c))
        except Exception as e:
            errors.append(f"scene_sheet_append: {e}")

    # 5. npc sheet append
    if "npc_sheet_append" in payload:
        try:
            p, c = apply_npc_sheet_append(game_path, payload["npc_sheet_append"])
            changes.append(("npc_sheet_append", p, c))
        except Exception as e:
            errors.append(f"npc_sheet_append: {e}")

    if errors:
        print("turn_commit: validation errors, nothing written:", file=sys.stderr)
        for err in errors:
            print(f"  - {err}", file=sys.stderr)
        sys.exit(1)

    if args.dry_run:
        print(json.dumps({
            "dry_run": True,
            "would_write": [{"action": a, "path": os.path.relpath(p, game_path)} for a, p, _ in changes],
        }, ensure_ascii=False, indent=2))
        sys.exit(0)

    tx = Transaction(game_path)
    try:
        for _action, path, content in changes:
            tx.stage_write(path, content)
        applied = tx.commit()
        print(json.dumps({
            "ok": True,
            "written": [os.path.relpath(p, game_path) for p in applied],
        }, ensure_ascii=False, indent=2))
        sys.exit(0)
    except Exception as e:
        print(f"turn_commit: commit failed: {e}", file=sys.stderr)
        sys.exit(2)
    finally:
        tx.cleanup()


if __name__ == "__main__":
    main()
