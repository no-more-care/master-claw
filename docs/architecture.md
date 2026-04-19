# MasterClaw — Architecture

## Overview

MasterClaw is an AI game master for the BlackBirdPie tabletop RPG, running on microClaw. The agent LLM reads souls/skills/locales as its instructions. Two interfaces: Discord (players) and Telegram (operator).

## Four layers

### 1. Souls — Personalities
Context prompts that define AI behavior.

| Soul | File | Channel | Purpose |
|------|------|---------|---------|
| GameMaster | `souls/gamemaster.md` | Discord | Running the game for players |
| Operator | `souls/operator.md` | Telegram | World management, diagnostics |

### 2. Skills — Procedures
Modular step-by-step instructions for specific mechanics.

| Skill | File | When used |
|-------|------|-----------|
| rules | `skills/rules/SKILL.md` | Authoritative rules source (loaded once) |
| actions | `skills/actions/SKILL.md` | Processing player declarations (rolls, results) |
| characters | `skills/characters/SKILL.md` | Character creation/management |
| narrator | `skills/narrator/SKILL.md` | World descriptions, NPC dialogue |
| session | `skills/session/SKILL.md` | Start/continue/end games |
| world | `skills/world/SKILL.md` | World event generation, NPC reactions |
| worldgen | `skills/worldgen/SKILL.md` | World creation from description |

### 3. Locales — Templates
Display formats and GM phrases per language.

```
locales/
├── ru/templates/   — Russian templates
└── en/templates/   — English templates
    ├── character_display.md
    ├── dice_pool.md
    ├── starter_character.md
    ├── game_file.md
    ├── state_file.md
    ├── log_entry.md
    └── prompts.md
```

### 4. Working Directory — Data
Persistent storage: worlds, games, characters, logs.

```
working_dir/shared/GameMaster/
├── worlds/<world_name>/
│   ├── world.md              — World description, locations, factions
│   ├── npcs.md               — NPC cards
│   ├── plot.md               — Plot (SECRET, never shown to players)
│   ├── player_guide.md       — What characters know
│   └── starter_characters.md — Ready-to-play characters (optional)
└── games/<game_name>/
    ├── game.md               — Game metadata (status, players, language)
    ├── state.md              — Current state (OVERRIDES worlds/)
    ├── log.md                — Chronological event log
    └── characters/
        └── <name>.md         — Character sheets (YAML schema)
```

## GameMaster operating modes

```
MODE 1: OPERATOR WORK ─── No active game
  │  Skills: worldgen, session
  ▼
MODE 2: PREPARATION ──── Game created, before start
  │  Skills: characters, narrator
  ▼
MODE 3: GAME ──────────── Active session
  │  Skills: actions, narrator, world, characters
  ▼
MODE 1: Game saved, waiting to continue
```

## Data flow

1. **Operator** (Telegram) → `session` creates game folder
2. **Player** (Discord) declares action → `actions` processes via character sheet
3. GM needs context → `narrator` reads world.md + npcs.md + state.md
4. World reacts → `world` generates event, updates state.md + log.md
5. Campaign grows → character files accumulate experience, conditions, aspects

## Data authority hierarchy

1. `skills/rules/SKILL.md` — single source of rules
2. `state.md` overrides `worlds/` (dead NPC stays dead)
3. Character sheet is law (not on sheet = cannot use)
4. Player confirmation required before rolling

## Information security

| File | Players | Operator | GM (AI) |
|------|---------|----------|---------|
| player_guide.md | Yes | Yes | Yes |
| world.md | No | Yes | Yes |
| npcs.md | No | Yes | Yes |
| plot.md | **NEVER** | Yes | Yes |
| state.md | Partial (scene) | Yes | Yes |
| log.md | No (on request) | Yes | As needed |
