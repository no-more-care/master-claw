# Advancement safety seam and shadow calibration (R4a/R4b)

`AdvancementCoordinator` checks existing eligibility and applies progression through
the unchanged `ProgressionService`. Its safety dependency is the application-level
`AdvancementSafetyDecider`, not a model pipeline. A dedicated snapshot factory
captures the existing projections and manifest-bounded history once into detached,
immutable canonical JSON. `LegacyAdvancementSafetyDecider` assembles that snapshot
and invokes the unchanged legacy prompt/pipeline; it never rereads semantic state.

Assessments separate stable `SafetyVerdict`/`SafetyReason` codes from legacy prose
in `display_detail` and evidence. Existing permit reasons and rejection text still
use the original prose. Provider/context/validation exceptions propagate unchanged.
The R4a seam adds no classifier authority or character revision binding.

## Checkpoint compatibility

The legacy adapter retains `pipeline_key="advancement_safety"`, the exact persisted
JSON payload, and the original output identity:
`masterclaw.app.advancement_coordinator.AdvancementAuthorizationCheckpoint:v1:28dbb06fd13c2bee`.
The model lives in the adapter now but explicitly retains its durable module identity.
No database migration or row rewrite occurs. Replay skips model invocation, while
context assembly still precedes checkpoint lookup, preserving existing exceptions.

`legacy_v1_fingerprint_projection(snapshot)` deliberately preserves the original
subset and hash algorithm: scope, scene revision, character ID, traits/aspects, and
request. History, locale, scene contents, character name/XP, and prompt contents are
still omitted. This is known semantic-identity debt, **not** a full-snapshot hash.
Keeping it is necessary for behavior-preserving replay of in-flight requests.

## R4b shadow calibration

`ShadowAdvancementSafetyDecider` returns the original legacy assessment unchanged.
It invokes `AdvancementSafetyClassifier` only after a fresh successful legacy decision,
including a fresh denial. Runtime-only `assessment.replayed` skips classification
for old and new checkpoint hits. This flag is not stored in the legacy payload.
Classifier errors, uncertain signals, and projection failures cannot change the legacy
result or prose. Cancellation still propagates. Off remains the default.

The adapter shares the existing classifier runtime/executor and resource lifecycle
with state dispatch. `advancement_safety.raise.v1` asks independent noul questions
`scene_safe_enough` and `downtime_available`. `advancement_safety.learn.v1` additionally
asks `learning_opportunity_supported`. English instructions support Russian/English
fiction. Questions do not ask for XP arithmetic or free-form provider reasoning.

Provider state is an allowlist of scene title, description and public facts (the fields
already shown by scene status), requested trait/aspect/learning justification, and
up to four same-scene `scene_patched` records' added/removed public facts. Other event
types, arbitrary state fields and event summaries, all chat, hidden/GM context, actor
character data, XP/levels, IDs, and legacy reason/evidence are excluded. Known IDs and
Discord mentions are redacted inside selected text. Each string is capped at 400
characters and each fact/aspect list at six strings. This is minimization, not semantic
secret detection: the application must keep its canonical public facts genuinely public.
No live world/GM context is loaded to supplement the snapshot.

`MASTERCLAW_CLASSIFIER__ADVANCEMENT__MODE=shadow` opts in. Advancement-specific
`ALLOW_THRESHOLD` (default 0.95) and `DENY_THRESHOLD` (default 0.05) must satisfy
deny < allow; neither the flat state threshold nor the inherited generic threshold
controls the advancement reduction. All required probabilities >= allow yield ALLOW;
any <= deny yields DENY, with stable priority unsafe, no_downtime, then
no_learning_opportunity. The middle region yields UNCERTAIN, not a provider failure.

The generic executor persists full noul distributions and provider/model/version,
request ID, usage/cost and timing through existing stage-span metadata. Its optional
generic reduction summary records `decision`, `decision_reason`, `decision_reference`,
the actual `decision_thresholds`,
and aggregate `agreement`; a legacy denial is NOT mislabeled as a false answer to
every independent question. No raw state or legacy prose enters these attributes.

## Remaining v2 boundary

Before using full semantic snapshot identity, define an explicit deployment and
checkpoint migration policy; silently changing the v1 hash would reject old rows.
Shadow must be calibrated on Russian/English fixtures before any future authority
proposal; no active/fallback mode is exposed. Neither persisted permit fields nor arithmetic
should gain character revision binding without a separate contract migration.
