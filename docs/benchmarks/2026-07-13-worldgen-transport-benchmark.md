# MasterClaw model-role mini-benchmark

Generated: `2026-07-13T14:03:26.135594+00:00`
Suite: `worldgen`; configuration: `production`; repeats: `1`

| Model | Transport | Passed | Calls | Input tok | Output tok | Reasoning tok | Cost | Time |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| `aion-labs/aion-3.0` | `prompt_json` | 3/4 | 5 | 4246 | 6552 | 3198 | $0.050898 | 145.3s |
| `aion-labs/aion-3.0` | `native_tool` | 2/4 | 5 | 4950 | 6377 | 2748 | $0.047928 | 121.7s |
| `aion-labs/aion-3.0-mini` | `prompt_json` | 3/4 | 4 | 2751 | 8831 | 7218 | $0.014289 | 175.2s |
| `aion-labs/aion-3.0-mini` | `native_tool` | 3/4 | 4 | 4102 | 8584 | 6831 | $0.014090 | 140.7s |
| `deepseek/deepseek-v4-pro` | `prompt_json` | 2/4 | 5 | 4297 | 11166 | 7205 | $0.040583 | 203.1s |
| `deepseek/deepseek-v4-pro` | `native_tool` | 3/4 | 4 | 3712 | 6847 | 2961 | $0.030286 | 249.5s |
| `google/gemini-3-flash-preview` | `prompt_json` | 4/4 | 4 | 2608 | 4977 | 3228 | $0.016235 | 41.1s |
| `google/gemini-3-flash-preview` | `native_tool` | 2/4 | 6 | 5521 | 7748 | 4907 | $0.026004 | 60.8s |
| `openai/gpt-5.6-luna` | `prompt_json` | 4/4 | 4 | 2611 | 2284 | 366 | $0.016315 | 22.1s |
| `openai/gpt-5.6-luna` | `native_tool` | 3/4 | 4 | 2631 | 2379 | 197 | $0.016905 | 23.9s |
| `qwen/qwen3.7-plus` | `prompt_json` | 4/4 | 4 | 2657 | 10730 | 8808 | $0.014585 | 285.2s |
| `qwen/qwen3.7-plus` | `native_tool` | 0/4 | 0 | 0 | 0 | 0 | $0.000000 | 2.6s |

## Scenario stability

