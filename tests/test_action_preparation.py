import asyncio
import json
import subprocess
import sys
from dataclasses import FrozenInstanceError

import pytest
from test_fiction_context_guards import (
    PROMPTS,
    FixedDecision,
    active_store,
    concurrent_mutation,
    message,
)

from masterclaw.app.action_preparation import (
    ActionPreparationService,
    MissingActionContext,
    PreparedAction,
)
from masterclaw.app.decision_checkpoints import (
    DecisionContextChangedError,
    decision_input_fingerprint,
    decision_output_type_name,
)
from masterclaw.app.fiction_context import FictionContextChangedError, FictionContextSnapshot
from masterclaw.app.handlers.support import HandlerSupport
from masterclaw.app.handlers.types import FictionContextSnapshot as LegacyFictionContextSnapshot
from masterclaw.app.message_handler import MessageApplication
from masterclaw.context.assembler import ContextAssembler, ContextAssemblyError
from masterclaw.context.manifests import PipelineName, manifest_for
from masterclaw.domain.state import PendingInteraction, PendingKind
from masterclaw.pipelines.action import ActionInterpretation
from masterclaw.pipelines.base import PipelineValidationError
from masterclaw.pipelines.state_decision import StateDecisionRouter

LEGACY_OUTPUT_TYPE = "masterclaw.pipelines.action.ActionInterpretation:v1:b27cbe6e9cfd9c13"


class Pipeline:
    output_type = ActionInterpretation

    def __init__(self, mutation=None, error=None):
        self.calls = []
        self.mutation = mutation
        self.error = error

    async def run(self, *, task, context):
        self.calls.append((task, context))
        if self.mutation:
            self.mutation()
        if self.error:
            raise self.error
        return ActionInterpretation(resolution="automatic", evidence=["The door is open"])


def assembler_for(store):
    support = HandlerSupport()
    support._store = store
    support._context = ContextAssembler(PROMPTS)
    return support


def service_for(store, pipeline):
    return ActionPreparationService(
        store=store, pipeline=pipeline, assemble_context=assembler_for(store)._assemble_context
    )


def legacy_fingerprint(msg, *, continuation=None, pending=None):
    # Literal pre-extraction projection, independent of service snapshot construction.
    return decision_input_fingerprint(
        {
            "stage": "action_interpretation",
            "message": msg.content,
            "continuation_context": continuation or {},
            "replacing_pending_id": None if pending is None else pending.interaction_id,
            "replacing_pending_revision": None if pending is None else pending.revision,
            "game_id": "game",
            "player_id": "alice",
            "character_id": "hero",
            "character_revision": 0,
            "scene_id": "room",
            "scene_revision": 0,
            "location_revision": 0,
            "participants": ["alice"],
        }
    )


