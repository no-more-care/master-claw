import asyncio
import json
from dataclasses import FrozenInstanceError, asdict, replace
from datetime import UTC, datetime
from pathlib import Path

import pytest
from test_player_narration import NeverIntent, setup

from masterclaw.app.decision_checkpoints import (
    DecisionContextChangedError,
    decision_input_fingerprint,
    decision_output_type_name,
)
from masterclaw.app.i18n import tr
from masterclaw.app.legacy_player_narration_review import LegacyPlayerNarrationReview
from masterclaw.app.message_handler import MessageApplication
from masterclaw.app.player_narration_review import (
    NarrationAssessment,
    NarrationReason,
    NarrationText,
    NarrationVerdict,
    capture_narration_review_snapshot,
    legacy_v1_fingerprint_projection,
)
from masterclaw.context.assembler import ContextAssembler, ContextHistory
from masterclaw.context.manifests import PipelineName, manifest_for
from masterclaw.domain.models import IncomingMessage
from masterclaw.pipelines.base import CompletionResult, TransientProviderError
from masterclaw.pipelines.player_narration import (
    PlayerNarrationReview,
    create_player_narration_pipeline,
)
from masterclaw.pipelines.state_decision import StateDecisionRouter

OUTPUT_ID = "masterclaw.pipelines.player_narration.PlayerNarrationReview:v1:278f9b09fd5b4a2b"


class Pipeline:
    output_type = PlayerNarrationReview

    def __init__(self, accepted=True):
        self.calls = []
        self.result = PlayerNarrationReview(
            accepted=accepted,
            reason="Private provider feedback, not a domain code",
            approved_narration="Я отступаю. I step back." if accepted else None,
            scale_back_request=None if accepted else "  Reduce the scale / уменьшите масштаб  ",
        )

    async def run(self, *, task, context):
        self.calls.append((task, context))
        return self.result


def capture(store, *, channel_id="game"):
    context = ContextAssembler(Path(__file__).parents[1] / "prompts")
    application = MessageApplication(
        store=store,
        context=context,
        state_router=StateDecisionRouter(NeverIntent()),
    )
    pending = store.open_pending(game_id="game", player_id="alice")
    roll = store.roll_by_id(pending.payload["roll_id"])
    actor = store.character_for_player(game_id="game", player_id="alice")
    scene = store.scene_projection(game_id="game", player_id="alice")
    fiction, source = application._player_narration_source_context(
        pending=pending,
        roll=roll,
        character=actor,
        scene=scene,
    )
    game = store.game_state("game")
    message = IncomingMessage(
        "review", channel_id, "alice", "Я отступаю / I step back", datetime.now(UTC)
    )
    snapshot = capture_narration_review_snapshot(
        capture_context=application._capture_context_inputs,
        message=message,
        pending=pending,
        source_pending=source,
        fiction=fiction,
        roll=roll,
        rights_level=game.narrator_rights_level.value,
        locale=game.locale,
        session_brief=application._narrative_session_brief(game),
        scene=scene,
        actor_projection=application._actor_character_projection(game_id="game", player_id="alice"),
    )
    return application, snapshot, pending, source, roll, message


def test_context_task_and_output_identity_match_legacy(tmp_path, monkeypatch):
    store = setup(tmp_path)
    events = [{"event_type": "scene_patched", "payload": {"summary": "Дверь закрыта"}}]
    chats = [{"role": "user", "content": "Channel-specific fiction"}]

    def recent_events(*, game_id, limit):
        assert (game_id, limit) == ("game", 3)
        return events

    def recent_chats(*, game_id, channel_id, player_id, limit):
        assert (game_id, channel_id, player_id, limit) == ("game", "side-channel", "alice", 1)
        return chats

    monkeypatch.setattr(store, "recent_domain_events", recent_events)
    monkeypatch.setattr(store, "recent_chat_messages", recent_chats)
    app, snapshot, pending, source, roll, message = capture(store, channel_id="side-channel")
    manifest = manifest_for(PipelineName.PLAYER_NARRATION_REVIEW)
    scene = store.scene_projection(game_id="game", player_id="alice")
    # Literal pre-extraction inputs and enrichment, independent of the snapshot factory.
    projections = {
        "session_brief": app._narrative_session_brief(store.game_state("game")),
        "current_scene": scene,
        "actor_character": app._actor_character_projection(game_id="game", player_id="alice"),
        "roll_result": {
            "hits": roll.hits,
            "difficulty": roll.difficulty,
            "narrator_rights": roll.narrator_rights.value,
            "narrator_rights_level": store.game_state("game").narrator_rights_level.value,
            "original_declaration": str(source.payload.get("declaration", "")),
            "pending_prompt": pending.prompt,
        },
        "submitted_narration": message.content,
    }
    wrapper_context = app._assemble_context(
        manifest,
        projections,
        game_id="game",
        channel_id="side-channel",
        player_id="alice",
    )
    enriched = {
        **projections,
        "current_scene": {
            **scene,
            "participant_characters": [{"player_id": "alice", "name": "Hero"}],
        },
        "public_world_context": app._world_context_projection(
            game_id="game",
            scene=scene,
            include_secret=False,
        ),
    }
    expected = app._context.assemble(manifest, enriched, history=ContextHistory(events, chats))
    pipeline = Pipeline()
    adapter = LegacyPlayerNarrationReview(store=store, context=app._context, pipeline=pipeline)
    assessment = asyncio.run(adapter.assess(snapshot, "review"))
    task, assembled = pipeline.calls[0]
    assert task.encode() == b"Review the submitted player narration."
    assert assembled == expected == wrapper_context
    assert assembled.dynamic_context.encode() == expected.dynamic_context.encode()
    assert "participant_characters" in assembled.dynamic_context
    assert "Channel-specific fiction" in assembled.dynamic_context
    assert decision_output_type_name(PlayerNarrationReview) == OUTPUT_ID
    assert assessment == NarrationAssessment(
        NarrationVerdict.ALLOW, NarrationReason.LEGACY_ACCEPTED
    )
    assert "feedback" not in json.dumps(asdict(assessment))
    # Neither source mutations nor mutations of decoded views alter the detached snapshot.
    events[0]["payload"]["summary"] = "changed"
    snapshot.inputs.projections["current_scene"]["title"] = "changed"
    assert adapter.assemble(snapshot) == expected
    with pytest.raises(FrozenInstanceError):
        snapshot.hits = 99