| Model | Transport | Scenario | Passed | Stable | Repairs |
|---|---|---|---:|:---:|---:|
| `aion-labs/aion-3.0` | `prompt_json` | `worldgen.grimdark_outline_has_playable_pressure` | 1/1 | yes | 0 |
| `aion-labs/aion-3.0` | `prompt_json` | `worldgen.public_sections_preserve_outline` | 0/1 | yes | 0 |
| `aion-labs/aion-3.0` | `prompt_json` | `worldgen.secret_plot_uses_public_world` | 1/1 | yes | 1 |
| `aion-labs/aion-3.0` | `prompt_json` | `worldgen.consistency_rejects_public_secret_conflict` | 1/1 | yes | 0 |
| `aion-labs/aion-3.0` | `native_tool` | `worldgen.grimdark_outline_has_playable_pressure` | 1/1 | yes | 0 |
| `aion-labs/aion-3.0` | `native_tool` | `worldgen.public_sections_preserve_outline` | 0/1 | yes | 0 |
| `aion-labs/aion-3.0` | `native_tool` | `worldgen.secret_plot_uses_public_world` | 0/1 | yes | 1 |
| `aion-labs/aion-3.0` | `native_tool` | `worldgen.consistency_rejects_public_secret_conflict` | 1/1 | yes | 0 |
| `aion-labs/aion-3.0-mini` | `prompt_json` | `worldgen.grimdark_outline_has_playable_pressure` | 1/1 | yes | 0 |
| `aion-labs/aion-3.0-mini` | `prompt_json` | `worldgen.public_sections_preserve_outline` | 0/1 | yes | 0 |
| `aion-labs/aion-3.0-mini` | `prompt_json` | `worldgen.secret_plot_uses_public_world` | 1/1 | yes | 0 |
| `aion-labs/aion-3.0-mini` | `prompt_json` | `worldgen.consistency_rejects_public_secret_conflict` | 1/1 | yes | 0 |
| `aion-labs/aion-3.0-mini` | `native_tool` | `worldgen.grimdark_outline_has_playable_pressure` | 1/1 | yes | 0 |
| `aion-labs/aion-3.0-mini` | `native_tool` | `worldgen.public_sections_preserve_outline` | 0/1 | yes | 0 |
| `aion-labs/aion-3.0-mini` | `native_tool` | `worldgen.secret_plot_uses_public_world` | 1/1 | yes | 0 |
| `aion-labs/aion-3.0-mini` | `native_tool` | `worldgen.consistency_rejects_public_secret_conflict` | 1/1 | yes | 0 |
| `deepseek/deepseek-v4-pro` | `prompt_json` | `worldgen.grimdark_outline_has_playable_pressure` | 1/1 | yes | 0 |
| `deepseek/deepseek-v4-pro` | `prompt_json` | `worldgen.public_sections_preserve_outline` | 0/1 | yes | 0 |
| `deepseek/deepseek-v4-pro` | `prompt_json` | `worldgen.secret_plot_uses_public_world` | 0/1 | yes | 1 |
| `deepseek/deepseek-v4-pro` | `prompt_json` | `worldgen.consistency_rejects_public_secret_conflict` | 1/1 | yes | 0 |
| `deepseek/deepseek-v4-pro` | `native_tool` | `worldgen.grimdark_outline_has_playable_pressure` | 1/1 | yes | 0 |
| `deepseek/deepseek-v4-pro` | `native_tool` | `worldgen.public_sections_preserve_outline` | 0/1 | yes | 0 |
| `deepseek/deepseek-v4-pro` | `native_tool` | `worldgen.secret_plot_uses_public_world` | 1/1 | yes | 0 |
| `deepseek/deepseek-v4-pro` | `native_tool` | `worldgen.consistency_rejects_public_secret_conflict` | 1/1 | yes | 0 |
| `google/gemini-3-flash-preview` | `prompt_json` | `worldgen.grimdark_outline_has_playable_pressure` | 1/1 | yes | 0 |
| `google/gemini-3-flash-preview` | `prompt_json` | `worldgen.public_sections_preserve_outline` | 1/1 | yes | 0 |
| `google/gemini-3-flash-preview` | `prompt_json` | `worldgen.secret_plot_uses_public_world` | 1/1 | yes | 0 |
| `google/gemini-3-flash-preview` | `prompt_json` | `worldgen.consistency_rejects_public_secret_conflict` | 1/1 | yes | 0 |
| `google/gemini-3-flash-preview` | `native_tool` | `worldgen.grimdark_outline_has_playable_pressure` | 0/1 | yes | 1 |
| `google/gemini-3-flash-preview` | `native_tool` | `worldgen.public_sections_preserve_outline` | 0/1 | yes | 1 |
| `google/gemini-3-flash-preview` | `native_tool` | `worldgen.secret_plot_uses_public_world` | 1/1 | yes | 0 |
| `google/gemini-3-flash-preview` | `native_tool` | `worldgen.consistency_rejects_public_secret_conflict` | 1/1 | yes | 0 |
| `openai/gpt-5.6-luna` | `prompt_json` | `worldgen.grimdark_outline_has_playable_pressure` | 1/1 | yes | 0 |
| `openai/gpt-5.6-luna` | `prompt_json` | `worldgen.public_sections_preserve_outline` | 1/1 | yes | 0 |
| `openai/gpt-5.6-luna` | `prompt_json` | `worldgen.secret_plot_uses_public_world` | 1/1 | yes | 0 |
| `openai/gpt-5.6-luna` | `prompt_json` | `worldgen.consistency_rejects_public_secret_conflict` | 1/1 | yes | 0 |
| `openai/gpt-5.6-luna` | `native_tool` | `worldgen.grimdark_outline_has_playable_pressure` | 1/1 | yes | 0 |
| `openai/gpt-5.6-luna` | `native_tool` | `worldgen.public_sections_preserve_outline` | 0/1 | yes | 0 |
| `openai/gpt-5.6-luna` | `native_tool` | `worldgen.secret_plot_uses_public_world` | 1/1 | yes | 0 |
| `openai/gpt-5.6-luna` | `native_tool` | `worldgen.consistency_rejects_public_secret_conflict` | 1/1 | yes | 0 |
| `qwen/qwen3.7-plus` | `prompt_json` | `worldgen.grimdark_outline_has_playable_pressure` | 1/1 | yes | 0 |
| `qwen/qwen3.7-plus` | `prompt_json` | `worldgen.public_sections_preserve_outline` | 1/1 | yes | 0 |
| `qwen/qwen3.7-plus` | `prompt_json` | `worldgen.secret_plot_uses_public_world` | 1/1 | yes | 0 |
| `qwen/qwen3.7-plus` | `prompt_json` | `worldgen.consistency_rejects_public_secret_conflict` | 1/1 | yes | 0 |
| `qwen/qwen3.7-plus` | `native_tool` | `worldgen.grimdark_outline_has_playable_pressure` | 0/1 | yes | 0 |
| `qwen/qwen3.7-plus` | `native_tool` | `worldgen.public_sections_preserve_outline` | 0/1 | yes | 0 |
| `qwen/qwen3.7-plus` | `native_tool` | `worldgen.secret_plot_uses_public_world` | 0/1 | yes | 0 |
| `qwen/qwen3.7-plus` | `native_tool` | `worldgen.consistency_rejects_public_secret_conflict` | 0/1 | yes | 0 |

