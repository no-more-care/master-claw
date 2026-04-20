# SKILL: Model Management

Manages the LLM model used by microClaw (global — affects both game master and operator). Operator-only (Telegram).

## When to use
- Operator asks what model is running
- Operator wants to switch to a different model
- Operator wants to try a specific LLM (e.g. cheaper, faster, smarter)
- Operator wants to register a new custom model as a preset

## Who can use
**Operator only** (Telegram soul). Never execute model management from Discord game channels.

## Commands

### Show current model
```bash
python3 /root/.microclaw/scripts/manage_models.py current
```

### List current + all presets
```bash
python3 /root/.microclaw/scripts/manage_models.py list
```
Shows current config, built-in presets, and user-added presets.

### Switch to a preset
```bash
python3 /root/.microclaw/scripts/manage_models.py set <preset_name>
```
Changes provider, model, and base_url in one step. **Automatically restarts microClaw** (3 sec delayed).

Common presets shipped by default:
- `grok-fast` — x-ai/grok-4.1-fast (cheap, current default)
- `grok-full` — x-ai/grok-4.1 (smarter, more expensive)
- `haiku` — anthropic/claude-haiku-4-5 (fast, good instruction following)
- `sonnet` — anthropic/claude-sonnet-4-5 (strong reasoning, pricier)
- `gpt-mini` — openai/gpt-5-mini
- `gemini-flash` — google/gemini-2.5-flash (cheap, good with Russian)
- `deepseek` — deepseek/deepseek-v3.2 (very cheap)

All built-in presets go through OpenRouter and use the existing API key.

### Change only the model ID (keep provider)
```bash
python3 /root/.microclaw/scripts/manage_models.py set-model <model_id>
```
Useful for trying a specific model without creating a preset. Keeps current provider/base_url/api_key.

### Add a custom preset (uses current api_key)
```bash
python3 /root/.microclaw/scripts/manage_models.py add <name> <provider> <model> <base_url>
```
Example: `add qwen-max openrouter qwen/qwen-max https://openrouter.ai/api/v1`

### Add a custom preset with its own API key (for switching providers)
```bash
python3 /root/.microclaw/scripts/manage_models.py add-full <name> <provider> <model> <base_url> <api_key>
```
Use this when the preset points to a different API (e.g. Anthropic direct, OpenAI direct) with a separate key.

### Remove a user preset
```bash
python3 /root/.microclaw/scripts/manage_models.py remove <name>
```
Built-in presets cannot be removed (but can be overridden by adding a user preset with the same name).

## Procedure

1. Operator asks about models or requests a switch
2. Run the relevant command
3. **IMMEDIATELY report the result to the operator** — the script schedules a delayed restart (3 seconds), so you MUST send your response before the restart happens. Do not do any other tool calls between running the script and responding.
4. After restart, the new model is active for all future requests

## Notes
- The `set` and `set-model` commands restart microClaw automatically; `add`/`remove` do not (no restart needed for preset edits)
- Config backups are created before every change in `/root/microclaw.config.backups/`
- User presets are stored in `/root/.microclaw/model_presets.yaml`
- Model changes affect BOTH the Discord game master and the Telegram operator
