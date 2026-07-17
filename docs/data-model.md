# Runtime data model

## Aggregate map

```text
World (worlds)
└── Game[] (games)
    ├── Scene[] (scenes)
    │   └── PlayerLocation[] (player_locations)
    ├── Character[] (characters; unique game_id + player_id)
    ├── PendingInteraction[] (pending_interactions)
    ├── Roll[] (rolls)
    │   └── RollHelper[] (roll_helpers)
    ├── SessionActivity (session_activity)
    └── DomainEvent[] (domain_events)

DiscordChannel (channel_bindings) ──> Game
DiscordChannel ──> active WorldProject (world_projects) ──> editable World draft
InboxMessage[] ──> application ──> OutboxMessage[]
LLMCall[] ──> optional Game
```

## World

`worlds` is the reusable setting container. It stores `world_id`, title, draft/publication status,
`content_json` and an optimistic `revision`. Generated content currently contains premise, themes,
locations, factions, tensions, three to six playable pregenerated characters and a separate secret
plot. Each pregen has a validated 18-point sheet, biography, existing faction affiliations and named
relationships to other pregens. Selecting one during preparation creates the player's canonical
character without another LLM call. Pregens do not carry placement metadata: when play starts, code
creates the opening scene from the world's first location and places every registered character
together. A game references a world by `world_id`; world content is not copied into every game.

`world_projects` persists the editor independently from a Discord channel. It records the world,
optional active channel, `collecting|review` stage, cumulative brief, normalized settings,
per-setting provenance (`player|default`) and revision. Exiting clears only the active-channel
lease: the draft remains selectable and can later be resumed with all inputs intact. Explicit
approval removes the editor project and publishes the world. `world_workspaces` is retained only as
a one-time migration source and is emptied after import.

World management is a state machine, not a fresh intent guess on every message:

```text
world selection --select approved world--> game preparation
world selection --create/long brief-----> collecting (temporary project id allocated)
collecting -------ordinary message------> collecting (merge through world-intake)
collecting -------explicit generate-----> review (run worldgen once)
review -----------ordinary message------> collecting (draft becomes dirty; no regeneration)
review -----------explicit approve------> world selection (publish to catalogue)
collecting/review--explicit exit---------> world selection (pause and retain draft)
world selection --select retained draft-> prior collecting/review state
```

The workspace branch runs before generic intent classification and before legacy application
commands. Before a revision is assumed, branch-local deterministic information requests are
checked: world catalogue and current world settings never call an LLM and never mutate the draft.
Only short whole-message controls or anchored patterns can change its state. Every other message
belongs to the active project and goes directly to world intake; words such as
`перегенерировать` inside a longer brief have no control meaning. Long free-form messages in world
selection bypass deterministic gates and start world intake directly. Thus a classifier cannot
replace an active workspace or trigger generation, and a revision after review cannot spend another
worldgen call without a separate explicit generation message.

Preparation and play use the same lifecycle-first rule. Commands and inferred intents cannot
override the persisted branch. Preparation permits character selection/creation, character and game
status, game configuration and explicit start. It rejects scene perception, actions, advancement and
other play-only operations. The first scene does not exist during preparation: starting the game
atomically creates it from the world's first location and places every registered character. Active
play permits scene/action/status/help/advancement operations and rejects setup operations. Explicit
rules questions are the sole informational branch available across every mode. Other deterministic
information requests remain limited to their lifecycle branch; for example, the world catalogue is
available in world selection and while editing a world draft.

World revisions use a compact delta contract. The reasoning model sees current structured settings
and the fresh player message, but does not reproduce the accumulated world brief. Code appends the
normalized revision with explicit later-wins precedence and merges only the fields named by the
typed result. This prevents both context amplification and accidental loss of prior premises.

The normalized settings contract always exposes genre, tone, scale, player role, themes and content
constraints; narrative style, perspective, detail and locale; narrator-rights, reserve-recovery and
progression policies; and pregenerated-character count/concepts. Missing fields receive explicit
defaults and remain labelled `default`. On generation, narrative and game-policy defaults are copied
into `worlds.content_json.game_defaults`; selecting that world applies the mechanical policies to
the new game, while narrative defaults are included in each narration session brief.

