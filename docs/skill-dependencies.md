# Skills — Dependencies and Interactions

## Dependency graph

```
worldgen ──creates──► worlds/<name>/ (world.md, plot.md, npcs.md, player_guide.md, starter_characters.md)
                           │
session ──creates──► games/<name>/ (game.md, state.md, log.md, characters/)
    │                      │
    │                      ▼
    ├──► characters ◄── actions (reads sheet for roll, updates reserve)
    │         │
    │         ▼
    │    character files (YAML)
    │
    ├──► narrator ◄── world (both read world.md, npcs.md, state.md)
    │         │
    │         ▼
    │    (descriptions, dialogue)
    │
    └──► rules (loaded once at start)
```

## What each skill reads / writes

| Skill | Reads | Writes |
|-------|-------|--------|
| **rules** | — | — (reference only) |
| **actions** | character file, state.md, rules | character file (reserve, conditions), log.md, state.md |
| **characters** | game.md, character file | character file, game.md |
| **narrator** | world.md, npcs.md, state.md, log.md (rarely) | — (describes only) |
| **session** | worlds/, games/ | game.md, state.md, log.md, characters/ (creates structure) |
| **world** | plot.md, npcs.md, state.md, character files (flags) | state.md, log.md |
| **worldgen** | — (takes description as input) | worlds/<name>/ (all files) |

## Typical session call order

1. `session` → load/create game
2. `characters` → create/pick characters
3. `narrator` → describe opening scene
4. **Game loop:**
   - Player declares action → `actions`
   - Description needed → `narrator`
   - World reacts → `world`
   - Character changes → `characters`
5. `session` → end/save game
