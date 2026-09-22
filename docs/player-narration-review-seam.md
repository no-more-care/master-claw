# Player narration review seam (R6a)

`NarrationReviewSnapshot` captures detached enriched inputs, bounded domain/channel history,
immutable fiction revisions, roll authority and submitted narration. Shared context capture keeps
the original enrichment order and limits; other handlers retain `_assemble_context`.

The handler calls `NarrationRightsDecider` and `NarrationTextPort`. CLI composes one
`LegacyPlayerNarrationReview` for both. A mutually exclusive legacy pipeline constructor argument
remains available to existing callers. Assessment contains stable legacy accept/reject codes,
never provider prose. Text materialization rereads the durable checkpoint, so worker restart and
repeated materialization cannot call the model. `replayed` is runtime-only assessment metadata.

The unchanged `PlayerNarrationReview` model remains the checkpoint payload at
`player_narration_review`; its schema identity is
`masterclaw.pipelines.player_narration.PlayerNarrationReview:v1:278f9b09fd5b4a2b`.
Prompt, manifest, task, repair behavior and publication/scale-back prose remain unchanged.
Revision checks, secret guards, consequence causation/metadata and commit replay stay in the handler.

`legacy_v1_fingerprint_projection` deliberately preserves the old subset: submitted text,
pending/source IDs and revisions, roll ID and fiction identity. It omits enriched scene/actor/world
projections, history, rights settings, roll values, original declaration and pending prompt.
These omissions are not fixed by the extraction: a full snapshot hash requires a separately
versioned deployment/migration policy, avoiding duplicate decisions or broken existing replay.

Future narrator-rights shadow work can consume this seam without changing text materialization,
but needs a separately privacy-reviewed projection and calibration policy. There is no classifier,
new authority mode, consequence planning change or persistent narrator policy in R6a.

## R6b: optional narrator-rights shadow

`player_narration_rights` has independent off/shadow configuration (default off), allow/deny
thresholds and timeout. CLI wraps the legacy decider only when enabled and shares the existing
classifier runtime/executor; the legacy adapter still supplies the text port. A fresh ALLOW or
DENY triggers one batched request after the authoritative checkpoint. Replay, baseline failure
or baseline uncertainty skips it. Provider failures and uncertain signals cannot alter the
assessment, publication text or downstream branches; cancellation propagates normally.
The unchanged post-review stale guard also covers canonical changes during the added await.

Taxonomy `player_narration_rights.v1` contains four independent noul propositions:
`preserves_resolved_outcome`, `within_rights_scope`, `actor_only`, `publicly_supported`.
True always means compliant. Any p(true) at/below deny threshold wins in that order, with stable
reasons `outcome_contradiction`, `rights_exceeded`, `other_pc_control`,
`unsupported_or_hidden_fact`; all at/above allow threshold means ALLOW; otherwise UNCERTAIN.
Only the aggregate is compared to legacy ALLOW/DENY, not fabricated per-question gold labels.

The outbound allowlist includes bounded submitted narration and canonical original declaration,
success/failure (no dice/hits/difficulty), immutable authority and rights level, actor/other-PC
public names and explicit roles, public scene title/description/facts, public NPC name/state,
and up to three canonical same-scene public fact deltas. Recursive canonical `id`/`*_id`
harvesting redacts their echoes from every outbound string. Raw history/chat, full sheets,
biography, revisions, IDs, GM/hidden metadata and legacy generated/feedback prose are excluded.
Missing bounded context warrants uncertainty; absence alone is not evidence of hidden knowledge.
Generic durable spans contain model/version/cost, four distributions, thresholds, aggregate,
reference/agreement and sanitized errors, never raw request state.

This does not introduce an authoritative classifier path or a v2 fingerprint. Any future
authoritative deployment requires a separate text materializer or an explicitly approved
exact-player-text publication policy with safety checks. Jev must never generate narration,
approved prose, scale-back feedback or consequence plans. Calibration and a separately versioned
fingerprint migration remain prerequisites, not behavior enabled by this shadow.
