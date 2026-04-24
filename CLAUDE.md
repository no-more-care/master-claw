# MasterClaw — CLAUDE.md

## What is this project?

MasterClaw is an AI game master system for the **BlackBirdPie** tabletop RPG, running on **microClaw** platform. The agent inside microClaw is an LLM configured via `microclaw.config.yaml` — any chat-capable model with tool-calling works. It reads the souls/skills/locales from this repo as its instructions. This CLAUDE.md is NOT read by the agent — it is context for Claude Code sessions working on this project.

See **Model tier notes** at the bottom of this file for tradeoffs.

Supports multiplayer campaigns via Discord (players) and Telegram (operator).

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

- Runs on a DigitalOcean droplet (connection details in memory, not in repo)
- Agent dir on server: `/root/.microclaw/`
- Branch: `develop`
- Pull updates: `cd /root/.microclaw && git pull origin develop`

## Model tier notes

MasterClaw's skills are prompt-heavy (lots of rules, procedures, templates). Model capability directly shapes session quality. Informal observations from playtesting:

- **Free tier** (free OpenRouter models, small self-hosted like Llama/Qwen): context leaks, rule violations, forgets state frequently. Useful only for smoke-testing pipeline wiring.
- **Cheap tier** (Grok-4.1-fast, GLM-5-turbo, DeepSeek-chat, Gemini Flash): works for casual play if you re-ground it periodically with reminders about state and rules. Breaks down if a player tries to manipulate the GM (e.g. arguing about difficulty, invoking non-existent traits). Expect occasional language drift on non-English games.
- **Mid tier** (Grok-4.1-full, GLM-5.1, DeepSeek-R1, Gemini Pro, Claude Haiku 4.5): noticeably more consistent. Still benefits from explicit reminders in long sessions but handles pushback better. Reasonable choice for serious campaigns on a budget.
- **Premium tier** (Claude Sonnet 4.6+, GPT-5, Claude Opus): expected to handle the full rule surface without hand-holding. ~10× the cost of cheap-tier. Not yet baseline-tested against this ruleset — retest when evaluating production upgrades.

Rules of thumb:
- Don't lock the codebase to a single provider's quirks. Keep prompts model-agnostic.
- After non-trivial skill changes, retest on at least one cheap-tier model (it will surface instruction-following gaps that a strong model would paper over).
- When a player reports "the GM lost state" or "the GM ignored a rule", first check the model tier before patching the skill.
