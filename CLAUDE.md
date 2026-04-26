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

- **Free tier** (free OpenRouter models, small self-hosted like Llama/Qwen): currently **not viable** for MasterClaw. Verified across two playtest passes (5 distinct models — glm-4.5-air, qwen3-next, llama-3.3, gpt-oss-120b, minimax-m2.5). Provider rate limits or outages knock out half the candidates on any given day. The ones that *do* respond either stall in tool-loops without emitting final text, or break the character/game schema (no trait levels, wrong reserve, invented enum values for `narrative_style` / `narrator_rights_level`, missing flags). Even the best-of-free (minimax-m2.5) loses to cheap-tier grok-4.1-fast on every dimension. Recheck periodically: the bar for "free" rises over time, and temporary promo / gift-credit models occasionally land. Until then, useful only for smoke-testing pipeline wiring.
- **Cheap tier** (roughly $0.05–$1 per M output): uneven. See the cheap-tier playtest notes below — **most cheap models fabricate dice rolls** (skip `scripts/roll.py` and invent results) and **mangle the character YAML schema**. Only Grok-4.1-fast passed the baseline 3-turn test cleanly and is the current cheap-tier recommendation. Retest before adopting anything else from this tier.
- **Mid tier** (GLM-5 / 5.1, DeepSeek-R1, Gemini-2.5 Pro, Grok-4.1 full, Claude Haiku 4.5): noticeably more consistent. Still benefits from explicit reminders in long sessions but handles pushback better. Reasonable choice for serious campaigns on a budget. Not re-baselined after the recent skill/script additions — do that before promoting one to default.
- **Premium tier** (Claude Sonnet 4.6+, GPT-5, Claude Opus): expected to handle the full rule surface without hand-holding. ~10× the cost of cheap-tier. Not yet baseline-tested against this ruleset — retest when evaluating production upgrades.

### Cheap-tier baseline (3-turn agentic playtest, April 2026)

Tested via the web channel with `gamemaster.md` soul: (1) create world + trader starter, (2) look around, (3) haggle using Торговля + Обаяние. All four models shared the same failure of **never creating a scene sheet in `games/<game>/scenes/`** despite improvising specifics — that's a skill-side gap, not model-side. Everything else was model-side:

| Model | Character YAML | Pool build | Uses `roll.py` | Narrative |
|---|---|---|---|---|
| `x-ai/grok-4.1-fast` ($0.20/$0.50) | ✅ schema-correct, 18 pts, reserve 7/7 | ✅ correct breakdown, waits for confirm | ✅ did not fabricate | crisp, concise |
| `z-ai/glm-4.7-flash` ($0.06/$0.40) | ❌ prose+table, allowed level 6 (>5) | ✅ breakdown ok | ❌ **fabricated** "1 success out of 4" | vivid but rambling; prior run leaked Chinese chars into Russian |
| `deepseek/deepseek-chat-v3.1` ($0.15/$0.75) | ❌ invented own "level 1 = aspects" schema, reserve 3 (should 7) | ❌ `"Уровень 1 = 1 кубик"` — the exact `level=dice` bug the soul warns about | ❌ **fabricated** "3 of 3 success" | best narrative voice of the four — but mechanics unsafe |
| `qwen/qwen3-235b-a22b-2507` ($0.07/$0.10) | ❌ traits without levels, reserve 3, invented `[Используется]` aspect state | ⚠️ no dice count shown | ❌ **fabricated** "4 успеха / 4к6" | flat, character name drift (`Kel_Torren` vs `Кэл Торрен`) |

**Takeaway:** fabricated rolls are the most dangerous failure — the model invents outcomes to please the player instead of yielding to real RNG, silently breaking the whole system's fairness. Three of four cheap models do it. Only Grok-4.1-fast respected the rule and stopped to wait for player confirmation.

**Recommendation for cheap-tier default: `x-ai/grok-4.1-fast`** until mid-tier is re-baselined.

Rules of thumb:
- Don't lock the codebase to a single provider's quirks. Keep prompts model-agnostic.
- After non-trivial skill changes, retest on at least one cheap-tier model (it will surface instruction-following gaps that a strong model would paper over).
- When a player reports "the GM lost state" or "the GM ignored a rule", first check the model tier before patching the skill.
- If a model silently produces roll results without calling `scripts/roll.py` (visible in runtime logs), treat it as disqualified from production — not a prompt tweak, a trust issue.
