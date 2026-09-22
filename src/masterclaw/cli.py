from __future__ import annotations

import argparse
import json
import logging
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from masterclaw.adapters.discord_bot import DiscordIngressClient
from masterclaw.adapters.openhands import OpenHandsCompletionPort, OpenHandsLLMRegistry
from masterclaw.app.action_capability_classifier import ActionCapabilityClassifier
from masterclaw.app.advancement_coordinator import AdvancementCoordinator
from masterclaw.app.advancement_safety import AdvancementSafetyDecider
from masterclaw.app.advancement_safety_classifier import (
    AdvancementSafetyClassifier,
    ShadowAdvancementSafetyDecider,
)
from masterclaw.app.legacy_advancement_safety import LegacyAdvancementSafetyDecider
from masterclaw.app.legacy_compound_planning import LegacyCompoundPlanDecider
from masterclaw.app.legacy_outcome_narrative_review import (
    LegacyAlwaysReviewDecider,
    LegacyNarrativeDraftGenerator,
    LegacyNarrativeTextEditor,
)
from masterclaw.app.legacy_player_narration_review import LegacyPlayerNarrationReview
from masterclaw.app.legacy_reserve_recovery import LegacyReserveRecoveryDecider
from masterclaw.app.message_handler import MessageApplication
from masterclaw.app.orchestrator import ChannelOrchestrator
from masterclaw.app.outcome_narrative_classifier import OutcomeNarrativeClassifier
from masterclaw.app.outcome_narrative_review import OutcomeNarrativePipeline
from masterclaw.app.player_narration_review import NarrationRightsDecider
from masterclaw.app.player_narration_rights_classifier import (
    PlayerNarrationRightsClassifier,
    ShadowNarrationRightsDecider,
)
from masterclaw.app.reserve_recovery_classifier import ReserveRecoveryClassifier
from masterclaw.app.state_dispatch_classifier import StateDispatchClassifier
from masterclaw.app.state_dispatch_service import StateDispatchDecisionService
from masterclaw.app.world_semantic_classifier import WorldSemanticClassifier
from masterclaw.app.worldgen_service import create_world_generation_service
from masterclaw.classifiers.policy import ClassifierMode, ClassifierUseCase
from masterclaw.config import ModelRole, OutputTransport, Settings
from masterclaw.context.assembler import ContextAssembler
from masterclaw.pipelines.action import create_action_pipeline
from masterclaw.pipelines.advancement import create_advancement_safety_pipeline
from masterclaw.pipelines.base import FallbackCompletionPort
from masterclaw.pipelines.character_creation import create_character_pipeline
from masterclaw.pipelines.compound_play import create_compound_play_pipeline
from masterclaw.pipelines.consequence import create_consequence_pipeline
from masterclaw.pipelines.conversation import (
    create_roleplay_reply_pipeline,
    create_rules_question_pipeline,
    create_scene_question_pipeline,
)
from masterclaw.pipelines.conversation_actions import (
    create_advancement_intake_pipeline,
    create_game_configuration_pipeline,
    create_roll_confirmation_pipeline,
)
from masterclaw.pipelines.narrative import (
    create_narrative_editor_pipeline,
    create_narrative_pipeline,
)
from masterclaw.pipelines.player_narration import create_player_narration_pipeline
from masterclaw.pipelines.reserve_recovery import create_reserve_recovery_pipeline
from masterclaw.pipelines.state_decision import StateDecisionRouter
from masterclaw.pipelines.world_intake import create_world_intake_pipeline
from masterclaw.runtime.composition import create_semantic_classifier
from masterclaw.runtime.resources import RuntimeResources
from masterclaw.runtime_lock import InstanceAlreadyRunning, single_instance
from masterclaw.storage.sqlite import SCHEMA_VERSION, SQLiteStore

