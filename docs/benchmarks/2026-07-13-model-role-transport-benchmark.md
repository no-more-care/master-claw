# MasterClaw model-role mini-benchmark

Generated: `2026-07-13T11:35:53.177077+00:00`

| Model | Transport | Passed | Calls | Input tok | Output tok | Reasoning tok | Cost | Time |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| `openai/gpt-5.6-luna` | `prompt_json` | 6/7 | 7 | 3052 | 990 | 516 | $0.008992 | 18.5s |
| `openai/gpt-5.6-luna` | `native_tool` | 6/7 | 7 | 3210 | 896 | 303 | $0.008586 | 20.2s |
| `aion-labs/aion-3.0-mini` | `prompt_json` | 7/7 | 7 | 3201 | 4599 | 4720 | $0.008480 | 83.3s |
| `aion-labs/aion-3.0-mini` | `native_tool` | 5/7 | 9 | 7170 | 6229 | 6001 | $0.011876 | 115.7s |
| `nex-agi/nex-n2-mini` | `prompt_json` | 7/7 | 7 | 3112 | 1735 | 1257 | $0.000251 | 13.0s |
| `nex-agi/nex-n2-mini` | `native_tool` | 3/7 | 4 | 2467 | 349 | 8 | $0.000097 | 19.2s |
| `nex-agi/nex-n2-pro` | `prompt_json` | 6/7 | 7 | 3112 | 1547 | 1295 | $0.002800 | 19.3s |
| `nex-agi/nex-n2-pro` | `native_tool` | 5/7 | 8 | 6162 | 2230 | 1558 | $0.003770 | 25.9s |
| `minimax/minimax-m2.7` | `prompt_json` | 5/7 | 8 | 3735 | 3073 | 2404 | $0.004347 | 150.2s |
| `minimax/minimax-m2.7` | `native_tool` | 5/7 | 7 | 3806 | 603 | 405 | $0.002181 | 24.7s |
| `minimax/minimax-m2-her` | `prompt_json` | 0/2 | 4 | 3202 | 999 | 0 | $0.002159 | 13.7s |
| `minimax/minimax-m2-her` | `native_tool` | 0/2 | 0 | 0 | 0 | 0 | $0.000000 | 0.1s |
| `openai/gpt-5.4-nano` | `prompt_json` | 2/5 | 5 | 2325 | 522 | 0 | $0.001118 | 6.9s |
| `openai/gpt-5.4-nano` | `native_tool` | 3/5 | 5 | 2327 | 572 | 0 | $0.001180 | 7.0s |
| `moonshotai/kimi-k2.5` | `prompt_json` | 2/2 | 2 | 751 | 1671 | 1502 | $0.004917 | 37.5s |
| `moonshotai/kimi-k2.5` | `native_tool` | 1/2 | 3 | 1298 | 901 | 419 | $0.003012 | 32.6s |

## Scenario results

