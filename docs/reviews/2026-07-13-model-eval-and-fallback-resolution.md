# Model evaluation and fallback review — resolution

This note records the changes applied for
[`2026-07-13-model-eval-and-fallback-plan.md`](2026-07-13-model-eval-and-fallback-plan.md).

## Closed now

- Benchmark runs use production role settings by default. Only the candidate model id changes;
  temperature, reasoning effort, limits, timeout and transport remain those loaded by `Settings`.
- `--config-mode fixed` provides an explicit temperature-zero comparison mode.
- Scenario `ContextHistory` is passed through the production `ContextAssembler` path.
- `--repeats N` defaults to one and records attempt numbers. Reports aggregate
  `passed/attempts`, stability and repair counts per model/transport/scenario.
- Context token estimation is constructed with the candidate model id, not the unrelated production
  model id.
- The matrix grew from 7 to 20 scenarios and now covers all eight `PipelineName` values:
  intent, advancement safety, action interpretation, consequence planning, outcome narration,
  player narration review, character creation and sectional world generation.
- New behavioural coverage includes actor/resource isolation, declarations controlling another PC,
  pending-message ambiguity, scene questions, all requested narrator-rights gates, recent-event
  continuity, adversarial difficulty and advancement claims, character concept adherence, world
  outline quality and rejection of public/secret contradictions.
- Declaration rules now explicitly forbid controlling another PC or borrowing their sheet/resources
  without deterministic help context.
- An offline benchmark test replaces live model dependencies and verifies repeated attempts, history
  propagation and stability aggregation without spending provider credits.

The review's monolithic `WorldDraft` example was adapted to the current four-stage world-generation
architecture. The benchmark measures outline generation and the final consistency critic; structural
identity and secret/public separation remain enforced by the staged service and its deterministic
validators.

## Deliberately deferred

Automatic primary-to-fallback escalation, confidence fields and escalation telemetry are not enabled
yet. The source review explicitly recommends designing those triggers after the new scenarios produce
repeated data. Benchmark rows now expose repair usage, which supplies one of those future signals.

The next paid evaluation should first run a strong reference model three times in production mode to
audit scenario stability, then compare candidate models on the same matrix. No paid benchmark was run
as part of this code change.
