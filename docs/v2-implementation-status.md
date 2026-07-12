# MasterClaw v2 — implementation status

## Increment 1 — foundation

Started 2026-07-13 on branch `develop-v2`.

Implemented:

- Python 3.12 package skeleton;
- OpenHands SDK 1.33.0 dependency;
- OpenRouter-only configuration with independent `state`, `reasoning` and `narrative` model roles;
- deterministic top-level router for the three agreed modes;
- SQLite bootstrap in WAL mode;
- durable idempotent Discord inbox and transactional outbox schema;
- sequential per-channel batch processor producing one addressed Discord response block;
- Docker Compose baseline for the isolated Linux droplet;
- unit tests for routing, batching and inbox ordering/deduplication.

Completed.

## Increment 2 — durable Discord boundary and domain core

Implemented:

- `masterclaw serve` Discord daemon entry point;
- sequential inbox claim/complete/fail and startup recovery;
- quiet-window batching that includes messages arriving while earlier messages are processed;
- one logical addressed response, safely split into Discord-sized transactional outbox records;
- per-channel delivery filtering and retry metadata;
- games, worlds, scenes, channel bindings and player pending interactions in SQLite;
- optimistic revisions for mutable game state;
- exactly one open pending interaction per player/game;
- independent-scene conflict predicate;
- readiness gate requiring a narrative channel and initial scene;
- bounded OpenHands intent pipeline: no tools, typed JSON, one repair maximum;
- prompt/context manifests, explicit projections, fragment versions and input budgets;
- deterministic BlackBirdPie pool, RNG, reserve and narrator-rights mechanics;
- separate starting-character policy: 3–9 traits, level 2–6, exactly 18 points;
- typed character persistence and relationship-flag validation;
- readiness-separated starting-character policy and advancement policy boundary;
- roll proposal persisted as a revision-bound pending interaction;
- idempotent roll confirmation: immutable roll, reserve update, pending resolution and domain event in one transaction;
- code-driven session activity clock starting at the `ACTIVE` transition;
- per-event credited time `min(actual interval, 5 minutes)` with out-of-order protection;
- session-level progression setting locked before `ACTIVE`;
- automatic award of one XP per newly completed 30-minute interval to every character;
- advancement above level 6 with cost equal to the new level;
- new level-2 traits for 3 XP with two aspects and mandatory learning justification;
- safe-scene gate for all advancement and transactional XP spending/audit events;
- deterministic `/game status` and `/xp status` commands without LLM calls;
- bounded GM/state advancement-safety pipeline with a scene-revision-bound permit;
- typed reasoning pipeline for action interpretation with code-side pool validation;
- reserve selected only by the player in the pending confirmation response;
- confirmation/cancellation handling and mechanical game-channel result;
- typed outcome-narrative pipeline with leakage gates;
- transactional multi-channel outbox: mechanical response to game channel and prose to narrative channel;
- immutable rolls keyed by Discord confirmation event for crash-safe resume after the pending interaction is closed;
- safe narrative fallback that never rerolls or rolls back committed mechanics;
- end-to-end declaration → proposal → confirmation → roll → dual-channel narrative test;
- equal-rights Discord preparation commands for world/game skeleton, progression setting,
  narrative channel, initial scene and start;
- typed world generation with secret plot separated from public context;
- typed starting-character generation with deterministic 18-point/aspect/flag validation;
- readiness requires at least one character and placement of every character;
- automatic XP award when each full 30-minute interval is crossed; no human GM role or settlement;
- typed GM/state advancement-safety decision bound to the current scene revision;
- revision-bound player narrator-rights pending, review and narrative-channel publication;
- typed scene-fact consequence patches for automatic actions, GM outcomes and accepted player narration;
- configurable narrator-rights levels (`disabled`, `minor`, `significant`, `madness`),
  with `disabled` deterministically retaining narration in the GM pipeline;
- schema compatibility version check on SQLite startup;
- Ruff lint/format gate and Linux CI workflow;
- versioned, transactional SQLite migration runner with fail-closed compatibility checks;
- dead-letter inspection and requeue commands for inbox/outbox recovery;
- online WAL-safe backup command plus systemd service and daily backup timer;
- OpenRouter role smoke test covering state, reasoning and narrative model contracts;
- deterministic multiplayer help, conditions/plot-item projections and social difficulty rules;
- independent-scene revision tests proving that unrelated scenes can commit without a shared lock;
- 79 passing tests covering the current core.

The daemon now contains tested vertical slices for world setup, character preparation,
automatic actions, roll-based actions, narrator-rights follow-up, progression and dual-channel output.
The repository implementation is feature-complete for the agreed v2 scope. Promotion to a live campaign
still requires environment validation with the real Discord token and configured OpenRouter models on the
Linux droplet; those checks cannot be performed safely from a source checkout without deployment secrets.

## Deployment acceptance remaining

1. Build the pinned container on Linux and run the unit/lint suite.
2. Run `masterclaw doctor` and `masterclaw model-smoke` with production model ids.
3. Exercise a private Discord staging channel through setup, action, restart and outbox recovery.
4. Take and restore an online backup before enabling the production service.
