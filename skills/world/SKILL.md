# SKILL: World Event Generation (BlackBirdPie)

## When to use
- Introduce new event, threat or opportunity
- NPC reacts to player actions
- Consequences of failure or success needed
- Players are inactive — need an impulse
- Update state.md after significant events

## Active game
Find GameMaster/games/ folder with status active.
Read game.md → get World field → load worlds/<world>/.

## Data sources
Read-only: worlds/<world>/world.md, npcs.md, plot.md
Read and update: games/<game>/state.md, log.md, characters/*.md

Priority: state.md always overrides worlds/.

## Principles

1. World reacts to players — victory means enemies learn, failure means situation escalates
2. Use flags — if someone is quiet, introduce events tied to their flags
3. GM never acts first — world creates pressure, NPCs take positions, but final actions require player rolls
4. World has memory — destroyed bridge stays destroyed, alliance stays alliance

## Algorithm

1. Read context: location, recent actions, flags, conditions, active threats
2. Choose event type:
   - World reaction — consequence of previous actions
   - New threat — pressure, approaching conflict
   - Opportunity — chance, information, ally, resource
   - Reveal scene — flag moment or NPC development
   - Neutral atmosphere — world details
3. Generate event in setting tone
4. Present to players: details, open choice, do not dictate reaction
5. Write to log.md
6. Update state.md if event changes the world

## Log format
```
## [Game moment]
**Event:** <name>
**Description:** <what happened>
**Participants:** <characters, NPCs>
**Consequences:** <what changed>
**Flags triggered:** <if applicable>
```