PRODUCTION_PROVIDER_RETRY_DELAYS = (5.0, 30.0)
HEALTHCHECK_REQUIRED_TABLES = frozenset(
    {
        "schema_version",
        "inbox_messages",
        "channel_bindings",
        "games",
        "worlds",
        "world_projects",
        "scenes",
        "characters",
        "pending_interactions",
        "decision_checkpoints",
        "event_operations",
        "domain_events",
        "rolls",
        "outbox_messages",
        "llm_calls",
        "stage_spans",
        "lexicon_candidates",
    }
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="masterclaw")
    subparsers = parser.add_subparsers(dest="command", required=True)
    init_db = subparsers.add_parser("init-db", help="Initialize the SQLite database")
    init_db.add_argument("--database", default="data/masterclaw.sqlite3")
    subparsers.add_parser("serve", help="Run the Discord daemon")
    subparsers.add_parser("doctor", help="Validate configuration and local resources")
    classifier_doctor = subparsers.add_parser(
        "classifier-doctor", help="Check the configured local System One sidecar without inference"
    )
    classifier_doctor.add_argument(
        "--smoke", action="store_true", help="Also send one synthetic choice/noul/score request"
    )
    healthcheck = subparsers.add_parser(
        "healthcheck", help="Check that the live SQLite database is readable without modifying it"
    )
    healthcheck.add_argument("--database")
    subparsers.add_parser("model-smoke", help="Call every configured OpenRouter model role")
    benchmark = subparsers.add_parser(
        "model-benchmark", help="Compare configured OpenRouter models across role scenarios"
    )
    benchmark.add_argument("--output", required=True)
    benchmark.add_argument("--models", nargs="*")
    benchmark.add_argument("--repeats", type=int, default=1)
    benchmark.add_argument("--suite", choices=("core", "worldgen"), default="core")
    benchmark.add_argument("--config-mode", choices=("production", "fixed"), default="production")
    benchmark.add_argument(
        "--transports",
        nargs="*",
        choices=tuple(OutputTransport),
        default=[OutputTransport.PROMPT_JSON, OutputTransport.NATIVE_TOOL],
    )
    backup = subparsers.add_parser("backup", help="Create an online SQLite backup")
    backup.add_argument("--database", default="data/masterclaw.sqlite3")
    backup_output = backup.add_mutually_exclusive_group(required=True)
    backup_output.add_argument("--output")
    backup_output.add_argument(
        "--output-dir", help="Create a timestamped backup generation in this directory"
    )
    dead_letter = subparsers.add_parser("dead-letter", help="Inspect or requeue failed work")
    dead_letter.add_argument("--database", default="data/masterclaw.sqlite3")
    dead_letter.add_argument("kind", choices=("inbox", "outbox"))
    dead_letter.add_argument("action", choices=("list", "requeue"))
    dead_letter.add_argument("id", nargs="?")
    performance = subparsers.add_parser(
        "performance-report", help="Analyze stage timings and LLM usage"
    )
    performance.add_argument("--database", default="data/masterclaw.sqlite3")
    performance.add_argument("--since-hours", type=int, default=24)
    performance.add_argument(
        "--group-by", choices=("stage", "component", "operation", "status"), default="stage"
    )
    performance.add_argument("--game-id")
    performance.add_argument("--channel-id")
    performance.add_argument("--stage-prefix")
    performance.add_argument("--status", choices=("ok", "error"))
    performance.add_argument("--limit", type=int, default=20)
    performance.add_argument("--format", choices=("table", "json", "csv"), default="table")
    routing_quality = subparsers.add_parser(
        "routing-quality-report",
        help="Report lexicon candidates and model routing quality rates",
    )
    routing_quality.add_argument("--database", default="data/masterclaw.sqlite3")
    routing_quality.add_argument("--since-hours", type=int, default=24 * 7)
    routing_quality.add_argument("--min-count", type=int, default=2)
    routing_quality.add_argument("--limit", type=int, default=50)
    routing_quality.add_argument("--format", choices=("table", "json"), default="table")
    calibration = subparsers.add_parser(
        "classifier-calibration-report", help="Read-only classifier shadow calibration aggregates"
    )
    calibration.add_argument("--database", default="data/masterclaw.sqlite3")
    calibration.add_argument("--since-hours", type=int, default=168)
    calibration.add_argument("--use-case")
    calibration.add_argument("--scope")
    calibration.add_argument("--limit", type=int, default=50)
    calibration.add_argument("--format", choices=("table", "json"), default="table")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "classifier-doctor":
        from masterclaw.classifier_doctor import run_classifier_doctor

        return run_classifier_doctor(smoke=args.smoke)
    if args.command == "init-db":
        SQLiteStore(args.database).initialize()
        return 0
    if args.command == "healthcheck":
        database = args.database or Settings().database_path
        _check_database_read_only(database)
        print(f"database-read-only: {Path(database).resolve()}")
        return 0
    if args.command == "doctor":
        settings = Settings()
        SQLiteStore(settings.database_path).initialize()
        prompt_path = Path(settings.prompt_path)
        ContextAssembler(prompt_path)
        registry = OpenHandsLLMRegistry(settings)
        for role in ModelRole:
            registry.create(role)
        print("configuration: OK")
        print("openhands-sdk: OK")
        print(f"database: {settings.database_path}")
        print(f"prompts: {prompt_path.resolve()}")
        return 0
    if args.command == "backup":
        database = Path(args.database)
        if not database.is_file():
            raise SystemExit(f"live database does not exist: {database}")
        target = Path(args.output) if args.output else _generation_path(Path(args.output_dir))
        path = SQLiteStore(database).backup(target)
        print(f"backup: {path.resolve()}")
        return 0
    if args.command == "model-smoke":
        from masterclaw.model_smoke import run

        print(json.dumps(run(Settings()), ensure_ascii=False, indent=2))
        return 0
    if args.command == "model-benchmark":
        from masterclaw.model_benchmark import BenchmarkConfigMode, BenchmarkSuite, run

        report = run(
            Settings(),
            output=args.output,
            selected_models=set(args.models) if args.models else None,
            transports=tuple(OutputTransport(value) for value in args.transports),
            repeats=args.repeats,
            config_mode=BenchmarkConfigMode(args.config_mode),
            suite=BenchmarkSuite(args.suite),
        )
        print(json.dumps(report["summaries"], ensure_ascii=False, indent=2))
        return 0
    if args.command == "classifier-calibration-report":
        from masterclaw.classifier_calibration import (
            SQLiteClassifierSpanSource,
            classifier_calibration_report,
            render_classifier_calibration_report,
        )

        try:
            report = classifier_calibration_report(
                SQLiteClassifierSpanSource(args.database),
                since_hours=args.since_hours,
                use_case=args.use_case,
                scope=args.scope,
                limit=args.limit,
            )
        except (ValueError, sqlite3.DatabaseError):
            raise SystemExit(
                "classifier calibration report: invalid options or unreadable database"
            ) from None
        print(render_classifier_calibration_report(report, args.format))
        return 0
    if args.command == "performance-report":
        from masterclaw.performance import performance_report, render_report

        store = SQLiteStore(args.database)
        store.initialize()
        report = performance_report(
            store,
            since_hours=args.since_hours,
            group_by=args.group_by,
            game_id=args.game_id,
            channel_id=args.channel_id,
            stage_prefix=args.stage_prefix,
            status=args.status,
            limit=args.limit,
        )
        print(render_report(report, args.format))
        return 0
    if args.command == "routing-quality-report":
        from masterclaw.routing_quality import render_routing_quality_report, routing_quality_report

        store = SQLiteStore(args.database)
        store.initialize()
        report = routing_quality_report(
            store,
            since_hours=args.since_hours,
            min_count=args.min_count,
            limit=args.limit,
        )
        print(render_routing_quality_report(report, args.format))
        return 0
    if args.command == "dead-letter":
        store = SQLiteStore(args.database)
        store.initialize()
        if args.action == "list":
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
        try:
            with single_instance(settings.database_path):
                return _serve(settings)
        except InstanceAlreadyRunning as error:
            raise SystemExit(str(error)) from None
    raise AssertionError(f"unhandled command: {args.command}")


