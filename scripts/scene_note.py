#!/usr/bin/env python3
"""
MasterClaw scene/NPC sheet notetaker.

Creates or updates the compact tag-bag YAML cheatsheets that live in
`games/<game>/scenes/<id>.md` and `games/<game>/npcs_adhoc/<id>.md`.
Companion to `turn_commit.py`:
- `scene_note.py` is the CREATE / FULL-WRITE path (one call, all fields).
- `turn_commit.py` handles in-action APPENDS (state_change, recent_interaction)
  bundled with the rest of Step 8 writes.

Why a separate script: the playtest showed agents skip sheet creation when
the only path is hand-built `write_file` with the full template. Folding
the boilerplate into a script with validated CLI flags makes "create the
sheet" cheaper than "skip it".

Usage:
  scene <scene_id> — create or update a scene sheet
    --type location|sub_location|event_site   # default: location
    --parent <scene_id>                       # default: null (top-level)
    --first-visited "день 1, утро"            # required on create
    --layout TAG (repeatable)                 # at least 2 on create
    --atmosphere TAG (repeatable)             # at least 2 on create
    --linked-npc ID (repeatable)
    --prop "key:tag1,tag2,..." (repeatable)   # parsed into props mapping
    --append-change "д2 полдень: ..."         # appends to state_changes
    --update                                  # allow overwrite of existing fields

  npc <npc_id> — create or update an improvised NPC sheet
    --type improvised|canonical_override      # default: improvised
    --first-seen "день 2, таверна"            # required on create
    --known-name "Эдвин" (or omit for null)
    --appearance TAG (repeatable)             # at least 2 on create
    --voice TAG (repeatable)                  # at least 1 on create
    --known-fact TAG (repeatable)
    --relation TAG (repeatable)
    --append-interaction "д2 полдень: ..."    # appends to recent_interactions
    --update

  connect — update scenes/_index.md graph
    --parent <scene_id> --type town|region|dungeon|...
    --add-sub-scene <scene_id> (repeatable)
    --link-from <scene_id> --link-to <scene_id> --direction <dir>

Common flags:
  --base PATH    override DEFAULT_BASE
  --json         emit a JSON summary on stdout instead of human text

Exit codes:
  0 — wrote (or would-write with --dry-run)
  1 — validation error (missing required field, bad value)
  2 — I/O / argument error / file not found / file exists without --update
"""

from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import yaml  # noqa: E402  — _mc_common already validated PyYAML availability

from _mc_common import (  # noqa: E402
    game_dir,
    read_text,
    write_text,
    die,
)


# ---------------------------------------------------------------------------
# YAML body parse / dump helpers
# ---------------------------------------------------------------------------

# Sheets are stored as: top comment line "# scene: <id>" or "# npc: <id>"
# followed by a blank line and a YAML document.

def parse_sheet(path: str) -> tuple[str | None, dict | None]:
    """Return (header_comment, yaml_dict). Both None if file missing."""
    text = read_text(path)
    if text is None:
        return None, None
    lines = text.split("\n", 1)
    header = lines[0] if lines else ""
    body = lines[1] if len(lines) > 1 else ""
    body = body.lstrip("\n")
    try:
        data = yaml.safe_load(body) or {}
    except yaml.YAMLError as e:
        raise ValueError(f"YAML parse error in {path}: {e}") from e
    if not isinstance(data, dict):
        raise ValueError(f"Sheet body is not a mapping in {path}")
    return header, data


def dump_sheet(header: str, data: dict) -> str:
    body = yaml.safe_dump(
        data,
        allow_unicode=True,
        sort_keys=False,
        default_flow_style=False,
        width=10000,
    )
    return f"{header}\n\n{body}"


def parse_prop(spec: str) -> tuple[str, list[str]]:
    """`stoyka:duboraya,carapiny` -> ("stoyka", ["duboraya","carapiny"])"""
    if ":" not in spec:
        raise ValueError(f"--prop expects 'key:tag1,tag2,...' format, got {spec!r}")
    key, vals = spec.split(":", 1)
    key = key.strip()
    tag_list = [t.strip() for t in vals.split(",") if t.strip()]
    if not key:
        raise ValueError(f"--prop key is empty in {spec!r}")
    if not tag_list:
        raise ValueError(f"--prop {key} has no tags in {spec!r}")
    return key, tag_list


