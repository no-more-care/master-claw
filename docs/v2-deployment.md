# MasterClaw v2 — Linux droplet deployment

Status: deployment runbook. Complete the acceptance checklist below before using the bot for a campaign.

## Chosen topology

- Python 3.12 (the supported OpenHands/Discord dependency contract is not yet certified on 3.13);
- one isolated Linux droplet;
- Docker Engine + Compose plugin;
- one MasterClaw application container;
- one persistent Docker volume containing SQLite database and WAL;
- a separate persistent Docker volume containing timestamped backup generations;
- `restart: unless-stopped`, optionally supervised by a small systemd unit invoking Compose;
- Discord bot API for game and narrative channels;
- OpenRouter as the only LLM provider.

Multiple application replicas are explicitly unsupported while SQLite and the single-channel worker model are in use.
`masterclaw serve` enforces this locally with an operating-system lock next to the configured
database. A second daemon exits instead of connecting another Discord client. Outbox messages are
also claimed atomically before delivery, preventing duplicate sends during accidental overlap;
abandoned claims become eligible again after two minutes.

The repository includes `deploy/masterclaw.service` plus a daily backup service/timer.
Install them under `/etc/systemd/system`, keep the checkout at `/opt/masterclaw`, then run:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now masterclaw.service
sudo systemctl enable --now masterclaw-backup.timer
```

## Discord requirements

Create a Discord application and bot, enable Message Content Intent, invite it with permission to
view/send messages in the game and narrative channels, and keep the token outside the repository.
Players communicate in ordinary language without tagging the bot. Slash messages are optional
shortcuts handled by the same durable inbox; native application-command registration is not
required.

The bot ignores ordinary messages in every channel until explicitly enabled. In the chosen game
channel, mention the bot and send `включи этот канал для игры` (for example,
`@MasterClaw включи этот канал для игры`). After confirmation, all non-bot messages in that channel
are monitored without requiring a mention. To stop monitoring while preserving the game binding,
mention it with `выключи этот канал для игры`. The allowlist is stored in SQLite, not `.env`, and
survives restarts.

A Discord thread inherits monitoring and the game binding from its enabled parent channel. A
conflicting thread binding is rejected instead of silently crossing sessions. Narrative output is
accepted only for a cached channel in the same Discord server and cannot be shared by two
games or routed to a DM. Attachments are not interpreted: the bot sends an explicit localized
unsupported notice and processes only the message's text, while reply metadata is retained.

The first world description does not create a game. It opens a persistent input card, clearly
marking player-supplied values and defaults. Players explicitly request draft generation, review
and revise the public result, then approve it into the world catalogue. The bot shows the catalogue
without an LLM; an explicit title or ordinal selection starts preparation. The same channel shows a
code-rendered status card above every response; these cards do not call an LLM and never expose
internal ids during normal conversation.

## Configuration

Copy `.env.example` to `.env` and set:

- `MASTERCLAW_DISCORD_TOKEN`;
- `MASTERCLAW_OPENROUTER_API_KEY`;
- four OpenRouter model ids for `STATE`, `REASONING`, `NARRATIVE` and `WORLDGEN` roles;
- per-role `REASONING_EFFORT` and `OUTPUT_TRANSPORT` (`prompt_json` or `native_tool`);
- optional database, prompts and debounce settings;
- optional `MASTERCLAW_LLM_MAX_CONCURRENCY` (default `4`) to bound shared provider calls.

The daemon does not dynamically route models by price or availability. Each pipeline selects one configured role.
`.env.example` uses Nex Mini for state, Nex Pro for reasoning and Luna for narrative and the interim
worldgen baseline. Worldgen remains independently configurable and defaults to `prompt_json` so
narratively strong models with unreliable native tooling can still satisfy strict schemas. Transport is
an explicit capability setting. For `prompt_json`, the adapter requests the provider's strict JSON
schema response format when the selected route supports it. If that route deterministically rejects
`response_format`, the adapter retries once without that option while retaining the exact same schema
in the prompt; it never switches to native tools, another role or another model implicitly.

## Commands

```bash
docker compose build
docker compose run --rm masterclaw masterclaw doctor
docker compose run --rm masterclaw masterclaw model-smoke
docker compose run --rm masterclaw masterclaw backup --database /app/data/masterclaw.sqlite3 --output /app/backups/manual.sqlite3
docker compose up -d
docker compose logs -f masterclaw
```

`doctor` validates settings, initializes/opens SQLite, loads the prompt manifest and constructs an
OpenHands client for every configured role. It imports and validates the installed SDK contract but
does not make an LLM request or spend tokens.
`model-smoke` does spend provider tokens. It exercises state routing; action, compound-request,
consequence and world-intake reasoning; narrative output; and both the creative and typed-structuring
stages of world generation. In addition to schema validation, it checks the expected routing branch,
preservation of every explicitly labelled world setting, absence of invented state for a passive
look, compound-part order and the requested pregen count. The command stops on the first failed
contract and is intended for deployment acceptance, not a frequent healthcheck.
The Compose healthcheck uses `masterclaw healthcheck` instead. It requires an existing database,
opens it with SQLite `mode=ro`, verifies required tables, the exact supported application schema
version and `quick_check`, and never initializes or migrates it. Startup creates a fresh current
schema or applies the supported migration to schema v10; it fails closed on a newer version. Take an
online backup before deploying a version that can migrate the database.

`mode=ro` describes the SQL connection, not the filesystem mount. A live WAL database may still
need its directory to permit SQLite's transient `-shm` coordination file, so keep the data volume
mounted read-write for `healthcheck` and `backup`; adding Docker `:ro` can fail with `unable to open
database file`. These commands do not perform application mutations or migrations. A pre/post
database checksum is the stricter acceptance check when proving that a backup smoke did not change
the source file.

## Persistence and recovery

SQLite runs in WAL mode. Discord events are persisted before processing. A clean or crashed restart
returns any `processing` inbox records to `pending`. Marking an inbox event processed and inserting
its outbox records share one transaction. Domain handlers may commit canonical changes before that
transaction. Since schema v8, a typed model decision journals its first
validated output by event, pipeline key, game scope, output-schema fingerprint and, for
revision-bound decisions, a fingerprint of the semantic request and CAS inputs. A later attempt
must validate and reuse that output, and fails closed if those bound inputs changed. After the
handler returns, its exact text, deliveries and response metadata are also journaled before inbox
completion. Together with causation ids, immutable
results and revision-aware replay guards, these checkpoints keep a recovered event on the same
branch and reproduce the same response without reapplying the change. Discord delivery remains a
separate boundary.

Outbox delivery is **at least once**. Atomic claims prevent two daemon workers from sending the
same row concurrently, and every retry reuses a stable Discord nonce plus the original source-event
reference. Discord acceptance and the subsequent SQLite `delivered_at` update cannot be one atomic
operation: a crash or database failure after `send` succeeds can resend that row. The stable nonce
reduces ambiguity and preserves retry identity, but the supported Discord client does not provide an
enforced deduplication acknowledgement, so this narrow duplicate window remains. Inbox/domain
idempotency still prevents a duplicate roll, state transition, or assistant-history turn.

Backups must capture the database consistently. `masterclaw backup` uses SQLite's online backup API
and is safe with WAL mode; it copies the live database without first initializing or migrating it.
The live data volume must nevertheless remain writable for WAL coordination as described above.
Do not copy only the main `.sqlite3` file while the daemon is active. The systemd timer writes a
new timestamped generation to the separate `masterclaw-backups` volume on every run instead of
overwriting `latest.sqlite3`. Periodically export verified generations to another host or object
store; a second volume protects against loss of the live-data volume, not loss of the Docker host.
Retention and off-host replication remain operator policy.

## Acceptance checklist

- `docker compose build` succeeds on the target Linux host;
- `masterclaw doctor` initializes the current schema and loads the prompt manifest;
- `masterclaw model-smoke` validates all four configured OpenRouter roles across the key typed
  scenario matrix, including both world-generation stages;
- a staging game produces mechanical output in the game channel and prose in the narrative channel;
- replayed ingress does not duplicate a roll or canonical response record, and a forced
  send-success/mark-failure exercise documents the residual Discord-message duplicate window;
- starting a second daemon against the same database is rejected;
- restart with pending inbox/outbox records resumes delivery;
- the container healthcheck succeeds without changing the database file or schema;
- a backup is created, restored into a temporary volume and passes `masterclaw doctor`;
- systemd service and backup timer are enabled and logs are visible through `journalctl`.

## Incident recovery

Stop the service before invasive repair. The command requires both the queue kind and action:

```bash
docker compose run --rm masterclaw masterclaw dead-letter inbox list
docker compose run --rm masterclaw masterclaw dead-letter outbox list
docker compose run --rm masterclaw masterclaw dead-letter inbox requeue <event-id>
docker compose run --rm masterclaw masterclaw dead-letter outbox requeue <numeric-outbox-id>
```

Correct the external cause before requeueing. Requeueing preserves the original idempotency keys.
Schema v7 adds a first-claim lifecycle snapshot. During upgrade, a game-bound inbox row that was
already attempted under v6 has no trustworthy pre-transition lifecycle, so migration moves it to
the inbox dead letter with an explicit `schema v7` diagnostic. Inspect the game's canonical
lifecycle and the event's intended transition before explicitly requeueing it; requeue is the
operator's acknowledgement that routing from the current lifecycle is safe. Untouched queued rows
remain pending and snapshot lifecycle normally on their first claim.
Schema v8 adds the typed-decision and exact-handler-result journals. Upgrading an already supported
v7 database creates these empty structures without re-running the v7 dead-letter rule; existing v7
pending or previously attempted rows retain their status and lifecycle snapshot.
Schema v9 adds event-scoped deterministic mutation operations and a separate durable provider retry
budget. Schema v10 adds the replay-safe lexicon-candidate observation ledger used by
`routing-quality-report`; upgrading creates an empty ledger and does not reinterpret prior logs as
candidate observations.
If the database is damaged, restore the most recent verified backup into a new volume and retain the
old volume for diagnosis. Never delete inbox, roll, domain-event or outbox rows to force a retry.
