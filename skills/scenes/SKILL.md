# SKILL: Scene and NPC Cheatsheets (BlackBirdPie)

Manages compact, tag-based narrative memory for the **improvised** content of a game: scenes the GM described ad-hoc and NPCs that were not written into the world before play.

Purpose: keep returning visits to the same place consistent. The lamp that flickers in the corner stays there. The bartender's scar stays above the left brow. Without a sheet, the GM re-improvises details on each visit and they drift.

## When to use

### Write (create / update)
- GM just described a location or sub-location that is **not** defined in `worlds/<world>/world.md` — create `games/<game>/scenes/<scene_id>.md`.
- GM introduced a new NPC who is **not** in `worlds/<world>/npcs.md` — create `games/<game>/npcs_adhoc/<npc_id>.md`.
- Something in a known scene/NPC changed (vase broken, blood on the floor, NPC lost a hand) — append to `state_changes` / update the relevant section.
- New connection between scenes established — update `games/<game>/scenes/_index.md`.

### Read
- Before composing a narrative block about a **previously described** scene or NPC — load the sheet first. Without this, re-narration will drift.
- When answering a player question like "что вокруг?" / "как выглядит NPC?" about a known place or person.

## What these sheets are NOT

- NOT plot, motivations, or secrets — those belong in `worlds/<world>/plot.md` and `state.md`.
- NOT full prose — that is `log.md` and the narrative channel itself.
- NOT character rules / stats / reserve — that is `characters/<name>.md`.
- NOT canonical world content — worlds/ stays authoritative for pre-written locations and NPCs. Sheets only cover ad-hoc additions and details that emerged during play.

Rule of thumb: if it would help you re-describe the same place/person consistently next session, it belongs here. If it changes what the character CAN do or what the story IS about, it belongs in state.md or a character sheet or plot.md.

## File structure

```
games/<game>/
├── scenes/
│   ├── _index.md                      ← scene graph (connections, parents, directions)
│   ├── <scene_id>.md                  ← per-scene sheet (YAML)
│   └── ...
└── npcs_adhoc/
    ├── <npc_id>.md                    ← per-NPC sheet (YAML)
    └── ...
```

## Naming conventions

- `scene_id` and `npc_id`: snake_case ASCII, no spaces, no Cyrillic, no punctuation.
- Scenes: prefix by parent location for uniqueness and discoverability.
  - Good: `ivren_tavern_bearded_fish`, `krekhol_herbalist_house`, `silverwood_forest_edge`
  - Bad: `tavern`, `herbalist`, `the_old_place`
- NPCs: short descriptive handle; if the name is known use it, otherwise use a role+hint.
  - Good: `edwin_stranger`, `old_woman_well`, `bartender_merrik`
  - Bad: `npc1`, `the_guy`, `мужик_в_плаще`
- Tag values INSIDE sheets — in the game language (the `language` field in `game.md`).
- YAML schema keys (`type`, `parent`, `layout`, etc.) — always English.

## Scene sheet

Template: `locales/{lang}/templates/scene_sheet.md`.

Minimum required fields when creating:
- `type` — `location` | `sub_location` | `event_site`
- `parent` — parent `scene_id` or `null` for top-level
- `first_visited` — "day/time"
- `layout` — at least 2–3 positional tags
- `atmosphere_tags` — at least 2 sensory tags

Optional but strongly encouraged: `props`, `linked_npcs`, `state_changes`.

Keep one sheet per discrete scene. A tavern building and its upstairs room can be one sheet with both in `layout`, OR two sheets (`<prefix>_tavern` parent, `<prefix>_tavern_room_2` sub_location) — pick based on how much detail each has. Use sub_location only if the place is visited on its own and accumulates > ~3 detail tags.

## NPC sheet

Template: `locales/{lang}/templates/npc_sheet.md`.

Create for NPCs not in `worlds/<world>/npcs.md`. For canonical NPCs: do NOT create a parallel sheet here. If new emergent facts about a canonical NPC must be remembered (e.g. they acquired an injury during play), put those in `state.md` under NPC status — sheets are for improvised NPCs only.

