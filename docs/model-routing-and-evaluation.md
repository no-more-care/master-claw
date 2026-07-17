# Model routing and evaluation baseline

Status: decision snapshot for 2026-07-13. No model evaluation was run while recording this
snapshot. Prices below are the OpenRouter rates supplied for the evaluation discussion, in USD per
1M input/output tokens; they are historical planning inputs rather than a live price quotation.

## Current production routes

| Work | Primary | Fallback | Interface | Decision |
| --- | --- | --- | --- | --- |
| General reasoning | `openai/gpt-5.6-luna` | `google/gemini-3-flash-preview` | prompt JSON | Luna led the general-role evaluation; Gemini is the conservative typed fallback. |
| World/module creative draft | `deepseek/deepseek-v4-pro` | `aion-labs/aion-3.0` | prompt JSON with one `module_plot` field | Temporary creative route; optimize for ideas and grimdark module writing, not schema discipline. |
| World/module consistency and structuring | `openai/gpt-5.6-luna` | `google/gemini-3-flash-preview` | strict prompt JSON | Checks the raw pitch and emits the complete `WorldDraft`. |
| Narrative draft | configured narrative model (currently Luna) | existing deterministic prose fallback on total failure | typed output | Creative candidates remain under evaluation. |
| Narrative editorial pass | `openai/gpt-5.6-luna` | `google/gemini-3-flash-preview` | strict prompt JSON | Repairs contradictions and phrasing without changing immutable mechanics. If both editors fail, the already validated raw narration is retained. |

World generation is exactly two semantic LLM stages: creative pitch, then consistency/JSON
structuring. Each stage may retry its own transport/schema once, and each route has the explicit
fallback above. The creative model never owns ids, game state or publication. Narrative uses the
same draft/editor separation, while dice, success, narrator rights and scene mutations remain
code-owned.

## Recorded candidates

| Model | Input | Output | Recorded disposition |
| --- | ---: | ---: | --- |
| `openai/gpt-5.6-luna` | $1.00 | $6.00 | General reasoning primary; world/narrative editor primary. |
| `google/gemini-3-flash-preview` | $0.50 | $3.00 | Reasoning and editorial fallback. |
| `deepseek/deepseek-v4-pro` | $0.435 | $0.87 | Temporary worldgen creative primary. |
| `aion-labs/aion-3.0` | $3.00 | $6.00 | Temporary worldgen creative fallback. |
| `aion-labs/aion-3.0-mini` | $0.70 | $1.40 | Promising, especially with an explicit output contract; still a candidate. |
| `nex-agi/nex-n2-mini` | $0.025 | $0.10 | Evaluated as a cheap small-role candidate; not in the current reasoning route. |
| `nex-agi/nex-n2-pro` | $0.25 | $1.00 | Evaluated as a cheap reasoning candidate; replaced by Luna in the current route. |
| `minimax/minimax-m2.7` | $0.24 | $0.96 | Candidate retained for later comparison. |
| `minimax/minimax-m2-her` | $0.30 | $1.20 | Candidate retained for later comparison. |
| `openai/gpt-5.4-nano` | $0.20 | $1.25 | Candidate retained for small typed roles. |
| `moonshotai/kimi-k2.5` | $0.375 | $2.025 | Narrative candidate; considered too slow/expensive for most other roles. |
| `qwen/qwen3.7-plus` | $0.32 | $1.28 | Removed from active candidates because of latency. |
| `x-ai/grok-4.3` | $1.25 | $2.50 | Removed from active candidates. |
| evaluated Grok flagship | $2.00 | $6.00 | Baseline only; removed from active candidates. |

No unrequested premium model is part of the worldgen route. In particular, Sol is excluded. Qwen
or an OpenAI comparison model may still be used in a future one-off worldgen evaluation only when
that evaluation is explicitly requested.

Raw and summarized historical results remain in:

- `docs/benchmarks/2026-07-13-model-role-transport-analysis.md`;
- `docs/benchmarks/2026-07-13-worldgen-transport-analysis.md`;
- `docs/benchmarks/2026-07-13-worldgen-transport-benchmark.json`.
