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
2. World: worlds/<world>/world.md (setting), npcs.md (characters before any NPC line)
3. Live state: games/<game>/state.md — read section "Текущая сцена" for current context

**Do NOT read log.md for routine descriptions.** Read log.md only when:
- Player asks about a specific past event
- You need to verify a fact established several scenes ago
- state.md "Текущая сцена" is missing or empty (then read last 5 log entries, update state.md)

Priority: state.md overrides worlds/. Dead NPC is not mentioned as alive.

## Narration principles

### 1. Specificity
Not "a dark corridor". But "the corridor smells of burnt insulation. Water drips somewhere. The lamp at the far end blinks every three seconds — and between blinks the dark is total."
Describe through senses: smell, sound, touch.

### 2. Information without interpretation
- ✗ "The guard is clearly nervous"
- ✓ "The guard looks slightly to the side when answering. Fingers on the belt — not relaxed."
Player draws conclusions. Exception: character has a trait for reading people.

### 2b. Character perspective — never describe what the character did not witness

All narration is filtered through what the active character could actually observe.
⛔ Do NOT describe events that happened off-screen or unnoticed as if the player saw them.

Failure means the character missed something — do not then describe that thing cinematically to the player.

✗ WRONG (failed watch roll): "A cloaked figure creeps in, trembling fingers, amulet falls — then slips away unseen."
  (The character didn't see this. The player now has meta-knowledge their character doesn't.)

✓ RIGHT (failed watch roll): "Dawn. Something catches your eye near the entrance — a glint in the sand. An amulet on a wet chain. No tracks. No sound. When did this get here?"
  (Character discovers evidence of something. The mystery remains.)

The player character knows only what they observed. The GM may know more — but does not share it until the character could realistically learn it.

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

If player has narrator rights → hand them the word and wait.

If GM narrates "yes, but...":
The player succeeded fully. Do not downgrade the result.
The "but" is a new world element — something that appears, starts, or shifts in response to the success. It must create a new decision point.
Ask internally: "What does the world do now that this succeeded?" — that is the "but".
- A new threat enters the scene
- A clock starts ticking (storm on the horizon, voices approaching)
- A new piece of information changes the picture
- Something unrelated to the action goes wrong nearby

⛔ The "but" must NOT be the failure consequence restated as a caveat on success.
⛔ The "but" must NOT make the success feel partial or diminished.

If GM narrates "no, and...":
The player failed. Add a complication or condition. The world pushes back harder.

## What to log in log.md
| Log | Don't log |
|---|---|
| Key location details established | Atmospheric flavour |
| Facts characters learned | Lines with no new info |
| NPC reactions that shift relationships | Player questions |
| Scene transitions | |

Format: **[Narrative]** <1-2 line note>