def _serve(settings: Settings) -> int:
    store = SQLiteStore(settings.database_path)
    store.initialize()
    registry = OpenHandsLLMRegistry(settings)

    def completion(role: ModelRole, model_config=None) -> OpenHandsCompletionPort:
        return _service_completion(registry, role, store, model_config=model_config)

    def reasoning_completion() -> FallbackCompletionPort:
        return FallbackCompletionPort(
            completion(ModelRole.REASONING),
            completion(ModelRole.REASONING, settings.reasoning_fallback_model),
        )

    def state_completion() -> FallbackCompletionPort:
        return FallbackCompletionPort(
            completion(ModelRole.STATE),
            completion(ModelRole.STATE, settings.state_fallback_model),
        )

    classifier = create_semantic_classifier(settings)
    resources = RuntimeResources()
    if classifier is not None:
        resources.add(classifier)
    state_decisions = StateDispatchDecisionService(
        store=store,
        baseline=StateDecisionRouter(state_completion()),
        classifier=(
            None
            if classifier is None
            else StateDispatchClassifier(
                classifier.executor,
                settings.classifier.for_use_case(ClassifierUseCase.STATE_DISPATCH),
            )
        ),
    )
    context = ContextAssembler(
        settings.prompt_path,
        model_ids={role: settings.model_for(role).model for role in ModelRole},
    )
    advancement_decider: AdvancementSafetyDecider = LegacyAdvancementSafetyDecider(
        store=store,
        context=context,
        safety_pipeline=create_advancement_safety_pipeline(state_completion()),
    )
    if classifier is not None:
        advancement_decider = ShadowAdvancementSafetyDecider(
            advancement_decider,
            AdvancementSafetyClassifier(classifier.executor, settings.classifier.advancement),
        )
    advancement = AdvancementCoordinator(
        store=store,
        decider=advancement_decider,
    )
    narration_review = LegacyPlayerNarrationReview(
        store=store,
        context=context,
        pipeline=create_player_narration_pipeline(state_completion()),
    )
    narration_decider: NarrationRightsDecider = narration_review
    if (
        classifier is not None
        and settings.classifier.player_narration_rights.mode is ClassifierMode.SHADOW
    ):
        narration_decider = ShadowNarrationRightsDecider(
            narration_review,
            PlayerNarrationRightsClassifier(
                classifier.executor, settings.classifier.player_narration_rights
            ),
        )
    application = MessageApplication(
        store=store,
        context=context,
        state_decisions=state_decisions,
        action_pipeline=create_action_pipeline(reasoning_completion()),
        action_capability_observer=(
            ActionCapabilityClassifier(classifier.executor, settings.classifier.action_capability)
            if classifier is not None
            and settings.classifier.action_capability.mode is ClassifierMode.SHADOW
            else None
        ),
        narrative_pipeline=OutcomeNarrativePipeline(
            draft=LegacyNarrativeDraftGenerator(
                create_narrative_pipeline(completion(ModelRole.NARRATIVE))
            ),
            decider=LegacyAlwaysReviewDecider(),
            observer=OutcomeNarrativeClassifier(
                classifier.executor, settings.classifier.outcome_narrative_review
            )
            if classifier is not None
            and settings.classifier.outcome_narrative_review.mode is ClassifierMode.SHADOW
            else None,
            editor=LegacyNarrativeTextEditor(
                context=context,
                reviewer=create_narrative_editor_pipeline(
                    completion(ModelRole.REASONING, settings.narrative_review_model)
                ),
                reviewer_fallback=create_narrative_editor_pipeline(
                    completion(ModelRole.REASONING, settings.narrative_review_fallback_model)
                ),
            ),
        ),
        advancement=advancement,
        narration_rights_decider=narration_decider,
        narration_text_port=narration_review,
        worldgen=create_world_generation_service(
            context=context,
            creative_completion=completion(ModelRole.WORLDGEN, settings.worldgen_creative_model),
            creative_fallback_completion=completion(
                ModelRole.WORLDGEN, settings.worldgen_creative_fallback_model
            ),
            structuring_completion=completion(ModelRole.WORLDGEN),
            structuring_fallback_completion=completion(
                ModelRole.WORLDGEN, settings.worldgen_fallback_model
            ),
        ),
        character_pipeline=create_character_pipeline(reasoning_completion()),
        world_semantic_observer=WorldSemanticClassifier(
            classifier.executor, settings.classifier.worldgen_semantics
        )
        if classifier is not None
        and settings.classifier.worldgen_semantics.mode is ClassifierMode.SHADOW
        else None,
        consequence_pipeline=create_consequence_pipeline(reasoning_completion()),
        reserve_recovery_decider=LegacyReserveRecoveryDecider(
            context=context,
            pipeline=create_reserve_recovery_pipeline(reasoning_completion()),
        ),
        reserve_recovery_observer=ReserveRecoveryClassifier(
            classifier.executor, settings.classifier.reserve_recovery
        )
        if classifier is not None
        and settings.classifier.reserve_recovery.mode is ClassifierMode.SHADOW
        else None,
        world_intake_pipeline=create_world_intake_pipeline(reasoning_completion()),
        scene_question_pipeline=create_scene_question_pipeline(reasoning_completion()),
        rules_question_pipeline=create_rules_question_pipeline(reasoning_completion()),
        roleplay_reply_pipeline=create_roleplay_reply_pipeline(completion(ModelRole.NARRATIVE)),
        compound_plan_decider=LegacyCompoundPlanDecider(
            store=store, pipeline=create_compound_play_pipeline(reasoning_completion())
        ),
        advancement_intake_pipeline=create_advancement_intake_pipeline(reasoning_completion()),
        game_configuration_pipeline=create_game_configuration_pipeline(state_completion()),
        roll_confirmation_pipeline=create_roll_confirmation_pipeline(state_completion()),
    )
    orchestrator = ChannelOrchestrator(store, application)
    client = DiscordIngressClient(
        store=store,
        orchestrator=orchestrator,
        debounce_seconds=settings.discord_debounce_seconds,
        resources=resources,
    )
    client.run(settings.discord_token.get_secret_value(), log_handler=None)
    return 0