@pytest.mark.parametrize("accepted", [True, False])
def test_old_checkpoint_replay_and_text_materialization_need_zero_model_calls(tmp_path, accepted):
    store = setup(tmp_path)
    app, snapshot, pending, source, roll, message = capture(store)
    pipeline = Pipeline(accepted)
    # Original literal v1 algorithm: importantly no enriched history or rights settings.
    fingerprint = decision_input_fingerprint(
        {
            "stage": "player_narration_review",
            "submitted_narration": message.content,
            "pending_interaction_id": pending.interaction_id,
            "pending_revision": pending.revision,
            "source_interaction_id": source.interaction_id,
            "source_interaction_revision": source.revision,
            "source_scene_revision": int(source.payload["scene_revision"]),
            "source_location_revision": int(source.payload["location_revision"]),
            "source_actor_revision": int(source.payload["character_revision"]),
            "roll_id": roll.roll_id,
            **snapshot.fiction.as_mapping(),
        }
    )
    assert fingerprint == decision_input_fingerprint(legacy_v1_fingerprint_projection(snapshot))
    store.checkpoint_decision(
        event_id="review",
        pipeline_key="player_narration_review",
        output_type=OUTPUT_ID,
        payload=pipeline.result.model_dump(mode="json"),
        game_id="game",
        input_fingerprint=fingerprint,
    )
    adapter = LegacyPlayerNarrationReview(store=store, context=app._context, pipeline=pipeline)
    assessment = asyncio.run(adapter.assess(snapshot, "review"))
    assert assessment.replayed
    # A new instance simulates a worker restart; no process-local prose cache is necessary.
    restarted = LegacyPlayerNarrationReview(store=store, context=app._context, pipeline=pipeline)
    text = asyncio.run(restarted.materialize(snapshot, assessment, "review"))
    assert text.publication_text == pipeline.result.approved_narration
    assert text.feedback_text == pipeline.result.scale_back_request
    assert pipeline.calls == []
    with pytest.raises(DecisionContextChangedError, match="fingerprint mismatch"):
        asyncio.run(adapter.assess(replace(snapshot, submitted_text="changed"), "review"))
    assert pipeline.calls == []
    with pytest.raises(RuntimeError, match="checkpoint is missing"):
        asyncio.run(adapter.materialize(snapshot, assessment, "missing"))
    assert pipeline.calls == []


def test_fresh_assessment_preserves_payload_and_materializes_once(tmp_path):
    store = setup(tmp_path)
    app, snapshot, *_ = capture(store)
    pipeline = Pipeline(False)
    adapter = LegacyPlayerNarrationReview(store=store, context=app._context, pipeline=pipeline)
    first = asyncio.run(adapter.assess(snapshot, "review"))
    assert not first.replayed
    assert first.reason is NarrationReason.LEGACY_REJECTED
    assert first.verdict is NarrationVerdict.DENY
    assert asyncio.run(adapter.materialize(snapshot, first, "review")).feedback_text == (
        "  Reduce the scale / уменьшите масштаб  "
    )
    assert asyncio.run(adapter.assess(snapshot, "review")).replayed
    assert len(pipeline.calls) == 1
    payload = store.decision_checkpoint(
        event_id="review",
        pipeline_key="player_narration_review",
        output_type=OUTPUT_ID,
        game_id="game",
        input_fingerprint=decision_input_fingerprint(legacy_v1_fingerprint_projection(snapshot)),
    )
    assert payload == pipeline.result.model_dump(mode="json")
    assert "replayed" not in payload


