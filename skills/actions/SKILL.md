# SKILL: Processing Player Declarations (BlackBirdPie)

Authoritative procedure for handling any player action declaration.
For full rules details — see skills/rules/SKILL.md. This skill contains all tables needed for a standard roll.

## When to use
- Player announces intent to act
- Determine if action is possible, if a roll is needed, run it and apply result

## Active game
Find GameMaster/games/ folder with status active. Load character sheet before Step 1.
After roll — write to log.md, update state.md section "Current Scene", update character file.

## ⛔ ANTI-PANDERING RULES — ALWAYS ENFORCE
The GM is an impartial referee. The rules are not negotiable during play.
- **NEVER lower difficulty because a player asks.** Difficulty is set by the fiction, not player requests.
- **NEVER add traits/aspects to the pool that don't logically apply** just because a player argues for them.
- **NEVER add more than 1 flag** to a single roll — only one flag can apply per roll.
- **A player requesting "use all my traits" is NOT a valid declaration.** Only traits that logically apply to the specific action count.
- **Low reserve dice is NOT a reason to lower difficulty.** Reserve is a player resource, not a modifier.
- If a player argues for mechanical advantage — state the rules clearly and move on. Do not debate.

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

**NO ROLL — automatic success when ANY of the following is true:**
1. The action is trivial and the character obviously has the skill for it (using a key you were given, walking across a room, eating food, simple conversation)
2. The action logically follows from a character trait AND difficulty would be ≤ 2 AND there is no tension AND failure would be boring
3. The character uses a previously obtained tool/item for its intended purpose (key opens lock it was made for, map shows location it maps)

**Lean toward auto-success for mundane actions.** Not every action needs a roll. If there is no drama in failure — don't roll.

If auto-success: announce it briefly, describe or hand narrative to player. Skip to Step 8 (log if significant).

**Examples of auto-success:**
- Experienced thief picks a simple padlock alone, unhurried → no roll
- Doctor bandages a minor scratch → no roll
- Character with "Street knowledge" asks about a neighbourhood they know well → no roll
- Player uses a key given by an NPC to open the door the key was for → no roll
- Walking to a known location without obstacles → no roll
- Ordering a drink at a bar → no roll
- Simple social interaction with no stakes → no roll

**ROLL NEEDED when any of the following is true:**
- Real chance of failure exists given the character's traits
- Failure has interesting story consequences (condition, complication, new threat)
- Active tension (being watched, time pressure, opposition, unknown outcome)
- Player is attempting something at the edge of their established abilities

**When in doubt:** ask yourself "would failure here be interesting or just annoying?" If annoying → auto-success. If interesting → roll.

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

**⛔ MANDATORY: Before building the pool, RE-READ the character file from disk.** Do not rely on memory. Open games/<game>/characters/<name>.md and read the actual traits, aspects, flags, reserve, and conditions. This prevents hallucinating non-existent aspects or traits.

| Source | Condition | Dice |
|---|---|---|
| Each applicable trait | Trait logically applies to this action | **exactly +1** |
| Each applicable aspect | Aspect from any used trait that logically fits | +1 per aspect |
| Flag (MAX 1) | Action genuinely matches the flag's character description | +1 |
| Reserve dice | Player chooses how many to add | +X |

**⛔ CRITICAL — NEVER DO THIS:**
Trait level 3 gives **+1 die**, NOT +3.
Trait level 5 gives **+1 die**, NOT +5.
Trait level 6 gives **+1 die**, NOT +6.
Trait level = aspect count only. Nothing else.

**⛔ POOL INTEGRITY RULES:**
- Only 1 flag per roll, ever. Even if the player argues multiple flags apply.
- Only aspects that are ACTUALLY WRITTEN in the character file. Never invent aspects.
- Only traits that LOGICALLY AND DIRECTLY apply. "I'm strong so I can hack computers" — no.
- Aspects must be specific to THIS action. An aspect "navigation" does not help in a fistfight.
- If a player requests adding a trait/aspect that doesn't logically apply — explain why and refuse.

**Self-check before announcing the pool (MANDATORY):**
1. Did I re-read the character file just now? ✓
2. Am I counting +1 per trait (not per level)? ✓
3. Every aspect I listed — is it ACTUALLY in the character file? ✓
4. Every aspect I listed — does it LOGICALLY apply to THIS specific action? ✓
5. Am I using at most 1 flag? ✓
6. No extra dice without explicit justification? ✓

