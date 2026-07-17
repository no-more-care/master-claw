# LLM pipeline contract

## Request layout

Every bounded pipeline sends three ordered messages and one output contract:

1. A stable `system` message containing the role contract, pipeline invariants and the
   manifest-selected rule fragments. The complete block is marked for prompt caching.
2. A dynamic `user` message containing only state projections and bounded recent history.
3. A final `user` message containing the current task and, when applicable, the fresh player
   message. Keeping it last prevents old context from obscuring the immediate request.
4. A typed output contract selected by the configured transport.

Rule fragments are stable and must never be mixed with state or chat history. State projections,
history and the current task are not cacheable. The order of system sections and tool definitions
is deterministic so provider prefix caches can reuse them.

Input budgets use LiteLLM's tokenizer for the model configured for the manifest role. If a provider
model has no known tokenizer, assembly falls back to a conservative UTF-8 byte estimate. Every call
stores a content-addressed prompt fingerprint covering system rules, strict schema, tool name and
transport. The manifest output budget is passed to the provider on both the initial and repair
attempt and is clamped to the configured model ceiling. The effective per-call output limit is also
part of the fingerprint; explicit prompt-fragment version fields are intentionally not used at this
stage.

## Output transports

`native_tool` exposes one forced terminal function such as `submit_action_interpretation`.
Its named parameters are generated from the pipeline Pydantic model as strict JSON Schema.
The function is not executable and cannot mutate MasterClaw state: its arguments are merely the
model's typed proposal. Exactly one matching call is required; text-only, missing, wrong or multiple
tool calls fail validation and receive at most one repair attempt.

`prompt_json` is the explicit alternate transport. It supplies the same strict schema in a
final prompt block and parses the returned JSON. It is used only for models/provider paths that do
not reliably preserve native tool calls.

Transport is configured independently for every model role with
`MASTERCLAW_<ROLE>_MODEL__OUTPUT_TRANSPORT`. Capability is verified by model eval; marketing claims
about tool use are not sufficient. The current defaults are:

| Role | Model | Transport | Reasoning effort |
|---|---|---|---|
| `state` | `nex-agi/nex-n2-mini` | `prompt_json` | `low` |
| `reasoning` | `nex-agi/nex-n2-pro` | `prompt_json` | `medium` |
| `narrative` | `openai/gpt-5.6-luna` | `native_tool` | `low` |
| `worldgen` | `openai/gpt-5.6-luna` (interim) | `prompt_json` | `medium` |

The Nex Mini provider returned an empty native-tool response through the pinned
OpenRouter/LiteLLM/OpenHands path on 2026-07-13, so production does not assume one transport works
for every model.

The 2026-07-13 transport benchmark confirmed this capability split. Luna passed every audited case
with either transport; Aion and both Nex models were more reliable with `prompt_json`; Kimi passed
both narrative cases with either transport. See
`benchmarks/2026-07-13-model-role-transport-analysis.md` for scores, cost and the evaluator audit.

## Validation and state safety

The LLM never receives database, shell or file-write tools. A terminal output call follows this
path:

```text
LLM tool arguments
  -> Pydantic output validation
  -> pipeline-specific semantic gates
  -> deterministic domain service
  -> revision/idempotency checks
  -> SQLite transaction
```

World/module preparation uses two separate ownership stages. The creative route (currently
DeepSeek with Aion 3.0 fallback) returns one deliberately loose plot pitch. The reasoning route
(Luna with Gemini fallback) receives that pitch plus the original brief, checks consistency and
alone emits the complete typed `WorldDraft`. The creative route cannot publish or mutate world
state.

Outcome prose follows the same draft/editor boundary. The configured narrator writes a typed raw
narrative, then Luna (Gemini fallback) conservatively checks it against the immutable source
context. The editor may repair prose and contradictions but cannot recalculate dice, success,
narrator rights or state transitions. If both editors are unavailable, the already schema-valid raw
prose is retained.

Dice, reserve, XP, narrator rights, lifecycle transitions and commits remain code-owned. A failed
transport, schema or semantic gate cannot write canonical state. The single repair call receives
the original dynamic context, validation error, schema and prior candidate.

## Conversational ingress

Free language is the primary Discord interface. A small state-role classifier selects a bounded
intent after deterministic mode and pending-interaction lookup. Dedicated typed intake pipelines
then extract world briefs, character requests, preparation settings, advancement requests and roll
confirmations. For example, “добавлю два куба” becomes a typed confirmation with
`reserve_spent: 2`; only deterministic mechanics may validate the amount, spend reserve and roll.
Questions and harmless roleplay have read-only bounded pipelines, while action declarations enter
the existing proposal/confirmation path. Slash syntax remains an optional shortcut and is never
required by a conversational response.

Common read-only requests bypass that classifier entirely. A conservative normalized synonym map
handles world catalogues, game/character/XP status and current-scene/participant descriptions;
bounded Levenshtein matching accepts spelling mistakes but rejects long action declarations. The
result is rendered from canonical SQLite projections with zero model calls.