@pytest.mark.parametrize(
    "continuation", [None, {}, {"player_answer": "Да", "pending_question": "Как?"}]
)
def test_task_and_assembled_context_are_byte_equivalent_to_legacy(
    tmp_path, continuation, monkeypatch
):
    store = active_store(tmp_path)

    def events(*, game_id, limit):
        assert game_id == "game" and limit == 6
        return [{"event_type": "scene_patched", "payload": {"summary": "Дверь открылась"}}]

    def chats(*, game_id, channel_id, player_id, limit):
        assert (game_id, channel_id, player_id, limit) == ("game", "game", "alice", 2)
        return [{"role": "user", "content": "I approach the door"}]

    monkeypatch.setattr(store, "recent_domain_events", events)
    monkeypatch.setattr(store, "recent_chat_messages", chats)
    support = assembler_for(store)
    pipeline = Pipeline()
    msg = message("prepare")
    scene = store.scene_projection(game_id="game", player_id="alice")
    expected_context = support._assemble_context(
        manifest_for(PipelineName.ACTION_INTERPRETATION),
        {
            "session_brief": {"game_id": "game", "locale": "ru", "participants_here": ["alice"]},
            "actor_character": {
                "player_id": "alice",
                "character_id": "hero",
                "revision": 0,
                "name": "Mara",
                "traits": [{"name": "Lore", "level": 2, "aspects": ["Runes"]}],
                "flags": [],
                "reserve": 7,
                "conditions": [],
                "plot_items": [],
                "temporary_bonuses": [],
            },
            "current_scene": scene,
        },
        game_id="game",
        channel_id="game",
        player_id="alice",
    )
    expected_task = "Interpret the declaration:\nI open the rune door."
    if continuation:
        expected_task += (
            "\nThe declaration continues a typed pending interaction. Use the following "
            "question/answer context as data, preserve the original intent, and do not "
            "reinterpret it as a separate action:\n"
            + json.dumps(continuation, ensure_ascii=False, sort_keys=True)
        )
    prepared = asyncio.run(
        service_for(store, pipeline).prepare(
            message=msg, game_id="game", locale="ru", continuation_context=continuation
        )
    )
    assert isinstance(prepared, PreparedAction)
    task, context = pipeline.calls[0]
    assert task.encode() == expected_task.encode()
    assert context.static_rules.encode() == expected_context.static_rules.encode()
    assert context.dynamic_context.encode() == expected_context.dynamic_context.encode()
    assert context == expected_context
    assert "## HISTORY domain_events" in context.dynamic_context
    assert "## HISTORY chat_messages" in context.dynamic_context
    assert "## STATE public_world_context" in context.dynamic_context
    assert prepared.snapshot.character == store.character_for_player(
        game_id="game", player_id="alice"
    )
    assert prepared.snapshot.scene == scene
    assert decision_output_type_name(ActionInterpretation) == LEGACY_OUTPUT_TYPE
    payload = store.decision_checkpoint(
        event_id=msg.event_id,
        pipeline_key="action_interpretation",
        output_type=LEGACY_OUTPUT_TYPE,
        game_id="game",
        input_fingerprint=legacy_fingerprint(msg, continuation=continuation),
    )
    assert payload == prepared.interpretation.model_dump(mode="json")


def test_legacy_checkpoint_payload_and_pending_fingerprint_replay_without_model_call(tmp_path):
    store = active_store(tmp_path)
    msg = message("legacy")
    pending = PendingInteraction(
        interaction_id="clarification-1",
        game_id="game",
        player_id="alice",
        scene_id="room",
        kind=PendingKind.CLARIFICATION,
        prompt="How?",
        revision=3,
    )
    continuation = {"player_answer": "Quietly"}
    checkpoint = dict(
        event_id=msg.event_id,
        pipeline_key="action_interpretation",
        output_type=LEGACY_OUTPUT_TYPE,
        game_id="game",
        input_fingerprint=legacy_fingerprint(msg, continuation=continuation, pending=pending),
    )
    payload = ActionInterpretation(resolution="automatic", evidence=["Legacy evidence"]).model_dump(
        mode="json"
    )
    store.checkpoint_decision(**checkpoint, payload=payload)
    pipeline = Pipeline(error=AssertionError("legacy replay must not call model"))
    prepared = asyncio.run(
        service_for(store, pipeline).prepare(
            message=msg,
            game_id="game",
            locale="ru",
            replacing_pending=pending,
            continuation_context=continuation,
        )
    )
    assert prepared.interpretation.evidence == ["Legacy evidence"]
    assert not pipeline.calls
    assert store.decision_checkpoint(**checkpoint) == payload


@pytest.mark.parametrize("kind", ["scene", "actor", "move", "participants"])
def test_service_revalidates_all_existing_fiction_revisions_after_model(tmp_path, kind):
    store = active_store(tmp_path)
    pipeline = Pipeline(mutation=lambda: concurrent_mutation(store, kind, "external"))
    service = service_for(store, pipeline)
    with pytest.raises(
        FictionContextChangedError, match="action interpretation context changed before commit"
    ):
        asyncio.run(service.prepare(message=message("stale"), game_id="game", locale="ru"))
    # The original result was checkpointed before the guard; retry must not call model again.
    with pytest.raises(DecisionContextChangedError, match="input fingerprint mismatch"):
        asyncio.run(service.prepare(message=message("stale"), game_id="game", locale="ru"))
    assert len(pipeline.calls) == 1


