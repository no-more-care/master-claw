# BlackBirdPie — Mechanics Reference

Quick cheat sheet. Authoritative source: `skills/rules/SKILL.md`.

## Dice pool

Each applicable element = **+1 die** (NOT +trait level!):
- Each applicable **trait**: +1
- Each applicable **aspect**: +1
- Matching **flag**: +1 (max 1 per roll)
- **Reserve dice**: player chooses how many to add (0..current reserve)
- A character may spend any number of their own available reserve dice on their own roll, including
  the entire reserve. A helper may contribute exactly one die to another character's pending roll.
- On a failed roll, all reserve dice spent on that roll return to their owners: the acting
  character receives all of their spent dice back (plus the normal failed-roll award, capped by
  maximum reserve), and each helper receives their one contributed die back.
- Because the service has no reliable session boundary, reserve recovery is selected per game:
  `safe_rest`, `roleplay_award`, or `both`. Safe rest restores every character to maximum; a system
  game-master roleplay award restores exactly one die to one character. There is no separate human GM or
  recovery command. A dedicated system game-master decision evaluates canonical resolved outcomes
  and scene state, supplies explicit evidence, and deterministic code applies and audits the result.
  A direct player request cannot trigger recovery by itself.

**Roll:** Nd6, each die showing 4-6 = 1 hit.

## Difficulty

| Difficulty | Level |
|------------|-------|
| 2 | Easy |
| 3 | Medium |
| 4 | Hard |
| 5 | Very hard |
| 6 | Extreme |
| 7+ | Nearly impossible |

## Narrator rights

| Result | Who narrates | Formula |
|--------|-------------|---------|
| Hits > Difficulty | **Player** | "Yes, and furthermore..." |
| Hits = Difficulty | **GM** | "Yes, but..." |
| Hits = Difficulty - 1 | **Player** | "No, but..." |
| Hits < Difficulty - 1 | **GM** | "No, and furthermore..." |

## Reserve dice

- Start: 7 | Maximum: 7
- **Success:** spent dice are LOST
- **Failure:** spent dice are RETURNED + player gains +1 (cap at 7)
- **Help:** give 1 die to another player; no roll needed, no penalty for helper. Lost on success, returned on failure.

## Character

- **Traits:** 3-7 total, levels 2-6, sum = **exactly 18**
- **Aspects:** count = trait level; can be added during play, locked once set
- **Flags:** minimum 3, at least 1 relationship required; locked once set; max 1 per roll
- **Experience:** ~1 point / 30 min of play; raise N→N+1 = N points; new trait at level 2 = 3 points

## Common mistakes

1. **Trait level 6 = +6 dice** — WRONG. Each trait = +1 die.
2. **Reserve die = +2** — WRONG. Each reserve die = +1.
3. **"Stress dice"** — no such term. Only "reserve dice".
4. **Rolling without confirmation** — WRONG. Always announce difficulty and wait for OK.
5. **GM describes actions for the player** — WRONG. Only the player decides what their character does.
6. **Multiple flags per roll** — WRONG. Maximum 1 flag per roll.
7. **Rolling for help** — WRONG. Help is automatic, no roll, no penalty.