def _service_completion(
    registry: OpenHandsLLMRegistry,
    role: ModelRole,
    store: SQLiteStore,
    *,
    model_config=None,
) -> OpenHandsCompletionPort:
    return OpenHandsCompletionPort(
        registry,
        role,
        store,
        model_config=model_config,
        retry_delays=PRODUCTION_PROVIDER_RETRY_DELAYS,
    )


def _generation_path(output_dir: Path) -> Path:
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S.%fZ")
    return output_dir / f"masterclaw-{timestamp}.sqlite3"


def _check_database_read_only(database: str | Path) -> None:
    path = Path(database)
    if not path.is_file():
        raise SystemExit(f"live database does not exist: {path}")
    uri = f"{path.resolve().as_uri()}?mode=ro"
    try:
        with sqlite3.connect(uri, uri=True, timeout=2) as connection:
            connection.execute("PRAGMA query_only = ON")
            tables = {
                str(row[0])
                for row in connection.execute(
                    "SELECT name FROM sqlite_schema WHERE type = 'table'"
                ).fetchall()
            }
            missing = HEALTHCHECK_REQUIRED_TABLES - tables
            if missing:
                raise sqlite3.DatabaseError(
                    f"required application tables are missing: {sorted(missing)}"
                )
            row = connection.execute("SELECT MAX(version) FROM schema_version").fetchone()
            current = None if row is None else row[0]
            if current != SCHEMA_VERSION:
                raise sqlite3.DatabaseError(
                    f"unsupported application schema {current}; expected {SCHEMA_VERSION}"
                )
            quick_check = [str(row[0]) for row in connection.execute("PRAGMA quick_check(1)")]
            if quick_check != ["ok"]:
                raise sqlite3.DatabaseError(f"SQLite quick_check failed: {'; '.join(quick_check)}")
    except sqlite3.Error as error:
        raise SystemExit(f"live database is not readable: {error}") from None


if __name__ == "__main__":
    raise SystemExit(main())