Minimum required fields when creating:
- `type` — `improvised` (99% of cases; `canonical_override` only for rare cases where world NPC's appearance needs a session-level override)
- `first_seen` — "day/time/scene"
- `appearance` — at least 2 tags
- `voice` — at least 1 tag

## Scene index (`_index.md`)

Plain YAML, single file per game. Describes the graph of scenes:

```yaml
<parent_id>:
  type: <town | region | dungeon | ...>
  sub_scenes: [<scene_id>, <scene_id>, ...]
  connections:
    - from: <scene_id>
      to: <scene_id>
      direction: <north | south | east | west | up | down | "across square" | ...>
```

Update the index whenever a new parent location is discovered, a new sub-scene is added under an existing parent, or a new connection between two scenes becomes known to the characters.

When `_index.md` is created:
- New games — written at game start by `session/SKILL.md` step 6 (the `# Scene graph` header only; the graph itself fills in as locations are visited).
- Existing games predating this skill — `session/SKILL.md` step 3b lazy-migrates the file with the same empty header on first continue. Old log.md is NOT retro-filled.
- After that, `scene_note.py connect` (or `scene_note.py scene` with `--update`) writes graph nodes and connections; never `edit_file` directly.

## Write discipline

- **Atomic with log.md:** when an action or scene opening produces new ad-hoc detail, the sheet write goes in the SAME turn as the log.md append. Do NOT defer "I'll write the sheet later" — you will forget, and next visit will drift.
- **Update, don't rewrite:** for state changes, append to the `state_changes:` list or add a new tag to the relevant key. Do not rewrite entire sections.
- **Never delete prior tags** unless the fact was narratively overwritten (e.g. "hearth: cold" after previously "hearth: live_fire" — append the change with a timestamp).

### Tooling

Two scripts cover the lifecycle. Always prefer them over `edit_file` on the sheet directly — they enforce schema and avoid YAML indentation drift.

**Create a new sheet** (or merge into an existing one with `--update`):

```bash
# Scene
python3 /root/.microclaw/scripts/scene_note.py <game> scene <scene_id> \
  --type location --first-visited "д1 утро" \
  --layout "<tag>" --layout "<tag>" \
  --atmosphere "<tag>" --atmosphere "<tag>" \
  [--linked-npc <id>] [--prop "key:tag,tag"]

# Improvised NPC
python3 /root/.microclaw/scripts/scene_note.py <game> npc <npc_id> \
  --first-seen "д1 утро, <scene>" [--known-name "Name"] \
  --appearance "<tag>" --appearance "<tag>" --voice "<tag>"

# Known connection between scenes (updates _index.md)
python3 /root/.microclaw/scripts/scene_note.py <game> connect \
  [--parent <id> --parent-type town --add-sub-scene <id>] \
  [--link-from <id> --link-to <id> --direction <north|...>]
```

`scene_note.py` enforces minimum required tags on create (`--type`, `--first-visited`/`--first-seen`, ≥2 layout / appearance, ≥2 atmosphere / ≥1 voice) and refuses snake_case violations.

**Append a state-change / interaction to an existing sheet** — bundle it with the rest of the turn's writes via `turn_commit.py`:

```json
{
  "scene_sheet_append": {"scene_id": "<id>", "state_change": "д<n> <time>: <change>"},
  "npc_sheet_append":   {"npc_id":   "<id>", "recent_interaction": "д<n> <time>: <event>"}
}
```

Or stand-alone via `scene_note.py ... --append-change "..."` / `--append-interaction "..."`.

⛔ `turn_commit.py` ONLY appends to existing sheets. Create the sheet with `scene_note.py` first, then `turn_commit.py` can fold the state-change into the same atomic action commit.

## Read discipline

Before narrating ANY block about a scene/NPC you've described before:

1. Check if a sheet exists — `read_file games/<game>/scenes/<scene_id>.md` or `npcs_adhoc/<npc_id>.md`.
2. Reuse the listed details. Do not invent new ones that contradict.
3. If you need a new detail (player asks "есть ли в таверне окна на запад?" and that's not in the sheet) — answer once, then immediately append the new tag to the sheet.

## Handoff with other skills

- `narrator/SKILL.md` section 10 (Seamless continuity) — requires sheet read before re-narrating. Sheets are the mechanism that enforces continuity.
- `actions/SKILL.md` Step 8 (Mandatory writes) — includes sheet writes when applicable.
- `session/SKILL.md` — initializes `scenes/_index.md` and `npcs_adhoc/` directory at game creation or on lazy migration of an old game.
- `world/SKILL.md` — world events that introduce new scenes or NPCs must trigger a sheet write before (or together with) the log entry.
