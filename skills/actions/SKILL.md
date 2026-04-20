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

**⛔ MANDATORY: Before building the pool, RE-READ the character file from disk.** Do not rely on memory. Open the character .md file and read the actual traits, aspects, flags, reserve, and conditions.

| Source | Condition | Dice |
|---|---|---|
| Each applicable trait | Trait logically applies to this action | **exactly +1** |
| Each applicable aspect | Aspect from any used trait that logically fits | +1 per aspect |
| Flag (MAX 1) | Action genuinely matches the flag | +1 |
| Reserve dice | Player chooses how many to add | +X |

⛔ Trait level 3 = +1 die, NOT +3. Each trait always gives exactly +1 die. Level = aspect count only.

**Self-check (MANDATORY):**
1. Did I re-read the character file just now? ✓
2. +1 per trait (not per level)? ✓
3. Every aspect I listed — is it ACTUALLY in the character file? ✓
4. Every aspect — does it LOGICALLY apply to THIS specific action? ✓
5. At most 1 flag? ✓

Announce the pool to the player before rolling.
→ Use display format from: `locales/{lang}/templates/dice_pool.md`

---

## Step 5. Roll

**⛔ MANDATORY: Use the dice script for ALL rolls. Never generate dice manually.**

```bash
python3 /root/.microclaw/scripts/roll.py <pool_size> <difficulty>
```

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
Post the narrative outcome (scene description, action result prose) to the narrative channel via the webhook script. Do NOT include dice numbers, pool breakdown, or reserve info — only the story.

---

## Step 7. Apply result and update reserve

**Success:** deduct spent reserve dice.
**Failure:** return spent dice + add 1 to reserve. Cap at 7.

Announce updated reserve to player.

---

## Step 8. ⛔ MANDATORY: Write files before next player response

**Do NOT send the next narrative response until all three writes are complete.**

### 8a. Append to log.md
→ Use format from: `locales/{lang}/templates/log_entry.md` (roll log entry)

### 8b. Update character file
- `reserve_dice.current` — reflect dice spent or returned
- `conditions` — add or remove based on result
- `change_log` — append entry with date and what changed

### 8c. Update state.md
- "Current scene" section: rewrite to reflect current situation (3–5 lines)
- NPC status, world changes, plot threads — update if affected

**Self-check:** log.md ✓ | character file ✓ | state.md ✓

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
