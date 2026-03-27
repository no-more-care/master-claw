# SKILL: Processing Player Declarations (BlackBirdPie)

Authoritative procedure for handling any player action declaration.
For full rules details — see skills/rules/SKILL.md. This skill contains all tables needed for a standard roll.

## When to use
- Player announces intent to act
- Determine if action is possible, if a roll is needed, run it and apply result

## Active game
Find GameMaster/games/ folder with status active. Load character sheet before Step 1.
After roll — write to log.md, update state.md section "Текущая сцена", update character file.

---

## Step 0. Validate the declaration

Before anything else, check the declaration against the character sheet.

**DENY — ask for a new declaration when:**
- Player uses equipment NOT on their sheet and NOT implied by a trait
  → "У [имя] нет [X] на листе. Что делаешь с тем, что есть?"
- Player invents a new ability mid-scene not established before
  → "Эта способность не записана. Что делаешь с имеющимися чертами?"
- Declaration contradicts established world state or state.md
  → State the contradiction, ask what they do instead
- Character has a condition that physically prevents this action
  → "Состояние [X] не позволяет это сделать. Можешь попробовать [alternative]?"

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

Announce to player:
> "Сложность X. При провале — [конкретное осложнение]."

Player may DECLINE after hearing the stakes. If they decline — no roll, no consequence, the moment passes.
Do NOT proceed to Step 4 until the player confirms they roll.

---

## Step 4. Build dice pool

| Source | Condition | Dice |
|---|---|---|
| Each applicable trait | Trait logically applies to this action | +1 |
| Each applicable aspect | Aspect from any used trait that logically fits | +1 |
| Flag | Action genuinely matches the flag's character description | +1 |
| Reserve dice | Player chooses how many to add | +X |

**Critical rule:** trait LEVEL is NOT dice count. A trait with level 6 still gives only +1 die.
Trait level defines only how many aspects that trait has.

Announce total before rolling:
> "Черты: [A] +1, [B] +1 | Аспекты: [a1] +1, [a2] +1 | Флаг: [F] +1 | Запас: +N | Итого: X кубов"

---

## Step 5. Roll

Generate N random numbers 1–6. Hits = dice showing 4, 5, or 6.

Show openly:
```
Бросок: [3, 5, 1, 6, 4, 2]
Успехи (4+): 5, 6, 4 → 3 успеха против сложности 3
```

---

## Step 6. Apply narrator rights

| Result | Who narrates | Form |
|---|---|---|
| Hits < difficulty − 1 | GM | "Нет, и к тому же…" |
| Hits = difficulty − 1 | Player | "Нет, но…" |
| Hits = difficulty | GM | "Да, но…" |
| Hits > difficulty | Player | "Да, и к тому же…" |

**When player narrates:** "Расскажи нам, что произошло. Ты можешь [добавить факт о мире / ввести NPC / описать преимущество]." Wait for their narration before continuing.

**When GM narrates "yes, but...":** achieve the player's original goal, add a complication, do NOT assign conditions.

**When GM narrates "no, and...":** describe the setback, apply complication or condition.

---

## Step 7. Apply result and update reserve

**Success:** announce result, apply narrator rights, deduct spent reserve dice.
**Failure:** announce result, apply complication or condition, return spent dice + add 1 to reserve. Cap at 7.

Announce reserve change: "Запас кубов: X/7"

---

## Step 8. Log and update files

Write to log.md:
```
## [Момент]
**Действие:** <кто, что>
**Бросок:** [кубы] → X успехов против сложности Y → успех/провал
**Назначенные состояния:** <если есть>
**Запас:** X → Y
**Последствия:** <что изменилось>
```

Update files:
- Character file: new reserve, conditions, new aspects or flags declared during action
- state.md → section "Текущая сцена": update 3–5 line summary of what is happening now
- state.md → section "Key NPC status" or "World changes" if the event affects the world

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