Natural world setup has two explicit approval gates. The first message only extracts and persists
inputs. The typed intake contract covers world/plot fields, narrative style/perspective/detail,
locale, narrator-rights, reserve recovery, progression and pregenerated-character count/concepts.
Code fills every omitted value and labels its provenance in the status card; enum options and
free-text fields remain visible so players know what can be changed. Creative and structuring models
are called only after players allow draft generation. Later details merge into the cumulative brief
and regenerate the review draft. A second, unmistakable world approval adds it to the reusable catalogue. A separate explicit
title or ordinal selection is required before code binds a game, creates its opening scene and
enters preparation. Praise, silence and unrelated messages never count as either approval. All
mode/status cards are code-rendered from canonical projections and add no LLM input or output
tokens.

Reserve recovery has a dedicated system game-master decision contract; it is not a Discord command
and there is no human-GM identity. The reasoning model sees the configured recovery mode, canonical
resolved outcome, current scene and character reserves. It may report completed safe rest or award
one die for evidenced roleplay. Deterministic code checks the configured mode, caps the reserve and
writes an idempotent audit event. A player's request to recover dice is explicitly non-authoritative.

An immutable roll is a special delivery boundary: if consequence planning fails after the roll has
already committed, the mechanical result is still returned immediately and no scene patch or prose
is published. Other pipeline failures occur before their corresponding state commit and remain
fail-closed/retryable.

## Benchmark policy

`masterclaw model-benchmark` compares `prompt_json` and `native_tool` separately. `--suite core`
contains frequent play pipelines; `--suite worldgen` preserves the historical transport comparison
scenarios used to choose the models for the new two-stage generation
workflow and its dedicated candidate list. Scenarios that
depend on equipment must explicitly include or exclude the equipment. In particular, lockpicking
with a lockpick set must propose a roll, while the same declaration with no available tools must
request clarification.

The default `--config-mode production` replaces only the model id and preserves each role's live
temperature, output limit, timeout, reasoning effort and configured transport. The opt-in
`--config-mode fixed` mode is for controlled model-to-model comparisons and uses deterministic
temperature-zero settings. Reports record the mode and effective role parameters.

Production reasoning and narrative-review routes have a 6000-token output ceiling. Narrow typed
contracts should normally finish far below it; the ceiling exists so genuinely complex structured
answers and reasoning tokens are not truncated. A typed-validation failure that has already used
the pipeline's one repair is terminal for that inbox attempt and is not retried as a whole message.
Transport/provider failures remain independently retryable.

`--repeats N` repeats every model/transport/scenario attempt. JSON and Markdown reports retain raw
attempts and aggregate `passed/attempts`, stability and repair count per scenario. Scenario history
is passed through the same `ContextHistory`/manifest assembly path used in production. The current
22-scenario combined matrix covers every typed pipeline, including multiplayer ownership, pending-message
ambiguity, narrator-rights boundaries, adversarial difficulty and advancement claims, scene
continuity and character creation. Historical benchmark-only world outline/section/critic contracts
remain available for model comparison, but they are not the runtime publication workflow.

World generation is a separate `worldgen` role with a larger output budget and timeout. Its default
transport is `prompt_json`: models may contribute strong narrative planning without being required
to produce a provider-native tool call. Runtime generation has exactly two semantic stages: a loose
creative module pitch followed by complete typed consistency/structuring. Cross-reference and
identity checks are part of `WorldDraft` Pydantic validation, so they use the structuring model's
single repair and then the configured structuring fallback instead of failing only after both
models have returned.

Automatic model escalation is intentionally not part of the current runtime contract. Repair use is
captured in benchmark output as a candidate signal, but fallback triggers and the fallback model will
be selected only after repeated production-config runs establish where cheaper models fail. Rare,
high-impact character generation remains assigned to reasoning; world generation uses its dedicated
quality-oriented role rather than speculative fallback logic.

Playable-character invariants are part of the typed structuring contract, not a post-pipeline
surprise: trait levels total 18, trait and aspect identities are unique, every trait has exactly one
aspect per level, and a relationship flag is present. Faction affiliations and named connections
must resolve inside the same draft. A violation therefore uses bounded structuring repair/fallback;
the creative world pitch is not regenerated. If both typed routes are exhausted, the handler keeps
the world workspace unchanged and returns the existing world-editor clarification. Transport
failures remain retryable.

The structuring worldgen primary and fallback have a 12000-token ceiling because a complete draft
may contain six biographies and six playable sheets, while provider completion accounting can also
include hidden reasoning tokens. Creative worldgen remains capped at 6000. If a pregen already has
a named connection but omitted the mechanical relationship flag, code derives that flag from the
existing connection instead of spending another model call or inventing a new relationship.

The adapter makes one provider attempt by default. Pipeline-level model fallback owns recovery;
stacking SDK retries underneath it would multiply a single timeout into minutes of silence.
Worldgen timeouts are 90 seconds for creative DeepSeek, 120 for creative Aion and structuring Luna,
and 150 for structuring Gemini.