# ---------------------------------------------------------------------------
# Scene
# ---------------------------------------------------------------------------

SCENE_TYPES = {"location", "sub_location", "event_site"}


def cmd_scene(args, game_path: str) -> dict:
    scene_id = args.id
    if not scene_id.replace("_", "").replace("-", "").isascii() or " " in scene_id:
        die(f"scene_id '{scene_id}' must be snake_case ASCII (no spaces / Cyrillic)", 1)

    path = os.path.join(game_path, "scenes", f"{scene_id}.md")
    existing_header, existing = parse_sheet(path)
    is_new = existing is None

    if is_new:
        # CREATE — enforce minimum fields
        if not args.first_visited:
            die("--first-visited is required on create", 1)
        if len(args.layout) < 2:
            die("at least 2 --layout tags required on create (got "
                f"{len(args.layout)})", 1)
        if len(args.atmosphere) < 2:
            die("at least 2 --atmosphere tags required on create (got "
                f"{len(args.atmosphere)})", 1)
        if args.type not in SCENE_TYPES:
            die(f"--type must be one of {sorted(SCENE_TYPES)} (got {args.type!r})", 1)

        data: dict = {
            "type": args.type,
            "parent": args.parent or None,
            "first_visited": args.first_visited,
            "last_visited": args.first_visited,
        }
        if args.linked_npc:
            data["linked_npcs"] = list(args.linked_npc)
        data["layout"] = list(args.layout)
        if args.prop:
            data["props"] = {}
            for spec in args.prop:
                key, tags = parse_prop(spec)
                data["props"][key] = tags
        data["atmosphere_tags"] = list(args.atmosphere)
        if args.append_change:
            data["state_changes"] = list(args.append_change)
        header = f"# scene: {scene_id}"
    else:
        # UPDATE — must specify --update
        if not args.update and not args.append_change:
            die(f"sheet already exists: {path}. Pass --update to merge fields, "
                "or --append-change to append a state-change entry only.", 2)
        data = dict(existing or {})

        # Apply field overrides only if --update flag set
        if args.update:
            if args.type and args.type in SCENE_TYPES:
                data["type"] = args.type
            if args.parent is not None:
                data["parent"] = args.parent or None
            if args.first_visited:
                # Don't overwrite first_visited unless empty — touch last_visited
                if not data.get("first_visited"):
                    data["first_visited"] = args.first_visited
                data["last_visited"] = args.first_visited
            if args.layout:
                # Merge: keep existing, add new without dups
                merged = list(data.get("layout") or [])
                for t in args.layout:
                    if t not in merged:
                        merged.append(t)
                data["layout"] = merged
            if args.atmosphere:
                merged = list(data.get("atmosphere_tags") or [])
                for t in args.atmosphere:
                    if t not in merged:
                        merged.append(t)
                data["atmosphere_tags"] = merged
            if args.linked_npc:
                merged = list(data.get("linked_npcs") or [])
                for n in args.linked_npc:
                    if n not in merged:
                        merged.append(n)
                data["linked_npcs"] = merged
            if args.prop:
                props = dict(data.get("props") or {})
                for spec in args.prop:
                    key, tags = parse_prop(spec)
                    existing_tags = list(props.get(key) or [])
                    for t in tags:
                        if t not in existing_tags:
                            existing_tags.append(t)
                    props[key] = existing_tags
                data["props"] = props

        if args.append_change:
            changes = list(data.get("state_changes") or [])
            for c in args.append_change:
                changes.append(c)
            data["state_changes"] = changes

        header = existing_header or f"# scene: {scene_id}"

    if args.dry_run:
        return {"would_write": path, "is_new": is_new, "fields": list(data.keys())}

    write_text(path, dump_sheet(header, data))
    return {"path": os.path.relpath(path, game_path), "created" if is_new else "updated": True}


# ---------------------------------------------------------------------------
# NPC
# ---------------------------------------------------------------------------

NPC_TYPES = {"improvised", "canonical_override"}


