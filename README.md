# MasterClaw

> `develop-v2` is an active architectural rewrite on OpenHands SDK. The legacy
> microClaw implementation remains in the repository as prompt/rules material and
> is not the runtime architecture of v2.

## v2 runtime

MasterClaw v2 is a deterministic Discord game-master service for BlackBirdPie:

- OpenHands SDK with OpenRouter-only, role-based model configuration;
- SQLite state, durable Discord inbox and transactional multi-channel outbox;
- code-owned mode routing, dice, reserve, XP, revisions and validation gates;
- bounded typed pipelines for world/character generation, action interpretation,
  advancement safety, consequences, narration and player narrator rights;
- separate game and narrative Discord channels;
- Docker Compose deployment for a single isolated Linux droplet.

Development checks:

```bash
python -m pip install -e ".[dev]"
python -m ruff check src tests
python -m ruff format --check src tests
python -m pytest -q
```

Runtime configuration starts from `.env.example`. See
`docs/openhands-refactoring-plan.md`, `docs/v2-implementation-status.md` and
`docs/v2-deployment.md`. Discord rendering and legacy-template reuse are documented in
`docs/v2-discord-formatting.md`.

Discord commands are ordinary durable messages beginning with `/`: `/world create`,
`/world generate`, `/game prepare`, `/game progression`, `/game rights`,
`/game narrative`, `/game scene`, `/character create`, `/character place`,
`/game start`, `/game status`, `/character status`, `/xp status`, `/advance` and
`/help`. All Discord participants have equal command permissions.

## Archived legacy v1 material

Everything below describes the retained microClaw source material, not the v2 runtime or deployment path.

AI-powered Game Master for **BlackBirdPie** tabletop RPG, running on [microClaw](https://github.com/microclaw/microclaw).

## What is this?

MasterClaw is a set of structured instructions (souls, skills, locale templates) that turn an LLM into a fully functional tabletop RPG game master. It handles dice mechanics, character management, world narration, session tracking, and multiplayer coordination.

Designed for the **BlackBirdPie** RPG system — a narrative-focused d6 system with trait-based dice pools and shared narrator rights.

## How it works

- **Souls** define the AI's personality and hard rules (game master for Discord, operator for Telegram)
- **Skills** are modular procedures: dice rolls, character creation, narration, session management, world generation
- **Locales** provide display templates and GM phrases per language (currently Russian and English)
- **Scripts** handle mechanics that need precision (dice rolling with true randomness)

The LLM reads these files as its instruction set, manages persistent game state (worlds, characters, logs), and runs live multiplayer sessions.

**Agent framework:** This project is designed for [microClaw](https://github.com/microclaw/microclaw), but it is not strictly required. You can use any agent framework that supports tool calling (file I/O, bash execution) and chat platform integration. Other frameworks from the same family (OpenClaw, MiniClaw, etc.) should require minimal configuration changes. With more effort, this can also be adapted to run on Claude Code, Cursor agents, or similar environments — but environment setup is on you in that case.

## Quick start

**Prerequisites:** Linux server, [microClaw](https://github.com/microclaw/microclaw) installed, Discord bot token, LLM API key.

```bash
git clone https://github.com/no-more-care/master-claw.git /root/.microclaw
cd /root/.microclaw && git checkout main
cp microclaw.config.example.yaml /root/microclaw.config.yaml
# Edit config: add your Discord/Telegram tokens and LLM API key
mkdir -p working_dir/shared/GameMaster/{worlds,games}
pip3 install pyyaml
systemctl restart microclaw
```

See **[Full Setup Guide](docs/setup.md)** for detailed step-by-step instructions including Discord bot creation, Telegram setup, and troubleshooting.

## Project structure

```
souls/           — AI personality definitions
skills/          — Modular game mechanic procedures
locales/         — Display templates per language (ru, en)
scripts/         — Utility scripts (dice roller, channel manager)
docs/            — Architecture, mechanics, and setup documentation
working_dir/     — Runtime game data (worlds, games, characters)
```

## Key features

- Full BlackBirdPie rules enforcement (dice pools, narrator rights, reserve mechanics)
- Automated dice rolling with correct hit counting and narrator rights assignment
- Character validation (18-point trait system, aspect limits, flag rules)
- Multi-language support with auto-detection
- Persistent game state across sessions
- World generation from free-form descriptions
- Multiplayer support with mid-session player joining

## Documentation

- [Setup Guide](docs/setup.md) — installation, configuration, first game
- [Architecture](docs/architecture.md) — system layers, data flow, operating modes
- [Mechanics Reference](docs/mechanics-reference.md) — BlackBirdPie rules cheat sheet
- [Skill Dependencies](docs/skill-dependencies.md) — how skills interact
- [Config Example](microclaw.config.example.yaml) — annotated configuration template

## License

[MIT](LICENSE)
