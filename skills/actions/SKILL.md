# SKILL: Processing Player Declarations (BlackBirdPie)

For all rules details — see skills/rules/SKILL.md. This skill covers procedure only.

## When to use
- Player announces intent to act
- Determine if a roll is needed
- Run the roll and apply the result

## Active game
Find GameMaster/games/ folder with status active. Load character sheet before processing.
After roll — write to log.md and update character file.

## Step 1. Receive declaration
Any player message describing an action = a declaration. Narrative style counts.
If goal is unclear — ask once.

## Step 2. Is a roll needed?
No roll:
- Action guaranteed possible for this character in these conditions
- No meaningful consequences on failure
- GM wants it to happen for story reasons
- Sometimes good things just must happen

Roll needed:
- Real chance of failure exists
- Failure has interesting consequences
- Situation creates tension

If no roll — announce automatic success, describe or hand to player.

## Step 3. Set difficulty
Consult rules/SKILL.md section 2. Announce to player:
"Difficulty X. On failure — [complication]."
Player may decline after hearing difficulty.
Do not process next step until approval by player.

## Step 4. Build dice pool
Consult rules/SKILL.md section 1 for exact calculation.

Summary:
- Each applicable trait: +1
- Each applicable aspect from used traits: +1
- Matching flag: +1
- Reserve dice: player chooses

Announce total before rolling:
"Trait X (+1) + aspects A, B (+2) + flag Y (+1) + 2 reserve = 6 dice."

## Step 5. Roll
Generate random numbers 1–6, show openly:
```
Roll: [3, 5, 1, 6, 4, 2]
Hits (4+): 5, 6, 4 → 3 hits vs difficulty 3
```

## Step 6. Apply narrator rights
Consult rules/SKILL.md section 3.

| Result | Narrator | Form |
|---|---|---|
| Hits < difficulty − 1 | GM | "No, and furthermore..." |
| Hits = difficulty − 1 | Player | "No, but..." |
| Hits = difficulty | GM | "Yes, but..." |
| Hits > difficulty | Player | "Yes, and furthermore..." |

## Step 7. Apply result and update reserve
Consult rules/SKILL.md section 4.

Success: announce, apply narrator rights, deduct spent reserve dice.
Failure: announce, apply complication or condition, return spent dice + add 1.

If player narrates: "Tell us what happened. You may [add world fact / introduce NPC / describe advantage]." and wait for desigion from that player.
If GM narrates: achieve player's goal, add complication, no conditions on "yes, but".

## Step 8. Log
Write to log.md:
```
## [Moment]
**Action:** <who, what>
**Roll:** [dice] → X hits vs difficulty Y → success/failure
**Conditions assigned:** <if any>
**Reserve:** X → Y
**Consequences:** <what changed>
```

Update character file: new reserve, conditions, any new aspects or flags declared during action.
If event changes world state — update state.md.

## Special cases

### Help
Another player describes help → gives 1 die from their reserve to the pool.
Success: helper's die lost. Failure: returned to helper.
Any number of helpers allowed if logically justified.

### Reducing difficulty
Difficulty 5+ → player may split into intermediate steps.
Each successful intermediate roll reduces final difficulty by 1.
Each intermediate roll carries its own risks.

### Traits as flaws
Player chooses suboptimal action because a trait, aspect or flag demands it → +1 die to reserve.
This is an offer, never a demand.
