# World-generation model evaluation plan

Initial transport run completed on 2026-07-13. See
[`2026-07-13-worldgen-transport-analysis.md`](2026-07-13-worldgen-transport-analysis.md) for the
human audit and recommendation.

World generation is evaluated independently from frequent play roles because it is rare, has a
higher cost of semantic failure, and benefits more from narrative quality than from minimal token
price. Production exposes a dedicated `worldgen` role with a 4000-token output budget, 180-second
timeout, medium reasoning effort and `prompt_json` transport. Luna is only the interim baseline until
the matrix below is evaluated.

## Candidates

| Model | Input / 1M | Output / 1M | Purpose |
|---|---:|---:|---|
| `aion-labs/aion-3.0` | $3.00 | $6.00 | roleplaying/storytelling specialist |
| `aion-labs/aion-3.0-mini` | $0.70 | $1.40 | lower-cost Aion comparison |
| `deepseek/deepseek-v4-pro` | $0.435 | $0.87 | DeepSeek narrative/reasoning candidate |
| `google/gemini-3-flash-preview` | $0.50 | $3.00 | fast structured-output baseline |
| `openai/gpt-5.6-luna` | $1.00 | $6.00 | current reliable baseline |
| `qwen/qwen3.7-plus` | $0.32 | $1.28 | inexpensive agent-trained comparison |

Prices and slugs were checked against the OpenRouter model catalogue on 2026-07-13. Relevant model
pages: [Aion 3.0](https://openrouter.ai/aion-labs/aion-3.0),
[DeepSeek V4 Pro](https://openrouter.ai/deepseek/deepseek-v4-pro),
[Gemini 3 Flash Preview](https://openrouter.ai/google/gemini-3-flash-preview),
[Qwen 3.7 Plus](https://openrouter.ai/qwen/qwen3.7-plus), and
[OpenAI GPT-5.6 family](https://openrouter.ai/openai).

## Scenarios and audit

The suite exercises all four stages rather than asking for one monolithic draft:

1. a grimdark outline with multiple locations, factions and playable tensions;
2. public expansion that preserves approved ids/factions and produces substantial descriptions;
3. a secret plot tied to public names, with enough substance for clues and escalation;
4. a critic that rejects a deliberate public/secret contradiction.

Mechanical checks measure schema completion, repair rate, identity preservation, grounding,
latency and cost. Raw outputs must also receive a human comparative audit for originality, faction
motivation, playable hooks, moral pressure, meaningful agency, tone adherence and avoidance of
generic cult/gore grimdark. Deterministic checks are intentionally not presented as a complete
creativity score.

Run both transports once to quantify tool-format fragility, then perform three production-config
`prompt_json` repeats for finalists:

```bash
masterclaw model-benchmark \
  --suite worldgen \
  --config-mode production \
  --transports prompt_json native_tool \
  --output docs/benchmarks/worldgen-transport.json

masterclaw model-benchmark \
  --suite worldgen \
  --config-mode production \
  --transports prompt_json \
  --repeats 3 \
  --models <finalists> \
  --output docs/benchmarks/worldgen-finalists.json
```
