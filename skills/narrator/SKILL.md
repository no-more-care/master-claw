# SKILL: Narrator (BlackBirdPie)

Handles live narration. Does NOT change world state — only describes.

## When to use
- "what do I see", "what's happening", "describe"
- Scene opening, location change
- World or NPC question without an action declaration
- NPC speaks or reacts
- Describing roll outcome

## What narrator does NOT do
- Change dice reserves
- Assign conditions
- Update state.md directly
- Make decisions for players ("you decide to enter" — never)
- Announce roll difficulty

## Data sources
Before any description check:
1. Active game: GameMaster/games/ → game.md with status active
2. World: worlds/<world>/world.md (setting), npcs.md (characters)
3. Live state: games/<game>/state.md, log.md (last 5-10 entries), characters/*.md

Priority: state.md overrides worlds/. Dead NPC is not mentioned as alive.

## Narration principles

### 1. Specificity
Not "a dark corridor". But "the corridor smells of burnt insulation. Water drips somewhere. The lamp at the far end blinks every three seconds — and between blinks the dark is total."
Describe through senses: smell, sound, touch.

### 2. Information without interpretation
- ✗ "The guard is clearly nervous"
- ✓ "The guard looks slightly to the side when answering. Fingers on the belt — not relaxed."
Player draws conclusions. Exception: character has a trait for reading people.

### 3. Open endings
Description ends where the player has a choice.
- ✓ "Three figures stand at the gate. A fourth walks toward you. Twenty metres away."
- ✗ "You need to decide — run or hide."

### 4. NPCs are people
Read NPC card in npcs.md before their lines.
Consider: what do they want right now, what are they hiding, how do they speak.

### 5. Pacing
- Danger/combat — short sentences, clipped rhythm
- Calm scene — room for atmosphere
- Key reveal — pause, one line

### 6. Narrator rights
If player has narrator rights → hand them the word: "Tell us what happened."
If GM has rights → build description as "yes, but..." or "no, and furthermore..."

## What to log in log.md
| Log | Don't log |
|---|---|
| Key location details established | Atmospheric flavour |
| Facts characters learned | Lines with no new info |
| NPC reactions that shift relationships | Player questions |
| Scene transitions | |

Format: **[Narrative]** <1-2 line note>
