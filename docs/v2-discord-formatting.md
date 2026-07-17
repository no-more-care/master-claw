# MasterClaw v2 — Discord output formatting

The v2 output layer reuses the useful parts of the legacy locale templates, but renders them in code instead of asking an LLM to reproduce formatting. All game-bound deterministic responses use the game's `ru`/`en` locale through the central `app/i18n.py` catalogue: commands, validation prompts, character/game/XP status, pool confirmation, roll results, narrator-rights handoff and terminal failure notices. Before a channel is bound to a game, the management UI defaults to Russian. The canonical source material reviewed for this layer is:

- `locales/{ru,en}/templates/game_response.md`;
- `locales/{ru,en}/templates/dice_pool.md`;
- `locales/{ru,en}/templates/character_display.md`;
- `locales/{ru,en}/templates/prompts.md`;
- `prompts/narrative/style.md` for prose constraints.

The mechanical game-channel result keeps the legacy compact structure: dice/pool, difficulty, narrator rights, reserve, and a clear next action. Long prose is delivered to the separate narrative channel. Formatting uses Discord-supported message Markdown: mentions, bold text, headings where useful, inline code and fenced code blocks. Markdown tables are not emitted because Discord does not render them as tables.

Every game-channel response begins with a deterministic Markdown status card. It uses native
Discord headings, bold labels, italics for provenance, quotes for prose and horizontal separators.
Box-drawing vertical borders are deliberately avoided because proportional fonts make their
junctions look misaligned. The card is delivered as a rich embed with a mode color: blurple for
world management, amber for preparation and green for active play. The conversational result stays
in ordinary message content. The card is rendered from SQLite and never consumes model tokens:

- world setup: stage and the complete editable contract grouped into world/plot, narrative,
  game-policy and pregenerated-character settings. Every value is marked as player-supplied or a
  default, including visible placeholders for omitted character concepts; after generation it also
  shows the public premise, themes, key locations, factions and tensions;
- preparation: world/player brief, public locations and factions, pregenerated-character options,
  registered players and readiness, plus the locked game settings;
- play: world, most recently active scene, a short location description and compact participant
  condition/reserve/location status.

The body beneath the card contains only the result or next conversational question. Internal
world, game, scene and character ids are omitted from normal conversational output; slash-command
diagnostics may still expose them for operators and tests.

Responses do not mention the author by default. Status and scene descriptions use character names,
not Discord account mentions, so routine output does not create notification highlights. A literal
mention is reserved for flows that explicitly require a particular player's next action, such as a
help/confirmation handoff.

All output passes through `split_discord_message`. The splitter targets 1800 characters—below Discord's 2000-character API limit—prefers blank lines, then line boundaries, then word boundaries. This makes headings and paragraphs natural message boundaries and leaves room for mentions or future decorations. Game-batch output and narrative-channel deliveries are split before they enter the transactional outbox, so every chunk has its own stable idempotency key and can be retried independently.

While a queued channel batch is being processed, the bot holds Discord's typing indicator. If an
event is still processing after 15 seconds, one idempotent, non-mention notice tells players that
the request is retained and may take one or two minutes. Expected validation failures return an
actionable message from the relevant domain gate. Unexpected transient failures are retried from
the durable inbox; deterministic validation failures are terminal after their bounded local repair.

LLM calls are stored in `llm_calls`, associated with `game_id` where a game is bound. Each record contains role, model, prompt fingerprint, response id, input/output/cache/reasoning tokens, response latency, provider cost when available, success flag and sanitized error. Metrics are retained only for later analysis and are not exposed through Discord commands.