## Attempt results

- **PASS** `aion-labs/aion-3.0` / `prompt_json` / `worldgen.grimdark_outline_has_playable_pressure` attempt 1 — 38.0s, $0.012795, checks: `{"premise_matches_brief": true, "multiple_locations": true, "multiple_factions": true, "multiple_tensions": true, "unique_location_ids": true}`
- **FAIL** `aion-labs/aion-3.0` / `prompt_json` / `worldgen.public_sections_preserve_outline` attempt 1 — 40.3s, $0.013164, checks: `{"preserves_location_ids": true, "preserves_factions": false, "descriptions_are_substantial": true}`
- **PASS** `aion-labs/aion-3.0` / `prompt_json` / `worldgen.secret_plot_uses_public_world` attempt 1 — 53.5s, $0.018684, checks: `{"substantial_plot": true, "uses_named_world_element": true, "not_generic_cult_only": true}`
- **PASS** `aion-labs/aion-3.0` / `prompt_json` / `worldgen.consistency_rejects_public_secret_conflict` attempt 1 — 13.6s, $0.006255, checks: `{"rejects_contradiction": true, "names_issue": true}`
- **PASS** `aion-labs/aion-3.0` / `native_tool` / `worldgen.grimdark_outline_has_playable_pressure` attempt 1 — 36.9s, $0.011529, checks: `{"premise_matches_brief": true, "multiple_locations": true, "multiple_factions": true, "multiple_tensions": true, "unique_location_ids": true}`
- **FAIL** `aion-labs/aion-3.0` / `native_tool` / `worldgen.public_sections_preserve_outline` attempt 1 — 26.8s, $0.009435, checks: `{"preserves_location_ids": true, "preserves_factions": false, "descriptions_are_substantial": true}`
- **FAIL** `aion-labs/aion-3.0` / `native_tool` / `worldgen.secret_plot_uses_public_world` attempt 1 — 46.2s, $0.021015, checks: `{"pipeline_completed": false}`
  Error: `PipelineValidationError("pipeline output invalid after one repair: 1 validation error for WorldSecretSection\nsecret_plot\n  String should have at most 4000 characters [type=string_too_long, input_value='SECRET PLOT: THE HEALING...trols it at the climax.', input_type=str]\n    For further information visit https://errors.pydantic.dev/2.13/v/string_too_long")`
