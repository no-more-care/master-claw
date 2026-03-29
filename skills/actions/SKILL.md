# SKILL: Processing Player Declarations (BlackBirdPie)

Authoritative procedure for handling any player action declaration.
For full rules details — see skills/rules/SKILL.md. This skill contains all tables needed for a standard roll.

## When to use
- Player announces intent to act
- Determine if action is possible, if a roll is needed, run it and apply result

## Active game
Find GameMaster/games/ folder with status active. Load character sheet before Step 1.
After roll — write to log.md, update state.md section "Current Scene", update character file.

---

## Step 0. Validate the declaration

Before anything else, check the declaration against the character sheet.

**DENY — ask for a new declaration when:**
- Player uses equipment NOT on their sheet and NOT implied by a trait
  → "[Name] doesn't have [X] on their sheet. What do you do with what you have?"
- Player invents a new ability mid-scene not established before
  → "That ability isn't on the sheet. What do you do with the traits you have?"
- Declaration contradicts established world state or state.md
  → State the contradiction, ask what they do instead
- Character has a condition that physically prevents this action
  → "Condition [X] prevents that. Can you try [alternative]?"

**Equipment implied by trait (no need on sheet):**
- Thief / lockpicking trait → lockpicks
- Doctor / medic trait → basic medkit
- Soldier trait → service weapon
- When in doubt: ask yourself "would this character obviously have this given their trait?" If yes, allow.

If declaration passes — proceed to Step 1.

---

## Step 1. Receive declaration

Any player message describing an action = a declaration. Narrative style counts.
If the goal is unclear — ask once before proceeding.

---

## Step 2. Is a roll needed?

**NO ROLL — automatic success when ALL of the following are true:**
1. The action logically follows from a character trait with no real uncertainty
2. The difficulty would be 2 or lower (easy, familiar, no opposition)
3. No active tension in the scene (no time pressure, no one watching, no threat)
4. Failure would have no interesting consequences for the story

If auto-success: announce it briefly, describe or hand narrative to player. Skip to Step 8 (log if significant).

**Examples of auto-success:**
- Experienced thief picks a simple padlock alone, unhurried → no roll
- Doctor bandages a minor scratch → no roll
- Character with "Street knowledge" asks about a neighbourhood they know well → no roll

**ROLL NEEDED when any of the following is true:**
- Real chance of failure exists given the character's traits
- Failure has interesting story consequences (condition, complication, new threat)
- Active tension (being watched, time pressure, opposition, unknown outcome)
- Player is attempting something at the edge of their established abilities

---

## Step 3. Set difficulty

| Level | Description | Examples |
|---|---|---|
| 2 | Easy | Distracted guard, familiar territory, no opposition |
| 3 | Medium | Standard obstacle, normal conditions |
| 4 | Hard | Alert enemies, adverse conditions, expert obstacle |
| 5 | Very hard | Expert opposition, near-impossible odds |
| 6 | Extreme | Legendary difficulty |
| 7+ | Nearly impossible | One in a million |

Set difficulty fast. Pace over precision.

Announce difficulty and failure stakes to the player. Output in Russian.

**⛔ MANDATORY PAUSE: wait for player response. Do NOT proceed to Step 4 without confirmation.**

Player may DECLINE after hearing the stakes. If they decline — no roll, no consequence, the moment passes.

WRONG: announce difficulty and immediately roll.
RIGHT: announce → wait for answer → receive "yes" → Step 4.

---

## Step 4. Build dice pool

| Source | Condition | Dice |
|---|---|---|
| Each applicable trait | Trait logically applies to this action | **exactly +1** |
| Each applicable aspect | Aspect from any used trait that logically fits | +1 per aspect |
| Flag | Action genuinely matches the flag's character description | +1 |
| Reserve dice | Player chooses how many to add | +X |

**⛔ CRITICAL — NEVER DO THIS:**
Trait level 3 gives **+1 die**, NOT +3.
Trait level 5 gives **+1 die**, NOT +5.
Trait level 6 gives **+1 die**, NOT +6.
Trait level = aspect count only. Nothing else.

**Self-check before announcing the pool:**
- Am I counting +1 per trait (not per level)? ✓
- Did I list every applicable aspect as a separate line? ✓
- No extra dice without explicit justification? ✓

