#!/usr/bin/env python3
"""
MasterClaw Model Manager
Manages LLM model/provider in microclaw.config.yaml with preset support.

Usage:
  python3 manage_models.py current                       — show current model
  python3 manage_models.py list                          — list current + all presets
  python3 manage_models.py set <preset_name>             — switch to preset
  python3 manage_models.py set-model <model_id>          — change model only, keep provider/key
  python3 manage_models.py add <name> <provider> <model> <base_url>          — add preset (uses current api_key)
  python3 manage_models.py add-full <name> <provider> <model> <base_url> <api_key>  — add preset with own api_key
  python3 manage_models.py remove <name>                 — remove a user preset

Presets are stored in /root/.microclaw/model_presets.yaml.
After changes, microClaw is restarted with a 3 second delay (agent has time to reply first).
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
PRESETS_PATH = "/root/.microclaw/model_presets.yaml"

# Built-in presets shipped with MasterClaw. User presets override these.
BUILTIN_PRESETS = {
    "grok-fast": {
        "provider": "openrouter",
        "model": "x-ai/grok-4.1-fast",
        "base_url": "https://openrouter.ai/api/v1",
    },
    "grok-full": {
        "provider": "openrouter",
        "model": "x-ai/grok-4.1",
        "base_url": "https://openrouter.ai/api/v1",
    },
    "haiku": {
        "provider": "openrouter",
        "model": "anthropic/claude-haiku-4-5",
        "base_url": "https://openrouter.ai/api/v1",
    },
    "sonnet": {
        "provider": "openrouter",
        "model": "anthropic/claude-sonnet-4-5",
        "base_url": "https://openrouter.ai/api/v1",
    },
    "gpt-mini": {
        "provider": "openrouter",
        "model": "openai/gpt-5-mini",
        "base_url": "https://openrouter.ai/api/v1",
    },
    "gemini-flash": {
        "provider": "openrouter",
        "model": "google/gemini-2.5-flash",
        "base_url": "https://openrouter.ai/api/v1",
    },
    "deepseek": {
        "provider": "openrouter",
        "model": "deepseek/deepseek-v3.2",
        "base_url": "https://openrouter.ai/api/v1",
    },
}


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


def load_user_presets():
    if not os.path.exists(PRESETS_PATH):
        return {}
    with open(PRESETS_PATH, "r") as f:
        data = yaml.safe_load(f) or {}
    return data.get("presets", {})


def save_user_presets(presets):
    os.makedirs(os.path.dirname(PRESETS_PATH), exist_ok=True)
    with open(PRESETS_PATH, "w") as f:
        yaml.dump({"presets": presets}, f, default_flow_style=False, allow_unicode=True, sort_keys=False)


def all_presets():
    """Builtin presets merged with user presets (user overrides builtin)."""
    merged = dict(BUILTIN_PRESETS)
    merged.update(load_user_presets())
    return merged


def restart_microclaw():
    """Schedule a delayed restart so the agent can finish responding first."""
    subprocess.Popen(
        ["bash", "-c", "sleep 3 && systemctl restart microclaw"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )
    return True, "microClaw restart scheduled (3 sec delay). Bot will briefly go offline."


def find_preset_name_for_current(config):
    """If current config matches a known preset, return its name."""
    current = {
        "provider": config.get("llm_provider"),
        "model": config.get("model"),
        "base_url": config.get("llm_base_url"),
    }
    for name, preset in all_presets().items():
        if (preset.get("provider") == current["provider"]
                and preset.get("model") == current["model"]
                and preset.get("base_url") == current["base_url"]):
            return name
    return None


def cmd_current(config):
    provider = config.get("llm_provider", "unknown")
    model = config.get("model", "unknown")
    base_url = config.get("llm_base_url", "default")
    preset_name = find_preset_name_for_current(config)

    print(f"Current model:")
    print(f"  Provider: {provider}")
    print(f"  Model:    {model}")
    print(f"  Base URL: {base_url}")
    if preset_name:
        print(f"  Preset:   {preset_name}")
    else:
        print(f"  Preset:   (custom, not matching any preset)")


def cmd_list(config):
    cmd_current(config)
    print()

    presets = all_presets()
    user_presets = load_user_presets()

    if not presets:
        print("No presets available.")
        return

    print(f"Available presets ({len(presets)}):")
    for name, preset in sorted(presets.items()):
        origin = "user" if name in user_presets else "builtin"
        has_key = " [own-key]" if preset.get("api_key") else ""
        print(f"  {name:<15} — {preset.get('model'):<35} ({preset.get('provider')}) [{origin}]{has_key}")


def cmd_set(config, preset_name):
    presets = all_presets()
    if preset_name not in presets:
        print(f"Error: preset '{preset_name}' not found. Use 'list' to see available presets.")
        return False

    preset = presets[preset_name]
    config["llm_provider"] = preset["provider"]
    config["model"] = preset["model"]
    config["llm_base_url"] = preset["base_url"]
    if preset.get("api_key"):
        config["api_key"] = preset["api_key"]

    backup = save_config(config)
    print(f"Switched to preset '{preset_name}':")
    print(f"  Provider: {preset['provider']}")
    print(f"  Model:    {preset['model']}")
    print(f"Config backed up to: {backup}")

    ok, msg = restart_microclaw()
    print(msg)
    return ok


def cmd_set_model(config, model_id):
    old_model = config.get("model", "unknown")
    config["model"] = model_id
    backup = save_config(config)
    print(f"Model changed: {old_model} → {model_id}")
    print(f"Config backed up to: {backup}")

    ok, msg = restart_microclaw()
    print(msg)
    return ok


def cmd_add(name, provider, model, base_url, api_key=None):
    if name in BUILTIN_PRESETS:
        print(f"Error: '{name}' is a built-in preset name. Pick a different name.")
        return False

    presets = load_user_presets()
    preset = {
        "provider": provider,
        "model": model,
        "base_url": base_url,
    }
    if api_key:
        preset["api_key"] = api_key
    presets[name] = preset
    save_user_presets(presets)
    print(f"Added preset '{name}':")
    print(f"  Provider: {provider}")
    print(f"  Model:    {model}")
    print(f"  Base URL: {base_url}")
    print(f"  Own key:  {'yes' if api_key else 'no (uses current config api_key)'}")
    return True


def cmd_remove(name):
    if name in BUILTIN_PRESETS and name not in load_user_presets():
        print(f"Error: '{name}' is a built-in preset and cannot be removed.")
        return False

    presets = load_user_presets()
    if name not in presets:
        print(f"Error: user preset '{name}' not found.")
        return False
    del presets[name]
    save_user_presets(presets)
    print(f"Removed user preset '{name}'")
    return True


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    command = sys.argv[1]

    # Commands that read/write config
    if command in ("current", "list", "set", "set-model"):
        config = load_config()

        if command == "current":
            cmd_current(config)
        elif command == "list":
            cmd_list(config)
        elif command == "set":
            if len(sys.argv) < 3:
                print("Usage: manage_models.py set <preset_name>")
                sys.exit(1)
            if not cmd_set(config, sys.argv[2]):
                sys.exit(1)
        elif command == "set-model":
            if len(sys.argv) < 3:
                print("Usage: manage_models.py set-model <model_id>")
                sys.exit(1)
            if not cmd_set_model(config, sys.argv[2]):
                sys.exit(1)

    # Commands that only touch presets file
    elif command in ("add", "add-full", "remove"):
        if command == "add":
            if len(sys.argv) < 6:
                print("Usage: manage_models.py add <name> <provider> <model> <base_url>")
                sys.exit(1)
            if not cmd_add(sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5]):
                sys.exit(1)
        elif command == "add-full":
            if len(sys.argv) < 7:
                print("Usage: manage_models.py add-full <name> <provider> <model> <base_url> <api_key>")
                sys.exit(1)
            if not cmd_add(sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5], sys.argv[6]):
                sys.exit(1)
        elif command == "remove":
            if len(sys.argv) < 3:
                print("Usage: manage_models.py remove <name>")
                sys.exit(1)
            if not cmd_remove(sys.argv[2]):
                sys.exit(1)
    else:
        print(f"Unknown command: {command}")
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