- **PASS** `aion-labs/aion-3.0` / `native_tool` / `worldgen.consistency_rejects_public_secret_conflict` attempt 1 — 11.8s, $0.005949, checks: `{"rejects_contradiction": true, "names_issue": true}`
- **PASS** `aion-labs/aion-3.0-mini` / `prompt_json` / `worldgen.grimdark_outline_has_playable_pressure` attempt 1 — 49.8s, $0.004138, checks: `{"premise_matches_brief": true, "multiple_locations": true, "multiple_factions": true, "multiple_tensions": true, "unique_location_ids": true}`
- **FAIL** `aion-labs/aion-3.0-mini` / `prompt_json` / `worldgen.public_sections_preserve_outline` attempt 1 — 85.3s, $0.006586, checks: `{"preserves_location_ids": false, "preserves_factions": false, "descriptions_are_substantial": true}`
- **PASS** `aion-labs/aion-3.0-mini` / `prompt_json` / `worldgen.secret_plot_uses_public_world` attempt 1 — 31.5s, $0.002693, checks: `{"substantial_plot": true, "uses_named_world_element": true, "not_generic_cult_only": true}`
- **PASS** `aion-labs/aion-3.0-mini` / `prompt_json` / `worldgen.consistency_rejects_public_secret_conflict` attempt 1 — 8.7s, $0.000872, checks: `{"rejects_contradiction": true, "names_issue": true}`
- **PASS** `aion-labs/aion-3.0-mini` / `native_tool` / `worldgen.grimdark_outline_has_playable_pressure` attempt 1 — 34.0s, $0.003717, checks: `{"premise_matches_brief": true, "multiple_locations": true, "multiple_factions": true, "multiple_tensions": true, "unique_location_ids": true}`
- **FAIL** `aion-labs/aion-3.0-mini` / `native_tool` / `worldgen.public_sections_preserve_outline` attempt 1 — 35.3s, $0.003851, checks: `{"preserves_location_ids": true, "preserves_factions": false, "descriptions_are_substantial": true}`
- **PASS** `aion-labs/aion-3.0-mini` / `native_tool` / `worldgen.secret_plot_uses_public_world` attempt 1 — 61.7s, $0.005442, checks: `{"substantial_plot": true, "uses_named_world_element": true, "not_generic_cult_only": true}`
- **PASS** `aion-labs/aion-3.0-mini` / `native_tool` / `worldgen.consistency_rejects_public_secret_conflict` attempt 1 — 9.7s, $0.001081, checks: `{"rejects_contradiction": true, "names_issue": true}`
- **PASS** `deepseek/deepseek-v4-pro` / `prompt_json` / `worldgen.grimdark_outline_has_playable_pressure` attempt 1 — 51.3s, $0.006116, checks: `{"premise_matches_brief": true, "multiple_locations": true, "multiple_factions": true, "multiple_tensions": true, "unique_location_ids": true}`
- **FAIL** `deepseek/deepseek-v4-pro` / `prompt_json` / `worldgen.public_sections_preserve_outline` attempt 1 — 42.1s, $0.008968, checks: `{"preserves_location_ids": false, "preserves_factions": false, "descriptions_are_substantial": true}`
- **FAIL** `deepseek/deepseek-v4-pro` / `prompt_json` / `worldgen.secret_plot_uses_public_world` attempt 1 — 96.5s, $0.023952, checks: `{"pipeline_completed": false}`
  Error: `PipelineValidationError("pipeline output invalid after one repair: 1 validation error for WorldSecretSection\n  Invalid JSON: EOF while parsing a value at line 1 column 0 [type=json_invalid, input_value='', input_type=str]\n    For further information visit https://errors.pydantic.dev/2.13/v/json_invalid")`
