# Performance telemetry and analysis

MasterClaw records one trace per incoming Discord message. Nested spans use stable structured
dimensions rather than prose log parsing:

- `trace_id`, `parent_span_id`, Discord `event_id` and `channel_id`, optional `game_id`;
- `stage`, `component`, `operation`, `status`, exact floating-point `duration_ms`;
- JSON attributes, sanitized error and UTC start time.

The current stage vocabulary covers ingress, inbox claim, message total, initial SQLite reads,
dispatch, context-history reads, context assembly, bounded pipeline totals, provider calls,
primary/fallback routes, validation, repair calls, domain readiness/advancement/roll gates, SQLite
write transactions, status-card rendering, outbox persistence and Discord delivery. Every persisted
span is also emitted as a single `stage_span` JSON log record. Telemetry persistence suppresses its
own instrumentation, so it cannot recursively create spans. Spans are buffered per trace and
inserted in one SQLite transaction when the trace closes, avoiding one telemetry write per stage.

`llm_calls` remains the token/cost authority and now carries the same trace, channel and event keys.
This makes model cost and latency filterable alongside non-model work. Provider retries are owned
by the adapter and each attempt has its own `llm.provider_attempt` span; explicit fallback and
schema-repair attempts also have their own spans.

## Console report

```bash
masterclaw performance-report --since-hours 24
masterclaw performance-report --group-by component --stage-prefix db. --since-hours 6
masterclaw performance-report --channel-id 123456789 --format json
masterclaw performance-report --game-id game_id --status error --format csv
```

Available grouping dimensions are `stage`, `component`, `operation` and `status`. Filters cover
time window, game, channel, stage prefix and success/error. The table and JSON formats include call
count, errors, total, average, p50, p95 and maximum duration; JSON also includes model token/cost
summaries and the slowest message traces. CSV emits the stage aggregate for external BI/notebook
analysis.

The report reads SQLite directly and never calls an LLM. Use `--database` to analyze a copied or
restored database without touching the live daemon.

## Weekly routing-quality review

Run the routing-quality report once a week against the live database, and retain its JSON output
with the other operational reports:

```bash
masterclaw routing-quality-report --since-hours 168 --min-count 2
masterclaw routing-quality-report --since-hours 168 --min-count 2 --format json
```

The lexicon section groups replay-safe observations by scenario, command and normalized phrase.
Each Discord event can contribute at most one observation, so an inbox or handler replay does not
inflate frequency. Candidates are limited to high-confidence read-only `SHOW_*` commands and
`ANSWER_PENDING`. Promotion remains a manual change: review meaning, locale and cross-scenario
collisions before adding an exact phrase to a scenario lexicon. The report never edits routing
rules automatically.

The quality section deliberately keeps three denominators separate:

- schema-repair rate is repair completions divided by bounded pipeline runs for the same typed
  output contract;
- invalid-result rate is bounded runs ending in `PipelineValidationError` divided by bounded runs
  for that contract; it does not claim that every caller caught and rendered the error;
- model-fallback rate is secondary-model completion attempts divided by primary-model completion
  attempts for that contract.

These are typed-output-contract aggregates because that is the stable operation identity already
persisted in performance spans. They must not be added together: a single pipeline run can use both
a secondary model and schema repair. Compare each rate with its own prior weekly window and
investigate abrupt increases before changing prompts or model assignments. The command reads
SQLite only after applying the normal schema migration and does not call a model.

## Semantic classifier shadow observations

When state dispatch shadow mode is enabled, `classifier.state_dispatch` spans persist a typed
observation in the existing `stage_spans.attributes_json` column. The generic semantic executor
uses `classifier.<use_case>` for other callers. No schema migration is needed.
The observation includes requested and resolved model, reported version/provider/request ID,
taxonomy/request fingerprint, full per-question probabilities and confidence, optional reference
answers/agreement, latency, and numeric token/cost metadata. Unknown versions remain null.
Generic observations identify `use_case` and optional `scope`; question results are under
`answers.<question_id>`. For state dispatch, the router's command is `reference.command` and the
scenario is `scope`. Previously stored flat observations are retained unchanged.
`StateDispatchDecisionService` owns the baseline/shadow sequence inside the original
`state_dispatch` checkpoint. Its normalized result keeps the baseline command, argument,
confidence and evidence, with replay/observation metadata outside the checkpoint payload.
Existing checkpoint output identities and the null input fingerprint remain unchanged;
replay executes neither model. The baseline LLM pipeline and generic classifier package no
longer import or wrap one another.
Free-form provider metadata and non-allowlisted usage fields are omitted; player messages,
arguments, history, player IDs and GM context are not included.

The observation's `outcome` is `eligible`, `uncertain`, `blocked`, or `error`; these are evaluation
signals and never authorize a command. Inspect `error_category` and `error_transient` to distinguish
configuration/authentication/request/response failures from rate-limit/server/timeout/network
failures. A handled shadow failure leaves the enclosing span status `ok` because routing succeeds
using the current router. Cancellation still propagates.

For calibration, query `attributes_json` on spans where `stage = 'classifier.state_dispatch'`.
The current performance report includes their latency, but its LLM cost summary and the routing
quality report do not yet aggregate classifier distributions or costs. The durable observations
are available for that separate reporting work; they are not lost when a model alias changes.

Jev reuses `MASTERCLAW_OPENROUTER_API_KEY`, optionally overridden by
`MASTERCLAW_CLASSIFIER_API_KEY`. Empty credentials fail during startup when shadow is enabled.
Its bounded HTTP pool is reused and closed after Discord's in-flight work is cancelled at shutdown.
Composition owns a provider-neutral resource bundle, and classifier concurrency is independently
configured by `MASTERCLAW_CLASSIFIER__MAX_CONCURRENCY`.

`MASTERCLAW_CLASSIFIER__STATE_DISPATCH__MODE`, `__THRESHOLD`, and `__TIMEOUT_SECONDS` select the
state-dispatch policy. The original flat `MASTERCLAW_CLASSIFIER__MODE`, `__THRESHOLD`, and
`__TIMEOUT_SECONDS` remain fallbacks for unspecified state-dispatch fields. `ADVANCEMENT` and
`ACTION` have separate policy structures defaulting to off; this change adds no application calls
for those use cases and no authority modes. The executor evaluates choice confidence plus the
selected probability, noul distance from uncertainty via its stronger polarity, and score confidence.
