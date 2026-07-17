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
an explicit capability setting rather than an automatic retry across incompatible API formats.

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
The Compose healthcheck uses `masterclaw healthcheck` instead. It requires an existing database,
opens it with SQLite `mode=ro`, verifies required tables, the exact supported application schema
version and `quick_check`, and never initializes or migrates it. Startup creates a fresh current
schema or applies the supported migration to schema v3; it fails closed on a newer version. Take an
online backup before deploying a version that can migrate the database.

## Persistence and recovery

SQLite runs in WAL mode. Discord events are persisted before processing. A clean or crashed restart returns any `processing` inbox records to `pending`. Domain completion and outbox creation share one transaction, so a committed response remains deliverable after restart.

Backups must capture the database consistently. `masterclaw backup` uses SQLite's online backup API
and is safe with WAL mode; it copies the live database without first initializing or migrating it.
Do not copy only the main `.sqlite3` file while the daemon is active. The systemd timer writes a
new timestamped generation to the separate `masterclaw-backups` volume on every run instead of
overwriting `latest.sqlite3`. Periodically export verified generations to another host or object
store; a second volume protects against loss of the live-data volume, not loss of the Docker host.
Retention and off-host replication remain operator policy.

## Acceptance checklist

- `docker compose build` succeeds on the target Linux host;
- `masterclaw doctor` initializes the current schema and loads the prompt manifest;
- `masterclaw model-smoke` validates all four configured OpenRouter roles;
- a staging game produces mechanical output in the game channel and prose in the narrative channel;
- duplicate Discord delivery does not duplicate a roll or response;
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
If the database is damaged, restore the most recent verified backup into a new volume and retain the
old volume for diagnosis. Never delete inbox, roll, domain-event or outbox rows to force a retry.
