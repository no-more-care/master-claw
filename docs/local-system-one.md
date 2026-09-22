# Local System One HTTP sidecar

MasterClaw can use an **externally supervised and prewarmed** System One service.
This adapter never imports ML runtimes, downloads weights, starts a server, loads a
model or starts subprocesses. Kev should initially run at concurrency **1**; additional
concurrency needs server-specific measurement. A Laya deployment may be implemented
as a separately supervised sidecar later; this slice only accepts its compatible
typed wire answers (discarding the allowlisted per-answer `rl_agent` metadata).

## Configuration

Set `MASTERCLAW_CLASSIFIER__PROVIDER=system_one_http`, then opt individual existing
use cases into `shadow`. All modes remain off/shadow; there is no authority mode.
The nested `MASTERCLAW_CLASSIFIER__SYSTEM_ONE_HTTP__` settings are:

- `ENDPOINT`: defaults to `http://127.0.0.1:8081/v1/systemone`.
- `MODEL`: stable served alias, default `local/system-one`; not a filesystem path.
- `EXPECTED_REVISION`: optional pinned deployment/model revision. Pin it for repeatable
  calibration. Catalog precedence is first non-null `revision`, then `version`, then
  official Kev's `run`. Comparison is exact; `base` is never interpreted as a revision.
  This setting is a SecretStr because a direct Kev run can contain a private path.
  Changing weights behind an unchanged tag requires restarting/rechecking the runtime.
- `MAX_CONCURRENCY`: defaults to 1, independent of Jev's existing concurrency default.
- `ALLOW_NON_LOOPBACK`: defaults false. Only loopback IP addresses or localhost are
  accepted otherwise. Non-loopback requires **both** explicit opt-in **and HTTPS**;
  remote HTTP is rejected before client creation, even with opt-in and a token.
- `TOKEN`: optional dedicated secret; `AUTH_HEADER` is `Authorization` (Bearer) or
  `X-API-Key`. Local auth never inherits the OpenRouter secret. Do not put tokens in URLs.

No client/backend is constructed when every use case is off. The adapter shares the
existing executor, resource shutdown and observation pipeline. It owns one lazy client
with environment proxies and redirects disabled. An injected transport is borrowed,
never closed by the adapter. No arbitrary external client can bypass those invariants.

## Sidecar protocol

`POST /v1/systemone` accepts `{model, state, questions}` with the existing independent
choice/noul/score contracts. Before first inference a bounded catalog check resolves
the configured alias against each entry's `id` or `aliases`. Ambiguous matches fail
closed. A successful catalog verification is cached once per adapter, including under
concurrent callers. The success envelope's `model` must equal the configured alias or
its verified canonical ID; observation metadata uses the safe canonical ID.
Choice has `choice`, full `probabilities`, and
`confidence`; score has numeric `score`, string-indexed `legend` and `probabilities`,
and `confidence`; noul has only `type` and `noul`. Noul distributions are synthesized
as true/false probabilities, not confidence. Missing/extra questions, unknown answer
fields (other than local `rl_agent`), label/legend mismatch, invalid probabilities and
inconsistent distributions are rejected. Jev keeps its original stricter answer field
policy. This is an HTTP integration contract, not an installed Kev/Laya server.

Official Kev inference has no version/revision, and none is required. Deployment pins
are checked against the catalog **before** inference, never against the response.
Only safe explicit catalog `revision`/`version` tags may enter version telemetry;
the raw `run` fallback and `base` values are never printed, cached or persisted.
No local price is invented: cost stays null unless a finite nonnegative billed `cost`
or numeric `usage.cost` is explicitly supplied. Only numeric usage is retained. Sidecar
provider strings, request IDs and private paths are not propagated; provider metadata
is `local_system_one`, with the verified canonical ID and safe explicit revision if present.

`GET /v1/models` accepts official Kev's `{models: [{id, aliases, run, base, ...}]}`
and optional OpenAI-style `{data: [{id, aliases?, revision?, version?}]}` custom catalogs.
The official shape is grounded in [kev.serve models() and Server.answer()](https://github.com/jaredpalmer/kev/blob/main/kev/serve.py).
For direct Kev, configure `MODEL=kev-latest` or its `jev-latest` alias and the actual
externally supervised port (the upstream CLI defaults to 8008). Model listing must
remain non-inferencing. Neither raw aliases lists, run/base paths, catalog bodies nor
errors are printed. Doctor shows only safe configured/resolved aliases and a boolean
indicating whether the requested deployment pin matched.

A catalog tag is a **best-available deployment assertion, not cryptographic weight
attestation**. A mutable run path/Hugging Face ref or a misconfigured server can reuse
the same tag for different weights. Operators must pin immutable artifacts externally;
restart the MasterClaw runtime after redeployment to invalidate its catalog cache.
Doctor uses a new adapter and therefore performs a fresh check. No expected-base
assertion is introduced; base is informational-only and excluded from output.

## Doctor and calibration

Run `masterclaw classifier-doctor` to validate config/access and query the catalog
with a short timeout. It needs no Discord/OpenRouter credentials or database. It does
**not request inference** by default. `masterclaw classifier-doctor --smoke` additionally
sends one fixed synthetic RU/EN request with one question of each primitive and checks
the normalized response contract. Smoke is not an accuracy evaluation and sends no
gameplay state. Failures give sanitized operational guidance, never start/download a
replacement model and never retry against OpenRouter. The general doctor is unchanged.

Calibrate Russian shadow behavior against independently reviewed samples before using
any thresholds for decisions. Compare taxonomy, actual model alias/revision, latency,
distribution and missing/error coverage in `classifier-calibration-report`. Local
secret-sensitive use cases must **never fallback to remote providers**; local failure
remains an isolated shadow error. This adapter does not broaden existing use-case
privacy projections or authorize sending private context.
