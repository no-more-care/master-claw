# Game Channel Response Skeleton

Standard structure for every GM reply in the Discord game channel. Every turn must include: (1) what the player needs to respond to (decision / roll / choice), (2) reserve dice count if it changed.

Long narrative prose goes to the narrative webhook separately — the game channel gets the BRIEF version + mechanics + call-to-action.

## Type 1. Action → roll announcement

```
<1-2 lines: bridge from the previous beat if player needs it to understand the situation>

🎲 Pool (N dice): <trait A> +1, <aspect> +1, <trait B> +1, flag "<F>" +1.
Difficulty: X. Reserve: <current>/7.
How many from reserve? (or "none")
```

## Type 2. Roll resolved → result

```
<roll.py output in full>

<1-3 lines of mechanical interpretation: what happened mechanically>

<if player narrates — handoff prompt reflecting narrator_rights_level (see rules/SKILL.md §11b):
 "Yes, and... — you narrate. Want +1 die on next roll, −1 difficulty on one check, or remove one condition?">

Reserve: <new>/7. <conditions if changed>
```

## Type 3. Auto-success (small action)

```
<1-3 lines of compact narration in chosen style>

<if player has a choice — call-to-action: "What next?" or a specific question>
```

## Type 4. Scene description / info query

```
<description / answer>

<no mechanical block if no roll needed>
```

## Mandatory rules for EVERY reply

- **Always** include a call-to-action or a clear next step — except when explicitly waiting for player's narration
- **Always** show reserve count if it changed this turn
- **Do not dump** full narrative in game channel — that goes to the webhook
- **Do not analyze** every trait/aspect as a list (unless player explicitly asks)
- **Multi-line structures** (tables, dice arrays, state dumps) — wrap in ```code blocks``` for readability in Discord
- **One code block** per coherent mechanical output (not 5 in a row)

## What NOT to write

- Full narrative prose (that's in the webhook)
- Justification of why each trait fits or not (unless asked)
- Status lines like "files updated", "narrative posted" — tech details, player doesn't need them
- Repeating the scene the player just read in the webhook