def cmd_npc(args, game_path: str) -> dict:
    npc_id = args.id
    if not npc_id.replace("_", "").replace("-", "").isascii() or " " in npc_id:
        die(f"npc_id '{npc_id}' must be snake_case ASCII (no spaces / Cyrillic)", 1)

    path = os.path.join(game_path, "npcs_adhoc", f"{npc_id}.md")
    existing_header, existing = parse_sheet(path)
    is_new = existing is None

    if is_new:
        if not args.first_seen:
            die("--first-seen is required on create", 1)
        if len(args.appearance) < 2:
            die("at least 2 --appearance tags required on create", 1)
        if len(args.voice) < 1:
            die("at least 1 --voice tag required on create", 1)
        if args.type not in NPC_TYPES:
            die(f"--type must be one of {sorted(NPC_TYPES)} (got {args.type!r})", 1)

        data: dict = {
            "type": args.type,
            "first_seen": args.first_seen,
            "known_name": args.known_name or None,
            "appearance": list(args.appearance),
            "voice": list(args.voice),
        }
        if args.known_fact:
            data["known_facts"] = list(args.known_fact)
        if args.relation:
            data["relation_to_party"] = list(args.relation)
        if args.append_interaction:
            data["recent_interactions"] = list(args.append_interaction)
        header = f"# npc: {npc_id}"
    else:
        if not args.update and not args.append_interaction:
            die(f"sheet already exists: {path}. Pass --update to merge fields, "
                "or --append-interaction to append an interaction entry only.", 2)
        data = dict(existing or {})

        if args.update:
            if args.type and args.type in NPC_TYPES:
                data["type"] = args.type
            if args.first_seen and not data.get("first_seen"):
                data["first_seen"] = args.first_seen
            if args.known_name is not None:
                data["known_name"] = args.known_name or None
            if args.appearance:
                merged = list(data.get("appearance") or [])
                for t in args.appearance:
                    if t not in merged:
                        merged.append(t)
                data["appearance"] = merged
            if args.voice:
                merged = list(data.get("voice") or [])
                for t in args.voice:
                    if t not in merged:
                        merged.append(t)
                data["voice"] = merged
            if args.known_fact:
                merged = list(data.get("known_facts") or [])
                for t in args.known_fact:
                    if t not in merged:
                        merged.append(t)
                data["known_facts"] = merged
            if args.relation:
                merged = list(data.get("relation_to_party") or [])
                for t in args.relation:
                    if t not in merged:
                        merged.append(t)
                data["relation_to_party"] = merged

        if args.append_interaction:
            ints = list(data.get("recent_interactions") or [])
            for c in args.append_interaction:
                ints.append(c)
            data["recent_interactions"] = ints

        header = existing_header or f"# npc: {npc_id}"

    if args.dry_run:
        return {"would_write": path, "is_new": is_new, "fields": list(data.keys())}

    write_text(path, dump_sheet(header, data))
    return {"path": os.path.relpath(path, game_path), "created" if is_new else "updated": True}


# ---------------------------------------------------------------------------
# Connect — scenes/_index.md graph
# ---------------------------------------------------------------------------

