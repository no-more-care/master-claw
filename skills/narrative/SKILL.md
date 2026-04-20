# SKILL: Narrative Channel Output (BlackBirdPie)

Posts narrative-only content to a dedicated Discord channel via webhook, separate from the main game channel. This creates a clean storyline log that players can re-read without mechanical noise.

## When to use
- After every scene description or location change
- After NPC dialogue
- After an action outcome is narrated (by GM or player)
- After a world event is described
- After a new character arrives in the scene

## Setup

Read `narrative_webhook` from game.md at session start.
- If set to a Discord webhook URL → dual-channel mode is active
- If set to "none" or missing → skip this skill entirely, single-channel mode

## What to send to narrative channel

| Send | Don't send |
|------|-----------|
| Scene descriptions | Dice pool breakdowns |
| NPC dialogue and reactions | Difficulty announcements |
| Action outcomes (narrative only) | Roll results (numbers) |
| World events | Reserve updates |
| Character arrivals | Call to action / "what do you do?" |
| Atmosphere and setting | Player narration prompts |
| | Character sheet displays |
| | Session status / file confirmations |

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

## Timing

Send to narrative channel AFTER the game channel response is composed, in the same turn. Typical flow:

1. Compose full game channel response (narrative + mechanics + call to action)
2. Extract the narrative portion
3. Call the webhook script with the narrative portion
4. Send the full response to the game channel

This ensures the narrative log stays in chronological order.

## Example

**Game channel post (full):**
> 📋 Pool: Stealth +1, Silent step +1, Camouflage +1, Flag: Cautious +1, Reserve +2 = 6 dice
>
> 🎲 [roll result from script]
>
> The guard turns the corner. You press into the shadow behind the generator — the hum covers your breathing. But his flashlight beam sweeps the floor, and your boot print in the dust catches it. He pauses. Tilts his head.
>
> Reserve: 5/7
> What do you do?

**Narrative channel post (clean):**
> The guard turns the corner. You press into the shadow behind the generator — the hum covers your breathing. But his flashlight beam sweeps the floor, and your boot print in the dust catches it. He pauses. Tilts his head.

## Troubleshooting

- **Script returns HTTP 401/404** → webhook URL is invalid or deleted. Ask the operator to regenerate it in Discord.
- **Text gets split into multiple messages** → normal for long narratives, script handles this automatically.
- **No post appears** → check webhook URL is stored correctly in game.md, check bash tool succeeded.
