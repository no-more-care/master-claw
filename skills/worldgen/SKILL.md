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

## Algorithm
1. Parse description, extract genre and concrete details
2. Choose folder name (latin, no spaces: iron_coast, deep_city)
3. Create all 4 files at once (unfilled = mark TODO)
4. Show summary + TODO list + suggestions for expansion

## Principles
- Specificity over completeness
- NPCs over lore — three living characters beat ten pages of history
- Hook = pressure, not assignment
- At least one secret must remain unknown until mid-game
