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
3. Live state: games/<game>/state.md — read "Current scene" section for current context

**Do NOT read log.md for routine descriptions.** Read log.md only when:
- Player asks about a specific past event
- You need to verify a fact established several scenes ago
- state.md "Current scene" is missing or empty (then read last 5 log entries, update state.md)

Priority: state.md overrides worlds/. Dead NPC is not mentioned as alive.

## Narration principles

### 1. Specificity
Not "a dark corridor". But "the corridor smells of burnt insulation. Water drips somewhere. The lamp at the far end blinks every three seconds — and between blinks the dark is total."
Describe through senses: smell, sound, touch.

### 2. Information without interpretation
- Wrong: "The guard is clearly nervous"
- Right: "The guard looks slightly to the side when answering. Fingers on the belt — not relaxed."
Player draws conclusions. Exception: character has a trait for reading people.

### 2b. Character perspective — never describe what the character did not witness

All narration is filtered through what the active character could actually observe.
Do NOT describe events that happened off-screen or unnoticed as if the player saw them.

Failure means the character missed something — do not then describe that thing cinematically.

- Wrong (failed watch roll): "A cloaked figure creeps in, trembling fingers, amulet falls — then slips away unseen." (Player now has meta-knowledge their character doesn't.)
- Right (failed watch roll): "Dawn. Something catches your eye near the entrance — a glint in the sand. An amulet on a wet chain. No tracks. No sound. When did this get here?" (Character discovers evidence. Mystery remains.)

The player character knows only what they observed.

### 3. Open endings
Description ends where the player has a choice.
- Right: "Three figures stand at the gate. A fourth walks toward you. Twenty metres away."
- Wrong: "You need to decide — run or hide."

### 4. NPCs are people
Read NPC card in npcs.md before their lines.
Consider: what do they want right now, what are they hiding, how do they speak.

### 5. Pacing
- Danger/combat — short sentences, clipped rhythm
- Calm scene — room for atmosphere
- Key reveal — pause, one line

### 6. Narrator rights

**If player has narrator rights →** provide brief setup (mechanical result + sensory context), then hand the word.
→ Use prompt from: `locales/{lang}/templates/prompts.md` (narrator rights handoff)

**If player declines to narrate →** GM narrates with the same result type. Do not punish for declining.

**If GM narrates "yes, but...":**
- Player succeeded FULLY. Do not downgrade the result.
- The "but" is a NEW world element — threat appears, clock starts, new info changes picture.
- Test: "would the player say I succeeded but wish I hadn't because of this complication?" If yes — good "but". If complication just makes success feel less good — wrong "but".
- Do NOT assign conditions on "yes, but..." — conditions belong to failures only.

**If GM narrates "no, and...":**
The player failed. Add a complication or condition. The world pushes back harder.

### 7. Narrator rights limits
⛔ See rules/SKILL.md section 11 for the complete hard limits.
If player narration exceeds limits → use scale-back prompt from `locales/{lang}/templates/prompts.md`. This is not optional.

### 8. Narrate player actions — not just scene transitions

⛔ EVERY resolved player action must be narrated, even auto-successes. Do NOT jump straight from action declaration to "now you're at the next location" — describe the action itself.

**What to narrate for each action:**
- HOW the character did it (use their applicable traits/aspects as flavour, but don't invent new capabilities)
- Sensory details of the doing (sound, motion, feel, what they see)
- The immediate result or micro-consequence
- NPC reactions if any
- Transition to the new state/scene if applicable

**Transform "I do X" into narrative prose:**
- Declaration: "Я подхожу к прилавку и спрашиваю цены"
  - Bad narration (too dry): "You approach the counter and ask prices."
  - Good: "Гарран протискивается между мешками с солью к прилавку. Торговец — щуплый коротышка в замасленном фартуке — поднимает голову, и его единственный глаз сужается, оценивая клиента. Цены здесь кусаются: медная кружка — три серебряных, связка лучин — два."
- Declaration: "Я бью гоблина топором"
  - Bad: "You hit the goblin."
  - Good: "Гарран перехватывает топор поудобнее и вкладывает в удар весь вес тела — лезвие со свистом рассекает воздух. [результат броска определяет исход]"

**Don't skip small actions.** Walking across the market, examining wares, returning for a letter — each is its own narrative beat. A player re-reading the narrative channel should be able to follow the whole path, not just the final destination.

### 9. Narrative style presets

The game's narrative style is set in `game.md` as `narrative_style`. Load it at session start. Apply the preset voice to ALL narrative output (scene descriptions, action narration, NPC dialogue coloring).

| Preset | Voice |
|---|---|
| **documentary** | Dry log of events. Minimal sensory detail, no atmosphere. "Character enters tavern. NPC speaks. Character leaves." Useful for dense mechanical scenarios. |
| **concise** | Clear prose, essential details only. No flowery language, no deep atmosphere — just what matters for the player to visualize and decide. |
| **narrative** (default) | Rich descriptions with sensory detail, thematic flavour, expanded action narration. Uses the character's traits/aspects to colour HOW they do things. Balanced pacing — detail in calm, brevity in danger. |
| **noir** | Same as narrative but with emphasis on shadow, moral ambiguity, cynicism, cigarette smoke. NPCs get terse dialogue with subtext. Details lean grim: rust, grime, rain, flickering lights. Narration pauses on small defeats. |
| **horror** | Same as narrative but with emphasis on dread, wrongness, the unseen. Details that imply threat: an unexplained sound, something moved when it shouldn't, a smell that doesn't belong. Build tension through what is NOT shown. Use short sentences at key moments to spike fear. |
| **custom** | If `narrative_style: custom`, read `narrative_style_description` field from game.md and follow it as your style guide. |

Defaults: if no style set, use `narrative`. Style is chosen at world creation or game start — see `skills/session/SKILL.md` and `skills/worldgen/SKILL.md`.

### 10. Seamless continuity

⛔ Narrative is a continuous story, not disconnected scenes. Each narrative post must flow from the previous one.

Before writing any narrative block, review the last few events from your session context (no need to re-read log.md for this — it should be in your working memory from recent messages). Pick up threads, reference what just happened, keep NPC/scene continuity.

- Wrong: scene 1 ends at market, next narrative opens "Гарран входит в комнату Хельги" with no transition
- Right: "Гарран выныривает из рыночной толчеи, пересекает площадь... поднимается по скрипучим ступеням... стук в дверь. Хельга открывает — и без единого слова протягивает запечатанный свиток."

Even when the player's declaration skips the transit ("иду к Хельге"), the narrative fills the bridge in 1-3 sentences.

## Narrative channel output

After composing any narrative block (scene description, NPC dialogue, action outcome), if `narrative_webhook` is set in game.md → post the narrative prose to the webhook URL via `skills/narrative/SKILL.md`. This is done via bash running `post_narrative.py`.

## What to log in log.md
| Log | Don't log |
|---|---|
| Key location details established | Atmospheric flavour |
| Facts characters learned | Lines with no new info |
| NPC reactions that shift relationships | Player questions |
| Scene transitions | |