- **PASS** `openai/gpt-5.6-luna` / `prompt_json` / `state.intent_action` — 2.4s, $0.000734, checks: `{"action_declaration": true, "confident": true}`
- **PASS** `openai/gpt-5.6-luna` / `prompt_json` / `state.advancement_unsafe` — 3.2s, $0.001187, checks: `{"denied_during_danger": true, "has_reason": true}`
- **PASS** `openai/gpt-5.6-luna` / `prompt_json` / `reasoning.action_roll_with_tools` — 3.4s, $0.001827, checks: `{"requires_roll": true, "uses_exact_trait": true, "uses_exact_aspect": true, "has_difficulty": true}`
- **FAIL** `openai/gpt-5.6-luna` / `prompt_json` / `reasoning.action_clarification_without_tools` — 3.8s, $0.002242, checks: `{"requests_clarification": true, "has_question": true, "does_not_require_roll": false}`
- **PASS** `openai/gpt-5.6-luna` / `prompt_json` / `reasoning.scene_patch` — 2.9s, $0.001063, checks: `{"removes_exact_locked_fact": true, "adds_open_fact": true, "preserves_unrelated_fact": true}`
- **PASS** `openai/gpt-5.6-luna` / `prompt_json` / `narrative.failed_lockpick_en` — 1.4s, $0.000935, checks: `{"mentions_door_or_lock": true, "does_not_claim_opened": true, "concise": true}`
- **PASS** `openai/gpt-5.6-luna` / `prompt_json` / `narrative.successful_lockpick_ru` — 1.3s, $0.001004, checks: `{"russian_output": true, "mentions_door": true, "establishes_opening": true, "concise": true}`
- **PASS** `openai/gpt-5.6-luna` / `native_tool` / `state.intent_action` — 2.3s, $0.000824, checks: `{"action_declaration": true, "confident": true}`
- **PASS** `openai/gpt-5.6-luna` / `native_tool` / `state.advancement_unsafe` — 1.3s, $0.001057, checks: `{"denied_during_danger": true, "has_reason": true}`
- **PASS** `openai/gpt-5.6-luna` / `native_tool` / `reasoning.action_roll_with_tools` — 2.9s, $0.001601, checks: `{"requires_roll": true, "uses_exact_trait": true, "uses_exact_aspect": true, "has_difficulty": true}`
- **FAIL** `openai/gpt-5.6-luna` / `native_tool` / `reasoning.action_clarification_without_tools` — 5.1s, $0.001656, checks: `{"requests_clarification": true, "has_question": true, "does_not_require_roll": false}`
- **PASS** `openai/gpt-5.6-luna` / `native_tool` / `reasoning.scene_patch` — 3.2s, $0.001155, checks: `{"removes_exact_locked_fact": true, "adds_open_fact": true, "preserves_unrelated_fact": true}`
- **PASS** `openai/gpt-5.6-luna` / `native_tool` / `narrative.failed_lockpick_en` — 3.0s, $0.001163, checks: `{"mentions_door_or_lock": true, "does_not_claim_opened": true, "concise": true}`
- **PASS** `openai/gpt-5.6-luna` / `native_tool` / `narrative.successful_lockpick_ru` — 2.3s, $0.001130, checks: `{"russian_output": true, "mentions_door": true, "establishes_opening": true, "concise": true}`
- **PASS** `aion-labs/aion-3.0-mini` / `prompt_json` / `state.intent_action` — 5.8s, $0.000566, checks: `{"action_declaration": true, "confident": true}`
- **PASS** `aion-labs/aion-3.0-mini` / `prompt_json` / `state.advancement_unsafe` — 9.6s, $0.001110, checks: `{"denied_during_danger": true, "has_reason": true}`
- **PASS** `aion-labs/aion-3.0-mini` / `prompt_json` / `reasoning.action_roll_with_tools` — 15.7s, $0.001855, checks: `{"requires_roll": true, "uses_exact_trait": true, "uses_exact_aspect": true, "has_difficulty": true}`
- **PASS** `aion-labs/aion-3.0-mini` / `prompt_json` / `reasoning.action_clarification_without_tools` — 14.1s, $0.001388, checks: `{"requests_clarification": true, "has_question": true, "does_not_require_roll": true}`
- **PASS** `aion-labs/aion-3.0-mini` / `prompt_json` / `reasoning.scene_patch` — 11.5s, $0.000973, checks: `{"removes_exact_locked_fact": true, "adds_open_fact": true, "preserves_unrelated_fact": true}`
- **PASS** `aion-labs/aion-3.0-mini` / `prompt_json` / `narrative.failed_lockpick_en` — 13.6s, $0.001285, checks: `{"mentions_door_or_lock": true, "does_not_claim_opened": true, "concise": true}`
- **PASS** `aion-labs/aion-3.0-mini` / `prompt_json` / `narrative.successful_lockpick_ru` — 13.0s, $0.001304, checks: `{"russian_output": true, "mentions_door": true, "establishes_opening": true, "concise": true}`
- **PASS** `aion-labs/aion-3.0-mini` / `native_tool` / `state.intent_action` — 8.3s, $0.001057, checks: `{"action_declaration": true, "confident": true}`
- **PASS** `aion-labs/aion-3.0-mini` / `native_tool` / `state.advancement_unsafe` — 8.5s, $0.001111, checks: `{"denied_during_danger": true, "has_reason": true}`
- **FAIL** `aion-labs/aion-3.0-mini` / `native_tool` / `reasoning.action_roll_with_tools` — 37.0s, $0.003482, checks: `{"pipeline_completed": false}`
  Error: `PipelineValidationError('pipeline output invalid after one repair: expected exactly one submit_action_interpretation tool call, got 0')`
