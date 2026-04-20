# SKILL: Channel Management

Manages Discord channel allowlist for microClaw. Operator-only (Telegram).

## When to use
- Operator asks to add a new Discord channel
- Operator asks to remove a Discord channel
- Operator asks to list allowed channels
- Setting up a narrative channel for a game

## Who can use
**Operator only** (Telegram soul). Never execute channel management from Discord game channels.

## Commands

### List channels
```bash
python3 /root/.microclaw/scripts/manage_channels.py list
```
Shows all currently allowed Discord channels.

### Add channel
```bash
python3 /root/.microclaw/scripts/manage_channels.py add <channel_id> [label]
```
Adds a Discord channel to the allowlist. The label is optional (for human reference only).
**Automatically restarts microClaw** and creates a config backup.

Example: `python3 /root/.microclaw/scripts/manage_channels.py add 1234567890 "narrative"`

### Remove channel
```bash
python3 /root/.microclaw/scripts/manage_channels.py remove <channel_id>
```
Removes a channel from the allowlist. Cannot remove the last channel.
**Automatically restarts microClaw** and creates a config backup.

## Procedure

1. Operator provides channel ID (or asks to create a channel for a purpose like "narrative")
2. Confirm the action with the operator before running
3. Run the appropriate command
4. **IMMEDIATELY report the result to the operator** — the script schedules a delayed restart (3 seconds), so you MUST send your response before the restart happens. Do not do any other tool calls between running the script and responding.
5. If adding a narrative channel: update game.md `narrative_channel` field for the active game (do this in a follow-up message after restart)

## Notes
- The script creates automatic config backups before every change
- microClaw restart takes a few seconds — the bot will briefly go offline
- Channel IDs are Discord snowflakes (large integers like 1485281375069409402)
- To get a channel ID: in Discord, enable Developer Mode, right-click channel → Copy Channel ID
