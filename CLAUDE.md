# MasterClaw — CLAUDE.md

## What is this project?

MasterClaw — система AI-гейммастера для настольной RPG **BlackBirdPie**, работающая на платформе **microClaw** (Claude как агент). Поддерживает мультиплеерные кампании через Discord (игроки) и Telegram (оператор).

## Architecture

Три слоя:
- **Souls** (`souls/`) — личности AI: `gamemaster.md` (Discord, для игроков) и `operator.md` (Telegram, для оператора)
- **Skills** (`skills/`) — модульные процедуры механик (7 скиллов)
- **Working directory** (`working_dir/`) — живые данные игр (миры, персонажи, логи, состояние)

## Key files

```
souls/gamemaster.md      — личность ГМ, 3 режима работы, жёсткие правила
souls/operator.md        — личность оператора/менеджера
skills/rules/SKILL.md    — ЕДИНСТВЕННЫЙ АВТОРИТЕТНЫЙ источник правил BlackBirdPie
skills/actions/SKILL.md  — обработка заявок игроков (бросок, сложность, права рассказчика)
skills/characters/SKILL.md — создание/управление персонажами (YAML-схема)
skills/narrator/SKILL.md — нарративные описания, диалоги NPC
skills/session/SKILL.md  — управление сессиями (старт, продолжение, архивация)
skills/world/SKILL.md    — генерация событий мира, реакции NPC
skills/worldgen/SKILL.md — генерация миров из описания (world.md, plot.md, npcs.md, player_guide.md)
working_dir/shared/GameMaster/rules.md — DEPRECATED, не использовать
```

## Critical rules (repeat everywhere intentionally)

1. **Уровень черты != количество кубов.** Каждая подходящая черта = +1 куб, НЕ +уровень.
2. **18 очков** — всегда сумма уровней черт персонажа.
3. **7 кубов** — максимум резерва.
4. **4-6 на d6** — это успех (hit).
5. **Права рассказчика:** hits > diff → игрок ("Да, и..."), hits = diff → ГМ ("Да, но..."), hits = diff-1 → игрок ("Нет, но..."), hits < diff-1 → ГМ ("Нет, и...").
6. **Всегда ждать подтверждения** игрока перед броском.
7. **Весь текст для игроков — на русском.**

## File structure for games

```
working_dir/shared/GameMaster/
├── worlds/<world>/
│   ├── world.md, npcs.md, plot.md, player_guide.md, starter_characters.md
└── games/<game>/
    ├── game.md, state.md, log.md
    └── characters/<character>.md
```

## Conventions

- Skills написаны на английском (экономия токенов), вывод игрокам — на русском
- plot.md — НИКОГДА не показывать игрокам
- state.md переопределяет worlds/ (убитый NPC остаётся мёртвым)
- Лист персонажа — закон (нельзя использовать то, чего нет в листе)
- Резерв: успех = потеря потраченных кубов; провал = возврат + 1 (макс 7)

## When editing skills

- Не ломать YAML-схему персонажей в `characters/SKILL.md`
- Сохранять паттерн повторения критических правил во всех файлах
- Все процедуры — пошаговые, с валидацией на каждом шаге
- Скиллы взаимосвязаны: actions использует characters, narrator читает world, session создаёт файлы для всех остальных
