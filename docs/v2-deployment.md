# MasterClaw v2 — Linux droplet deployment

Status: deployment runbook. Complete the acceptance checklist below before using the bot for a campaign.

## Chosen topology

- one isolated Linux droplet;
- Docker Engine + Compose plugin;
- one MasterClaw application container;
- one persistent Docker volume containing SQLite database, WAL and backups;
- `restart: unless-stopped`, optionally supervised by a small systemd unit invoking Compose;
- Discord bot API for game and narrative channels;
- OpenRouter as the only LLM provider.

Multiple application replicas are explicitly unsupported while SQLite and the single-channel worker model are in use.

The repository includes `deploy/masterclaw.service` plus a daily backup service/timer.
Install them under `/etc/systemd/system`, keep the checkout at `/opt/masterclaw`, then run:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now masterclaw.service
sudo systemctl enable --now masterclaw-backup.timer
```

## Discord requirements

Create a Discord application and bot, enable Message Content Intent, invite it with permission to view/send messages in the game and narrative channels, and keep the token outside the repository. Commands use Discord messages beginning with `/`; native application-command registration is deliberately not required, so the same durable inbox path handles commands and ordinary play messages.

## Configuration

Copy `.env.example` to `.env` and set:

- `MASTERCLAW_DISCORD_TOKEN`;
- `MASTERCLAW_OPENROUTER_API_KEY`;
- three OpenRouter model ids for `STATE`, `REASONING` and `NARRATIVE` roles;
- optional database, prompts and debounce settings.

The daemon does not dynamically route models by price or availability. Each pipeline selects one configured role.
`.env.example` uses `openrouter/x-ai/grok-4.5` for all three roles; deployments may override each role independently.

## Commands

```bash
docker compose build
docker compose run --rm masterclaw masterclaw doctor
docker compose run --rm masterclaw masterclaw model-smoke
docker compose run --rm masterclaw masterclaw backup --database /app/data/masterclaw.sqlite3 --output /app/data/backups/manual.sqlite3
docker compose up -d
docker compose logs -f masterclaw
```

`doctor` validates settings, initializes/opens SQLite and loads the prompt manifest without making an LLM request.
Startup also verifies the supported SQLite schema version and fails closed on a newer or otherwise incompatible schema.

## Persistence and recovery

SQLite runs in WAL mode. Discord events are persisted before processing. A clean or crashed restart returns any `processing` inbox records to `pending`. Domain completion and outbox creation share one transaction, so a committed response remains deliverable after restart.

Backups must capture the database consistently. `masterclaw backup` uses SQLite's online backup API and is safe with WAL mode; do not copy only the main `.sqlite3` file while the daemon is active. Scheduling and retention remain deployment concerns.

## Acceptance checklist

- `docker compose build` succeeds on the target Linux host;
- `masterclaw doctor` reports a compatible schema and prompt manifest;
- `masterclaw model-smoke` validates all three configured OpenRouter roles;
- a staging game produces mechanical output in the game channel and prose in the narrative channel;
- duplicate Discord delivery does not duplicate a roll or response;
- restart with pending inbox/outbox records resumes delivery;
- a backup is created, restored into a temporary volume and passes `masterclaw doctor`;
- systemd service and backup timer are enabled and logs are visible through `journalctl`.

## Incident recovery

Stop the service before invasive repair. Inspect failed records with `masterclaw dead-letter list`, correct the external cause, then use `masterclaw dead-letter requeue` for the selected inbox event or outbox id. Requeueing preserves the original idempotency keys. If the database is damaged or an incompatible deployment was started, restore the most recent verified backup into a new volume and retain the old volume for diagnosis. Never delete inbox, roll, domain-event or outbox rows to force a retry.