**Correct example:**
Character has trait "Seamanship" (level 3, aspects: navigation, sailor's knots, reading weather) searching for supplies on shore.
→ Traits: Seamanship +1 | Aspects: sailor's knots +1, reading weather +1 | Flag: Adventurer +1 | Total: 4 dice ✓

**Wrong example:**
→ "Seamanship (3) → 3 dice" ✗ — level ≠ dice.

Announce the pool to the player before rolling. Output in Russian. Format every source as a separate line:
```
Traits: [A] +1, [B] +1
Aspects: [a1] +1, [a2] +1, [a3] +1
Flag: [F] +1
Reserve: +N
Total: X dice
```

---

## Step 5. Roll

Generate N random numbers 1–6. Hits = dice showing 4, 5, or 6.

**Count hits by listing them explicitly** — do not count in your head.
Go through the array one by one: mark each die ≥ 4 as a hit. Sum only after listing.

WRONG: "[4, 6, 2, 5, 3, 1, 6, 4] → 2 hits" (miscounting)
RIGHT: "[4, 6, 2, 5, 3, 1, 6, 4] → hits: 4✓ 6✓ 2✗ 5✓ 3✗ 1✗ 6✓ 4✓ → 5 hits"

Show roll result to player. Output in Russian. Format:
```
Roll: [3, 5, 1, 6, 4, 2]
Hits (4+): 5✓ 6✓ 4✓ → 3 hits vs difficulty 3
```

---

## Step 6. Apply narrator rights

| Result | Comparison | Who narrates | Form |
|---|---|---|---|
| Failure + complication | Hits **<** difficulty − 1 | **GM** | "No, and furthermore..." |
| Failure + silver lining | Hits **=** difficulty − 1 | **Player** | "No, but..." |
| Success + cost | Hits **=** difficulty | **GM** | "Yes, but..." |
| Full success | Hits **>** difficulty | **Player** | "Yes, and furthermore..." |

**⛔ CRITICAL CHECK before assigning narrator rights:**
Is hits strictly GREATER THAN difficulty? → Player narrates.
Is hits EQUAL TO difficulty? → GM narrates. This is NOT a full success.

**Example:** 3 hits vs difficulty 3 → 3 = 3 → **GM** narrates "Yes, but..." ← common mistake zone.
**Example:** 4 hits vs difficulty 3 → 4 > 3 → **Player** narrates "Yes, and furthermore...".
**Example:** 2 hits vs difficulty 3 → 2 = 3−1 → **Player** narrates "No, but...".

**⛔ NEVER narrate for the player when narrator rights belong to them.**
Instead hand the word to the player. Output in Russian.
Wait for the player's narration before continuing.

**When GM narrates "yes, but...":** achieve the player's original goal, add a complication, do NOT assign conditions.

**When GM narrates "no, and...":** describe the setback, apply complication or condition.

---

## Step 7. Apply result and update reserve

**Success:** announce result, apply narrator rights, deduct spent reserve dice.
**Failure:** announce result, apply complication or condition, return spent dice + add 1 to reserve. Cap at 7.

Announce updated reserve to player. Output in Russian.

---

## Step 8. ⛔ MANDATORY: Write files before next player response

**Do NOT send the next narrative response until all three file writes below are complete.**
File updates are not optional. Skipping them corrupts game state.

### 8a. Write to log.md (append)
```
## [Scene/moment label]
**Action:** <character> — <what they attempted>
**Roll:** [dice array] → X hits vs difficulty Y → success/failure
**Narrator:** GM / Player
**Conditions:** <assigned or "none">
**Reserve:** X → Y
**Consequences:** <what concretely changed in the world>
```

### 8b. Update character file
- `reserve_dice.current` — reflect dice spent or returned
- `conditions` — add or remove based on result
- `change_log` — append entry with date and what changed
- New aspects or flags declared during play → add and set `locked: true`

### 8c. Update state.md
- Section "Current Scene": rewrite to reflect where characters are NOW, what is happening NOW (3–5 lines)
- Section "Key NPC status": update any NPC whose status changed
- Section "World changes": record any new facts (found items, locations discovered, relationships shifted)
- Section "Active plot threads": add new threads that emerged

**Self-check before writing player response:**
- Did I append to log.md? ✓
- Did I update character reserve and conditions? ✓
- Did I rewrite state.md "Current Scene" to match current situation? ✓

---

## Special cases

### Help
Another player describes help → gives 1 die from their reserve to the pool.
Success: helper's die is lost. Failure: returned to helper.
Any number of helpers allowed if logically justified.

### Reducing difficulty
Difficulty 5+ → player may split into intermediate steps.
Each successful intermediate roll reduces final difficulty by 1.
Each intermediate roll carries its own risks.

### Traits as flaws
Player chooses suboptimal action because a trait, aspect or flag demands it → +1 die to reserve.
This is an offer, never a demand.

### Narrator rights limits (even with "yes, and...")
Players CANNOT use narrator rights to:
- Introduce equipment or abilities not on their sheet
- Fundamentally change their character's nature
- Create facts that contradict established world state
- Eliminate threats entirely without any cost

Narrator rights = describe HOW success happens, not WHAT your character is capable of.
