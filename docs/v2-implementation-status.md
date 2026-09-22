# MasterClaw v2 — implementation status

## Increment 1 — foundation

Started 2026-07-13 on branch `develop-v2`.

Implemented:

- Python 3.12 package skeleton;
- OpenHands SDK 1.33.0 dependency;
- OpenRouter-only configuration with independent `state`, `reasoning`, `narrative` and `worldgen`
  model roles;
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
- readiness gate requiring a narrative channel and at least one character; initial scene and
  placement are created atomically only at explicit game start;
- bounded OpenHands pipelines with separated cached system rules, dynamic context and current task;
- strict typed terminal output tools plus explicit prompt-JSON alternate transport;
- per-role output transport and reasoning-effort configuration with one repair maximum;
- 6000-token production ceilings for reasoning and narrative-review roles, with exhausted typed
  validation excluded from whole-inbox retries;
- prompt/context manifests, explicit projections, selected fragment ids and input budgets;
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
- reserve recovery policy selected at game preparation (`safe_rest`, `roleplay_award`, or `both`), with
  system game-master adjudication from canonical context and audited before/after values;
- confirmation/cancellation handling and mechanical game-channel result;
- typed outcome-narrative pipeline with leakage gates;
- transactional multi-channel outbox: mechanical response to game channel and prose to narrative channel;
- persistent per-channel Discord ingress allowlist, controlled by an explicit bot mention; enabled
  channels accept ordinary player messages without requiring mentions;
- conversational-first Discord routing for world and character creation, automatic opening-scene
  placement, game start/configuration/status, rules and scene questions, roleplay, help,
  advancement, action declarations and natural-language reserve confirmation; slash forms remain
  optional shortcuts;
- persistent two-gate world editor: provenance-labelled input/default settings, explicit draft
  generation, public review with revision/regeneration, and separate approval before preparation;
- strict world-editor state routing: selection has only select/create branches; an allocated
  temporary project owns every ordinary message until explicit generate, approve or exit controls;
  long briefs bypass deterministic gates, controls use whole-message matching, reviewed edits become
  dirty without auto-regeneration, exit retains a resumable draft, and transitions/applied fields
  are emitted in operational logs;
- lifecycle-first preparation/play routing: commands cannot override state, preparation cannot see
  a not-yet-created scene, active play cannot enter setup, and only rules questions are global;
- worldgen-owned playable pregens with validated sheets, biographies, faction affiliations and
  inter-pregen relationships, selectable without an additional LLM call; all characters are placed
  together in the opening scene by the explicit game-start transition;
- pregen sheet and cross-reference invariants enforced inside the typed world-structuring contract,
  keeping repair local and preventing full creative-worldgen retries;
- deterministic framed cards for world setup, preparation and play, including public world data,
  pregenerated characters, readiness, latest active scene and compact character conditions;
- scenario-local exact routing for common world-list, status, sheet, XP and scene-information
  requests, with paraphrases delegated to a bounded scenario state model;
- pure snapshot-based dispatch with a tested command → pending → workspace → exact phrase →
  scenario-model priority table and scenario/command/source/gate telemetry;
- mode-aware phrase disambiguation (`что ты умеешь` is help during play), an explicit slash-command
  allowlist, and REVIEW-stage clarification instead of an implicit world revision;
- fully migrated scenario-first routing for world selection/editing, preparation, play, pending and
  roll resume, with per-scenario exact lexicons, closed command sets, dynamic state-model schemas,
  bounded repair and explicit fallbacks; legacy global intents, post-gate and fuzzy matchers removed;
- descriptor-driven STATE projections and history depth, including world catalogue, workspace,
  pending interaction, current scene and actor-character context;
- single-source slash routing by scenario `CommandId`, information commands during pending, precise
  usage for bad arguments and deterministic world-settings rendering with provenance;
- manifest-declared invalid-output fallbacks, bounded context degradation, oversized-input guard,
  stale-pending reminders/expiry, isolated inbox-message failures and provider retry backoff;
- same-role STATE fallback completion and high-confidence exact-lexicon candidate telemetry;
- thin message lifecycle facade with decision execution and world-management, preparation,
  information, play, command and shared-context handlers split into `app/handlers/`;
- storage-enforced single active world project per channel and deterministic workspace lookup;
- reusable world catalogue with code-rendered cards, ordinal/title selection and a separate
  selection gate between approved world creation and game preparation;
- deterministic recognition of explicit new-world requests and common world-list wording before
  scenario classification;
- complete world-editor settings card covering narrative policy, narrator rights, reserve recovery,
  progression and pregenerated characters, with explicit defaults/options and Markdown-only
  horizontal sectioning;
- mode-colored Discord rich embeds for deterministic status context, with ordinary response text
  kept separate and routine author/player mentions removed;
- one-daemon-per-database runtime locking and atomic outbox delivery claims, preventing duplicate
  Discord replies from overlapping local processes;
- parent-linked performance spans across ingress, DB, context, pipelines, fallback/repair, domain
  gates and delivery, plus trace-linked LLM usage and the filterable `performance-report` CLI;
