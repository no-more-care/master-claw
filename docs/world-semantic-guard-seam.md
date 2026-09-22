# World semantic guard (W1)

`WorldGenerationService` retains generation and structural validation authority. Its
injected synchronous `WorldSemanticGuard` defaults to `LegacyWorldSemanticGuard`.
This is an extraction of the historical deterministic rules, not a new classifier,
provider request, or change to world acceptance policy.

Validation order remains: schema and references, playable sheets, confirmed settings,
requested pregen count and themes, semantic guard (distinct requested concepts,
content boundaries, setting adherence), locale/script checks, then secret-leak checks.
Primary structuring and all validation still share the same fallback boundary. The
fallback is validated once and any failure propagates. Cancellation is not converted
to a fallback. The static `_validate_draft` entry point still supplies the legacy guard.

`WorldSemanticSnapshot` stores detached immutable JSON copies of confirmed settings,
the exact draft serialization, and separately retained setting-adherence metadata
(which the pipeline model excludes from normal serialization). It is a local-only
input, **not an external-provider projection**. In particular, legacy content-boundary
checks still examine private draft text. Secret detection and the public/private
boundary remain in the service; the snapshot must never be sent directly to a model
or telemetry sink.

Normalization, stemming, concept assignment, boundary-negation rules, adherence
order, and rejection messages are unchanged. Guard failures use the dependency-neutral
`WorldSemanticGuardError`; the service translates them to the original
`masterclaw.app.worldgen_service.WorldGenerationError` with the same message and no
displayed exception chain. Pipeline models, schemas, prompts, manifests, creative and
structuring context/task bytes, and provider call ordering are unchanged.

## W2: post-commit public-only shadow

`WorldSemanticObserver` is optional and runs only when `commit_world_generation`
returns `True`. Committed replay and concurrent losers neither project nor observe.
The observer receives a separate immutable `PublicWorldSemanticSnapshot` built from
the accepted committed content/settings, never the legacy private snapshot. Exceptions
are best-effort and leave the committed draft and success response unchanged.
`CancelledError` propagates conventionally; cancellation or a crash after commit can
lose the observation, and replay intentionally does not recreate it.

`MASTERCLAW_CLASSIFIER__WORLDGEN_SEMANTICS__MODE` defaults to `off`; `shadow` uses
the existing shared runtime/executor with independent allow/deny/timeout settings.
Off composition creates no observer, projection or classifier call.

The `worldgen_semantics.v1` request batches `settings_realized` (only present public
genre/tone/scale/player-role settings) and, when requested concepts exist,
`concepts_realized_by_distinct_templates`. True means compliant. There is **no remote
content-boundaries question**. Public world premise, themes, location names/descriptions,
factions, tensions, and bounded public pregen names/roles/concepts/hooks/biographies are
allowlisted. Internal identifiers and mentions are redacted recursively. Private
content, boundary text, adherence self-claims, raw creative drafts, sheets, histories,
and provider metadata are excluded. Locally detected private echoes, unsafe records,
absent semantic requirements and exceeded bounds produce typed skipped observations
with no call; there is no silent truncation. Boundary adherence remains local authority.

The telemetry-only reducer is `review` on any decisive false, prioritizing settings
then distinct concepts; `pass` requires every answer at or above the allow threshold;
otherwise `uncertain`. No result changes acceptance or regenerated prose. Legacy
acceptance is positive-branch policy, not semantic gold, so no reference/agreement is
fabricated. See performance telemetry guidance for calibration limits.