- **PASS** `deepseek/deepseek-v4-pro` / `prompt_json` / `worldgen.consistency_rejects_public_secret_conflict` attempt 1 — 13.2s, $0.001546, checks: `{"rejects_contradiction": true, "names_issue": true}`
- **PASS** `deepseek/deepseek-v4-pro` / `native_tool` / `worldgen.grimdark_outline_has_playable_pressure` attempt 1 — 86.1s, $0.009359, checks: `{"premise_matches_brief": true, "multiple_locations": true, "multiple_factions": true, "multiple_tensions": true, "unique_location_ids": true}`
- **FAIL** `deepseek/deepseek-v4-pro` / `native_tool` / `worldgen.public_sections_preserve_outline` attempt 1 — 142.2s, $0.015220, checks: `{"preserves_location_ids": false, "preserves_factions": false, "descriptions_are_substantial": true}`
- **PASS** `deepseek/deepseek-v4-pro` / `native_tool` / `worldgen.secret_plot_uses_public_world` attempt 1 — 11.4s, $0.002763, checks: `{"substantial_plot": true, "uses_named_world_element": true, "not_generic_cult_only": true}`
- **PASS** `deepseek/deepseek-v4-pro` / `native_tool` / `worldgen.consistency_rejects_public_secret_conflict` attempt 1 — 9.8s, $0.002944, checks: `{"rejects_contradiction": true, "names_issue": true}`
- **PASS** `google/gemini-3-flash-preview` / `prompt_json` / `worldgen.grimdark_outline_has_playable_pressure` attempt 1 — 10.3s, $0.004319, checks: `{"premise_matches_brief": true, "multiple_locations": true, "multiple_factions": true, "multiple_tensions": true, "unique_location_ids": true}`
- **PASS** `google/gemini-3-flash-preview` / `prompt_json` / `worldgen.public_sections_preserve_outline` attempt 1 — 11.5s, $0.004409, checks: `{"preserves_location_ids": true, "preserves_factions": true, "descriptions_are_substantial": true}`
- **PASS** `google/gemini-3-flash-preview` / `prompt_json` / `worldgen.secret_plot_uses_public_world` attempt 1 — 10.4s, $0.004103, checks: `{"substantial_plot": true, "uses_named_world_element": true, "not_generic_cult_only": true}`
- **PASS** `google/gemini-3-flash-preview` / `prompt_json` / `worldgen.consistency_rejects_public_secret_conflict` attempt 1 — 8.9s, $0.003404, checks: `{"rejects_contradiction": true, "names_issue": true}`
- **FAIL** `google/gemini-3-flash-preview` / `native_tool` / `worldgen.grimdark_outline_has_playable_pressure` attempt 1 — 21.9s, $0.009538, checks: `{"pipeline_completed": false}`
  Error: `PipelineValidationError('pipeline output invalid after one repair: 5 validation errors for WorldOutline\nlocation_seeds.0\n  Input should be an object [type=model_type, input_value=None, input_type=NoneType]\n    For further information visit https://errors.pydantic.dev/2.13/v/model_type\nlocation_seeds.1\n  Input should be an object [type=model_type, input_value=None, input_type=NoneType]\n    For further information visit https://errors.pydantic.dev/2.13/v/model_type\nlocation_seeds.2\n  Input should be an object [type=model_type, input_value=None, input_type=NoneType]\n    For further information visit https://errors.pydantic.dev/2.13/v/model_type\nlocation_seeds.3\n  Input should be an object [type=model_type, input_value=None, input_type=NoneType]\n    For further information visit https://errors.pydantic.dev/2.13/v/model_type\nlocation_seeds.4\n  Input should be an object [type=model_type, input_value=None, input_type=NoneType]\n    For further information visit https://errors.pydantic.dev/2.13/v/model_type')`
- **FAIL** `google/gemini-3-flash-preview` / `native_tool` / `worldgen.public_sections_preserve_outline` attempt 1 — 19.4s, $0.009266, checks: `{"pipeline_completed": false}`
  Error: `PipelineValidationError('pipeline output invalid after one repair: 2 validation errors for WorldPublicSections\nlocations.0\n  Input should be an object [type=model_type, input_value=None, input_type=NoneType]\n    For further information visit https://errors.pydantic.dev/2.13/v/model_type\nlocations.1\n  Input should be an object [type=model_type, input_value=None, input_type=NoneType]\n    For further information visit https://errors.pydantic.dev/2.13/v/model_type')`
