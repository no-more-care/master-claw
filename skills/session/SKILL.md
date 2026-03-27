# SKILL: Session Management (BlackBirdPie)

Manages top-level game flow: starting a game in a chosen world, switching between active games, status overview, archiving.

All game files are at:
/root/.microclaw/working_dir/shared/GameMaster/

## File structure

GameMaster/
├── worlds/<world>/
│   ├── world.md
│   ├── npcs.md
│   ├── plot.md
│   └── player_guide.md
└── games/<game>/
    ├── game.md        ← status: active / finished
    ├── state.md
    ├── log.md
    └── characters/<character>.md

worlds/ — read-only during play. games/ — live state, changes during play.

## Commands

### start game / new game

New game:
1. List folders in GameMaster/worlds/
2. Ask: which world, game name, players
3. Create GameMaster/games/<name>/
4. Create game.md, state.md (from world.md), log.md, characters/
5. Announce ready

Continue existing game:
1. List games with status active in game.md
2. Load game.md, state.md (full), character sheets
3. If "Текущая сцена" in state.md is empty or missing — read last 10 log.md entries to reconstruct it, then write it into state.md
4. Summary: current scene, characters, dice reserves

### game status
Game: <name>
World: <world>
Status: active
Characters: <name> — dice reserve: X/7, conditions: <list>
Last events: <3 log entries>
Active plot threads: <from state.md>

### end game (requires confirmation)
1. Set status to finished, record date
2. Add final entry to log.md
3. Do not delete data — only mark as finished

### delete game (irreversible, explicit request only)
1. Show list of files to be deleted
2. Request confirmation: "delete [name]"
3. Delete folder entirely

## File templates

### game.md
# Game: <name>
**World:** <folder in worlds/>
**Status:** active
**Start date:** <date>
**End date:** —
## Players
| Player | Character | File |
|---|---|---|
## GM notes

### state.md
# World state: <game name>
> Base data: GameMaster/worlds/<world>/world.md

## Текущая сцена
<3–5 строк: где находятся персонажи прямо сейчас, что происходит, активные угрозы или возможности.
Обновлять ПОСЛЕ каждого значимого действия или смены сцены. Это основной источник контекста для нарратора — вместо чтения log.md целиком.>

## Current party location
## World changes
## Active plot threads
## Key NPC status
| NPC | Status | Relation to party | Last interaction |
## Faction positions

### log.md
# Game log: <game name>
## [Date / Game moment]
**Event:** <name>
**Participants:** <characters, NPCs>
**Description:** <what happened>
**Rolls:** <who, difficulty, result>
**Consequences:** <what changed>