- **PASS** `aion-labs/aion-3.0-mini` / `native_tool` / `reasoning.action_clarification_without_tools` — 13.8s, $0.001192, checks: `{"requests_clarification": true, "has_question": true, "does_not_require_roll": true}`
- **PASS** `aion-labs/aion-3.0-mini` / `native_tool` / `reasoning.scene_patch` — 12.2s, $0.001445, checks: `{"removes_exact_locked_fact": true, "adds_open_fact": true, "preserves_unrelated_fact": true}`
- **PASS** `aion-labs/aion-3.0-mini` / `native_tool` / `narrative.failed_lockpick_en` — 11.6s, $0.001232, checks: `{"mentions_door_or_lock": true, "does_not_claim_opened": true, "concise": true}`
- **FAIL** `aion-labs/aion-3.0-mini` / `native_tool` / `narrative.successful_lockpick_ru` — 24.3s, $0.002358, checks: `{"pipeline_completed": false}`
  Error: `PipelineValidationError('pipeline output invalid after one repair: expected exactly one submit_narrative_result tool call, got 0')`
- **PASS** `nex-agi/nex-n2-mini` / `prompt_json` / `state.intent_action` — 1.9s, $0.000012, checks: `{"action_declaration": true, "confident": true}`
- **PASS** `nex-agi/nex-n2-mini` / `prompt_json` / `state.advancement_unsafe` — 0.9s, $0.000019, checks: `{"denied_during_danger": true, "has_reason": true}`
- **PASS** `nex-agi/nex-n2-mini` / `prompt_json` / `reasoning.action_roll_with_tools` — 3.8s, $0.000093, checks: `{"requires_roll": true, "uses_exact_trait": true, "uses_exact_aspect": true, "has_difficulty": true}`
- **PASS** `nex-agi/nex-n2-mini` / `prompt_json` / `reasoning.action_clarification_without_tools` — 4.0s, $0.000084, checks: `{"requests_clarification": true, "has_question": true, "does_not_require_roll": true}`
- **PASS** `nex-agi/nex-n2-mini` / `prompt_json` / `reasoning.scene_patch` — 0.6s, $0.000013, checks: `{"removes_exact_locked_fact": true, "adds_open_fact": true, "preserves_unrelated_fact": true}`
- **PASS** `nex-agi/nex-n2-mini` / `prompt_json` / `narrative.failed_lockpick_en` — 1.1s, $0.000015, checks: `{"mentions_door_or_lock": true, "does_not_claim_opened": true, "concise": true}`
- **PASS** `nex-agi/nex-n2-mini` / `prompt_json` / `narrative.successful_lockpick_ru` — 0.7s, $0.000016, checks: `{"russian_output": true, "mentions_door": true, "establishes_opening": true, "concise": true}`
- **FAIL** `nex-agi/nex-n2-mini` / `native_tool` / `state.intent_action` — 0.6s, $0.000000, checks: `{"pipeline_completed": false}`
  Error: `litellm.APIError: APIError: OpenrouterException - Provider returned an empty response`
- **PASS** `nex-agi/nex-n2-mini` / `native_tool` / `state.advancement_unsafe` — 4.8s, $0.000029, checks: `{"denied_during_danger": true, "has_reason": true}`
- **FAIL** `nex-agi/nex-n2-mini` / `native_tool` / `reasoning.action_roll_with_tools` — 0.7s, $0.000000, checks: `{"pipeline_completed": false}`
  Error: `litellm.APIError: APIError: OpenrouterException - Provider returned an empty response`
- **FAIL** `nex-agi/nex-n2-mini` / `native_tool` / `reasoning.action_clarification_without_tools` — 0.7s, $0.000000, checks: `{"pipeline_completed": false}`
  Error: `litellm.APIError: APIError: OpenrouterException - Provider returned an empty response`
