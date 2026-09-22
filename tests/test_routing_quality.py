import asyncio
import json
from pathlib import Path

import pytest

from masterclaw.app.message_handler import MessageApplication
from masterclaw.cli import main
from masterclaw.context.assembler import ContextAssembler
from masterclaw.domain.models import GameLifecycle, IncomingMessage
from masterclaw.domain.state import GameState, WorldState
from masterclaw.pipelines.base import CompletionResult, PipelineValidationError
from masterclaw.pipelines.state_decision import StateDecisionRouter
from masterclaw.routing_quality import (
    render_routing_quality_report,
    routing_quality_report,
)
from masterclaw.storage.sqlite import SQLiteStore
from masterclaw.telemetry import bind_trace, reset_trace, stage_span


def test_lexicon_candidate_ledger_is_idempotent_and_aggregates_distinct_events(
    tmp_path,
) -> None:
    store = SQLiteStore(tmp_path / "quality.sqlite3")
    store.initialize()
    observation = {
        "scenario": "play",
        "command": "show_game_status",
        "normalized_phrase": "how is the game going",
        "confidence": 0.97,
    }

    assert store.record_lexicon_candidate(event_id="event-1", **observation)
    assert not store.record_lexicon_candidate(event_id="event-1", **observation)
    assert store.record_lexicon_candidate(event_id="event-2", **observation)
    with pytest.raises(RuntimeError, match="replay identity mismatch"):
        store.record_lexicon_candidate(
            event_id="event-1",
            **{**observation, "command": "show_scene"},
        )

    report = routing_quality_report(store, since_hours=1, min_count=2)

    assert report["lexicon_candidates"] == [
        {
            "scenario": "play",
            "command": "show_game_status",
            "normalized_phrase": "how is the game going",
            "observations": 2,
            "average_confidence": 0.97,
            "first_seen": report["lexicon_candidates"][0]["first_seen"],
            "last_seen": report["lexicon_candidates"][0]["last_seen"],
        }
    ]


def test_quality_rates_keep_pipeline_and_model_denominators_separate(tmp_path) -> None:
    store = SQLiteStore(tmp_path / "quality.sqlite3")
    store.initialize()
    binding = bind_trace(store, trace_id="quality:test")
    try:
        with stage_span(
            "pipeline.total",
            component="bounded_pipeline",
            operation="ActionInterpretation",
        ):
            with stage_span(
                "pipeline.repair_completion",
                component="bounded_pipeline",
                operation="ActionInterpretation",
            ):
                pass
            with stage_span(
                "fallback.primary",
                component="completion_fallback",
                operation="ActionInterpretation",
            ):
                pass
            with stage_span(
                "fallback.secondary",
                component="completion_fallback",
                operation="ActionInterpretation",
            ):
                pass
        with pytest.raises(PipelineValidationError):
            with stage_span(
                "pipeline.total",
                component="bounded_pipeline",
                operation="ActionInterpretation",
            ):
                raise PipelineValidationError("invalid after bounded repair")
    finally:
        reset_trace(binding)

    report = routing_quality_report(store, since_hours=1, min_count=1)
    quality = report["pipeline_quality"]

    assert quality == [
        {
            "pipeline_contract": "ActionInterpretation",
            "pipeline_runs": 2,
            "repair_attempts": 1,
            "invalid_results": 1,
            "model_primary_attempts": 1,
            "model_fallback_attempts": 1,
            "repair_rate": 0.5,
            "invalid_result_rate": 0.5,
            "model_fallback_rate": 1.0,
        }
    ]
    assert "denominators are intentionally separate" in render_routing_quality_report(
        report, "table"
    )
    assert json.loads(render_routing_quality_report(report, "json")) == report


def test_high_confidence_read_only_state_decision_is_persisted_once_on_replay(
    tmp_path,
) -> None:
    class StatusDecision:
        async def complete(self, **_kwargs) -> CompletionResult:
            return CompletionResult(
                '{"command":"show_game_status","argument":null,"confidence":0.97,'
                '"evidence":"the player asks for current session state"}',
                used_tool=True,
            )

    store = SQLiteStore(tmp_path / "quality.sqlite3")
    store.initialize()
    store.create_world(WorldState("world", "World"))
    store.create_game(GameState("game", "world", GameLifecycle.ACTIVE, locale="en"))
    store.bind_channel(channel_id="channel", game_id="game")
    application = MessageApplication(
        store=store,
        context=ContextAssembler(Path(__file__).parents[1] / "prompts"),
        state_router=StateDecisionRouter(StatusDecision()),
    )
    message = IncomingMessage.now(
        event_id="status-candidate",
        channel_id="channel",
        author_id="alice",
        content="Could you remind me where the session currently stands?",
    )

    asyncio.run(application(message))
    asyncio.run(application(message))

    with store.connect() as connection:
        rows = connection.execute(
            """SELECT event_id, scenario, command, normalized_phrase, confidence
               FROM lexicon_candidates"""
        ).fetchall()
    assert [dict(row) for row in rows] == [
        {
            "event_id": "status-candidate",
            "scenario": "play",
            "command": "show_game_status",
            "normalized_phrase": "could you remind me where the session currently stands",
            "confidence": 0.97,
        }
    ]


def test_routing_quality_cli_renders_json(tmp_path, capsys) -> None:
    database = tmp_path / "quality.sqlite3"
    store = SQLiteStore(database)
    store.initialize()
    store.record_lexicon_candidate(
        event_id="candidate",
        scenario="play",
        command="show_scene",
        normalized_phrase="what is around me",
        confidence=0.99,
    )

    assert (
        main(
            [
                "routing-quality-report",
                "--database",
                str(database),
                "--since-hours",
                "1",
                "--min-count",
                "1",
                "--format",
                "json",
            ]
        )
        == 0
    )

    report = json.loads(capsys.readouterr().out)
    assert report["lexicon_candidates"][0]["normalized_phrase"] == "what is around me"