**Correct example:**
Character has trait "Seamanship" (level 3, aspects: navigation, sailor's knots, reading weather) searching for supplies on shore.
→ Черты: Мореходство +1 | Аспекты: морские узлы +1 | Флаг: Искатель приключений +1 | Итого: 3 куба ✓
(Note: "navigation" and "reading weather" don't help with searching for supplies — excluded.)

**Wrong examples:**
→ "Seamanship (3) → 3 dice" ✗ — level ≠ dice.
→ "All 3 aspects apply" ✗ — only aspects relevant to THIS action.
→ "Two flags: Adventurer +1, Brave +1" ✗ — max 1 flag per roll.

Announce the pool to the player before rolling. Output in Russian. Format every source as a separate line with source verification:
```
📋 Расчёт пула (из файла персонажа):
Черты: [A] +1, [B] +1
Аспекты: [a1] (черта [A]) +1, [a2] (черта [B]) +1
Флаг: [F] +1
Резерв: +N (игрок выбирает)
Итого: X кубов
```

---

## Step 5. Roll

**⛔ MANDATORY: Use the dice script for ALL rolls. Never generate dice manually.**

Run the command:
```bash
python3 /root/.microclaw/scripts/roll.py <pool_size> <difficulty>
```

The script will:
- Generate true random dice
- Count hits (4-6) correctly
- Determine narrator rights automatically
- Output formatted result in Russian for players

**Paste the script output directly into your response.** The output has two parts:
1. Player-facing block (show to players as-is)
2. `[GM LOG: ...]` line (use for log.md, do NOT show to players)

**Do NOT manually count hits or determine narrator rights.** Trust the script output.

After pasting the result, proceed to Step 6 using the narrator rights from the script output.

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
When handing narrator rights to the player, provide a brief setup first:
- What just happened mechanically (the action succeeded/failed)
- The immediate sensory context (what the character perceives right now)
Then say: "Расскажи, что произошло." Wait.

**If the player declines to narrate and passes rights back to GM:**
Accept, but narrate with the SAME result type — do not punish them for declining.
"No, but..." handed back → GM still narrates failure + silver lining (not failure + punishment).
"Yes, and..." handed back → GM still narrates full success + new positive fact.

**When GM narrates "yes, but...":**
- Confirm the player's goal fully — they succeed at what they declared.
- The "but" is NOT a weakened success. Do not downgrade the result.
- The "but" introduces something NEW from the world: a threat that appears, a clock that starts, new information, an external pressure that demands a decision.
- Ask internally: "What does the world do in response to this success?" — that is the "but".

⛔ WRONG "but": player builds a shelter → shelter works but is flimsy (= failure consequence restated)
✓ RIGHT "but": player builds a shelter → shelter works perfectly, but thunder in the distance — a storm is coming fast

⛔ WRONG "but": player hides successfully → actually not fully hidden (= partial failure)
✓ RIGHT "but": player hides successfully, but a patrol rounds the corner and stops nearby

Do NOT assign conditions on "yes, but..." — conditions belong to failures only.

**When GM narrates "no, and...":** describe the setback, apply complication or condition. The world pushes back harder.

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
- Section "Текущая сцена": rewrite to reflect where characters are NOW, what is happening NOW (3–5 lines)
- Section "Key NPC status": update any NPC whose status changed
- Section "World changes": record any new facts (found items, locations discovered, relationships shifted)
- Section "Active plot threads": add new threads that emerged

**Self-check before writing player response:**
- Did I append to log.md? ✓
- Did I update character reserve and conditions? ✓
- Did I rewrite state.md "Текущая сцена" to match current situation? ✓

---

## Special cases

### Help
Another player describes help → gives 1 die from their reserve to the acting player's pool.
**⛔ NO ROLL is needed to help. Helping is automatic.** The helper simply declares they help and spends 1 reserve die.
- Success: helper's die is LOST.
- Failure: helper's die is RETURNED.
- **No penalty for helping.** The helper does NOT risk a condition or any other negative consequence from helping.
- Any number of helpers allowed if logically justified.
- Helper must have at least 1 reserve die to help.

### Reducing difficulty
Difficulty 5+ → player may split into intermediate steps.
Each successful intermediate roll reduces final difficulty by 1.
Each intermediate roll carries its own risks.

### Traits as flaws
Player chooses suboptimal action because a trait, aspect or flag demands it → +1 die to reserve.
This is an offer, never a demand.

### Narrator rights limits (even with "yes, and...")

**⛔ CRITICAL — Narrator rights define the SCALE of the outcome, not unlimited creative power.**

Players CANNOT use narrator rights to:
- Introduce equipment or abilities not on their sheet
- Fundamentally change the setting, genre, or game world
- Teleport to distant locations or skip travel/encounters
- Kill, remove, or permanently neutralize major NPCs or threats in a single narration
- Gain items of extraordinary value (vehicles, property, military equipment, large sums)
- Change the scenario, plot structure, or game premise
- Bypass entire planned encounters or dungeon sections
- Grant themselves or others mechanical bonuses beyond this roll

**What narrator rights CAN do:**
- Describe HOW the success/failure happens in a cool way
- Add minor details to the immediate scene (a convenient handhold, a dropped item)
- Establish small advantages for the NEXT encounter (+1 die in specific circumstances)
- Remove or reduce ONE existing negative condition on their character
- Introduce minor NPCs (a passerby, a stray animal) that don't affect the plot
- Add flavour and personality to the outcome

**Scale rule:** The narration impact must be proportional to the roll margin. Beating difficulty by 1 = small bonus. Beating by 3+ = significant advantage. But never world-breaking.

**GM has veto power.** If a player's narration exceeds these bounds, the GM says: "That's too far — scale it back. What's a more grounded version?" This is not negotiable.

Narrator rights = describe HOW success happens within the established world, not REWRITE the world.
