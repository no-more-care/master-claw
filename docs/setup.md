# MasterClaw — Setup Guide

Step-by-step guide to set up MasterClaw from scratch on a Linux server.

## Prerequisites

- **Linux server** (Ubuntu 22.04+ recommended, e.g. DigitalOcean droplet)
- **Python 3.10+** with PyYAML (`pip3 install pyyaml`)
- **Git**
- **Discord bot** — you'll need a bot token and a server to add it to
- **Telegram bot** (optional) — for operator/management interface

## Step 1: Install microClaw

microClaw is the agent platform that runs the LLM and connects it to Discord/Telegram.

See the [microClaw documentation](https://github.com/microclaw/microclaw) for installation. Quick version:

```bash
# Download and install (check microClaw repo for latest method)
curl -fsSL https://get.microclaw.com | bash

# Run the setup wizard
microclaw setup
```

This creates:
- `/root/.microclaw/` — data directory (souls, skills, working_dir)
- `/root/microclaw.config.yaml` — main config file
- `microclaw.service` — systemd service

## Step 2: Create Discord Bot

1. Go to [Discord Developer Portal](https://discord.com/developers/applications)
2. Click "New Application" → name it (e.g. "MasterClaw")
3. Go to "Bot" → click "Reset Token" → copy the token
4. Enable these Privileged Gateway Intents:
   - **Message Content Intent** (required for reading messages)
   - **Server Members Intent** (optional, for member info)
5. Go to "OAuth2" → "URL Generator":
   - Scopes: `bot`
   - Bot Permissions: `Send Messages`, `Read Message History`, `View Channels`
6. Copy the generated URL → open in browser → add bot to your Discord server
7. Create a channel for the game (e.g. `#game`) and optionally a `#narrative` channel
8. Enable Developer Mode in Discord (Settings → Advanced → Developer Mode)
9. Right-click the game channel → "Copy Channel ID"

## Step 3: Create Telegram Bot (optional)

1. Message [@BotFather](https://t.me/BotFather) on Telegram
2. Send `/newbot` → follow prompts → copy the token
3. Get your Telegram user ID (message [@userinfobot](https://t.me/userinfobot))

## Step 4: Clone MasterClaw

```bash
cd /root/.microclaw

# If the directory already has default content from microClaw setup:
# Back up any existing files you want to keep, then:
git init
git remote add origin https://github.com/no-more-care/master-claw.git
git fetch origin
git checkout -b develop origin/develop
```

Or fresh clone:
```bash
git clone https://github.com/no-more-care/master-claw.git /root/.microclaw
cd /root/.microclaw
git checkout develop
```

## Step 5: Configure microClaw

Edit `/root/microclaw.config.yaml`. See `microclaw.config.example.yaml` in this repo for a template.

Key settings to configure:

```yaml
channels:
  discord:
    enabled: true
    default_account: "main"
    accounts:
      main:
        enabled: true
        soul_path: "/root/.microclaw/souls/gamemaster.md"
        no_mention: true
        allowed_channels:
        - <YOUR_GAME_CHANNEL_ID>
        bot_token: "<YOUR_DISCORD_BOT_TOKEN>"

  telegram:
    enabled: true                    # set false if not using
    default_account: "main"
    accounts:
      main:
        enabled: true
        soul_path: "/root/.microclaw/souls/operator.md"
        allowed_user_ids:
        - <YOUR_TELEGRAM_USER_ID>
        bot_token: "<YOUR_TELEGRAM_BOT_TOKEN>"
        bot_username: "MasterClaw"

# LLM provider — any OpenAI-compatible API
llm_provider: "openrouter"           # or "openai", "anthropic", etc.
api_key: "<YOUR_LLM_API_KEY>"
model: "x-ai/grok-4.1-fast"         # or any model your provider supports
llm_base_url: "https://openrouter.ai/api/v1"

data_dir: "/root/.microclaw"
working_dir: "/root/.microclaw/working_dir"
working_dir_isolation: shared
souls_dir: "/root/.microclaw/souls"
```

## Step 6: Initialize Game Data Directory

```bash
mkdir -p /root/.microclaw/working_dir/shared/GameMaster/worlds
mkdir -p /root/.microclaw/working_dir/shared/GameMaster/games
```

## Step 7: Install Python Dependencies

```bash
pip3 install pyyaml
```

Required for `scripts/roll.py` (dice roller) and `scripts/manage_channels.py` (channel manager).

## Step 8: Start microClaw

```bash
systemctl start microclaw
systemctl enable microclaw    # auto-start on boot

# Check status
systemctl status microclaw

# View logs
journalctl -u microclaw -f
```

## Step 9: Test

1. **Discord:** Go to your game channel, mention the bot or type a message. It should respond as the Game Master.
2. **Telegram:** Message your bot. It should respond as the operator.
3. **Create a world:** In Telegram, ask the operator to create a world (e.g. "Create a cyberpunk world in Siberia")
4. **Start a game:** In Discord, tell the GM to start a game in that world

## Adding a Narrative Channel (optional)

1. Create a `#narrative` channel in Discord
2. From Telegram, tell the operator: "Add Discord channel <CHANNEL_ID>"
   - This runs `manage_channels.py` which updates config and restarts microClaw
3. When starting a game, provide the narrative channel ID when asked

## Updating MasterClaw

```bash
cd /root/.microclaw
git pull origin develop
# Skills and souls reload automatically (microClaw reads them from disk)
# For config changes: systemctl restart microclaw
```

## Troubleshooting

**Bot doesn't respond in Discord:**
- Check `allowed_channels` includes your channel ID
- Check `systemctl status microclaw` for errors
- Check `journalctl -u microclaw -f` for live logs
- Verify bot has Message Content Intent enabled in Discord Developer Portal

**Bot responds but doesn't follow game rules:**
- Verify `soul_path` points to the correct soul file
- Check that `skills/` directory has all SKILL.md files
- Try `/reload-skills` in the chat (microClaw built-in command)

**Dice script doesn't work:**
- Check `python3 /root/.microclaw/scripts/roll.py 5 3` works manually
- Verify Python 3 is installed

**Channel management fails:**
- Check `python3 /root/.microclaw/scripts/manage_channels.py list` works
- Verify PyYAML is installed: `python3 -c "import yaml"`
