# SKILL: World Generation (BlackBirdPie)

Takes a free-form world description and creates a structured template for running a live game.

## When to use
- GM wants to create a new world from scratch
- Turn a free description into a working template
- Expand an existing template

## Input
Free-form description — one sentence to several paragraphs.
If incomplete — ask at most 3 questions: tone/genre, scale, character types.

## Template structure
Saved to GameMaster/worlds/<world_name>/

### world.md
```
# World: <Name>
**Default narrative style:** <documentary | concise | narrative | noir | horror | custom>

## In one line
## Setting
## Starting location
## Key locations
### <Name>
- Feel:
- Why players will come here:
- What can go wrong:
## Factions
### <Name>
- Goal:
- Methods:
- Attitude to outsiders:
- Why important to plot:
## Atmosphere and tone
## World details (for immersion)
```

The `default_narrative_style` is a hint used at game creation — when starting a game in this world, the GM proposes this style first (player can override). Pick the style that best fits the tone:
- Grimdark cyberpunk, post-apocalyptic → `noir`
- Ghost stories, cosmic horror, survival with unknown threats → `horror`
- Classic fantasy, adventure, heroic tales → `narrative`
- Wargame-style campaigns, mechanical focus → `concise` or `documentary`

### plot.md
```
# Plot: <Name>
## Hook
## What is really happening
## Central conflict
## Key scenes
### Scene: <Name>
- Core:
- Where:
- Who:
- Stakes:
- Player hook:
## Possible endings
## Secrets and open questions
```

### npcs.md
```
# Key characters: <Name>
## <NPC name>
- Role:
- Motivation:
- Secret:
- Default attitude to players:
- How they can help:
- How they can harm:
- Detail:
```

### player_guide.md
```
# Player guide: <Name>
> Only what characters could know before the game.
## World in one line
## What you know about the setting
## Factions (public info)
## How it starts
## FAQ
```

### starter_characters.md
Up to 5 ready-to-play characters. Each must be valid by the character schema (characters/SKILL.md):
- traits_points_total == 18
- 3–7 traits, each level 2–6, aspects count == level
- At least 1 flag with type: relationship (locked: true)
- reserve_dice.current == 7, experience.earned == 0

→ Use display format from: `locales/{lang}/templates/starter_character.md`

Repeat the block for each of 2–5 characters. Characters must be diverse: different backgrounds, archetypes, play styles. Each should feel like they belong in this specific world — not generic.

## Algorithm
1. Parse description, extract genre and concrete details
2. Choose folder name (latin, no spaces: iron_coast, deep_city)
3. Pick `default_narrative_style` based on genre/tone:
   - horror/ghost/unknown-threat → `horror`
   - cyberpunk/noir/grimdark → `noir`
   - heroic/adventure/classic fantasy → `narrative`
   - wargame/mechanical-heavy → `concise`
4. Create all 4 files at once (unfilled = mark TODO)
5. Generate 3–5 starter characters thematically fitting the world, validate each against character schema rules, save to starter_characters.md
6. Show summary including chosen default style + TODO list + suggestions for expansion

## Principles
- Specificity over completeness
- NPCs over lore — three living characters beat ten pages of history
- Hook = pressure, not assignment
- At least one secret must remain unknown until mid-game
