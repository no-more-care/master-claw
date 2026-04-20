# SKILL: Narrative Channel Output (BlackBirdPie)

Posts narrative-only content to a dedicated Discord channel via webhook, separate from the main game channel. This creates a clean storyline log that players can re-read without mechanical noise.

## When to use
- After EVERY player action is resolved (even auto-successes) — narrate WHAT and HOW they did it
- After scene description or location change
- After NPC dialogue
- After a world event is described
- After a new character arrives in the scene

Style, voice and action-narration rules live in `skills/narrator/SKILL.md` sections 8-10. The narrative skill handles DELIVERY (webhook); the narrator skill defines HOW the prose is composed.

## Setup

Read `narrative_webhook` from game.md at session start.
- If set to a Discord webhook URL → dual-channel mode is active
- If set to "none" or missing → skip this skill entirely, single-channel mode

## What to send to narrative channel

| Send | Don't send |
|------|-----------|
| Player action narration (how they did it, using their traits) | Dice pool breakdowns |
| Scene descriptions and transitions | Difficulty announcements |
| NPC dialogue and reactions | Roll results (numbers) |
| World events | Reserve updates |
| Character arrivals | Call to action / "what do you do?" |
| Atmosphere and setting | Player narration prompts |
| Small connective narration (walking, searching, watching) | Character sheet displays |
| | Session status / file confirmations |

**Key rule:** if something happened IN THE FICTION, it goes to narrative. If it happened IN THE MECHANICS, it does not.

## How to send

Use the narrative posting script via bash:
```bash
python3 /root/.microclaw/scripts/post_narrative.py '<webhook_url>' '<narrative_text>'
```

Or for long text with newlines, pipe via stdin:
```bash
cat <<'EOF' | python3 /root/.microclaw/scripts/post_narrative.py '<webhook_url>' -
<narrative text here, can span multiple lines>
EOF
```

The script posts directly to Discord via webhook — no LLM, no microClaw permission model, just HTTP POST. Long messages are auto-split at paragraph boundaries (Discord's 2000-char limit).

## Format rules

The narrative text should be **pure storytelling prose**:
- No emoji headers (no 🎲, 📋, ✅, ❌)
- No mechanical labels (no "Difficulty:", "Hits:", "Reserve:")
- No pool breakdowns or dice arrays
- No meta-commentary about rules or procedures
- Discord markdown OK (*italics*, **bold**, `code`, > quotes)
- Same voice and style as the game channel narration, just without the mechanical wrapper

## Timing and message structure

Narrative posts are separate from the game channel. For each turn, split the narrative into 1-2 distinct messages sent in sequence:

1. **Action narration** — what the player character DID and HOW (always post when an action was resolved)
2. **Scene/world narration** — what the world shows in response, NPC reactions, scene transitions (post only if something changed)

For purely descriptive turns (no player action — just "describe the room"), skip message 1 and post only message 2.

Continuity matters: before composing, review the previous narrative posts in session context. Pick up threads, reference the previous scene, smooth transitions. See narrator/SKILL.md section 10 (Seamless continuity).

### Flow per turn
1. Resolve the action (actions/SKILL.md steps 0-7)
2. Compose action narration (1-4 sentences, action-focused)
3. POST it to narrative webhook
4. If scene changed, compose scene narration (1-5 sentences)
5. POST it to narrative webhook as a second message
6. Compose full game channel response (mechanics + brief narrative + call to action)
7. Send to game channel

## Examples

### Example 1: Action + scene change

Player declaration: "Покупаю веревку и иду к Хельге за письмом"

**Narrative message 1 (action):**
> Гарран кладёт перед торговцем три медяка. Тот проверяет монеты на зуб, кряхтит, и бросает на прилавок свёрнутую кольцом пеньковую верёвку — двадцать локтей, грубая, но прочная. Гарран перебрасывает её через плечо и ныряет обратно в рыночную толчею.

**Narrative message 2 (scene transition):**
> Улицы Нижнего города провожают его запахом тухлой рыбы и дешёвого пива. За углом — переулок, где снимает комнату Хельга. Скрипучая лестница на второй этаж, знакомая дверь. Стук. Пауза. Хельга открывает, прижимая палец к губам, и протягивает запечатанный красным воском свиток.

### Example 2: Pure action, no scene change

Player declaration: "Осматриваю комнату"

**Narrative message 1 (action + observation):**
> Гарран медленно обводит комнату взглядом. Над камином — потускневший герб, двух лисиц. На столе, рядом с недоеденной краюхой хлеба, вскрытое письмо и сломанная восковая печать. У окна сундук, приоткрытый — виднеется угол зелёного плаща.

No scene message needed.

### Example 3: Auto-success, small action

Player declaration: "Проверяю, сколько у меня осталось медяков"

**Narrative message 1 (action):**
> Гарран отходит в тень между ларьками, развязывает пояс и пересчитывает монеты. Семь медяков и одна серебрушка. Негусто.

## Troubleshooting

- **Script returns HTTP 401/404** → webhook URL is invalid or deleted. Ask the operator to regenerate it in Discord.
- **Text gets split into multiple messages** → normal for long narratives, script handles this automatically.
- **No post appears** → check webhook URL is stored correctly in game.md, check bash tool succeeded.
