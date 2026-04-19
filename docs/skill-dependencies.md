# Skills — Зависимости и взаимосвязи

## Граф зависимостей

```
worldgen ──creates──► worlds/<name>/ (world.md, plot.md, npcs.md, player_guide.md, starter_characters.md)
                           │
session ──creates──► games/<name>/ (game.md, state.md, log.md, characters/)
    │                      │
    │                      ▼
    ├──► characters ◄── actions (читает лист для броска, обновляет резерв)
    │         │
    │         ▼
    │    character files (YAML)
    │
    ├──► narrator ◄── world (оба читают world.md, npcs.md, state.md)
    │         │
    │         ▼
    │    (описания, диалоги)
    │
    └──► rules (загружается 1 раз при старте)
```

## Что читает / пишет каждый skill

| Skill | Читает | Пишет |
|-------|--------|-------|
| **rules** | — | — (справочник) |
| **actions** | character file, state.md, rules | character file (резерв, состояния), log.md, state.md |
| **characters** | game.md, character file | character file, game.md |
| **narrator** | world.md, npcs.md, state.md, log.md (редко) | — (только описывает) |
| **session** | worlds/, games/ | game.md, state.md, log.md, characters/ (создаёт структуру) |
| **world** | plot.md, npcs.md, state.md, character files (флаги) | state.md, log.md |
| **worldgen** | — (принимает описание) | worlds/<name>/ (все файлы) |

## Порядок вызова в типичной сессии

1. `session` → загрузить/создать игру
2. `characters` → создать/выбрать персонажей
3. `narrator` → описать начальную сцену
4. **Цикл игры:**
   - Игрок заявляет действие → `actions`
   - Нужно описание → `narrator`
   - Мир реагирует → `world`
   - Персонаж меняется → `characters`
5. `session` → завершить/сохранить игру
