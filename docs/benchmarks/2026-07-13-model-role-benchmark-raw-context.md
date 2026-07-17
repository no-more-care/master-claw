# MasterClaw model-role mini-benchmark

Generated: `2026-07-13T10:25:15.954485+00:00`

| Model | Passed | Calls | Input tok | Output tok | Reasoning tok | Cost | Time |
|---|---:|---:|---:|---:|---:|---:|---:|
| `x-ai/grok-4.5` | 5/6 | 7 | 3597 | 4211 | 3732 | $0.030732 | 105.2s |
| `openai/gpt-5.6-luna` | 5/6 | 7 | 2214 | 770 | 330 | $0.006834 | 15.7s |
| `aion-labs/aion-3.0-mini` | 5/6 | 6 | 1752 | 3052 | 3024 | $0.005499 | 58.8s |
| `nex-agi/nex-n2-mini` | 4/6 | 7 | 2249 | 1760 | 1329 | $0.000232 | 13.1s |
| `nex-agi/nex-n2-pro` | 5/6 | 7 | 2257 | 2121 | 2222 | $0.002685 | 21.0s |
| `qwen/qwen3.7-plus` | 5/6 | 7 | 2289 | 6102 | 5512 | $0.008543 | 191.1s |
| `x-ai/grok-4.3` | 5/6 | 7 | 3450 | 3560 | 3172 | $0.012272 | 44.4s |

## Scenario results

- **PASS** `x-ai/grok-4.5` / `state.intent_action` — 4.9s, $0.003304, checks: `{"action_declaration": true, "confident": true}`
- **PASS** `x-ai/grok-4.5` / `state.advancement_unsafe` — 2.9s, $0.003066, checks: `{"denied_during_danger": true, "has_reason": true}`
- **FAIL** `x-ai/grok-4.5` / `reasoning.action_roll` — 51.0s, $0.012114, checks: `{"requires_roll": false, "uses_exact_trait": true, "uses_exact_aspect": true, "has_difficulty": false}`
- **PASS** `x-ai/grok-4.5` / `reasoning.scene_patch` — 6.7s, $0.006012, checks: `{"removes_exact_locked_fact": true, "adds_open_fact": true, "preserves_unrelated_fact": true}`
- **PASS** `x-ai/grok-4.5` / `narrative.failed_lockpick_en` — 35.4s, $0.002854, checks: `{"mentions_door_or_lock": true, "does_not_claim_opened": true, "concise": true}`
- **PASS** `x-ai/grok-4.5` / `narrative.successful_lockpick_ru` — 4.3s, $0.003382, checks: `{"russian_output": true, "mentions_door": true, "establishes_opening": true, "concise": true}`
- **PASS** `openai/gpt-5.6-luna` / `state.intent_action` — 3.7s, $0.000631, checks: `{"action_declaration": true, "confident": true}`
- **PASS** `openai/gpt-5.6-luna` / `state.advancement_unsafe` — 2.7s, $0.001006, checks: `{"denied_during_danger": true, "has_reason": true}`
- **FAIL** `openai/gpt-5.6-luna` / `reasoning.action_roll` — 4.5s, $0.002868, checks: `{"requires_roll": false, "uses_exact_trait": true, "uses_exact_aspect": true, "has_difficulty": false}`
- **PASS** `openai/gpt-5.6-luna` / `reasoning.scene_patch` — 2.0s, $0.001019, checks: `{"removes_exact_locked_fact": true, "adds_open_fact": true, "preserves_unrelated_fact": true}`
- **PASS** `openai/gpt-5.6-luna` / `narrative.failed_lockpick_en` — 1.3s, $0.000671, checks: `{"mentions_door_or_lock": true, "does_not_claim_opened": true, "concise": true}`
- **PASS** `openai/gpt-5.6-luna` / `narrative.successful_lockpick_ru` — 1.4s, $0.000639, checks: `{"russian_output": true, "mentions_door": true, "establishes_opening": true, "concise": true}`
- **PASS** `aion-labs/aion-3.0-mini` / `state.intent_action` — 7.7s, $0.000685, checks: `{"action_declaration": true, "confident": true}`
- **PASS** `aion-labs/aion-3.0-mini` / `state.advancement_unsafe` — 7.7s, $0.000824, checks: `{"denied_during_danger": true, "has_reason": true}`
- **FAIL** `aion-labs/aion-3.0-mini` / `reasoning.action_roll` — 16.6s, $0.001732, checks: `{"requires_roll": false, "uses_exact_trait": true, "uses_exact_aspect": true, "has_difficulty": false}`
- **PASS** `aion-labs/aion-3.0-mini` / `reasoning.scene_patch` — 14.5s, $0.001196, checks: `{"removes_exact_locked_fact": true, "adds_open_fact": true, "preserves_unrelated_fact": true}`
- **PASS** `aion-labs/aion-3.0-mini` / `narrative.failed_lockpick_en` — 4.8s, $0.000427, checks: `{"mentions_door_or_lock": true, "does_not_claim_opened": true, "concise": true}`
- **PASS** `aion-labs/aion-3.0-mini` / `narrative.successful_lockpick_ru` — 7.5s, $0.000636, checks: `{"russian_output": true, "mentions_door": true, "establishes_opening": true, "concise": true}`
- **PASS** `nex-agi/nex-n2-mini` / `state.intent_action` — 2.9s, $0.000011, checks: `{"action_declaration": true, "confident": true}`
- **PASS** `nex-agi/nex-n2-mini` / `state.advancement_unsafe` — 0.9s, $0.000016, checks: `{"denied_during_danger": true, "has_reason": true}`
- **FAIL** `nex-agi/nex-n2-mini` / `reasoning.action_roll` — 5.5s, $0.000131, checks: `{"requires_roll": false, "uses_exact_trait": true, "uses_exact_aspect": true, "has_difficulty": false}`
- **PASS** `nex-agi/nex-n2-mini` / `reasoning.scene_patch` — 2.0s, $0.000042, checks: `{"removes_exact_locked_fact": true, "adds_open_fact": true, "preserves_unrelated_fact": true}`
- **PASS** `nex-agi/nex-n2-mini` / `narrative.failed_lockpick_en` — 1.1s, $0.000019, checks: `{"mentions_door_or_lock": true, "does_not_claim_opened": true, "concise": true}`
- **FAIL** `nex-agi/nex-n2-mini` / `narrative.successful_lockpick_ru` — 0.7s, $0.000012, checks: `{"russian_output": true, "mentions_door": true, "establishes_opening": false, "concise": true}`
- **PASS** `nex-agi/nex-n2-pro` / `state.intent_action` — 1.2s, $0.000151, checks: `{"action_declaration": true, "confident": true}`
- **PASS** `nex-agi/nex-n2-pro` / `state.advancement_unsafe` — 1.9s, $0.000265, checks: `{"denied_during_danger": true, "has_reason": true}`
- **FAIL** `nex-agi/nex-n2-pro` / `reasoning.action_roll` — 11.9s, $0.001468, checks: `{"pipeline_completed": false}`
  Error: `PipelineValidationError("pipeline output invalid after one repair: 1 validation error for ActionInterpretation\n  Invalid JSON: EOF while parsing a value at line 1 column 0 [type=json_invalid, input_value='', input_type=str]\n    For further information visit https://errors.pydantic.dev/2.13/v/json_invalid")`
