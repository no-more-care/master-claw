# Model/role/transport benchmark analysis

The raw run is preserved in
`2026-07-13-model-role-transport-benchmark.json` and its generated Markdown report. It executed
88 scenarios on 2026-07-13, took 592.5 seconds and cost $0.063766 according to provider telemetry.

## Evaluator audit

Two false negatives were identified after inspecting the typed outputs:

1. A clarification may retain a known lock difficulty. Selecting `clarification`, asking a question
   and not rolling are the relevant requirements; `difficulty = null` is not a domain invariant.
2. Kimi's Russian phrase "дверь отступает ... тёмный проём" unambiguously establishes opening but
   was outside the original lexical roots.

The benchmark code now reflects both corrections. The table below applies only these two mechanical
corrections to the raw scores; costs, calls and latency remain unchanged.

| Model | `prompt_json` | `native_tool` | Audited conclusion |
|---|---:|---:|---|
| `openai/gpt-5.6-luna` | 7/7 | 7/7 | Reliable with either transport; native is the production choice. |
| `aion-labs/aion-3.0-mini` | 7/7 | 5/7 | Strong typed reasoning through prompt JSON; native calls were intermittently omitted. |
| `nex-agi/nex-n2-mini` | 7/7 | 3/7 | Best price/latency result through prompt JSON; native provider path is unreliable. |
| `nex-agi/nex-n2-pro` | 7/7 | 6/7 | Strong prompt-JSON reasoning; native produced a string `"None"` in a nullable field. |
| `minimax/minimax-m2.7` | 6/7 | 6/7 | Tool transport works, but Russian narrative fidelity failed and prompt latency was high. |
| `minimax/minimax-m2-her` | 0/2 | 0/2 | Returned bare prose instead of JSON; OpenRouter exposed no tool-capable endpoint. |
| `openai/gpt-5.4-nano` | 3/5 | 4/5 | Fast and promising for state via native tools, but action reasoning was incorrect. |
| `moonshotai/kimi-k2.5` | 2/2 | 2/2 | Best narrative-only candidate; slower and more expensive than Luna in this mini-run. |

## Routing decision

- Keep `nex-agi/nex-n2-mini` + `prompt_json` for state: 7/7 at $0.000251 and 13.0 seconds for
  the complete seven-scenario cross-role run.
- Keep `nex-agi/nex-n2-pro` + `prompt_json` for reasoning as the conservative default. Nex Mini and
  Aion both passed this small reasoning set, but the set is too small to replace the stronger tier.
- Keep `openai/gpt-5.6-luna` + `native_tool` for routine narrative and use Kimi as a deliberate
  narrative-quality option. Kimi passed both narrative cases but took 37.5 seconds and $0.004917.
- Retain per-role, per-model transport configuration. A conventional native tool contract is the
  preferred interface when the provider path preserves it, not a universal compatibility layer.

The next evaluation should add longer narrative continuity, conflicting evidence, multiple actors,
adversarial JSON/tool text, and repeated runs for variance before changing production routing.
