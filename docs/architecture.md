# MasterClaw v1 — archived architecture

> This document describes the retired microClaw runtime retained as rules and prompt reference.
> It does not describe the active v2 Python service. For v2, use
> `docs/openhands-refactoring-plan.md`, `docs/v2-implementation-status.md`, and
> `docs/v2-deployment.md`.

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

## Runtime operating modes

```
WORLD SELECTION ── choose approved world / resume draft / create draft
  ├─ WORLD PROJECT (collecting ↔ review; explicit approve or pause)
  └─ PREPARATION (settings, pregens, player characters; no scene yet)
       └─ explicit start creates opening scene and placements
            └─ ACTIVE PLAY (actions, scene, status, help, advancement)
```

The persisted branch is authoritative. Neither a slash command nor an LLM decision may jump to a
different mode. Rules questions are handled as the only cross-mode informational branch.

### v2 message dispatch

The active Python service resolves the persisted state to a scenario, builds a read-only
`DispatchSnapshot`, and passes it to the pure `app.dispatch.decide()` function before executing any
handler. The documented priority is:

1. slash commands declared by the selected scenario;
2. resumed rolls and pending interactions;
3. an open world workspace;
4. exact phrases declared by the selected scenario;
5. the bounded scenario state model.

`app.scenarios.resolve_scenario()` maps persisted state to
one of world selection, world editing (collecting/review), preparation, play, narrow pending, or roll
resume scenarios before text is interpreted. Each descriptor owns its exact normalized phrases,
slash commands, closed LLM command set, context projections, history budget, and fallback. Common
`SHOW_RULES`, `SHOW_HELP`, and `CLARIFY` commands are present in every interactive scenario. The same
phrase may intentionally map differently per scenario; for example, “что ты умеешь” shows the world
catalogue during selection and mode-specific help elsewhere.

`pipelines.state_decision` generates a Pydantic output contract whose command enum contains only the
selected scenario's commands. Invalid model output gets one bounded repair attempt and then the
scenario's explicit fallback. There is no global intent classifier or post-classification mode gate:
an unavailable command cannot be emitted by that scenario's schema. Selection arguments are resolved
by exact title/name; paraphrases belong to the state model rather than fuzzy global matching. Decisions
record `scenario`, `command`, `source`, gate result, and evidence in telemetry.

Scenario descriptors also control the state model's projection list and chat-history depth. World
selection receives the catalogue, play receives the current scene and actor, and pending scenarios
receive the outstanding interaction. High-confidence model decisions for routine information or
pending replies emit normalized `lexicon_candidate` telemetry for later exact-lexicon promotion.

Each typed pipeline manifest declares its invalid-output fallback. Routine schema failures are
translated into stage-specific clarification instead of escaping to the orchestrator. Context
assembly degrades bounded history and long projection strings before failing, and oversized player
messages are rejected before any model call. Claimed inbox messages are persisted one at a time, so
one handler failure cannot discard successful siblings or fail unrelated messages. Provider retries
use typed transient failures and 5/30-second backoff; deterministic failures are not retried.

`app.message_handler.MessageApplication` is a thin 145-line lifecycle facade. Pure decision
execution lives in `app.handlers.dispatching`; world management, preparation, information, play,
slash-command, and shared context responsibilities live in separate handler groups. The facade keeps
the existing constructor as the composition root, so callers do not depend on handler internals.

World-project ownership is also enforced in storage: a named partial unique index allows at most one
active project per channel, reads are deterministic and assert uniqueness, and both dispatch and the
creation handler reject attempts to replace an open editor project.

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
