from __future__ import annotations

import argparse
import logging
from pathlib import Path

from masterclaw.adapters.discord_bot import DiscordIngressClient
from masterclaw.adapters.openhands import OpenHandsCompletionPort, OpenHandsLLMRegistry
from masterclaw.app.advancement_coordinator import AdvancementCoordinator
from masterclaw.app.message_handler import MessageApplication
from masterclaw.app.orchestrator import ChannelOrchestrator
from masterclaw.config import ModelRole, Settings
from masterclaw.context.assembler import ContextAssembler
from masterclaw.pipelines.action import create_action_pipeline
from masterclaw.pipelines.advancement import create_advancement_safety_pipeline
from masterclaw.pipelines.character_creation import create_character_pipeline
from masterclaw.pipelines.consequence import create_consequence_pipeline
from masterclaw.pipelines.intent import create_intent_pipeline
from masterclaw.pipelines.narrative import create_narrative_pipeline
from masterclaw.pipelines.player_narration import create_player_narration_pipeline
from masterclaw.pipelines.worldgen import create_worldgen_pipeline
from masterclaw.storage.sqlite import SQLiteStore


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="masterclaw")
    subparsers = parser.add_subparsers(dest="command", required=True)
    init_db = subparsers.add_parser("init-db", help="Initialize the SQLite database")
    init_db.add_argument("--database", default="data/masterclaw.sqlite3")
    subparsers.add_parser("serve", help="Run the Discord daemon")
    subparsers.add_parser("doctor", help="Validate configuration and local resources")
    subparsers.add_parser("model-smoke", help="Call every configured OpenRouter model role")
    backup = subparsers.add_parser("backup", help="Create an online SQLite backup")
    backup.add_argument("--database", default="data/masterclaw.sqlite3")
    backup.add_argument("--output", required=True)
    dead_letter = subparsers.add_parser("dead-letter", help="Inspect or requeue failed work")
    dead_letter.add_argument("--database", default="data/masterclaw.sqlite3")
    dead_letter.add_argument("kind", choices=("inbox", "outbox"))
    dead_letter.add_argument("action", choices=("list", "requeue"))
    dead_letter.add_argument("id", nargs="?")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "init-db":
        SQLiteStore(args.database).initialize()
        return 0
    if args.command == "doctor":
        settings = Settings()
        SQLiteStore(settings.database_path).initialize()
        prompt_path = Path(settings.prompt_path)
        ContextAssembler(prompt_path)
        print("configuration: OK")
        print(f"database: {settings.database_path}")
        print(f"prompts: {prompt_path.resolve()}")
        return 0
    if args.command == "backup":
        store = SQLiteStore(args.database)
        store.initialize()
        path = store.backup(args.output)
        print(f"backup: {path.resolve()}")
        return 0
    if args.command == "model-smoke":
        import json

        from masterclaw.model_smoke import run

        print(json.dumps(run(Settings()), ensure_ascii=False, indent=2))
        return 0
    if args.command == "dead-letter":
        store = SQLiteStore(args.database)
        store.initialize()
        if args.action == "list":
            import json

            rows = store.failed_inbox() if args.kind == "inbox" else store.failed_outbox()
            print(json.dumps(rows, ensure_ascii=False, indent=2))
            return 0
        if args.id is None:
            raise SystemExit("dead-letter requeue requires an id")
        if args.kind == "inbox":
            store.requeue_failed_inbox(args.id)
        else:
            store.requeue_failed_outbox(int(args.id))
        return 0
    if args.command == "serve":
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        )
        settings = Settings()
        store = SQLiteStore(settings.database_path)
        store.initialize()
        registry = OpenHandsLLMRegistry(settings)
        intent = create_intent_pipeline(OpenHandsCompletionPort(registry, ModelRole.STATE))
        context = ContextAssembler(settings.prompt_path)
        advancement = AdvancementCoordinator(
            store=store,
            context=context,
            safety_pipeline=create_advancement_safety_pipeline(
                OpenHandsCompletionPort(registry, ModelRole.STATE)
            ),
        )
        application = MessageApplication(
            store=store,
            context=context,
            intent_pipeline=intent,
            action_pipeline=create_action_pipeline(
                OpenHandsCompletionPort(registry, ModelRole.REASONING)
            ),
            narrative_pipeline=create_narrative_pipeline(
                OpenHandsCompletionPort(registry, ModelRole.NARRATIVE)
            ),
            advancement=advancement,
            player_narration_pipeline=create_player_narration_pipeline(
                OpenHandsCompletionPort(registry, ModelRole.STATE)
            ),
            worldgen_pipeline=create_worldgen_pipeline(
                OpenHandsCompletionPort(registry, ModelRole.REASONING)
            ),
            character_pipeline=create_character_pipeline(
                OpenHandsCompletionPort(registry, ModelRole.REASONING)
            ),
            consequence_pipeline=create_consequence_pipeline(
                OpenHandsCompletionPort(registry, ModelRole.REASONING)
            ),
        )
        orchestrator = ChannelOrchestrator(store, application)
        client = DiscordIngressClient(
            store=store,
            orchestrator=orchestrator,
            debounce_seconds=settings.discord_debounce_seconds,
        )
        client.run(settings.discord_token.get_secret_value(), log_handler=None)
        return 0
    raise AssertionError(f"unhandled command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