- immutable rolls keyed by Discord confirmation event for crash-safe resume after the pending interaction is closed;
- safe narrative fallback that never rerolls or rolls back committed mechanics;
- end-to-end declaration → proposal → confirmation → roll → dual-channel narrative test;
- equal-rights Discord preparation controls for progression, narrative channel, characters and
  explicit start;
- two-stage module generation (DeepSeek → Aion creative route, then Luna → Gemini consistency and
  typed structuring) and a conservative Luna → Gemini editorial pass after raw narration;
- typed world generation with secret plot separated from public context;
- typed starting-character generation with deterministic 18-point/aspect/flag validation;
- readiness requires at least one character; placement is deferred to the start transition;
- automatic XP award when each full 30-minute interval is crossed; no separate human GM role;
- typed GM/state advancement-safety decision bound to the current scene revision;
- revision-bound player narrator-rights pending, review and narrative-channel publication;
- typed scene-fact consequence patches for automatic actions, GM outcomes and accepted player narration;
- configurable narrator-rights levels (`disabled`, `minor`, `significant`, `madness`),
  with `disabled` deterministically retaining narration in the GM pipeline;
- Ruff lint/format gate and Linux CI workflow;
- versioned SQLite schema v10 with typed decision/handler replay journals, event-scoped mutation
  operations, durable provider retry state and an idempotent lexicon-candidate ledger; initialization
  and supported legacy migrations remain fail-closed for newer schemas;
- dead-letter inspection and requeue commands for inbox/outbox recovery;
- pre-migration WAL-safe backup command plus timestamped generations in a separate backup volume,
  systemd service and daily timer;
- OpenRouter role smoke test covering state routing; action, compound, consequence and world-intake
  reasoning; narrative output; and the creative plus typed-structuring worldgen contracts;
- deterministic multiplayer help, conditions/plot-item projections and social difficulty rules;
- independent-scene revision tests proving that unrelated scenes can commit without a shared lock;
- persistent per-session LLM token, cost and latency telemetry, plus a weekly routing-quality report
  for lexicon candidates and separately-denominated repair, invalid-result and model-fallback rates;
- Discord typing status, terminal failure notices and logical 1800-character message chunks;
- code-rendered Discord formats adapted from the legacy localized templates;
- manifest-bounded recent domain-event and chat-message history in every LLM context;
- an offline OpenHands SDK contract check in unit tests, `masterclaw doctor`, CI and the built image;
- a cross-platform `uv.lock` with the OpenHands 1.33.0 compatibility set pinned;
- pytest coverage reporting in CI (84% across the 643-test final local suite on 2026-07-23);
- model-role benchmark with corrected equipment-present/equipment-absent action scenarios and
  independent comparison of prompt JSON against native tool calling;
- audited eight-model live matrix: 88 scenarios, two output transports, $0.063766 total provider
  telemetry cost, with raw output and routing analysis retained under `docs/benchmarks/`;
- fully localized game-bound deterministic UI for Russian and English campaigns;
- two-stage world generation with a loose creative pitch followed by complete typed consistency
  structuring; semantic cross-reference failures participate in bounded repair and model fallback;
- same-scene chat-history isolation for shared multi-scene Discord channels;
- committed-roll consequence fallback that always delivers immutable mechanics;
- live narrator-rights definitions for all four configured levels and typed `gm_automatic`
  outcome authority;
- model-tokenizer context estimates and content-addressed prompt fingerprints in LLM telemetry;
- direct CLI and Discord ingress/outbox adapter tests;
- reproducible model evaluation with production/fixed configuration modes, manifest-bound history,
  repeated attempts, scenario stability and repair-rate reporting;
- a combined 22-scenario role matrix covering all typed pipelines, including adversarial multiplayer,
  narrator-rights, continuity, world-generation and character-creation cases;
- a dedicated four-scenario worldgen quality suite and six-model candidate matrix, separated from
  frequent play-role evaluation;
- 643 passing tests covering scenario routing, failure degradation, handler boundaries, lifecycle
  gates, crash/replay guards, staged worlds, prompt contracts and performance telemetry.

The daemon now contains tested vertical slices for world setup, character preparation,
automatic actions, roll-based actions, narrator-rights follow-up, progression and dual-channel output.
The repository implementation is feature-complete for the agreed v2 scope. Local acceptance on
2026-07-23 built the pinned Linux container, ran `doctor`, read-only SQL health checks, an isolated
online backup/restore cycle and the eight-contract OpenRouter model smoke with semantic post-checks.
Promotion to a live campaign still requires the private Discord staging path and target-host
operations listed below.

## Production deployment acceptance remaining

1. Build this exact revision on the target Linux host and run the unit/lint suite plus `doctor`.
2. Exercise a private Discord staging channel through setup, action, restart, outbox recovery and
   the documented send-success/mark-failure duplicate window.
3. Take and restore an online backup of the target deployment before enabling the production
   service.
4. Enable and inspect the systemd service and backup timer on the target host.
