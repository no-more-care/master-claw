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
