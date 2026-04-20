#!/usr/bin/env python3
"""
MasterClaw Channel Manager
Manages Discord allowed_channels in microclaw.config.yaml.

Usage:
  python3 manage_channels.py list
  python3 manage_channels.py add <channel_id> [label]
  python3 manage_channels.py remove <channel_id>

After add/remove, the microClaw service is restarted automatically.
"""

import sys
import os
import subprocess
import shutil
from datetime import datetime

try:
    import yaml
except ImportError:
    print("Error: PyYAML not installed. Run: pip3 install pyyaml")
    sys.exit(1)

CONFIG_PATH = "/root/microclaw.config.yaml"
BACKUP_DIR = "/root/microclaw.config.backups"
DISCORD_ACCOUNT = "main"


def load_config():
    with open(CONFIG_PATH, "r") as f:
        return yaml.safe_load(f)


def save_config(config):
    os.makedirs(BACKUP_DIR, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
    backup_path = os.path.join(BACKUP_DIR, f"microclaw.config.yaml.bak.{timestamp}")
    shutil.copy2(CONFIG_PATH, backup_path)

    with open(CONFIG_PATH, "w") as f:
        yaml.dump(config, f, default_flow_style=False, allow_unicode=True, sort_keys=False)

    return backup_path


def get_allowed_channels(config):
    try:
        return config["channels"]["discord"]["accounts"][DISCORD_ACCOUNT].get("allowed_channels", [])
    except (KeyError, TypeError):
        return []


def set_allowed_channels(config, channels):
    config["channels"]["discord"]["accounts"][DISCORD_ACCOUNT]["allowed_channels"] = channels


def restart_microclaw():
    result = subprocess.run(
        ["systemctl", "restart", "microclaw"],
        capture_output=True, text=True
    )
    if result.returncode == 0:
        return True, "microClaw restarted successfully"
    else:
        return False, f"Restart failed: {result.stderr.strip()}"


def cmd_list(config):
    channels = get_allowed_channels(config)
    if not channels:
        print("No allowed channels configured (all channels accepted)")
        return

    print(f"Allowed channels ({len(channels)}):")
    for ch in channels:
        print(f"  - {ch}")


def cmd_add(config, channel_id, label=None):
    try:
        channel_id = int(channel_id)
    except ValueError:
        print(f"Error: '{channel_id}' is not a valid channel ID")
        return False

    channels = get_allowed_channels(config)

    if channel_id in channels:
        print(f"Channel {channel_id} already in allowed list")
        return True

    channels.append(channel_id)
    set_allowed_channels(config, channels)
    backup = save_config(config)

    label_str = f" ({label})" if label else ""
    print(f"Added channel {channel_id}{label_str}")
    print(f"Config backed up to: {backup}")

    ok, msg = restart_microclaw()
    print(msg)
    return ok


def cmd_remove(config, channel_id):
    try:
        channel_id = int(channel_id)
    except ValueError:
        print(f"Error: '{channel_id}' is not a valid channel ID")
        return False

    channels = get_allowed_channels(config)

    if channel_id not in channels:
        print(f"Channel {channel_id} not in allowed list")
        return False

    if len(channels) <= 1:
        print("Error: cannot remove the last channel. At least one must remain.")
        return False

    channels.remove(channel_id)
    set_allowed_channels(config, channels)
    backup = save_config(config)

    print(f"Removed channel {channel_id}")
    print(f"Config backed up to: {backup}")

    ok, msg = restart_microclaw()
    print(msg)
    return ok


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    command = sys.argv[1]
    config = load_config()

    if command == "list":
        cmd_list(config)
    elif command == "add":
        if len(sys.argv) < 3:
            print("Usage: manage_channels.py add <channel_id> [label]")
            sys.exit(1)
        label = sys.argv[3] if len(sys.argv) > 3 else None
        if not cmd_add(config, sys.argv[2], label):
            sys.exit(1)
    elif command == "remove":
        if len(sys.argv) < 3:
            print("Usage: manage_channels.py remove <channel_id>")
            sys.exit(1)
        if not cmd_remove(config, sys.argv[2]):
            sys.exit(1)
    else:
        print(f"Unknown command: {command}")
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
