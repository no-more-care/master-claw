# SKILL: Processing Player Declarations (BlackBirdPie)

Authoritative procedure for handling any player action declaration.
For full rules — see skills/rules/SKILL.md.

## When to use
- Player announces intent to act
- Determine if action is possible, if a roll is needed, run it and apply result

## Active game
Find GameMaster/games/ folder with status active. Load character sheet before Step 1.
After roll — write to log.md, update state.md, update character file.

⛔ See rules/SKILL.md section 15 (GM Impartiality) — enforce at every step.

---

## Step 0. Validate the declaration

Before anything else, check the declaration against the character sheet.

**DENY — ask for a new declaration when:**
- Player uses equipment NOT on their sheet and NOT implied by a trait
- Player invents a new ability mid-scene not established before
- Declaration contradicts established world state or state.md
- Character has a condition that physically prevents this action

**Equipment implied by trait (no need on sheet):**
A character with a relevant professional trait obviously has basic tools of their trade.
When in doubt: "would this character obviously have this given their trait?" If yes, allow.

If declaration passes — proceed to Step 1.

---

## Step 0b. Multi-part player message (narration + new declaration)

Discord player messages often combine:
- Resolution of PENDING narrator rights (player's narration of a previous roll)
- AND a NEW action declaration

⛔ When you detect both in one message, process them SEQUENTIALLY — do not discard or merge.

**Detection heuristics:**
- Pending narrator rights are active if the last resolved roll gave the player "Yes, and..." or "No, but..." AND they haven't narrated yet
- Player's narration usually comes first as descriptive prose ("Я нахожу пару золотых слитков в песке")
- New declaration follows as intent ("потом иду дальше / направляюсь к Хельге / осматриваю следующее")

**Procedure:**
1. **Process the narration first:**
   - Validate against narrator rights limits (rules/SKILL.md section 11)
   - If accepted: post player's narration to narrative webhook, append to log.md, update state.md current scene + world changes, update character file if relevant (new items, conditions).
   - If rejected (over-scoped): respond with scale-back prompt in game channel, DO NOT process the new declaration yet — wait for player to revise
2. **Then process the new declaration** — restart from Step 0 with the new intent.

If unclear whether the message is a single narration or narration+declaration — ask: "Это часть наррации или уже новое действие? Если и то, и то — разделю."

Never silently drop pending narration to jump to the next roll.

---

## Step 1. Receive declaration

Any player message describing an action = a declaration. Narrative style counts.
If the goal is unclear — ask once before proceeding.

---

## Step 2. Is a roll needed?

**NO ROLL — automatic success when ANY of the following is true:**
1. The action is trivial and the character obviously has the skill for it (using a key you were given, walking across a room, eating food, simple conversation)
2. The action logically follows from a character trait AND difficulty would be ≤ 2 AND there is no tension AND failure would be boring
3. The character uses a previously obtained tool/item for its intended purpose

**Lean toward auto-success for mundane actions.** Not every action needs a roll. If there is no drama in failure — don't roll.

If auto-success: announce it briefly, describe or hand narrative to player. Skip to Step 8 (log if significant).

**ROLL NEEDED when any of the following is true:**
- Real chance of failure exists given the character's traits
- Failure has interesting story consequences
- Active tension (being watched, time pressure, opposition, unknown outcome)
- Player is attempting something at the edge of their established abilities

**When in doubt:** "would failure here be interesting or just annoying?" If annoying — auto-success. If interesting — roll.

---

## Step 3. Set difficulty

| Level | Description |
|---|---|
| 2 | Easy — distracted guard, familiar territory |
| 3 | Medium — standard obstacle, normal conditions |
| 4 | Hard — alert enemies, adverse conditions |
| 5 | Very hard — expert opposition |
| 6 | Extreme — legendary difficulty |
| 7+ | Nearly impossible |

Set difficulty based on the FICTION (what the world demands), never based on player resources or requests.

Announce difficulty and failure stakes to the player.

**⛔ MANDATORY PAUSE: wait for player response. Do NOT proceed to Step 4 without confirmation.**

Player may DECLINE after hearing the stakes. If they decline — no roll, no consequence, the moment passes.

---

## Step 4. Build dice pool

**⛔ MANDATORY: Use `build_pool.py` to construct the pool. Do NOT compute the pool by hand.** The script reads the character file from disk, validates that every cited trait/aspect/flag actually exists, and renders the canonical one-line display. Hand-building is the single largest source of past mistakes (level=dice confusion, missing aspects, hallucinated flag text).

```bash
python3 /root/.microclaw/scripts/build_pool.py <game> <character> \
  --trait "Trait A" --trait "Trait B" \
  --aspect "aspect 1" --aspect "aspect 2" \
  [--flag "exact flag text"] \
  --difficulty <D>
```

Pass `--reserve-spent N` only AFTER the player picks their reserve count in Step 5.

**Pool rules the script enforces:**

| Source | Condition | Dice |
|---|---|---|
| Each applicable trait | Trait logically applies to this action | **exactly +1** |
| Each applicable aspect | Aspect from one of the listed `--trait`s that logically fits | +1 per aspect |
| Flag (MAX 1) | Action genuinely matches the flag | +1 |
| Reserve dice | Player chooses how many to add | +X |

⛔ Trait level 3 = +1 die, NOT +3. Each trait always gives exactly +1 die. Level = aspect count only. The script will refuse to multiply by level — if you find yourself "explaining" why level should add more dice, you're wrong; trust the script.

**Self-check before invoking the script:**
1. Every `--trait` LOGICALLY applies to THIS action? ✓
2. Every `--aspect` belongs to one of the listed traits AND logically applies? ✓
3. At most one `--flag`? ✓

If the script returns a validation error (typo, missing trait, aspect on wrong trait) — fix the args and re-run; do NOT hand-author the pool string to bypass the script.

Paste the script's stdout (one or two lines) into your game-channel response. Then announce difficulty and failure stakes if the script didn't include them, and STOP — wait for the player.

---

## Step 5. Roll

**⛔ MANDATORY: Use the dice script for ALL rolls. Never generate dice manually.**

After the player names a reserve count, optionally re-run `build_pool.py` with `--reserve-spent N` to print the final pool line for the log, then roll:

```bash
python3 /root/.microclaw/scripts/roll.py <pool_size> <difficulty>
```

`<pool_size>` = base pool from `build_pool.py` + reserve dice the player added.

The script generates true random dice, counts hits (4-6), determines narrator rights.
**Paste the player-facing block into your response.** Use `[GM LOG: ...]` for log.md only.
**Do NOT manually count hits or determine narrator rights.** Trust the script output.

---

## Step 6. Apply narrator rights

The script output tells you who narrates. Follow it.

**When handing narrator rights to the player:**
Provide a brief setup (what happened mechanically, sensory context), then hand the word. Wait.

**If the player declines to narrate and passes rights back to GM:**
Accept and narrate with the SAME result type — do not punish for declining.

**When GM narrates "yes, but...":**
- The player succeeded FULLY. Do not downgrade the result.
- The "but" is a NEW world element (threat appears, clock starts, new info changes picture).
- Test: "would the player say I succeeded but wish I hadn't because of this complication?" If yes — good "but".
- Do NOT assign conditions on "yes, but..." — conditions belong to failures only.

**When GM narrates "no, and...":** describe setback, apply complication or condition.

⛔ Narrator rights limits: see rules/SKILL.md section 11 for hard limits on what players can/cannot narrate.

## Step 6b. Send narrative to narrative channel

If `narrative_webhook` is set in game.md → use `skills/narrative/SKILL.md`:

1. **Compose action narration** — describe WHAT the character did and HOW, using their applicable traits/aspects for flavour. This applies to EVERY resolved action, including auto-successes.
2. **POST action narration** to narrative webhook as a separate message.
3. If scene changed, location changed, or the world reacted — **compose scene narration** and POST it as a second message.

Use the style preset from game.md `narrative_style` field. See narrator/SKILL.md sections 8-10 for voice, action narration, and continuity rules.

Do NOT include dice numbers, pool breakdown, or reserve info — only the story.

⛔ **Posting to the webhook is NOT the end of the turn.** Continue to Step 7-8 and then send a response in the game channel following `locales/{lang}/templates/game_response.md`. Game channel response is REQUIRED on every turn — see gamemaster.md Rule 0b3.

---

## Step 7. Apply result and update reserve

**Success:** deduct spent reserve dice.
**Failure:** return spent dice + add 1 to reserve. Cap at 7.

Announce updated reserve to player.

---

## Step 8. ⛔ MANDATORY: Write files before next player response

**Do NOT send the next narrative response until all applicable writes are complete.**

⛔ The single most-skipped step in playtest. `roll.py`'s output ends with a `[NEXT STEP: ...]` sentinel reminding you to call `turn_commit.py` before responding. If you skip Step 8, the log loses the action, state.md drifts from reality, character reserve is stale, and the next session starts on broken state. None of this is recoverable from chat history.

Order, every roll-resolved action, no exceptions:
1. **NEW scene or NPC introduced this turn?** → `scene_note.py` (Step 8b below) FIRST.
2. `turn_commit.py` (Step 8a below) — log + character + state + sheet appends in one atomic call.
3. THEN compose and emit the game-channel narrative.

If you find yourself drafting narrative prose immediately after `roll.py` — STOP. Run the writes first; the prose will be identical, but the writes are non-recoverable later.

### 8a. Use `turn_commit.py` for the bundle

A single atomic call replaces the old "edit log.md, then character.md, then state.md, then a scene sheet" sequence (4-6 separate `read+edit` pairs). Pipe a JSON payload via stdin:

```bash
cat <<JSON | python3 /root/.microclaw/scripts/turn_commit.py <game>
{
  "log_entry": {
    "kind": "roll",
    "heading": "<short scene/moment label>",
    "action": "<actor> — <what they tried>",
    "roll": "<Xd6 [n,n,n] → K hits vs diff D → outcome>",
    "narrator": "Игрок | Мастер",
    "conditions": "нет",
    "reserve": "X → Y",
    "consequences": "<what changed in the world>"
  },
  "character_update": {
    "name": "<character file basename>",
    "reserve": <new absolute reserve>,
    "add_conditions": [{"text":"...", "source":"..."}],
    "remove_conditions": ["<exact text>"],
    "change_log_entry": "<one-line summary>"
  },
  "state_update": {
    "current_scene": "<3–5 lines, current situation>",
    "sections": {"Key NPC status": "<updated rows>"}
  },
  "scene_sheet_append": {"scene_id": "<id>", "state_change": "д<n> <time>: <change>"},
  "npc_sheet_append":   {"npc_id":   "<id>", "recent_interaction": "д<n> <time>: <event>"}
}
JSON
```

Any section can be omitted. The script:
- renders the log entry per `locales/{lang}/templates/log_entry.md`
- updates only the `Current scene` section of `state.md` (and any keys you list under `sections`)
- merges `reserve` / `conditions` / `change_log` on the character without rewriting the rest
- appends to `state_changes:` / `recent_interactions:` on existing sheets

It is atomic: a validation error (e.g. unknown character name) leaves nothing written.

⛔ `turn_commit.py` does NOT create new sheets — only appends to existing ones. If a brand-new scene or NPC needs a sheet, create it first via `scene_note.py` (Step 8b), then optionally append the state-change in the same `turn_commit.py` call.

### 8b. Create scene / NPC sheets when needed

If the action described a NEW scene not in `worlds/<world>/world.md`, OR an improvised NPC not in `worlds/<world>/npcs.md` — create the sheet via `scene_note.py` BEFORE calling `turn_commit.py` (so the append in Step 8a lands on an existing sheet).

```bash
# New scene with the minimum required tags
python3 /root/.microclaw/scripts/scene_note.py <game> scene <scene_id> \
  --type location --first-visited "д1 утро" \
  --layout "<tag>" --layout "<tag>" \
  --atmosphere "<tag>" --atmosphere "<tag>" \
  [--linked-npc <npc_id>] [--prop "key:tag,tag"]

# New improvised NPC
python3 /root/.microclaw/scripts/scene_note.py <game> npc <npc_id> \
  --first-seen "д1 утро, <scene>" [--known-name "Имя"] \
  --appearance "<tag>" --appearance "<tag>" \
  --voice "<tag>"

# A new known connection between scenes
python3 /root/.microclaw/scripts/scene_note.py <game> connect \
  --link-from <id> --link-to <id> --direction <north|...>
```

See `skills/scenes/SKILL.md` for full rules on when to create vs append.

### 8c. Self-check

log.md ✓ | character file ✓ | state.md ✓ | scene/npc sheet (if applicable) ✓ — all via `turn_commit.py` (+ `scene_note.py` for new sheets). If you find yourself reaching for `edit_file` on log.md / state.md / character file, STOP — that is the old flow and is now superseded.

---

## Special cases

### Help
Another player declares help → gives 1 die from their reserve to the acting player's pool.
**⛔ NO ROLL needed to help.** The helper simply declares and spends 1 reserve die.
- Success: helper's die is LOST. Failure: helper's die is RETURNED.
- **No penalty for helping.** No conditions, no difficulty increase for the helper.
- Helper must have ≥ 1 reserve die.

### Reducing difficulty
Difficulty 5+ → player may split into intermediate steps.
Each successful intermediate roll reduces final difficulty by 1.

### Traits as flaws
Player chooses suboptimal action because a trait/flag demands it → +1 die to reserve.
This is an offer, never a demand.

### Narrator rights limits
⛔ See rules/SKILL.md section 11 for the complete list.
Short version: players narrate HOW success happens within the established world — they cannot change the setting, introduce major equipment, skip encounters, or eliminate key threats.
GM has veto power. If narration exceeds limits, ask for a scaled-back version.
