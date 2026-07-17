# MasterClaw model-role mini-benchmark

Generated: `2026-07-13T10:34:18.861716+00:00`

| Model | Passed | Calls | Input tok | Output tok | Reasoning tok | Cost | Time |
|---|---:|---:|---:|---:|---:|---:|---:|
| `x-ai/grok-4.5` | 6/6 | 6 | 3382 | 4663 | 4283 | $0.033782 | 39.1s |
| `openai/gpt-5.6-luna` | 6/6 | 6 | 2179 | 653 | 291 | $0.006097 | 11.9s |
| `aion-labs/aion-3.0-mini` | 5/6 | 7 | 2468 | 3746 | 3709 | $0.006972 | 66.4s |
| `nex-agi/nex-n2-mini` | 6/6 | 6 | 2220 | 787 | 344 | $0.000134 | 6.8s |
| `nex-agi/nex-n2-pro` | 6/6 | 6 | 2220 | 646 | 404 | $0.001201 | 9.3s |
| `qwen/qwen3.7-plus` | 5/6 | 6 | 2226 | 6990 | 6448 | $0.009660 | 207.3s |
| `x-ai/grok-4.3` | 5/6 | 6 | 3234 | 2922 | 2605 | $0.010541 | 45.7s |

## Scenario results

- **PASS** `x-ai/grok-4.5` / `state.intent_action` — 3.7s, $0.002600, checks: `{"action_declaration": true, "confident": true}`
- **PASS** `x-ai/grok-4.5` / `state.advancement_unsafe` — 3.1s, $0.003452, checks: `{"denied_during_danger": true, "has_reason": true}`
- **PASS** `x-ai/grok-4.5` / `reasoning.action_roll` — 10.3s, $0.008468, checks: `{"requires_roll": true, "uses_exact_trait": true, "uses_exact_aspect": true, "has_difficulty": true}`
- **PASS** `x-ai/grok-4.5` / `reasoning.scene_patch` — 5.5s, $0.005112, checks: `{"removes_exact_locked_fact": true, "adds_open_fact": true, "preserves_unrelated_fact": true}`
- **PASS** `x-ai/grok-4.5` / `narrative.failed_lockpick_en` — 5.4s, $0.004624, checks: `{"mentions_door_or_lock": true, "does_not_claim_opened": true, "concise": true}`
- **PASS** `x-ai/grok-4.5` / `narrative.successful_lockpick_ru` — 11.1s, $0.009526, checks: `{"russian_output": true, "mentions_door": true, "establishes_opening": true, "concise": true}`
- **PASS** `openai/gpt-5.6-luna` / `state.intent_action` — 2.5s, $0.000765, checks: `{"action_declaration": true, "confident": true}`
- **PASS** `openai/gpt-5.6-luna` / `state.advancement_unsafe` — 1.4s, $0.001057, checks: `{"denied_during_danger": true, "has_reason": true}`
- **PASS** `openai/gpt-5.6-luna` / `reasoning.action_roll` — 3.0s, $0.001505, checks: `{"requires_roll": true, "uses_exact_trait": true, "uses_exact_aspect": true, "has_difficulty": true}`
- **PASS** `openai/gpt-5.6-luna` / `reasoning.scene_patch` — 1.4s, $0.001095, checks: `{"removes_exact_locked_fact": true, "adds_open_fact": true, "preserves_unrelated_fact": true}`
- **PASS** `openai/gpt-5.6-luna` / `narrative.failed_lockpick_en` — 1.0s, $0.000659, checks: `{"mentions_door_or_lock": true, "does_not_claim_opened": true, "concise": true}`
- **PASS** `openai/gpt-5.6-luna` / `narrative.successful_lockpick_ru` — 2.5s, $0.001016, checks: `{"russian_output": true, "mentions_door": true, "establishes_opening": true, "concise": true}`
- **PASS** `aion-labs/aion-3.0-mini` / `state.intent_action` — 6.6s, $0.000671, checks: `{"action_declaration": true, "confident": true}`
- **PASS** `aion-labs/aion-3.0-mini` / `state.advancement_unsafe` — 5.1s, $0.000687, checks: `{"denied_during_danger": true, "has_reason": true}`
- **FAIL** `aion-labs/aion-3.0-mini` / `reasoning.action_roll` — 15.8s, $0.001825, checks: `{"requires_roll": false, "uses_exact_trait": true, "uses_exact_aspect": true, "has_difficulty": false}`
- **PASS** `aion-labs/aion-3.0-mini` / `reasoning.scene_patch` — 9.6s, $0.000978, checks: `{"removes_exact_locked_fact": true, "adds_open_fact": true, "preserves_unrelated_fact": true}`
- **PASS** `aion-labs/aion-3.0-mini` / `narrative.failed_lockpick_en` — 18.3s, $0.001840, checks: `{"mentions_door_or_lock": true, "does_not_claim_opened": true, "concise": true}`
- **PASS** `aion-labs/aion-3.0-mini` / `narrative.successful_lockpick_ru` — 11.0s, $0.000972, checks: `{"russian_output": true, "mentions_door": true, "establishes_opening": true, "concise": true}`
- **PASS** `nex-agi/nex-n2-mini` / `state.intent_action` — 0.9s, $0.000011, checks: `{"action_declaration": true, "confident": true}`
- **PASS** `nex-agi/nex-n2-mini` / `state.advancement_unsafe` — 1.4s, $0.000020, checks: `{"denied_during_danger": true, "has_reason": true}`
- **PASS** `nex-agi/nex-n2-mini` / `reasoning.action_roll` — 1.8s, $0.000046, checks: `{"requires_roll": true, "uses_exact_trait": true, "uses_exact_aspect": true, "has_difficulty": true}`
- **PASS** `nex-agi/nex-n2-mini` / `reasoning.scene_patch` — 1.3s, $0.000028, checks: `{"removes_exact_locked_fact": true, "adds_open_fact": true, "preserves_unrelated_fact": true}`
- **PASS** `nex-agi/nex-n2-mini` / `narrative.failed_lockpick_en` — 0.7s, $0.000014, checks: `{"mentions_door_or_lock": true, "does_not_claim_opened": true, "concise": true}`
- **PASS** `nex-agi/nex-n2-mini` / `narrative.successful_lockpick_ru` — 0.7s, $0.000015, checks: `{"russian_output": true, "mentions_door": true, "establishes_opening": true, "concise": true}`
- **PASS** `nex-agi/nex-n2-pro` / `state.intent_action` — 0.9s, $0.000109, checks: `{"action_declaration": true, "confident": true}`
- **PASS** `nex-agi/nex-n2-pro` / `state.advancement_unsafe` — 2.3s, $0.000253, checks: `{"denied_during_danger": true, "has_reason": true}`
- **PASS** `nex-agi/nex-n2-pro` / `reasoning.action_roll` — 2.7s, $0.000454, checks: `{"requires_roll": true, "uses_exact_trait": true, "uses_exact_aspect": true, "has_difficulty": true}`
- **PASS** `nex-agi/nex-n2-pro` / `reasoning.scene_patch` — 0.9s, $0.000118, checks: `{"removes_exact_locked_fact": true, "adds_open_fact": true, "preserves_unrelated_fact": true}`
- **PASS** `nex-agi/nex-n2-pro` / `narrative.failed_lockpick_en` — 1.6s, $0.000131, checks: `{"mentions_door_or_lock": true, "does_not_claim_opened": true, "concise": true}`
- **PASS** `nex-agi/nex-n2-pro` / `narrative.successful_lockpick_ru` — 1.0s, $0.000135, checks: `{"russian_output": true, "mentions_door": true, "establishes_opening": true, "concise": true}`
- **PASS** `qwen/qwen3.7-plus` / `state.intent_action` — 32.0s, $0.001029, checks: `{"action_declaration": true, "confident": true}`
- **PASS** `qwen/qwen3.7-plus` / `state.advancement_unsafe` — 33.0s, $0.001472, checks: `{"denied_during_danger": true, "has_reason": true}`
- **FAIL** `qwen/qwen3.7-plus` / `reasoning.action_roll` — 71.5s, $0.003492, checks: `{"requires_roll": false, "uses_exact_trait": true, "uses_exact_aspect": true, "has_difficulty": true}`
- **PASS** `qwen/qwen3.7-plus` / `reasoning.scene_patch` — 15.3s, $0.000776, checks: `{"removes_exact_locked_fact": true, "adds_open_fact": true, "preserves_unrelated_fact": true}`
- **PASS** `qwen/qwen3.7-plus` / `narrative.failed_lockpick_en` — 20.3s, $0.001444, checks: `{"mentions_door_or_lock": true, "does_not_claim_opened": true, "concise": true}`
- **PASS** `qwen/qwen3.7-plus` / `narrative.successful_lockpick_ru` — 35.3s, $0.001446, checks: `{"russian_output": true, "mentions_door": true, "establishes_opening": true, "concise": true}`
- **PASS** `x-ai/grok-4.3` / `state.intent_action` — 5.2s, $0.001168, checks: `{"action_declaration": true, "confident": true}`
- **PASS** `x-ai/grok-4.3` / `state.advancement_unsafe` — 6.6s, $0.001473, checks: `{"denied_during_danger": true, "has_reason": true}`
- **PASS** `x-ai/grok-4.3` / `reasoning.action_roll` — 8.6s, $0.002427, checks: `{"requires_roll": true, "uses_exact_trait": true, "uses_exact_aspect": true, "has_difficulty": true}`
- **FAIL** `x-ai/grok-4.3` / `reasoning.scene_patch` — 8.5s, $0.001989, checks: `{"removes_exact_locked_fact": true, "adds_open_fact": false, "preserves_unrelated_fact": true}`
- **PASS** `x-ai/grok-4.3` / `narrative.failed_lockpick_en` — 7.4s, $0.001607, checks: `{"mentions_door_or_lock": true, "does_not_claim_opened": true, "concise": true}`
- **PASS** `x-ai/grok-4.3` / `narrative.successful_lockpick_ru` — 9.4s, $0.001877, checks: `{"russian_output": true, "mentions_door": true, "establishes_opening": true, "concise": true}`