def test_prepared_snapshot_is_detached_and_prepared_reuse_skips_context_and_model(tmp_path):
    store = active_store(tmp_path)
    pipeline = Pipeline()
    prepared = asyncio.run(
        service_for(store, pipeline).prepare(message=message("first"), game_id="game", locale="ru")
    )
    with pytest.raises(FrozenInstanceError):
        prepared.snapshot.scene_json = "{}"
    scene = prepared.snapshot.scene
    scene["state"]["facts"].append("mutated copy")
    assert "mutated copy" not in prepared.snapshot.scene_json

    def forbidden_assembly(*args, **kwargs):
        raise AssertionError("prepared reuse must not assemble")

    service = ActionPreparationService(store=store, assemble_context=forbidden_assembly)
    reused = asyncio.run(
        service.prepare(
            message=message("reuse"), game_id="game", locale="ru", prepared_result=prepared
        )
    )
    assert reused.interpretation is prepared.interpretation
    assert reused.context == prepared.context
    assert len(pipeline.calls) == 1
    concurrent_mutation(store, "participants", "external")
    with pytest.raises(
        FictionContextChangedError, match="prepared action context changed before commit"
    ):
        asyncio.run(
            service.prepare(
                message=message("stale"), game_id="game", locale="ru", prepared_result=prepared
            )
        )


@pytest.mark.parametrize(
    "error", [PipelineValidationError("invalid"), ContextAssemblyError("bad context")]
)
def test_original_failure_object_propagates(tmp_path, error):
    store = active_store(tmp_path)
    service = service_for(store, Pipeline(error=error))
    with pytest.raises(type(error)) as raised:
        asyncio.run(service.prepare(message=message("error"), game_id="game", locale="ru"))
    assert raised.value is error


def test_absent_pipeline_and_missing_actor_context_keep_existing_order(tmp_path):
    store = active_store(tmp_path)
    with pytest.raises(
        PipelineValidationError, match="action interpretation pipeline is unavailable"
    ):
        asyncio.run(
            service_for(store, None).prepare(
                message=message("no-pipeline"), game_id="missing", locale="ru"
            )
        )
    result = asyncio.run(
        service_for(store, Pipeline()).prepare(
            message=message("no-actor"), game_id="missing", locale="ru"
        )
    )
    assert result is MissingActionContext.CHARACTER
    assert LegacyFictionContextSnapshot is FictionContextSnapshot


def test_missing_scene_skips_model_and_returns_existing_response_code(tmp_path, monkeypatch):
    store = active_store(tmp_path)
    monkeypatch.setattr(store, "scene_projection", lambda **kwargs: None)
    pipeline = Pipeline()
    result = asyncio.run(
        service_for(store, pipeline).prepare(
            message=message("no-scene"), game_id="game", locale="ru"
        )
    )
    assert result is MissingActionContext.SCENE
    assert not pipeline.calls


def test_message_application_accepts_service_or_legacy_pipeline_not_both(tmp_path):
    store = active_store(tmp_path)
    service = service_for(store, Pipeline())
    kwargs = dict(
        store=store,
        context=ContextAssembler(PROMPTS),
        state_router=StateDecisionRouter(FixedDecision("declare_action")),
    )
    app = MessageApplication(**kwargs, action_preparation=service)
    assert app._action_preparation is service
    with pytest.raises(ValueError, match="provide action preparation or a legacy action pipeline"):
        MessageApplication(**kwargs, action_preparation=service, action_pipeline=Pipeline())


@pytest.mark.parametrize("first", ["action_preparation", "fiction_context", "handlers.play"])
def test_preparation_import_boundary_has_no_cycle(first):
    process = subprocess.run(
        [
            sys.executable,
            "-c",
            f"import masterclaw.app.{first}; import masterclaw.cli; "
            "from masterclaw.app.handlers.play import PreparedAction; "
            "from masterclaw.app.action_preparation import PreparedAction as Canonical; "
            "assert PreparedAction is Canonical",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert process.returncode == 0, process.stderr
