# MasterClaw

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

## Project structure

```
souls/           — AI personality definitions
skills/          — Modular game mechanic procedures
locales/         — Display templates per language (ru, en)
scripts/         — Utility scripts (dice roller)
docs/            — Architecture and mechanics documentation
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

- [Architecture](docs/architecture.md) — system layers, data flow, operating modes
- [Mechanics Reference](docs/mechanics-reference.md) — BlackBirdPie rules cheat sheet
- [Skill Dependencies](docs/skill-dependencies.md) — how skills interact

## License

[MIT](LICENSE)