Initial generation is a two-model workflow: a creative model produces one loose module pitch, then
the structuring model checks consistency and returns the complete typed draft. Starting-sheet rules
and cross-entity identities/references are validated while parsing that draft, so a malformed sheet
or dangling reference participates in the bounded structuring repair and model fallback without
restarting the creative stage or the entire inbox message. Code repeats the domain invariants as a
defensive gate before `content_json` and the world revision are committed. If typed generation is
exhausted, the active workspace and any prior review draft remain unchanged.

## Game and session state

`games` stores the session identity and policy:

- lifecycle: `draft -> preparing -> active -> paused/finished`;
- whether progression is enabled;
- narrator-rights level;
- locale and narrative Discord channel;
- optimistic revision.

`channel_bindings` maps a Discord channel to a game. `session_activity` holds the activity clock and
credited XP intervals. These are deterministic application state, not LLM memory.

`monitored_channels` is the independent Discord ingress allowlist. A row means ordinary player
messages from that channel are accepted even without mentioning the bot. Adding or removing this
row never creates, deletes or detaches a game in `channel_bindings`.

## Scene state

Each `scenes` row belongs to one game and stores a title, `state_json` and revision. The current
projection primarily exposes persistent scene facts and participants. `player_locations` maps each
player in a game to one scene, allowing different scenes to advance independently when they do not
touch the same entities.

Scene changes proposed by an LLM are minimal add/remove fact patches. Application code checks the
expected scene revision and applies the patch transactionally.

## Character state

`characters` is unique per `(game_id, player_id)` and contains:

- identity, owner and biography;
- `sheet_json`: name, traits, levels, aspects, flags and current/maximum reserve;
- `conditions_json`: textual conditions with their sources;
- `plot_items_json`: named possessions or plot-relevant items with descriptions;
- earned and spent XP;
- optimistic revision.

The domain representation is `CharacterState -> CharacterSheet -> Trait/Flag`. Available XP is
derived as `experience_earned - experience_spent`. Numeric and structural invariants are validated
in code. Action context exposes exact trait/aspect/flag names, reserve, conditions and plot items;
the model cannot invent missing equipment.

## Pending actions and immutable results

`pending_interactions` stores restart-safe player decisions such as pool confirmation, player
narration and clarification. At most one interaction can be open for a player in a game. Payloads
carry revision-bound proposals rather than mutable model conversations.

Confirmed results are immutable rows in `rolls`, keyed by both interaction and Discord confirmation
event. Dice, hits, difficulty, narrator rights and reserve-after are stored for audit and safe resume.

Per-game rules are stored separately from mutable scene state. `game_rules` records the selected
`reserve_recovery_mode` (`safe_rest`, `roleplay_award`, or `both`). There is no separate human GM
identity.
The system game-master pipeline adjudicates recovery from canonical scene/outcome context, and
deterministic code writes a domain event containing its evidence and the exact before/after values.
Recovery never relies on an inferred session boundary or a direct player request.
`roll_helpers` records contributing characters without modifying the immutable result.

## Event and delivery boundaries

`inbox_messages` is the durable Discord ingress queue with pending/processing/processed/failed
states. `outbox_messages` is the idempotent transactional delivery queue; `embed_json` optionally
stores the deterministic Discord rich-embed presentation alongside ordinary message content.
`domain_events` records
committed facts with unique causation ids. `llm_calls` records model role, tokens, cache usage,
latency, cost and success, optionally associated with a game and linked to available
trace/channel/event keys. `stage_spans` stores the parent-linked timing tree for application, DB,
context, pipeline, retry/repair, domain-gate and delivery work.
Every call also records a SHA-256 `prompt_fingerprint` over the actual cached system text, strict
output schema, terminal-tool name and selected transport. This provides content-addressed prompt
audit without maintaining prompt version numbers.

The canonical state is the current aggregate rows plus their revisions. Domain events provide audit
and bounded recent context; they are not replayed as an unbounded prompt history.

## What an LLM sees

Each pipeline manifest selects a minimal projection, fixed rule fragments and small limits for
recent events/messages. Secret or unrelated fields are omitted. The prompt is therefore a task-local
view of canonical state, never a serialized database or a long-lived agent memory.

When an acting player has a scene location, recent chat is limited to authors currently in that
same scene, even if several independent scenes share one Discord channel. Preparation workflows
without a player location do not receive unrelated channel history.
