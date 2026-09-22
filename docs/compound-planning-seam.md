# Compound planning seam (R8)

`CompoundPlanningCoordinator` separates preparation/decomposition from the existing compound
execution handler. Its frozen snapshot contains event/game identity, the exact original request,
continuation flag, detached context inputs and immutable assembled context. A prepared result
contains only original request text and canonical plan JSON; its accessor returns a detached
`CompoundPlayPlan`. Neither snapshot nor result contains a store, callback or service locator.

The legacy adapter has two deliberately separate phases:

- Synchronous `LegacyCompoundPlanningCapture` obtains the exact PLAY scenario projections,
  preserves the original/clarification request shape, then uses the shared detached capture and
  assembler. Enrichment order, actor/participant projections, session locale, two domain events,
  two chat messages and channel/player history filters are unchanged. Assembly remains **outside**
  the handler's `except PipelineValidationError`, including errors from injected assemblers.
- Async `LegacyCompoundPlanDecider` owns the unchanged `DecisionPipeline[CompoundPlayPlan]` and
  `run_checkpointed_decision` call. Task bytes (including the continuation suffix), key
  `compound_play`, output identity
  `masterclaw.pipelines.compound_play.CompoundPlayPlan:v1:be3c94493785178a`, payload and NULL input
  fingerprint are retained. Replay still assembles context as before but invokes no planning
  model. This slice does not silently add fingerprint binding or change existing checkpoint rows.

MessageApplication accepts an injected decider/coordinator, or the mutually exclusive temporary
legacy pipeline shim. CLI injects the legacy decider without coupling execution to a provider.
The handler's block beginning with the first plan-use/HELP gate is unchanged: clarification,
conditional confirmation, preflight, consequences, pending mutations, idempotency and all later
execution behavior remain there. The generative pipeline model, prompt and manifest are unchanged.

## Existing classifier evidence, no duplicate calls

Compound routing is already a scenario-bounded `compound_play` candidate in `state_dispatch.v1`.
There is **no** new classifier use case, configuration or call for compound planning. Existing
`classifier.state_dispatch` observations record `answers.command.choice` and `reference.command`;
look for `compound_play` on either side of that decision/reference pair. For aggregate evidence:

```bash
masterclaw classifier-calibration-report --use-case state_dispatch --scope play --since-hours 168 --format json
```

Inspect the command question's confusion matrix/buckets for predicted or reference
`compound_play`. This is routing evidence, not evidence that individual plan parts are correct.
The existing state-dispatch checkpoint still skips both routing baseline and routing shadow on
replay; the separate compound checkpoint skips generative planning. No duplicate provider cost or
new classifier authority is introduced. A future planning-policy change requires its own design,
calibration and checkpoint compatibility review rather than reusing routing labels as part-level
ground truth.