- **PASS** `google/gemini-3-flash-preview` / `native_tool` / `worldgen.secret_plot_uses_public_world` attempt 1 — 10.6s, $0.003870, checks: `{"substantial_plot": true, "uses_named_world_element": true, "not_generic_cult_only": true}`
- **PASS** `google/gemini-3-flash-preview` / `native_tool` / `worldgen.consistency_rejects_public_secret_conflict` attempt 1 — 9.0s, $0.003330, checks: `{"rejects_contradiction": true, "names_issue": true}`
- **PASS** `openai/gpt-5.6-luna` / `prompt_json` / `worldgen.grimdark_outline_has_playable_pressure` attempt 1 — 9.2s, $0.007134, checks: `{"premise_matches_brief": true, "multiple_locations": true, "multiple_factions": true, "multiple_tensions": true, "unique_location_ids": true}`
- **PASS** `openai/gpt-5.6-luna` / `prompt_json` / `worldgen.public_sections_preserve_outline` attempt 1 — 3.7s, $0.003105, checks: `{"preserves_location_ids": true, "preserves_factions": true, "descriptions_are_substantial": true}`
- **PASS** `openai/gpt-5.6-luna` / `prompt_json` / `worldgen.secret_plot_uses_public_world` attempt 1 — 6.3s, $0.004797, checks: `{"substantial_plot": true, "uses_named_world_element": true, "not_generic_cult_only": true}`
- **PASS** `openai/gpt-5.6-luna` / `prompt_json` / `worldgen.consistency_rejects_public_secret_conflict` attempt 1 — 2.8s, $0.001279, checks: `{"rejects_contradiction": true, "names_issue": true}`
- **PASS** `openai/gpt-5.6-luna` / `native_tool` / `worldgen.grimdark_outline_has_playable_pressure` attempt 1 — 9.4s, $0.006939, checks: `{"premise_matches_brief": true, "multiple_locations": true, "multiple_factions": true, "multiple_tensions": true, "unique_location_ids": true}`
- **FAIL** `openai/gpt-5.6-luna` / `native_tool` / `worldgen.public_sections_preserve_outline` attempt 1 — 5.0s, $0.003892, checks: `{"preserves_location_ids": true, "preserves_factions": false, "descriptions_are_substantial": true}`
- **PASS** `openai/gpt-5.6-luna` / `native_tool` / `worldgen.secret_plot_uses_public_world` attempt 1 — 6.3s, $0.004451, checks: `{"substantial_plot": true, "uses_named_world_element": true, "not_generic_cult_only": true}`
- **PASS** `openai/gpt-5.6-luna` / `native_tool` / `worldgen.consistency_rejects_public_secret_conflict` attempt 1 — 3.2s, $0.001623, checks: `{"rejects_contradiction": true, "names_issue": true}`
- **PASS** `qwen/qwen3.7-plus` / `prompt_json` / `worldgen.grimdark_outline_has_playable_pressure` attempt 1 — 74.9s, $0.005296, checks: `{"premise_matches_brief": true, "multiple_locations": true, "multiple_factions": true, "multiple_tensions": true, "unique_location_ids": true}`
- **PASS** `qwen/qwen3.7-plus` / `prompt_json` / `worldgen.public_sections_preserve_outline` attempt 1 — 42.7s, $0.003117, checks: `{"preserves_location_ids": true, "preserves_factions": true, "descriptions_are_substantial": true}`
- **PASS** `qwen/qwen3.7-plus` / `prompt_json` / `worldgen.secret_plot_uses_public_world` attempt 1 — 118.9s, $0.003834, checks: `{"substantial_plot": true, "uses_named_world_element": true, "not_generic_cult_only": true}`
- **PASS** `qwen/qwen3.7-plus` / `prompt_json` / `worldgen.consistency_rejects_public_secret_conflict` attempt 1 — 48.7s, $0.002338, checks: `{"rejects_contradiction": true, "names_issue": true}`
- **FAIL** `qwen/qwen3.7-plus` / `native_tool` / `worldgen.grimdark_outline_has_playable_pressure` attempt 1 — 1.0s, $0.000000, checks: `{"pipeline_completed": false}`
  Error: `LLMBadRequestError('litellm.BadRequestError: OpenrouterException - {"error":{"message":"Provider returned error","code":400,"metadata":{"raw":"{\\"error\\":{\\"message\\":\\"<400> InternalError.Algo.InvalidParameter: The tool_choice parameter does not support being set to required or object in thinking mode\\",\\"type\\":\\"invalid_request_error\\",\\"param\\":null,\\"code\\":\\"invalid_parameter_error\\"},\\"id\\":\\"chatcmpl-c667c41f-e01b-90a0-bd5f-c1ace499b43e\\",\\"request_id\\":\\"c667c41f-e01b-90a0-bd5f-c1ace499b43e\\"}","provider_name":"Alibaba","is_byok":false}},"user_id":"user_3B3M0FhyEZrHFF57G8GKQsxR8gJ"}')`