- **FAIL** `nex-agi/nex-n2-mini` / `native_tool` / `reasoning.scene_patch` — 3.7s, $0.000022, checks: `{"removes_exact_locked_fact": false, "adds_open_fact": true, "preserves_unrelated_fact": true}`
- **PASS** `nex-agi/nex-n2-mini` / `native_tool` / `narrative.failed_lockpick_en` — 4.3s, $0.000022, checks: `{"mentions_door_or_lock": true, "does_not_claim_opened": true, "concise": true}`
- **PASS** `nex-agi/nex-n2-mini` / `native_tool` / `narrative.successful_lockpick_ru` — 4.5s, $0.000024, checks: `{"russian_output": true, "mentions_door": true, "establishes_opening": true, "concise": true}`
- **PASS** `nex-agi/nex-n2-pro` / `prompt_json` / `state.intent_action` — 0.9s, $0.000120, checks: `{"action_declaration": true, "confident": true}`
- **PASS** `nex-agi/nex-n2-pro` / `prompt_json` / `state.advancement_unsafe` — 1.1s, $0.000176, checks: `{"denied_during_danger": true, "has_reason": true}`
- **PASS** `nex-agi/nex-n2-pro` / `prompt_json` / `reasoning.action_roll_with_tools` — 2.5s, $0.000455, checks: `{"requires_roll": true, "uses_exact_trait": true, "uses_exact_aspect": true, "has_difficulty": true}`
- **FAIL** `nex-agi/nex-n2-pro` / `prompt_json` / `reasoning.action_clarification_without_tools` — 7.2s, $0.000911, checks: `{"requests_clarification": true, "has_question": true, "does_not_require_roll": false}`
- **PASS** `nex-agi/nex-n2-pro` / `prompt_json` / `reasoning.scene_patch` — 3.8s, $0.000285, checks: `{"removes_exact_locked_fact": true, "adds_open_fact": true, "preserves_unrelated_fact": true}`
- **PASS** `nex-agi/nex-n2-pro` / `prompt_json` / `narrative.failed_lockpick_en` — 2.4s, $0.000525, checks: `{"mentions_door_or_lock": true, "does_not_claim_opened": true, "concise": true}`
- **PASS** `nex-agi/nex-n2-pro` / `prompt_json` / `narrative.successful_lockpick_ru` — 1.3s, $0.000327, checks: `{"russian_output": true, "mentions_door": true, "establishes_opening": true, "concise": true}`
- **PASS** `nex-agi/nex-n2-pro` / `native_tool` / `state.intent_action` — 1.5s, $0.000217, checks: `{"action_declaration": true, "confident": true}`
- **PASS** `nex-agi/nex-n2-pro` / `native_tool` / `state.advancement_unsafe` — 1.6s, $0.000291, checks: `{"denied_during_danger": true, "has_reason": true}`
- **FAIL** `nex-agi/nex-n2-pro` / `native_tool` / `reasoning.action_roll_with_tools` — 8.8s, $0.001433, checks: `{"pipeline_completed": false}`
  Error: `PipelineValidationError("pipeline output invalid after one repair: 1 validation error for ActionInterpretation\n  Value error, roll cannot contain a clarification question [type=value_error, input_value={'resolution': 'roll', 't...ation_question': 'None'}, input_type=dict]\n    For further information visit https://errors.pydantic.dev/2.13/v/value_error")`
