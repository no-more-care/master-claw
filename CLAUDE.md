# MasterClaw — CLAUDE.md

## What is this project?

MasterClaw is an AI game master system for the **BlackBirdPie** tabletop RPG, running on **microClaw** (Claude as agent). Supports multiplayer campaigns via Discord (players) and Telegram (operator).

## Architecture

Four layers:
- **Souls** (`souls/`) — AI personalities: `gamemaster.md` (Discord, players) and `operator.md` (Telegram, operator)
- **Skills** (`skills/`) — modular procedure files (7 skills)
- **Locales** (`locales/`) — display templates and GM phrases per language (`ru/`, `en/`)
- **Working directory** (`working_dir/`) — live game data (worlds, characters, logs, state)

## Key files

```
souls/gamemaster.md        — GM personality, hard rules checklist, 3 operating modes
souls/operator.md          — operator/manager personality

skills/rules/SKILL.md      — SINGLE AUTHORITATIVE source for all BlackBirdPie rules
skills/actions/SKILL.md    — procedure for processing player declarations (Steps 0-8)
skills/characters/SKILL.md — character creation/management (YAML schema, validation)
skills/narrator/SKILL.md   — narration principles, NPC dialogue, character perspective
skills/session/SKILL.md    — session management (start, continue, join, end)
skills/world/SKILL.md      — world event generation, NPC reactions
skills/worldgen/SKILL.md   — world generation from description

scripts/roll.py            — dice rolling script (true random, auto narrator rights)

locales/{lang}/templates/  — display formats and GM phrases per language
  character_display.md     — readable character format for players
  dice_pool.md             — pool calculation display
  starter_character.md     — starter character card
  game_file.md             — game.md template
  state_file.md            — state.md template
  log_entry.md             — log entry format
  prompts.md               — ready-to-use GM phrases

working_dir/shared/GameMaster/rules.md — DEPRECATED, never use
```

## Design principles

### Single source of truth
- `rules/SKILL.md` is the ONLY canonical source for game mechanics
- Other skills reference rules, never duplicate them with full explanations
- Character YAML schema lives only in `characters/SKILL.md`

### Locale system
- All display formats and GM phrases live in `locales/{lang}/templates/`
- Skills reference templates via `locales/{lang}/templates/<name>.md`
- Language auto-detected from players, persisted in `game.md` as `language: <code>`
- Missing locale → translate English templates on the fly

### Instruction files are English-only
- All skill and soul files are written in English
- No Russian/Cyrillic in instructions (except forbidden-string patterns in rules for detection)
- No hardcoded character names — use generic `[Character A]`, `[Player B]`

### Dice rolling
- All rolls via `scripts/roll.py <pool_size> <difficulty>`
- GM never generates dice manually — script handles random, hit counting, narrator rights
- Script output has player-facing block + `[GM LOG: ...]` for internal logging

## Critical rules (key numbers)

1. **Trait level != dice count.** Each applicable trait = +1 die, NOT +level.
2. **18 points** — always the sum of trait levels per character.
3. **7 dice** — maximum reserve.
4. **4-6 on d6** = hit.
5. **Narrator rights:** hits > diff → player, hits = diff → GM, hits = diff-1 → player, hits < diff-1 → GM.
6. **Always wait** for player confirmation before rolling.
7. **Max 1 flag** per roll.
8. **Help = give 1 die** from reserve, no roll needed, no penalty for helper.
9. **GM is impartial** — never lower difficulty on request, never add non-applicable traits.

## File structure for games

```
working_dir/shared/GameMaster/
├── worlds/<world>/
│   ├── world.md, npcs.md, plot.md, player_guide.md, starter_characters.md
└── games/<game>/
    ├── game.md (includes language field), state.md, log.md
    └── characters/<character>.md
```

## Conventions

- plot.md — NEVER show to players
- state.md overrides worlds/ (dead NPC stays dead)
- Character sheet is law (cannot use what's not on sheet)
- Reserve: success = lose spent dice; failure = return + 1 (cap 7)
- Mandatory file writes after every action: log.md, character file, state.md
- Re-read character file before every pool calculation (prevents hallucinated aspects)
- Re-read state.md + character files every 10-15 messages (prevents context drift)

## When editing skills

- Do not break the YAML character schema in `characters/SKILL.md`
- `rules/SKILL.md` is canonical — add rule details there, reference from other files
- Keep procedures step-by-step with validation at each step
- Skills are interdependent: actions uses characters, narrator reads world, session creates files for all others
- Templates go in `locales/`, not inline in skill files
- Examples must be generic (no real character/game names)

## Deployment

- Droplet: `[REDACTED]` (root, ssh key ed25519)
- Agent dir: `/root/.microclaw/`
- Branch: `develop`
- Pull updates: `cd /root/.microclaw && git pull origin develop`
