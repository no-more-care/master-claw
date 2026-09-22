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

Classifier calibration is available through the read-only report below. The performance report's
LLM cost summary and the routing-quality report remain separate; do not add their model-call costs
to classifier span costs as though these were disjoint billing sources.

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

## Weekly classifier calibration report

```bash
masterclaw classifier-calibration-report --database data/masterclaw.sqlite3 --since-hours 168
masterclaw classifier-calibration-report --since-hours 168 --use-case state_dispatch --scope play --format json
masterclaw classifier-calibration-report --since-hours 168 --use-case player_narration_rights --limit 50 --format json
```

This command opens an **existing** SQLite file with `mode=ro`; it never calls `initialize`, applies
migrations, creates a missing database, loads application settings, constructs a provider, or
writes gameplay/telemetry. An empty SQLite database or one without `stage_spans` produces an empty
report. Only `stage LIKE 'classifier.%'` rows within the UTC time window are read. A small query
port isolates SQLite details from aggregation/rendering. JSON is key-sorted and table order is
deterministic; no timestamps identifying individual observations are emitted.

New executor spans explicitly serialize `observation_schema_version: "v1"`. The report requires
this field in the raw attributes, not a Pydantic-inferred default. Rows without it are `legacy`,
including previously stored generic-shaped observations; unsupported explicit versions are
`unknown_version`. Invalid JSON/current-v1 contracts, inconsistent primitive distributions,
unsafe metadata, unversioned taxonomy names and stage/use-case mismatches are `malformed`.
These categories are separate counts and never enter calibrated rates, latency or usage totals.
Any syntactically versioned taxonomy is accepted with observation schema v1, including generic
score use cases; the reporter does not import application adapters or whitelist production tasks.
The model/provider's reported `version` is a grouping label, not the observation schema version.
No old rows are rewritten or silently promoted by this command.

Groups separate use case, scope, mode, taxonomy, requested/resolved model, provider/upstream
provider and resolved version. `--limit` selects groups by descending count, then these dimensions
lexically; all-window summary totals are computed **before** group limiting. Omitted group count
is explicit. `--use-case`/`--scope` are exact matches against raw metadata. Parseable rows outside
those filters are `filtered_out`; unparseable rows cannot be assigned a scope and are counted as
malformed within the requested time window. Thus scanned = filtered-out + valid + legacy +
malformed + unknown-version + skipped. Group question statistics are never mixed across taxonomy
scales. Explicit current-v1 `SkippedClassifierObservation` spans have `outcome=skipped` and a typed
reason (`projection_truncated` or `no_candidates`), not empty answer distributions. They are counted
separately by use case/scope/taxonomy/requested model/reason and excluded from **all** calibrated
outcome, agreement, latency, token and cost denominators. Unknown reasons or extra unsafe fields are
malformed. `--limit` also bounds skipped groups by descending count then lexical dimensions;
`omitted_skipped_groups` reports omissions, while `rows.skipped` includes every matching skip.
Inspect these skips in the weekly workflow before interpreting recovery coverage; a projection
bound is missing evidence, not a negative classification.

Metric denominators:

- Outcome rates (`eligible`, `uncertain`, `blocked`, `error`, `off`) divide by all valid matching
  observations. A handled classifier error counts as an error even when the outer span is `ok`.
  Error category and transient true/false/null are reported separately; null means unreported.
- Agreement ignores the stored `agreement` boolean and is recomputed. A successful row contributes
  once when it has both aggregate decision and explicit decision reference; comparison uses
  `decision_comparison` when present (e.g. capable→proceed), otherwise `decision`. Without an
  aggregate pair, a row contributes once if any explicit question reference is comparable, and
  agrees only if every comparable question agrees. Missing references, references to absent
  questions, wrong-type/out-of-range references, error/off rows are excluded, not disagreements.
  The denominator is `comparable_rows`; zero gives null, never a fabricated zero accuracy.
- Per-question matrices/buckets use only that question's explicit comparable reference. Aggregate
  ALLOW/DENY is never treated as four noul gold labels. Choice compares declared labels; noul uses
  p(true) >= .5 against a boolean; score compares numeric values with absolute tolerance 1e-6
  (and Python's default relative tolerance). Score references must lie within the recorded scale.
- Latency p50/p95 uses nearest rank (ceil(p*n)) over valid finite nonnegative observation
  `latency_ms`, including error/off rows; no samples gives null. Raw enclosing span duration is
  not substituted. Choice selected-probability and confidence buckets are separate; noul reports
  p(true) buckets only, never treating yes-probability as confidence. Score reports confidence
  buckets plus score min/max/mean on its own scale. Buckets are [0,.5), [.5,.8), [.8,.9),
  [.9,.95), [.95,1], with counts and explicit-reference denominators.
- Cost is counted once per valid span: observation `cost` wins, otherwise numeric `usage.cost`.
  Nested cost details are never added. Unknown/nonfinite/negative usage costs are missing, not zero
  samples; `observed_rows`/`missing_rows` expose coverage. Tokens use `input_tokens` before
  `prompt_tokens`, `output_tokens` before `completion_tokens`; `total_tokens` wins, otherwise total
  is derived only when both input and output are known. Only finite nonnegative integral values
  count. Cached/reasoning detail counters are subsets and are not added again. Token sample counts
  accompany each sum. Usage is never multiplied by question count, joined to `llm_calls`, or
  deduplicated using request keys: two actual invocations of identical input remain two costs.
  A cost sum exceeding floating-point range is null (with coverage retained), never Infinity or
  a report crash. Such anomalous metadata is not usable billing evidence.

Output allowlists aggregate dimensions, stable labels and numeric metrics. It never emits
request/request-key, trace/event/game/channel/player IDs, raw state/history/messages/context,
provider error bodies, span operations, or arbitrary usage metadata. Validation failures are
counted without rendering their values or exception messages.

Weekly: save JSON for the same window/filter set, inspect exclusion and missing-reference/cost
coverage first, compare model-alias resolution and error/latency/cost changes, then compare
per-taxonomy distributions and explicit-reference disagreement cases. Use independently reviewed
examples before changing thresholds. This report is calibration evidence, **not automatic
threshold promotion**, a safety proof, an authority mode or permission to publish classifier prose.
