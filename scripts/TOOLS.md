# MasterClaw script tools — quick reference

Flat-file utilities the GM agent calls via bash. All scripts:
- read input from args or stdin
- write result to stdout (human or JSON)
- write diagnostics to stderr
- use exit codes: 0 = ok, 1 = validation error, 2 = I/O / argument error
- run on Python 3.10+ with PyYAML

Below: when each script fires in the turn lifecycle, what it replaces, and how to wire it into the skills later.

---

## `roll.py <pool> <difficulty>` (existing)

**When.** actions/SKILL.md Step 5, after player confirms.
**Replaces.** Manual dice — never used after this was added.
**Input.** CLI positional.
**Output.** Player-facing block + `[GM LOG: ...]` line.

---

## `post_narrative.py <webhook> <text> [flags]` (existing + extended)

**When.** actions/SKILL.md Step 6b / narrator/SKILL.md after composing narrative.
**Replaces.** Direct HTTP to Discord; now also gates on style budget and language lint.

**New flags:**
- `--style <preset>` — soft warn if over word budget (see narrator/SKILL.md §9).
- `--max-words N` — override preset budget.
- `--lint-lang ru` — BLOCK if ASCII words detected.
- `--force` — bypass lint refusal for proper nouns.

**Typical call:**
```bash
python3 /root/.microclaw/scripts/post_narrative.py '<url>' '<text>' \
    --style documentary --lint-lang ru
```

---

## `lint_lang.py --lang <code> < text` (existing)

**When.** Invoked by `post_narrative.py` via `--lint-lang`. Can also be called standalone for log / state writes.
**Input.** Text on stdin.
**Output.** JSON `{lang, count, warnings:[{word, context}]}`.
**Exit.** 0 clean, 1 warnings found.

---

## `session_snapshot.py <game>` (existing)