- **FAIL** `nex-agi/nex-n2-pro` / `native_tool` / `reasoning.action_clarification_without_tools` — 7.6s, $0.000956, checks: `{"requests_clarification": true, "has_question": true, "does_not_require_roll": false}`
- **PASS** `nex-agi/nex-n2-pro` / `native_tool` / `reasoning.scene_patch` — 2.9s, $0.000407, checks: `{"removes_exact_locked_fact": true, "adds_open_fact": true, "preserves_unrelated_fact": true}`
- **PASS** `nex-agi/nex-n2-pro` / `native_tool` / `narrative.failed_lockpick_en` — 1.2s, $0.000228, checks: `{"mentions_door_or_lock": true, "does_not_claim_opened": true, "concise": true}`
- **PASS** `nex-agi/nex-n2-pro` / `native_tool` / `narrative.successful_lockpick_ru` — 2.2s, $0.000238, checks: `{"russian_output": true, "mentions_door": true, "establishes_opening": true, "concise": true}`
- **PASS** `minimax/minimax-m2.7` / `prompt_json` / `state.intent_action` — 41.9s, $0.001156, checks: `{"action_declaration": true, "confident": true}`
- **PASS** `minimax/minimax-m2.7` / `prompt_json` / `state.advancement_unsafe` — 2.4s, $0.000492, checks: `{"denied_during_danger": true, "has_reason": true}`
- **PASS** `minimax/minimax-m2.7` / `prompt_json` / `reasoning.action_roll_with_tools` — 14.3s, $0.000907, checks: `{"requires_roll": true, "uses_exact_trait": true, "uses_exact_aspect": true, "has_difficulty": true}`
- **FAIL** `minimax/minimax-m2.7` / `prompt_json` / `reasoning.action_clarification_without_tools` — 45.9s, $0.000883, checks: `{"requests_clarification": true, "has_question": true, "does_not_require_roll": false}`
- **PASS** `minimax/minimax-m2.7` / `prompt_json` / `reasoning.scene_patch` — 30.8s, $0.000392, checks: `{"removes_exact_locked_fact": true, "adds_open_fact": true, "preserves_unrelated_fact": true}`
- **PASS** `minimax/minimax-m2.7` / `prompt_json` / `narrative.failed_lockpick_en` — 10.2s, $0.000245, checks: `{"mentions_door_or_lock": true, "does_not_claim_opened": true, "concise": true}`
- **FAIL** `minimax/minimax-m2.7` / `prompt_json` / `narrative.successful_lockpick_ru` — 4.6s, $0.000273, checks: `{"russian_output": true, "mentions_door": true, "establishes_opening": false, "concise": true}`
- **PASS** `minimax/minimax-m2.7` / `native_tool` / `state.intent_action` — 3.7s, $0.000382, checks: `{"action_declaration": true, "confident": true}`
- **PASS** `minimax/minimax-m2.7` / `native_tool` / `state.advancement_unsafe` — 3.6s, $0.000302, checks: `{"denied_during_danger": true, "has_reason": true}`
- **PASS** `minimax/minimax-m2.7` / `native_tool` / `reasoning.action_roll_with_tools` — 5.0s, $0.000280, checks: `{"requires_roll": true, "uses_exact_trait": true, "uses_exact_aspect": true, "has_difficulty": true}`
- **FAIL** `minimax/minimax-m2.7` / `native_tool` / `reasoning.action_clarification_without_tools` — 2.9s, $0.000341, checks: `{"requests_clarification": true, "has_question": true, "does_not_require_roll": false}`
- **PASS** `minimax/minimax-m2.7` / `native_tool` / `reasoning.scene_patch` — 4.4s, $0.000182, checks: `{"removes_exact_locked_fact": true, "adds_open_fact": true, "preserves_unrelated_fact": true}`
- **PASS** `minimax/minimax-m2.7` / `native_tool` / `narrative.failed_lockpick_en` — 3.5s, $0.000472, checks: `{"mentions_door_or_lock": true, "does_not_claim_opened": true, "concise": true}`
- **FAIL** `minimax/minimax-m2.7` / `native_tool` / `narrative.successful_lockpick_ru` — 1.6s, $0.000222, checks: `{"russian_output": false, "mentions_door": false, "establishes_opening": false, "concise": true}`
- **FAIL** `minimax/minimax-m2-her` / `prompt_json` / `narrative.failed_lockpick_en` — 10.1s, $0.001462, checks: `{"pipeline_completed": false}`
  Error: `PipelineValidationError("pipeline output invalid after one repair: 1 validation error for NarrativeResult\n  Invalid JSON: expected value at line 1 column 1 [type=json_invalid, input_value='Mara knelt by the iron d...with a frustrated sigh.', input_type=str]\n    For further information visit https://errors.pydantic.dev/2.13/v/json_invalid")`
- **FAIL** `minimax/minimax-m2-her` / `prompt_json` / `narrative.successful_lockpick_ru` — 3.6s, $0.000697, checks: `{"pipeline_completed": false}`
  Error: `PipelineValidationError("pipeline output invalid after one repair: 1 validation error for NarrativeResult\n  Invalid JSON: expected value at line 1 column 1 [type=json_invalid, input_value='**Тихо и профе... конец сцены)', input_type=str]\n    For further information visit https://errors.pydantic.dev/2.13/v/json_invalid")`