- **PASS** `nex-agi/nex-n2-pro` / `reasoning.scene_patch` — 3.5s, $0.000502, checks: `{"removes_exact_locked_fact": true, "adds_open_fact": true, "preserves_unrelated_fact": true}`
- **PASS** `nex-agi/nex-n2-pro` / `narrative.failed_lockpick_en` — 1.6s, $0.000175, checks: `{"mentions_door_or_lock": true, "does_not_claim_opened": true, "concise": true}`
- **PASS** `nex-agi/nex-n2-pro` / `narrative.successful_lockpick_ru` — 1.0s, $0.000124, checks: `{"russian_output": true, "mentions_door": true, "establishes_opening": true, "concise": true}`
- **PASS** `qwen/qwen3.7-plus` / `state.intent_action` — 21.9s, $0.001266, checks: `{"action_declaration": true, "confident": true}`
- **PASS** `qwen/qwen3.7-plus` / `state.advancement_unsafe` — 34.5s, $0.001463, checks: `{"denied_during_danger": true, "has_reason": true}`
- **PASS** `qwen/qwen3.7-plus` / `reasoning.action_roll` — 46.5s, $0.002339, checks: `{"requires_roll": true, "uses_exact_trait": true, "uses_exact_aspect": true, "has_difficulty": true}`
- **PASS** `qwen/qwen3.7-plus` / `reasoning.scene_patch` — 14.7s, $0.000653, checks: `{"removes_exact_locked_fact": true, "adds_open_fact": true, "preserves_unrelated_fact": true}`
- **PASS** `qwen/qwen3.7-plus` / `narrative.failed_lockpick_en` — 34.4s, $0.001280, checks: `{"mentions_door_or_lock": true, "does_not_claim_opened": true, "concise": true}`
- **FAIL** `qwen/qwen3.7-plus` / `narrative.successful_lockpick_ru` — 39.1s, $0.001542, checks: `{"russian_output": true, "mentions_door": true, "establishes_opening": false, "concise": true}`
- **PASS** `x-ai/grok-4.3` / `state.intent_action` — 5.7s, $0.001443, checks: `{"action_declaration": true, "confident": true}`
- **PASS** `x-ai/grok-4.3` / `state.advancement_unsafe` — 5.0s, $0.001391, checks: `{"denied_during_danger": true, "has_reason": true}`
- **FAIL** `x-ai/grok-4.3` / `reasoning.action_roll` — 17.8s, $0.004964, checks: `{"requires_roll": false, "uses_exact_trait": false, "uses_exact_aspect": false, "has_difficulty": false}`
- **PASS** `x-ai/grok-4.3` / `reasoning.scene_patch` — 6.8s, $0.001914, checks: `{"removes_exact_locked_fact": true, "adds_open_fact": true, "preserves_unrelated_fact": true}`
- **PASS** `x-ai/grok-4.3` / `narrative.failed_lockpick_en` — 4.4s, $0.001181, checks: `{"mentions_door_or_lock": true, "does_not_claim_opened": true, "concise": true}`
- **PASS** `x-ai/grok-4.3` / `narrative.successful_lockpick_ru` — 4.8s, $0.001379, checks: `{"russian_output": true, "mentions_door": true, "establishes_opening": true, "concise": true}`
