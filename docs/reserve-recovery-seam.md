# Reserve recovery seam (R7a)

`ReserveRecoveryCoordinator` owns the existing best-effort recovery sequence. After the unchanged
game-exists guard, its first decision lookup is the custom `reserve_recovery_decision` event at
`reserve-decision:{outcome_causation_id}`. Replay never captures context/history or calls a model,
even if no decider is configured. Fresh work captures a detached `ReserveRecoverySnapshot`, calls
`ReserveRecoveryDecider`, filters eligibility using the original game mode, writes the same custom
first-writer-wins payload, and applies only the canonical returned targets through `GameService`.

`SafeRestEligibility` and `RoleplayEligibility` carry stable verdict/reason codes, with historical
provider prose kept separately in `display_detail`. They contain no amount, delta or reserve cap.
`LegacyReserveRecoveryDecider` retains the existing prompt, manifest and generative pipeline.
`LegacyReserveRecoverySnapshotCapture` reuses shared context capture: current player-scoped scene
with the supplied scene as fallback, participant enrichment, actor and reserve projections, resolved
outcome and mode, four domain events and two chat messages, the original optional player filter
and no channel filter. Legacy reserve quantities remain in the opaque context input bundle only;
semantic assessment does not calculate amounts.

MessageApplication composes the coordinator with the shared context-capture dependency and the
injected decider. CLI constructs the legacy decider. Direct coordinator injection is also supported;
coordinator, decider and historical pipeline arguments are mutually exclusive. The existing handler
method and all callers retain their signatures and delegate orchestration. Coordinator/contracts
have no direct context/pipeline/provider imports.

Compatibility boundaries retained deliberately:

- Pipeline model remains physically unchanged. Its schema hash suffix is `17dfdac2f5118909` for
  regression checks, **not** a durable checkpoint identity. The custom event has no pipeline output
  schema binding, input fingerprint, context revision binding or version migration. R7a adds none.
- Custom payload remains exactly `game_id`, `safe_rest_completed`, `safe_rest_reason`, `awards`
  (`player_id`, `reason`). It does not store typed assessment or eligibility enums. In particular,
  mode filtering may make safe rest false while retaining its provider reason; this historical
  oddity is preserved.
- Safe rest restores all characters to their existing maxima; roleplay restores exactly one,
  capped at maximum. Store validation owns unique/existing award targets. GameService rechecks
  the current mode on application. A failed rest stops the remaining awards in the same unchanged
  best-effort exception boundary.
- Exact causation keys stay `reserve-rest:{outcome}` and `reserve-award:{outcome}:{player}`. A retry
  after partial application reuses chosen targets; already-applied operations do not award again.
  Event prose is preserved. Cancellation is not swallowed by `except Exception`.

## Reserve eligibility shadow (R7b)

`ReserveRecoveryObserver` is optional and observes the **stored canonical assessment**, never the
raw provider result. The coordinator still filters with the captured legacy mode, then commits
the exact custom payload. A companion store API returns atomic insert ownership alongside the
unchanged payload. Only the invocation that actually inserted runs the observer; an existing
checkpoint or concurrent first-writer loser skips it. The original store API still returns just
the payload. Observation happens before the unchanged idempotent GameService effects.

This is intentionally lossy telemetry: a crash after checkpoint commit may lose its observation;
replay must not manufacture a second model call. Provider errors, uncertainty and ordinary observer
exceptions cannot prevent effects. Cancellation continues to propagate, and the committed targets
remain available for effect replay. No checkpoint schema/fingerprint/version improvement is made.

`reserve_recovery.v1` batches mode-dependent noul questions: `safe_rest_completed` where permitted,
and `strong_roleplay.candidate_N` for each award candidate. Local mapping retains player identifiers;
the provider receives only opaque candidate labels, sanitized public names, actor/participant flags,
bounded outcome text/evidence and public scene description/facts/threats. IDs (including nested
canonical `id` keys echoed in text), mentions, reserve quantities, private sheet fields, raw history,
GM context and recovery-provider reasons are excluded. Public names matching an internal identifier
are redacted rather than leaking that identifier. No full-history evidence is sent.

Own nested `CLASSIFIER__RESERVE_RECOVERY` configuration defaults to off. Shadow shares the existing
runtime/executor/pool and cannot become authoritative. Per-question allow is p(true) >= allow
threshold; deny is p(true) <= deny threshold; otherwise uncertain. Aggregate is uncertain if any
question is uncertain, otherwise recovery if any qualifies, otherwise none. Exact canonical
checkpoint booleans/award membership provide per-question references. Agreement compares the
predicted binary polarity at .5 with those references (not thresholded eligibility), matching the
generic calibration reporter; distributions and allow/deny thresholds remain recorded separately.

The default candidate bound is eight (configurable 1–31). A larger eligible roster skips the entire
provider call with typed `projection_truncated` telemetry; it is never silently truncated or treated
as denial. An award-only empty roster similarly records `no_candidates`. Both are explicit skipped
non-evaluations, excluded from calibrated rates and agreement. Safe-rest-only mode needs no roster.
Calibration evidence cannot authorize arithmetic, alter recovery mode or resolve the custom-v1
checkpoint debt; those require separate design and review.