**When.** session/SKILL.md continue step 2 (instead of pointwise reads); every 10-15 turns for re-sync.
**Replaces.** Reading game.md + state.md + characters/*.md + log.md tail + scenes/_index.md separately.
**Output.** ≤80-line briefing: header / scene / party / last 3 rolls / plot / NPCs in scene / sub-scenes.

---

## `build_pool.py <game> <character> [flags] --difficulty D` (NEW)

**When.** actions/SKILL.md Step 4 — replace the manual "read character, compute pool, write string" flow.
**Replaces.** Agent computing pool in memory. Script validates against the character file on disk.

**Flags (repeat as needed):**
- `--trait <name>` — repeatable. Preferred when names contain commas.
- `--aspect <name>` — repeatable; must belong to one of the listed `--trait`s.
- `--flag <text>` — at most one (validated).
- `--reserve-spent N` — how many reserve dice the player added (after confirm).
- `--difficulty D` — required.
- `--lang ru|en` — optional override; default from game.md.
- `--json` — emit structured JSON instead of the one-line display.

**Validation catches:**
- Trait not on character → suggests close match.
- Aspect belongs to a trait not in `--trait` → explicit error.
- Reserve-spent > current → error.
- >1 flag → error.

**Typical call (pre-confirm):**
```bash
python3 build_pool.py silverwood_glen garran \
  --trait "Боевой молот" --trait "Крепкий, как наковальня" \
  --aspect "мощные удары" --aspect "железная хватка" \
  --flag "Не доверяет магии" \
  --difficulty 3
```

**Output (stdout):**
```
🎲 Пул (5 кубов): Боевой молот +1, Крепкий, как наковальня +1, мощные удары +1, железная хватка +1, флаг «Не доверяет магии» +1. Резерв?
Сложность: 3.
```

Pipe this line directly into `render_response.py` as `pool_line`.

---

## `scene_note.py <game> {scene|npc|connect} ...` (NEW)

**When.** scenes/SKILL.md write discipline, and `actions/SKILL.md` Step 8b — whenever the agent has improvised a scene or NPC NOT defined in `worlds/`. Create the sheet here BEFORE calling `turn_commit.py` (the latter only appends to existing sheets).

**Replaces.** Hand-built `write_file` of the sheet template plus YAML you must format yourself. Now: argparse-driven flags, schema-validated, snake_case enforced.

**Subcommands:**

| Sub | Purpose | Required on create |
|---|---|---|
| `scene <id>` | create or update `scenes/<id>.md` | `--type`, `--first-visited`, ≥2 `--layout`, ≥2 `--atmosphere` |
| `npc <id>` | create or update `npcs_adhoc/<id>.md` | `--first-seen`, ≥2 `--appearance`, ≥1 `--voice` |
| `connect` | upsert nodes / edges in `scenes/_index.md` | one of `--parent` or both `--link-from`+`--link-to` |

`<id>` must be snake_case ASCII (no spaces, no Cyrillic). Refuses to overwrite an existing sheet without `--update`. Use `--append-change "..."` (scene) / `--append-interaction "..."` (npc) to add a single timeline entry without touching other fields.

**Typical calls:**

```bash
# new scene
python3 scene_note.py silverwood_glen scene irvin_market_square \
  --type location --first-visited "д1 утро" \
  --layout "вход_юг" --layout "лоток_булочника_северо-восток" \
  --atmosphere "пар_от_печи" --atmosphere "хлеб+специи" \
  --linked-npc "yanov_baker" \
  --prop "лоток:дубовый,навес,2_корзины"

# new improvised NPC
python3 scene_note.py silverwood_glen npc yanov_baker \
  --first-seen "д1 утро, рынок Ирвина" --known-name "Старый Янов" \
  --appearance "красное_лицо" --appearance "ловкие_руки" \
  --voice "хриплый"

# graph upsert
python3 scene_note.py silverwood_glen connect \
  --parent irvin --parent-type town \
  --add-sub-scene irvin_market_square --add-sub-scene irvin_tavern_black_cat
```

**Output.** Human-readable status by default (`path: ..., created: True`); `--json` for machine-parseable form. Use `--dry-run` to preview without writing.

---

## `turn_commit.py <game> [--dry-run]` (NEW)

**When.** actions/SKILL.md Step 8 — batch-write log / character / state / scene sheet / npc sheet in one atomic call.
**Replaces.** 3-5 separate `read + edit` pairs. Failures roll back.

**Input.** JSON on stdin. Schema:
```json
{
  "log_entry": {
    "kind": "roll",
    "heading": "Разрушение осколка в подвале",
    "action": "Гарран — удар молотом",
    "roll": "6d6 [6,5,6,2,3,4] → 4 succ vs diff 2",
    "narrator": "Игрок",
    "conditions": "нет",
    "reserve": "0 → 0",
    "consequences": "осколок разбит, жилки погасли"
  },
  "character_update": {
    "name": "garran",
    "reserve": 0,
    "add_conditions": [{"text": "...", "source": "..."}],
    "remove_conditions": ["<text to match>"],
    "change_log_entry": "разбил большой осколок"
  },
  "state_update": {
    "current_scene": "Подвал Крёкхолла. Жилки погасли.",
    "sections": {"Key NPC status": "обновлённый текст"}
  },
  "scene_sheet_append": {
    "scene_id": "krekhol_ritual_cellar",
    "state_change": "d2 midday: осколок разрушен"
  },
  "npc_sheet_append": {
    "npc_id": "edwin_stranger",
    "recent_interaction": "d2 evening: заметил молот"
  }
}
```

Any top-level section can be omitted. `--dry-run` validates and reports `{would_write: [...]}` without touching files.

**Output (stdout, after commit):**
```json
{
  "ok": true,
  "written": ["log.md", "characters/garran.md", "state.md"]
}
```

---

## `render_response.py [--lang ru] [--check]` (NEW)

**When.** Final step of every turn in game channel — compose the player-facing message.
**Replaces.** Manual assembly per game_response.md. The agent provides structured data + optional free-form prose; the script produces consistent layout (code blocks for mechanics, reserve display, CTA, proper section order).

**Freedom preserved.** Every type accepts:
- `flavor_prefix` — free text above the mechanics (supports markdown: italics, bold)
- `flavor_suffix` — free text below

The script never edits or validates flavor prose. It only renders the mechanical/structural block.

**Types:**

| type | Required | Optional | Code block? |
|---|---|---|---|
| `action_roll` | pool_line, difficulty, reserve_current | failure_stakes, cta | yes (mechanics) |
| `roll_result` | roll_block, reserve_after | interpretation, reserve_delta, conditions_changed, narrator_rights_prompt, cta | yes (roll_block only) |
| `auto_success` | body | reserve_after, reserve_delta, cta | no |
| `scene_description` | body | — | no |
| `narrator_handoff` | setup, rights_level | options_hint | no |
| `reserve_update` | reserve_before, reserve_after | reason | no |
| `simple` | body | code_block (bool or "auto"), cta | configurable |

**Rights level → prompt** (for `narrator_handoff`): built-in table for `disabled`, `minor`, `significant`, `madness` in both languages. Override via `options_hint`.

**Typical call:**
```bash
cat <<'JSON' | python3 render_response.py --lang ru
{
  "type": "roll_result",
  "flavor_prefix": "*Молот идёт вниз с хрустом.*",
  "roll_block": "<verbatim from roll.py>",
  "interpretation": "Осколок треснул вдоль трещины. Полный успех.",
  "narrator_rights_prompt": "**Да, и кроме того...** — твой ход.",
  "reserve_after": 0,
  "reserve_delta": 0,
  "cta": "Рассказывай."
}
JSON
```

**Validation.** `--check` runs only the schema check (reports missing fields) without rendering. Use this to pre-flight payload structure.

---

## Turn lifecycle after integration

Numbers in parentheses = LLM iterations (approximate).

### Full action with roll (before → after)

| Step | Before | After |
|---|---|---|
| 0. Validate | read character (1) | read character (1) |
| 4. Build pool | read character again + mental math + format (2-3) | `build_pool.py` (1) |
| 5. Announce & wait | emit text (1) | `render_response.py --type action_roll` (1) |
| Confirm + roll | `roll.py` (1) | `roll.py` (1) |
| 6b. Narrative | read scene sheet + compose + `post_narrative.py` (2-3) | same (2-3) |
| 8. Writes | 3-4 separate edits (6-8) | `turn_commit.py` (1) |
| 9. Game response | emit text (1) | `render_response.py --type roll_result` (1) |

**Before: ~13-17 iterations. After: ~8 iterations.**

### Scene question (narrator mode)

| Before | After |
|---|---|
| read state + world + scene sheet (2-3) | read scene sheet (1) or session_snapshot (1) |
| compose + post (2) | same (2) |
| emit game response (1) | `render_response.py --type scene_description` (1) |

**Before: ~5. After: ~4.**

---

## Wired into skills

As of April 2026, all of the above tools are referenced from the skill files and soul:

- `build_pool.py` — `actions/SKILL.md` Step 4, `souls/gamemaster.md` Rule 0c
- `roll.py` — `actions/SKILL.md` Step 5, `souls/gamemaster.md` "Dice rolling" section
- `turn_commit.py` — `actions/SKILL.md` Step 8a, `souls/gamemaster.md` Rule 0b
- `scene_note.py` — `scenes/SKILL.md` write discipline, `actions/SKILL.md` Step 8b
- `post_narrative.py` — `actions/SKILL.md` Step 6b, `souls/gamemaster.md` Rule 0a8
- `session_snapshot.py` — `session/SKILL.md` continue step
- `render_response.py` — opt-in; the old per-turn manual assembly per `game_response.md` template still works, render_response is for agents that benefit from structured emission. Not wired as mandatory.

If a tool returns a validation error, fix the args and re-run. Do NOT fall back to hand-built `edit_file` / inline pool strings — those are the patterns the scripts replace.
