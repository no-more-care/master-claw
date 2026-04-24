"""
Shared helpers for MasterClaw scripts.

Kept small and dependency-light. Only PyYAML is required (present on the droplet).
Loads locale templates, parses character files tolerantly, locates game directories.
"""

from __future__ import annotations

import os
import re
import sys
from typing import Any

try:
    import yaml
except ImportError:
    print("Error: PyYAML not installed (apt install python3-yaml)", file=sys.stderr)
    sys.exit(2)


DEFAULT_BASE = "/root/.microclaw/working_dir/shared/GameMaster"
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ---------------------------------------------------------------------------
# Game directory resolution
# ---------------------------------------------------------------------------

def game_dir(game: str, base: str | None = None) -> str:
    """Return absolute path to a game directory. Raises FileNotFoundError if missing."""
    base = base or DEFAULT_BASE
    path = os.path.join(base, "games", game)
    if not os.path.isdir(path):
        raise FileNotFoundError(f"Game directory not found: {path}")
    return path


def game_meta(game_path: str) -> dict:
    """Extract minimal fields from game.md (language, narrative_style, etc.)."""
    game_md_path = os.path.join(game_path, "game.md")
    text = read_text(game_md_path) or ""
    meta = {
        "language": _extract_md_field(text, "language") or _extract_md_field(text, "Язык") or "ru",
        "narrative_style": _extract_md_field(text, "narrative_style") or _extract_md_field(text, "Стиль нарратива") or "gamemaster",
        "narrator_rights_level": _extract_md_field(text, "narrator_rights_level") or _extract_md_field(text, "Уровень прав рассказчика") or "minor",
        "narrative_webhook": _extract_md_field(text, "narrative_webhook") or _extract_md_field(text, "Вебхук нарратива") or "none",
        "world": _extract_md_field(text, "world") or _extract_md_field(text, "Мир") or "",
    }
    # Strip surrounding asterisks/quotes some templates add
    for k, v in meta.items():
        if isinstance(v, str):
            meta[k] = v.strip().strip('"').strip("*").strip()
    return meta


def _extract_md_field(text: str, key: str) -> str | None:
    """Match `key: value` or `**Key:** value` in markdown."""
    patterns = [
        rf"^\*\*{re.escape(key)}:\*\*\s*(.+?)\s*$",
        rf"^{re.escape(key)}\s*:\s*(.+?)\s*$",
    ]
    for pat in patterns:
        m = re.search(pat, text, re.MULTILINE)
        if m:
            return m.group(1).strip()
    return None


# ---------------------------------------------------------------------------
# File I/O
# ---------------------------------------------------------------------------

def read_text(path: str) -> str | None:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except (FileNotFoundError, IsADirectoryError):
        return None


def write_text(path: str, content: str) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


# ---------------------------------------------------------------------------
# Character file (markdown with YAML body, tolerant to agent drift)
# ---------------------------------------------------------------------------

def load_character(char_path: str) -> tuple[str, dict]:
    """Return (heading_line, parsed_yaml). Raises FileNotFoundError / ValueError."""
    text = read_text(char_path)
    if text is None:
        raise FileNotFoundError(char_path)
    lines = text.split("\n", 1)
    if not lines:
        raise ValueError(f"Empty file: {char_path}")
    heading = lines[0]
    body = lines[1] if len(lines) > 1 else ""
    body = _normalize_character_yaml(body)
    try:
        data = yaml.safe_load(body) or {}
    except yaml.YAMLError as e:
        raise ValueError(f"YAML parse error in {char_path}: {e}") from e
    return heading, data


def _normalize_character_yaml(text: str) -> str:
    """Fix common agent-produced indentation drift.

    Observed corruption: list items for `change_log:` or `conditions:` or `flags:`
    appended by the agent start at column 0 ("- foo") instead of "  - foo".

    Strategy: when the file enters a block-list key (line like "key:" followed
    by a 2-space indented "- "), any subsequent bare "- " at column 0 that
    appears BEFORE the next top-level key is re-indented to match the list.
    """
    lines = text.split("\n")
    out = []
    in_block_list = False
    block_indent = 2  # default
    for i, line in enumerate(lines):
        if not line:
            out.append(line)
            continue
        # Top-level key — resets list tracking
        if re.match(r"^[a-zA-Z_][\w]*:", line):
            # Check if this key opens a block list
            in_block_list = False
            key_name = line.split(":", 1)[0]
            # Look ahead for indented list item
            for j in range(i + 1, min(i + 4, len(lines))):
                nxt = lines[j]
                if not nxt.strip():
                    continue
                m = re.match(r"^(\s+)-\s", nxt)
                if m:
                    in_block_list = True
                    block_indent = len(m.group(1))
                    break
                if re.match(r"^[a-zA-Z_]", nxt):
                    break
            out.append(line)
            continue
        # Bare list item at column 0 — re-indent if inside a list context
        if in_block_list and re.match(r"^-\s", line):
            out.append((" " * block_indent) + line)
            continue
        out.append(line)
    return "\n".join(out)


def dump_character(heading: str, data: dict) -> str:
    """Serialize back to the markdown+YAML format."""
    body = yaml.safe_dump(data, allow_unicode=True, sort_keys=False, default_flow_style=False, width=10000)
    return heading + "\n\n" + body


# ---------------------------------------------------------------------------
# Locale templates
# ---------------------------------------------------------------------------

def template_path(lang: str, name: str) -> str:
    return os.path.join(REPO_ROOT, "locales", lang, "templates", name)


def load_template(lang: str, name: str) -> str | None:
    """Load a template file; fall back to English if missing."""
    path = template_path(lang, name)
    text = read_text(path)
    if text is not None:
        return text
    fallback = template_path("en", name)
    return read_text(fallback)


# ---------------------------------------------------------------------------
# State file section helpers
# ---------------------------------------------------------------------------

def replace_md_section(text: str, heading: str, new_body: str) -> str:
    """Rewrite the `## heading` section with `new_body`. If missing, append."""
    pattern = re.compile(rf"(^##\s+{re.escape(heading)}\s*\n)(.*?)(?=^##\s|\Z)", re.MULTILINE | re.DOTALL)
    replacement = rf"\1{new_body.rstrip()}\n\n"
    new_text, n = pattern.subn(replacement, text)
    if n == 0:
        # Append at end
        if not text.endswith("\n"):
            text += "\n"
        new_text = text + f"\n## {heading}\n{new_body.rstrip()}\n"
    return new_text


# ---------------------------------------------------------------------------
# Error formatting
# ---------------------------------------------------------------------------

def die(msg: str, code: int = 1) -> None:
    print(f"Error: {msg}", file=sys.stderr)
    sys.exit(code)
