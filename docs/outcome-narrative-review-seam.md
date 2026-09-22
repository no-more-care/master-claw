# Outcome narrative review seam (D1)

`OutcomeNarrativePipeline` is the application-owned `DecisionPipeline[NarrativeResult]` facade.
The sequence is deliberately unchanged in authority and provider calls:

1. `LegacyNarrativeDraftGenerator` invokes the existing narrator with the exact task and assembled
   source context, then extracts the immutable `roll_result` using the historical marker/JSON
   algorithm. Missing, malformed or non-object outcome context raises the same validation error.
2. `LegacyAlwaysReviewDecider` makes the baseline semantic assessment: REPAIR with stable reason
   `LEGACY_ALWAYS_REVIEW`. This is a local unconditional rule, not another model invocation.
3. `LegacyNarrativeTextEditor` builds the exact review manifest, projections and task. Context
   assembly errors retain their normalized error; any degradation stops both editors. Primary
   editor failure invokes the existing fallback once, with identical context/task. Failure of both
   produces `PipelineValidationError("narrative review is unavailable")`. Cancellation still
   propagates, and no raw draft is returned or published.

The frozen review snapshot holds task, immutable assembled source context, draft prose and detached
immutable-outcome JSON. `NarrativeReviewAssessment` contains only verdict and stable reason; it
cannot carry corrected prose or provider feedback. The editor owns publication text. The facade
always calls the editor, including for injected publish/uncertain assessments: this slice adds no
authority shortcut, classifier, shadow observation, asynchronous provider stage or independent
draft/editor checkpoint.

`NarrativeResult` and low-level narrator/editor factories and system prompts remain in
`pipelines/narrative.py`. The existing `ReviewedNarrativePipeline` constructor and reviewed factory
remain compatibility wrappers; local composition imports avoid a pipelines/application module
cycle. CLI explicitly composes the application ports and facade; MessageApplication accepts the
generic decision-pipeline protocol rather than a concrete reviewed implementation.

## Checkpoint and publication boundaries

Handlers retain their existing outer checkpoints without edits:

- Automatic: original inbox event ID and `outcome_narration:automatic`, with the exact existing
  declaration/post-effect fiction fingerprint.
- Roll: original confirmation event ID and `outcome_narration:roll:{roll_id}`, with the exact
  existing roll/declaration/fiction fingerprint.
- Output identity remains
  `masterclaw.pipelines.narrative.NarrativeResult:v1:4d85796a91601dec`; only final `narrative` is
  checkpointed. Compatible existing NULL-fingerprint callers/rows retain NULL semantics.

Replay skips draft, semantic decider and editor together. Stale fiction guards and secret-fragment
redaction remain handler-owned and can replace even a validated/checkpointed final narrative with
the original deterministic fallback. This seam does not publish the intermediate draft.

The v1 identity debt remains explicit: existing fingerprints bind their historical subset, not
complete assembled source/history, model aliases, review policy or all publication settings. NULL
identities are not silently upgraded. Any future semantic authority or exact-player-text strategy
requires separate calibration and a deliberate migration/deployment policy; adding a raw/editor
checkpoint or changing outer fingerprint meaning is outside D1.

## Raw-draft semantic shadow (D2)

The optional `OutcomeNarrativeObserver` runs **after** the authoritative legacy editor (including
fallback) succeeds, but sees the original raw snapshot, never the edited result. The facade returns
the exact editor result object regardless of shadow classification, uncertainty or ordinary errors.
Cancellation still propagates. Existing outer checkpoint replay skips every stage, and existing
post-pipeline stale guards cover any state changes during added shadow latency. Editor failure
never invokes the classifier. Default/off composition adds no observer or provider calls.

`outcome_narrative_review.v1` batches four independent noul questions, with true always compliant:
preserving the resolved outcome, using established facts, respecting actor/viewpoint/authority,
and using public knowledge. Any decisive false selects repair, in stable priority order:
`mechanics_contradiction`, `invented_or_contradicted_fact`, `viewpoint_or_authority_violation`,
`hidden_knowledge`. All p(true) >= the use-case allow threshold selects publish; intermediate
evidence selects uncertain. This is calibration only, not text publication authority.

The privacy allowlist includes sanitized raw draft, normalized automatic/success/failure and
authority, bounded hits/difficulty/declaration, actor and other-PC public names, and public scene
description/facts. It sends no dice, IDs/mentions, raw source/history/chat, sheet/biography, prompts,
GM/secret context, provider errors or edited prose. Canonical nested IDs are harvested locally from
structured inputs and redacted wherever echoed in selected text. Known private/GM fragments echoed
in selected text cause a skip; private source sections themselves are never sent. No consequence
summary is added because the snapshot provides no independent canonical-summary provenance.

Ambiguous/malformed/nonpublic projections use typed `projection_unavailable`; degraded or oversized source,
text or participant/fact bounds use `projection_truncated`. These make no provider call and remain
outside calibrated and agreement denominators. Bounds are 128k source characters locally, 4000 raw
draft, 1500 declaration, 1000 scene description, eight facts of 300 characters and twelve participant
roster entries with 200-character names. They are explicit coverage gaps, never silent truncation.

Nested `CLASSIFIER__OUTCOME_NARRATIVE_REVIEW` settings default off and have independent allow/deny
thresholds and timeout; shadow shares the existing executor/backend/resource lifecycle. Observations
retain model/cost/distributions and stable aggregate decisions, but have **no semantic reference**:
legacy always-review is editing policy, not a gold repair label, so agreement is null. Future active
publish behavior requires independently labeled samples and must retain editor fallback on every
uncertain/error case; no active mode or checkpoint migration is introduced here.
