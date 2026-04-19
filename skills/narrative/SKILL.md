# SKILL: Narrative Channel Output (BlackBirdPie)

Handles sending narrative-only content to a dedicated narrative Discord channel, separate from the main game channel. This creates a clean storyline log that players can re-read without mechanical noise.

## When to use
- After every scene description or location change
- After NPC dialogue
- After an action outcome is narrated (by GM or player)
- After a world event is described
- After a new character arrives in the scene

## Setup
Read `narrative_channel` from game.md at session start.
- If set to a channel ID → dual-channel mode is active
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

Use the `send_message` tool:
```
send_message(chat_id=<narrative_channel_id>, text=<narrative_text>)
```

## Format rules

The narrative channel text should be **pure storytelling prose**:
- No emoji headers (no 🎲, 📋, ✅, ❌)
- No mechanical labels (no "Difficulty:", "Hits:", "Reserve:")
- No pool breakdowns or dice arrays
- No meta-commentary about rules or procedures
- Use Discord markdown for emphasis (*italics* for inner thoughts, **bold** for dramatic beats)
- Keep the same voice and style as the game channel narration, just without the mechanical wrapper

## Timing

Send to narrative channel BEFORE posting the full response (narrative + mechanics) to the game channel. This ensures the narrative log stays in chronological order even if the game channel response is longer.

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
