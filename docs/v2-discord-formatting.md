# MasterClaw v2 — Discord output formatting

The v2 output layer reuses the useful parts of the legacy locale templates, but renders them in code instead of asking an LLM to reproduce formatting. The canonical source material reviewed for this layer is:

- `locales/{ru,en}/templates/game_response.md`;
- `locales/{ru,en}/templates/dice_pool.md`;
- `locales/{ru,en}/templates/character_display.md`;
- `locales/{ru,en}/templates/prompts.md`;
- `prompts/narrative/style.md` for prose constraints.

The mechanical game-channel result keeps the legacy compact structure: dice/pool, difficulty, narrator rights, reserve, and a clear next action. Long prose is delivered to the separate narrative channel. Formatting uses Discord-supported message Markdown: mentions, bold text, headings where useful, inline code and fenced code blocks. Markdown tables are not emitted because Discord does not render them as tables.

All output passes through `split_discord_message`. The splitter targets 1800 characters—below Discord's 2000-character API limit—prefers blank lines, then line boundaries, then word boundaries. This makes headings and paragraphs natural message boundaries and leaves room for mentions or future decorations. Game-batch output and narrative-channel deliveries are split before they enter the transactional outbox, so every chunk has its own stable idempotency key and can be retried independently.

While a queued channel batch is being processed, the bot holds Discord's typing indicator. Expected validation failures return an actionable message from the relevant domain gate. Unexpected failures are retried from the durable inbox; after retries are exhausted, one idempotent, user-friendly notice is queued to the game channel and the original event remains in the dead-letter queue for recovery.

LLM calls are stored in `llm_calls`, associated with `game_id` where a game is bound. Each record contains role, model, response id, input/output/cache/reasoning tokens, response latency, provider cost when available, success flag and sanitized error. Metrics are retained only for later analysis and are not exposed through Discord commands.