- **FAIL** `qwen/qwen3.7-plus` / `native_tool` / `worldgen.public_sections_preserve_outline` attempt 1 — 0.6s, $0.000000, checks: `{"pipeline_completed": false}`
  Error: `LLMBadRequestError('litellm.BadRequestError: OpenrouterException - {"error":{"message":"Provider returned error","code":400,"metadata":{"raw":"{\\"error\\":{\\"message\\":\\"<400> InternalError.Algo.InvalidParameter: The tool_choice parameter does not support being set to required or object in thinking mode\\",\\"type\\":\\"invalid_request_error\\",\\"param\\":null,\\"code\\":\\"invalid_parameter_error\\"},\\"id\\":\\"chatcmpl-595e9ee7-dedd-9fd6-b2fa-5cb7255fc783\\",\\"request_id\\":\\"595e9ee7-dedd-9fd6-b2fa-5cb7255fc783\\"}","provider_name":"Alibaba","is_byok":false}},"user_id":"user_3B3M0FhyEZrHFF57G8GKQsxR8gJ"}')`
- **FAIL** `qwen/qwen3.7-plus` / `native_tool` / `worldgen.secret_plot_uses_public_world` attempt 1 — 0.4s, $0.000000, checks: `{"pipeline_completed": false}`
  Error: `LLMBadRequestError('litellm.BadRequestError: OpenrouterException - {"error":{"message":"Provider returned error","code":400,"metadata":{"raw":"{\\"error\\":{\\"message\\":\\"<400> InternalError.Algo.InvalidParameter: The tool_choice parameter does not support being set to required or object in thinking mode\\",\\"type\\":\\"invalid_request_error\\",\\"param\\":null,\\"code\\":\\"invalid_parameter_error\\"},\\"id\\":\\"chatcmpl-e4013b45-d727-9c19-9aec-acf9eeeed172\\",\\"request_id\\":\\"e4013b45-d727-9c19-9aec-acf9eeeed172\\"}","provider_name":"Alibaba","is_byok":false}},"user_id":"user_3B3M0FhyEZrHFF57G8GKQsxR8gJ"}')`
- **FAIL** `qwen/qwen3.7-plus` / `native_tool` / `worldgen.consistency_rejects_public_secret_conflict` attempt 1 — 0.6s, $0.000000, checks: `{"pipeline_completed": false}`
  Error: `LLMBadRequestError('litellm.BadRequestError: OpenrouterException - {"error":{"message":"Provider returned error","code":400,"metadata":{"raw":"{\\"error\\":{\\"message\\":\\"<400> InternalError.Algo.InvalidParameter: The tool_choice parameter does not support being set to required or object in thinking mode\\",\\"type\\":\\"invalid_request_error\\",\\"param\\":null,\\"code\\":\\"invalid_parameter_error\\"},\\"id\\":\\"chatcmpl-7e50909c-9a06-9720-89d8-63a2f4a916b4\\",\\"request_id\\":\\"7e50909c-9a06-9720-89d8-63a2f4a916b4\\"}","provider_name":"Alibaba","is_byok":false}},"user_id":"user_3B3M0FhyEZrHFF57G8GKQsxR8gJ"}')`