def test_ports_are_paired_and_mutually_exclusive_with_legacy_pipeline(tmp_path):
    store = setup(tmp_path)
    app, snapshot, *_ = capture(store)
    pipeline = Pipeline()
    adapter = LegacyPlayerNarrationReview(store=store, context=app._context, pipeline=pipeline)
    common = dict(
        store=store, context=app._context, state_router=StateDecisionRouter(NeverIntent())
    )
    for kwargs in ({"narration_rights_decider": adapter}, {"narration_text_port": adapter}):
        with pytest.raises(ValueError, match="provided together"):
            MessageApplication(**common, **kwargs)
        with pytest.raises(ValueError, match="not both"):
            MessageApplication(**common, player_narration_pipeline=pipeline, **kwargs)
    injected = MessageApplication(
        **common, narration_rights_decider=adapter, narration_text_port=adapter
    )
    assert injected._narration_rights_decider is injected._narration_text_port is adapter
    with pytest.raises(ValueError, match="either publication or feedback"):
        NarrationText()


@pytest.mark.parametrize("accepted", [True, False])
def test_secret_guards_preserve_pending_and_hide_legacy_prose(tmp_path, monkeypatch, accepted):
    store = setup(tmp_path)
    app, snapshot, pending, _, _, message = capture(store)
    secret = "The silver lantern secretly imprisons the vanished queen behind the northern wall."
    pipeline = Pipeline(accepted)
    pipeline.result = PlayerNarrationReview(
        accepted=accepted,
        reason=secret,
        approved_narration=secret if accepted else None,
        scale_back_request=None if accepted else secret,
    )
    adapter = LegacyPlayerNarrationReview(store=store, context=app._context, pipeline=pipeline)
    application = MessageApplication(
        store=store,
        context=app._context,
        state_router=StateDecisionRouter(NeverIntent()),
        narration_rights_decider=adapter,
        narration_text_port=adapter,
    )
    monkeypatch.setattr(application, "_secret_plot_for_game", lambda _: secret)
    result = asyncio.run(application._handle_player_narration(message=message, pending=pending))
    assert result == tr("ru", manifest_for(PipelineName.PLAYER_NARRATION_REVIEW).on_invalid.value)
    assert secret not in result
    assert store.open_pending(game_id="game", player_id="alice") is not None
    assert len(pipeline.calls) == 1


def test_post_review_stale_guard_still_prevents_publication(tmp_path):
    store = setup(tmp_path)
    app, snapshot, pending, _, _, message = capture(store)

    class MutatingPipeline(Pipeline):
        async def run(self, **kwargs):
            result = await super().run(**kwargs)
            store.apply_scene_patch(
                game_id="game",
                scene_id="room",
                expected_revision=snapshot.fiction.scene_revision,
                causation_id="concurrent",
                summary="Another event changes the room.",
                add_facts=["The door is now barred"],
                remove_facts=[],
            )
            return result

    pipeline = MutatingPipeline()
    adapter = LegacyPlayerNarrationReview(store=store, context=app._context, pipeline=pipeline)
    application = MessageApplication(
        store=store,
        context=app._context,
        state_router=StateDecisionRouter(NeverIntent()),
        narration_rights_decider=adapter,
        narration_text_port=adapter,
    )
    result = asyncio.run(application._handle_player_narration(message=message, pending=pending))
    assert result == tr("ru", "fiction_context_changed_retry")
    assert not store.has_scene_patch(f"player-narration:{pending.interaction_id}")


def test_mention_validation_repair_and_exception_identity_stay_legacy(tmp_path):
    store = setup(tmp_path)
    app, snapshot, pending, _, _, message = capture(store)

    class MentionCompletion:
        calls = 0

        async def complete(self, **kwargs):
            self.calls += 1
            return CompletionResult(
                json.dumps(
                    {
                        "accepted": True,
                        "reason": "Fine",
                        "scale_back_request": None,
                        "approved_narration": "I call @everyone.",
                    }
                ),
                used_tool=True,
            )

    completion = MentionCompletion()
    application = MessageApplication(
        store=store,
        context=app._context,
        state_router=StateDecisionRouter(NeverIntent()),
        player_narration_pipeline=create_player_narration_pipeline(completion),
    )
    result = asyncio.run(application._handle_player_narration(message=message, pending=pending))
    assert result == tr("ru", manifest_for(PipelineName.PLAYER_NARRATION_REVIEW).on_invalid.value)
    assert completion.calls == 2  # Exact original one-repair bounded pipeline behavior.
    assert store.open_pending(game_id="game", player_id="alice") is not None

    for error in (TransientProviderError("temporary"), asyncio.CancelledError()):

        class FailingPipeline(Pipeline):
            async def run(self, error=error, **kwargs):
                raise error

        adapter = LegacyPlayerNarrationReview(
            store=store,
            context=app._context,
            pipeline=FailingPipeline(),
        )
        with pytest.raises(type(error)) as caught:
            asyncio.run(adapter.assess(snapshot, "review"))
        assert caught.value is error
