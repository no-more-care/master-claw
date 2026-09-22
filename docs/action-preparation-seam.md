# Action preparation seam and capability shadow (R5a/R5b)

`ActionPreparationService` owns action-specific actor/scene reads, the unchanged
`ACTION_INTERPRETATION` manifest/projections/task, checkpoint invocation and actor,
scene, location and participant revision revalidation. It receives a narrow callable
for existing shared context enrichment/assembly; that common implementation is not
moved or duplicated. The service consumes the provider-neutral `DecisionPipeline`
port with the unchanged generative `ActionInterpretation` output.

`MessageApplication` constructs the service from the existing `action_pipeline`
argument (including the CLI path), or accepts an explicitly injected
`action_preparation` service. Supplying both is rejected. `PreparedAction` carries
the canonical interpretation and immutable `ActionPreparationSnapshot`: frozen
character state, revision guard and serialized scene whose accessor returns a copy.
No store, callback or service locator is exposed in the result.

Shared `FictionContextSnapshot` and `FictionContextChangedError` now live in
`app/fiction_context.py`; `handlers/types.py` re-exports the same objects for legacy
imports. `handlers/play.py` likewise re-exports the relocated `PreparedAction`.
The pure actor projection helper is shared with the existing context enrichment.

## Exact compatibility boundary

The key remains `action_interpretation`; persisted output identity is unchanged:
`masterclaw.pipelines.action.ActionInterpretation:v1:b27cbe6e9cfd9c13`.
Fingerprint contents are still stage, raw declaration, continuation context (or
empty mapping), replacing pending ID/revision, and the original fiction revision
mapping. History/context are intentionally NOT added to this legacy fingerprint.
Task wording, continuation JSON formatting, context enrichment/history limits,
checkpoint-before-revalidation ordering, and model-call counts remain unchanged.
Existing checkpoint replay still assembles context but does not call the model.

Handler response translations, exception mapping, and effect-first automatic-action
replay remain in the handler. Downstream rejection/clarification, automatic
consequences, pool validation, activity credits and pending-roll creation are unchanged.
Prepared compound actions reread and validate current canonical context without
assembling context or invoking a model again.

## R5b capability shadow

An optional application `ActionCapabilityObserver` runs only after a fresh authoritative
interpretation has been checkpointed and passed existing fiction revision guards.
A runtime pipeline invocation marker detects replay without changing the checkpoint
key, schema, fingerprint, payload, or lookup count. Checkpoint replay and prepared
compound-action reuse skip the observer. Off/default composition does not create it.
Handler branches and deterministic mechanics are untouched.

The immutable observer input contains the raw declaration, captured canonical actor
and scene, plus current participant names/roles. The classifier builds a separate
strict allowlist: declaration; actor name, trait/aspect names, flag texts, item
names/descriptions and public conditions; scene title/description/facts and participant
public names/roles. It sends no GM/hidden context, chat, unrelated history, biography,
provider prose, XP, levels, reserve, revisions, temporary bonus metadata, or internal
IDs. Recursive `id`/`*_id` harvesting covers nested lists and domain tuples; known IDs,
Discord mentions and snowflakes are redacted from selected text, not merely from keys.
Meaningful public names remain unless equal to an internal ID. As with advancement,
the application must keep canonical public facts genuinely public.

Projection bounds: declaration 3000 characters, scene description 1000; other strings
200; at most 8 traits with 4 aspects each, 6 flags/conditions, 8 items/facts and 12
participants. Missing support in a bounded projection cannot establish impossibility.

`action_capability.v1` batches three independent choice questions:

- feasibility: possible / impossible / underspecified;
- sheet_support: supported / unsupported / not_required / uncertain;
- authority_scope: own_character / other_pc_control / narrator_world_change / uncertain.

The sidecar never chooses a trait, pool, difficulty, outcome or narration. A confident
contradiction of fiction or authority yields observational `blocked`; all necessary
positive signals yield `capable`; otherwise `uncertain`. Sheet support is advisory:
an unsupported sheet alone yields uncertainty, never a prohibition. Pool membership,
ownership and arithmetic remain exclusively in existing deterministic mechanics.

`MASTERCLAW_CLASSIFIER__ACTION_CAPABILITY__MODE=shadow` opts in independently of the
reserved legacy `ACTION` settings. `CAPABLE_THRESHOLD` defaults to 0.95 and
`BLOCKED_THRESHOLD` to 0.98 (both 0.5–1); both winning probability and confidence must
meet the threshold. The generic flat/state threshold does not control this reduction.
There is no active, authority or classifier fallback mode.

The generic executor writes distributions, aggregate capability/reason/thresholds,
model/version/request ID, usage/cost and latency in the existing durable span. Baseline
roll/automatic maps only to `proceed`, rejected to `blocked`, clarification to `uncertain`.
`decision_comparison` maps capable to proceed for agreement; no invented per-question
baseline answers or raw state are recorded. One shared executor/backend/resource bundle
serves state, advancement and action capability observers.

Observer failure or uncertainty leaves `PreparedAction` unchanged. Cancellation still
propagates. Because shadow adds an await, the service repeats the same revision guard
after an executed observer: an actual concurrent canonical change returns the existing
stale-context error, independently of classifier output. This extra read/guard does not
run when off, on replay or on prepared reuse.

Full semantic fingerprints and any future authority proposal require separate calibration
and migration policy; R5b deliberately retains legacy checkpoint identity and mechanics.