- **FAIL** `minimax/minimax-m2-her` / `native_tool` / `narrative.failed_lockpick_en` — 0.0s, $0.000000, checks: `{"pipeline_completed": false}`
  Error: `litellm.NotFoundError: NotFoundError: OpenrouterException - {"error":{"message":"No endpoints found that support tool use. Try disabling \"submit_narrative_result\". To learn more about provider routing, visit: https://openrouter.ai/docs/guides/routing/provider-selection","code":404}}`
- **FAIL** `minimax/minimax-m2-her` / `native_tool` / `narrative.successful_lockpick_ru` — 0.0s, $0.000000, checks: `{"pipeline_completed": false}`
  Error: `litellm.NotFoundError: NotFoundError: OpenrouterException - {"error":{"message":"No endpoints found that support tool use. Try disabling \"submit_narrative_result\". To learn more about provider routing, visit: https://openrouter.ai/docs/guides/routing/provider-selection","code":404}}`
- **FAIL** `openai/gpt-5.4-nano` / `prompt_json` / `state.intent_action` — 1.0s, $0.000119, checks: `{"action_declaration": false, "confident": true}`
- **PASS** `openai/gpt-5.4-nano` / `prompt_json` / `state.advancement_unsafe` — 1.3s, $0.000228, checks: `{"denied_during_danger": true, "has_reason": true}`
- **FAIL** `openai/gpt-5.4-nano` / `prompt_json` / `reasoning.action_roll_with_tools` — 1.6s, $0.000328, checks: `{"requires_roll": false, "uses_exact_trait": true, "uses_exact_aspect": true, "has_difficulty": true}`
- **FAIL** `openai/gpt-5.4-nano` / `prompt_json` / `reasoning.action_clarification_without_tools` — 1.4s, $0.000307, checks: `{"requests_clarification": true, "has_question": true, "does_not_require_roll": false}`
- **PASS** `openai/gpt-5.4-nano` / `prompt_json` / `reasoning.scene_patch` — 1.7s, $0.000136, checks: `{"removes_exact_locked_fact": true, "adds_open_fact": true, "preserves_unrelated_fact": true}`
- **PASS** `openai/gpt-5.4-nano` / `native_tool` / `state.intent_action` — 0.9s, $0.000126, checks: `{"action_declaration": true, "confident": true}`
- **PASS** `openai/gpt-5.4-nano` / `native_tool` / `state.advancement_unsafe` — 1.4s, $0.000248, checks: `{"denied_during_danger": true, "has_reason": true}`
- **FAIL** `openai/gpt-5.4-nano` / `native_tool` / `reasoning.action_roll_with_tools` — 1.8s, $0.000304, checks: `{"requires_roll": false, "uses_exact_trait": true, "uses_exact_aspect": true, "has_difficulty": true}`
- **FAIL** `openai/gpt-5.4-nano` / `native_tool` / `reasoning.action_clarification_without_tools` — 2.0s, $0.000335, checks: `{"requests_clarification": true, "has_question": true, "does_not_require_roll": false}`
- **PASS** `openai/gpt-5.4-nano` / `native_tool` / `reasoning.scene_patch` — 1.0s, $0.000167, checks: `{"removes_exact_locked_fact": true, "adds_open_fact": true, "preserves_unrelated_fact": true}`
- **PASS** `moonshotai/kimi-k2.5` / `prompt_json` / `narrative.failed_lockpick_en` — 21.1s, $0.002713, checks: `{"mentions_door_or_lock": true, "does_not_claim_opened": true, "concise": true}`
- **PASS** `moonshotai/kimi-k2.5` / `prompt_json` / `narrative.successful_lockpick_ru` — 16.3s, $0.002204, checks: `{"russian_output": true, "mentions_door": true, "establishes_opening": true, "concise": true}`
- **PASS** `moonshotai/kimi-k2.5` / `native_tool` / `narrative.failed_lockpick_en` — 9.2s, $0.000733, checks: `{"mentions_door_or_lock": true, "does_not_claim_opened": true, "concise": true}`
- **FAIL** `moonshotai/kimi-k2.5` / `native_tool` / `narrative.successful_lockpick_ru` — 23.3s, $0.002278, checks: `{"russian_output": true, "mentions_door": true, "establishes_opening": false, "concise": true}`