def cmd_connect(args, game_path: str) -> dict:
    """Update or create the scene graph in scenes/_index.md.

    Schema (per scenes/SKILL.md):
        <parent_id>:
          type: <town | region | dungeon | ...>
          sub_scenes: [<scene_id>, ...]
          connections:
            - {from: <id>, to: <id>, direction: <dir>}
    """
    index_path = os.path.join(game_path, "scenes", "_index.md")
    text = read_text(index_path) or "# Scene graph\n"
    head, _, body = text.partition("\n")
    if not head.startswith("#"):
        head, body = "# Scene graph", text
    body = body.lstrip("\n")
    try:
        graph = yaml.safe_load(body) if body.strip() else {}
    except yaml.YAMLError as e:
        die(f"failed to parse {index_path}: {e}", 2)
    if graph is None:
        graph = {}
    if not isinstance(graph, dict):
        die(f"_index.md root must be a mapping; got {type(graph).__name__}", 2)

    if not args.parent and not (args.link_from and args.link_to):
        die("connect requires either --parent or both --link-from and --link-to", 1)

    parent_id = args.parent
    if parent_id:
        node = dict(graph.get(parent_id) or {})
        if args.parent_type:
            node["type"] = args.parent_type
        if args.add_sub_scene:
            subs = list(node.get("sub_scenes") or [])
            for s in args.add_sub_scene:
                if s not in subs:
                    subs.append(s)
            node["sub_scenes"] = subs
        graph[parent_id] = node

    if args.link_from and args.link_to:
        # Place the connection under the from-scene's parent if discoverable,
        # else at the top-level under from-scene's id (treats from as parent of itself)
        host_id = None
        for pid, pnode in graph.items():
            if not isinstance(pnode, dict):
                continue
            if args.link_from in (pnode.get("sub_scenes") or []) or args.link_from == pid:
                host_id = pid
                break
        if host_id is None:
            host_id = args.link_from
            graph.setdefault(host_id, {"type": "auto"})

        node = dict(graph.get(host_id) or {})
        conns = list(node.get("connections") or [])
        new_conn = {"from": args.link_from, "to": args.link_to}
        if args.direction:
            new_conn["direction"] = args.direction
        # Dedup by (from, to)
        if not any(c.get("from") == args.link_from and c.get("to") == args.link_to for c in conns):
            conns.append(new_conn)
        node["connections"] = conns
        graph[host_id] = node

    if args.dry_run:
        return {"would_write": index_path, "graph_keys": list(graph.keys())}

    out = head + "\n\n" + yaml.safe_dump(
        graph, allow_unicode=True, sort_keys=False, default_flow_style=False, width=10000
    )
    write_text(index_path, out)
    return {"path": os.path.relpath(index_path, game_path), "updated": True}


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def _add_common(p: argparse.ArgumentParser) -> None:
    """Common flags repeated on each subparser so they can appear after the
    subcommand keyword (more natural for CLI usage)."""
    p.add_argument("--base", default=None)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--json", action="store_true")


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("game", help="Game name under GameMaster/games/")
    _add_common(ap)
    sub = ap.add_subparsers(dest="kind", required=True)

    sc = sub.add_parser("scene", help="create or update a scene sheet")
    _add_common(sc)
    sc.add_argument("id", help="scene_id (snake_case ASCII)")
    sc.add_argument("--type", default="location",
                    help="location | sub_location | event_site")
    sc.add_argument("--parent", default=None)
    sc.add_argument("--first-visited", default=None)
    sc.add_argument("--layout", action="append", default=[])
    sc.add_argument("--atmosphere", action="append", default=[])
    sc.add_argument("--linked-npc", action="append", default=[])
    sc.add_argument("--prop", action="append", default=[],
                    help="'key:tag1,tag2,...' (repeatable)")
    sc.add_argument("--append-change", action="append", default=[])
    sc.add_argument("--update", action="store_true",
                    help="allow merge into existing sheet")

    np_ = sub.add_parser("npc", help="create or update an NPC sheet")
    _add_common(np_)
    np_.add_argument("id", help="npc_id (snake_case ASCII)")
    np_.add_argument("--type", default="improvised",
                     help="improvised | canonical_override")
    np_.add_argument("--first-seen", default=None)
    np_.add_argument("--known-name", default=None)
    np_.add_argument("--appearance", action="append", default=[])
    np_.add_argument("--voice", action="append", default=[])
    np_.add_argument("--known-fact", action="append", default=[])
    np_.add_argument("--relation", action="append", default=[])
    np_.add_argument("--append-interaction", action="append", default=[])
    np_.add_argument("--update", action="store_true")

    cn = sub.add_parser("connect", help="update scenes/_index.md graph")
    _add_common(cn)
    cn.add_argument("--parent", default=None,
                    help="parent scene_id to upsert as a graph node")
    cn.add_argument("--parent-type", default=None,
                    help="type for the parent node (town/region/dungeon/...)")
    cn.add_argument("--add-sub-scene", action="append", default=[])
    cn.add_argument("--link-from", default=None)
    cn.add_argument("--link-to", default=None)
    cn.add_argument("--direction", default=None,
                    help="north / south / east / west / up / down / 'across square' / ...")

    return ap


def main():
    args = build_parser().parse_args()
    try:
        game_path = game_dir(args.game, args.base)
    except FileNotFoundError as e:
        die(str(e), 2)

    if args.kind == "scene":
        result = cmd_scene(args, game_path)
    elif args.kind == "npc":
        result = cmd_npc(args, game_path)
    elif args.kind == "connect":
        result = cmd_connect(args, game_path)
    else:
        die(f"unknown subcommand: {args.kind}", 2)

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        for k, v in result.items():
            print(f"{k}: {v}")


if __name__ == "__main__":
    main()
