import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from masterclaw.app.action_service import ActionService
from masterclaw.app.message_handler import MessageApplication
from masterclaw.context.assembler import ContextAssembler
from masterclaw.domain.characters import CharacterState
from masterclaw.domain.mechanics import CharacterSheet, Flag, FlagType, PoolProposal, Trait
from masterclaw.domain.models import GameLifecycle, HandlerResponse, IncomingMessage
from masterclaw.domain.state import (
    GameState,
    PendingInteraction,
    PendingKind,
    PendingStatus,
    WorldState,
)
from masterclaw.pipelines.action import create_action_pipeline
from masterclaw.pipelines.base import CompletionResult
from masterclaw.pipelines.state_decision import StateDecisionRouter
from masterclaw.pipelines.world_intake import create_world_intake_pipeline
from masterclaw.storage.sqlite import SQLiteStore
from masterclaw.telemetry import current_game_id


class FakeCompletion:
    def __init__(self) -> None:
        self.calls = 0

    async def complete(self, **kwargs) -> CompletionResult:
        self.calls += 1
        assert '"value":"world_management"' in kwargs["context"]
        return CompletionResult(
            '{"command":"clarify","argument":null,"confidence":1,"evidence":"needs clarification"}',
            used_tool=True,
        )


class StaticCompletion:
    def __init__(self, response: str) -> None:
        self.response = response

    async def complete(self, **kwargs) -> CompletionResult:
        return CompletionResult(self.response, used_tool=True)


def _add_playable_character(store: SQLiteStore) -> None:
    sheet = CharacterSheet(
        "Hero",
        tuple(Trait(f"Trait {index}", 3, ()) for index in range(6)),
        (
            Flag("Friend", FlagType.RELATIONSHIP),
            Flag("Goal", FlagType.GOAL),
            Flag("Belief", FlagType.BELIEF),
        ),
    )
    store.create_character(CharacterState("hero", "game", "alice", "Bio", sheet))
    store.create_scene(scene_id="scene", game_id="game", title="Scene")
    store.place_player(game_id="game", player_id="alice", scene_id="scene")


def test_unbound_message_gets_world_mode_before_llm_classification(tmp_path) -> None:
    store = SQLiteStore(tmp_path / "db.sqlite3")
    store.initialize()
    app = MessageApplication(
        store=store,
        context=ContextAssembler(Path(__file__).parents[1] / "prompts"),
        state_router=StateDecisionRouter(FakeCompletion()),
    )
    response = asyncio.run(
        app(
            IncomingMessage.now(
                event_id="1", channel_id="channel", author_id="alice", content="Создай мир"
            )
        )
    )
    assert "управление мирами" in response.lower()
    assert "пайплайн генерации миров не настроен" in response.lower()


def test_slash_command_does_not_spend_an_llm_call(tmp_path) -> None:
    store = SQLiteStore(tmp_path / "db.sqlite3")
    store.initialize()
    completion = FakeCompletion()
    app = MessageApplication(
        store=store,
        context=ContextAssembler(Path(__file__).parents[1] / "prompts"),
        state_router=StateDecisionRouter(completion),
    )
    response = asyncio.run(
        app(
            IncomingMessage.now(
                event_id="1", channel_id="channel", author_id="alice", content="/world create"
            )
        )
    )
    assert "/world create" in response
    assert completion.calls == 0


def test_unbound_english_message_uses_english_application_locale(tmp_path) -> None:
    store = SQLiteStore(tmp_path / "db.sqlite3")
    store.initialize()
    app = MessageApplication(
        store=store,
        context=ContextAssembler(Path(__file__).parents[1] / "prompts"),
        state_router=StateDecisionRouter(FakeCompletion()),
    )

    response = asyncio.run(
        app(
            IncomingMessage.now(
                event_id="english-world",
                channel_id="channel",
                author_id="alice",
                content="Create a new world",
            )
        )
    )

    assert "I am not sure what you want to do" in response
    assert "You can view the world catalogue" in response
    assert "WORLD MANAGEMENT" in response


def test_nonempty_world_catalog_is_fully_localized_for_english_request(tmp_path) -> None:
    store = SQLiteStore(tmp_path / "db.sqlite3")
    store.initialize()
    store.create_world(WorldState("english-world", "Storm City"))
    app = MessageApplication(
        store=store,
        context=ContextAssembler(Path(__file__).parents[1] / "prompts"),
        state_router=StateDecisionRouter(FakeCompletion()),
    )

    response = asyncio.run(
        app(
            IncomingMessage.now(
                event_id="english-catalog",
                channel_id="channel",
                author_id="alice",
                content="what can you do",
            )
        )
    )

    assert "AVAILABLE WORLDS" in response
    assert "**Themes:**" in response
    assert "ДОСТУПНЫЕ МИРЫ" not in response
    assert "**Темы:**" not in response


def test_successful_world_selection_declares_its_completion_game(tmp_path) -> None:
    store = SQLiteStore(tmp_path / "db.sqlite3")
    store.initialize()
    store.create_world(WorldState("storm", "Storm City"))
    world = store.world_state("storm")
    assert world is not None
    store.update_world_content(
        world_id=world.world_id,
        expected_revision=world.revision,
        status="approved",
        content={
            "locations": [
                {
                    "location_id": "harbor",
                    "name": "Storm Harbor",
                    "description": "A harbor beneath a permanent storm.",
                }
            ]
        },
    )
    completion = FakeCompletion()
    app = MessageApplication(
        store=store,
        context=ContextAssembler(Path(__file__).parents[1] / "prompts"),
        state_router=StateDecisionRouter(completion),
    )

    response = asyncio.run(
        app(
            IncomingMessage.now(
                event_id="select-storm",
                channel_id="channel",
                author_id="alice",
                content="choose world Storm City",
            )
        )
    )

    assert isinstance(response, HandlerResponse)
    assert response.completion_game_id == "game_select-storm"
    assert store.channel_state("channel").game_id == "game_select-storm"
    assert completion.calls == 0


def test_world_creation_does_not_persist_after_channel_binds_during_intake(tmp_path) -> None:
    store = SQLiteStore(tmp_path / "db.sqlite3")
    store.initialize()
    store.create_world(WorldState("other-world", "Other World"))
    store.create_game(GameState("other-game", "other-world", GameLifecycle.ACTIVE))

    class BindingWorldIntake:
        async def complete(self, **kwargs) -> CompletionResult:
            store.bind_channel(channel_id="channel", game_id="other-game")
            return CompletionResult(
                '{"title":"Ember Reach",'
                '"brief":"A volcanic frontier where rival guilds race for ancient engines.",'
                '"specified_fields":["title"]}',
                used_tool=True,
            )

    app = MessageApplication(
        store=store,
        context=ContextAssembler(Path(__file__).parents[1] / "prompts"),
        state_router=StateDecisionRouter(
            StaticCompletion(
                '{"command":"create_world","argument":null,"confidence":1,'
                '"evidence":"explicit detailed world request"}'
            )
        ),
        world_intake_pipeline=create_world_intake_pipeline(BindingWorldIntake()),
    )

    response = asyncio.run(
        app(
            IncomingMessage.now(
                event_id="world-race",
                channel_id="channel",
                author_id="alice",
                content=(
                    "Create a new world called Ember Reach about guilds racing for "
                    "ancient engines on a volcanic frontier."
                ),
            )
        )
    )

    assert "No changes were saved" in response
    assert store.channel_state("channel").game_id == "other-game"
    assert store.world_state("world_world-race") is None
    assert store.world_workspace("channel") is None


def test_world_selection_cas_does_not_overwrite_unrelated_binding_or_create_game(
    tmp_path,
    monkeypatch,
) -> None:
    store = SQLiteStore(tmp_path / "db.sqlite3")
    store.initialize()
    store.create_world(WorldState("storm", "Storm City"))
    world = store.world_state("storm")
    assert world is not None
    store.update_world_content(
        world_id=world.world_id,
        expected_revision=world.revision,
        status="approved",
        content={
            "locations": [
                {
                    "location_id": "harbor",
                    "name": "Storm Harbor",
                    "description": "A harbor beneath a permanent storm.",
                }
            ]
        },
    )
    store.create_world(WorldState("other-world", "Other World"))
    store.create_game(GameState("other-game", "other-world", GameLifecycle.ACTIVE))
    original_prepare = store.prepare_game_and_bind_if_channel_available

    def bind_other_game_first(game, *, channel_id):
        store.bind_channel(channel_id=channel_id, game_id="other-game")
        return original_prepare(game, channel_id=channel_id)

    monkeypatch.setattr(
        store,
        "prepare_game_and_bind_if_channel_available",
        bind_other_game_first,
    )
    app = MessageApplication(
        store=store,
        context=ContextAssembler(Path(__file__).parents[1] / "prompts"),
        state_router=StateDecisionRouter(FakeCompletion()),
    )

    response = asyncio.run(
        app(
            IncomingMessage.now(
                event_id="selection-race",
                channel_id="channel",
                author_id="alice",
                content="choose world Storm City",
            )
        )
    )

    assert "No changes were saved" in response
    assert store.channel_state("channel").game_id == "other-game"
    assert store.game_state("game_selection-race") is None


def test_frozen_game_response_panel_does_not_follow_a_later_live_rebind(tmp_path) -> None:
    store = SQLiteStore(tmp_path / "db.sqlite3")
    store.initialize()
    store.create_world(WorldState("world-a", "Frozen World A"))
    store.create_world(WorldState("world-b", "Live World B"))
    store.create_game(GameState("game-a", "world-a", GameLifecycle.ACTIVE))
    store.create_game(GameState("game-b", "world-b", GameLifecycle.ACTIVE))
    store.bind_channel(channel_id="channel", game_id="game-b")
    app = MessageApplication(
        store=store,
        context=ContextAssembler(Path(__file__).parents[1] / "prompts"),
        state_router=StateDecisionRouter(FakeCompletion()),
    )

    response = asyncio.run(
        app(
            IncomingMessage(
                event_id="queued-a-after-rebind",
                channel_id="channel",
                author_id="alice",
                content="/status",
                created_at=datetime(2026, 1, 1, tzinfo=UTC),
                routing_game_id="game-a",
                routing_lifecycle=GameLifecycle.ACTIVE,
                has_routing_snapshot=True,
            )
        )
    )

    assert "Frozen World A" in response
    assert "Live World B" not in response


def test_rejected_status_spam_does_not_update_gameplay_activity_clock(tmp_path) -> None:
    store = SQLiteStore(tmp_path / "db.sqlite3")
    store.initialize()
    store.create_world(WorldState("world", "World"))
    store.create_game(GameState("game", "world", GameLifecycle.ACTIVE))
    store.bind_channel(channel_id="channel", game_id="game")
    start = datetime(2026, 1, 1, tzinfo=UTC)
    store.start_activity_clock(game_id="game", started_at=start)
    completion = FakeCompletion()
    app = MessageApplication(
        store=store,
        context=ContextAssembler(Path(__file__).parents[1] / "prompts"),
        state_router=StateDecisionRouter(completion),
    )
    asyncio.run(
        app(
            IncomingMessage(
                event_id="1",
                channel_id="channel",
                author_id="alice",
                content="/status",
                created_at=start + timedelta(minutes=9),
            )
        )
    )
    assert store.activity_state("game")["active_seconds"] == 0
    assert completion.calls == 0


def test_answering_a_live_clarification_records_gameplay_activity(tmp_path) -> None:
    class PendingAnswerCompletion:
        async def complete(self, **kwargs) -> CompletionResult:
            return CompletionResult(
                '{"command":"answer_pending","argument":null,"confidence":1,'
                '"evidence":"direct answer"}',
                used_tool=True,
            )

    store = SQLiteStore(tmp_path / "db.sqlite3")
    store.initialize()
    store.create_world(WorldState("world", "World"))
    store.create_game(GameState("game", "world", GameLifecycle.ACTIVE))
    store.bind_channel(channel_id="channel", game_id="game")
    start = datetime(2026, 1, 1, tzinfo=UTC)
    store.start_activity_clock(game_id="game", started_at=start)
    store.put_pending(
        PendingInteraction(
            interaction_id="clarification",
            game_id="game",
            player_id="alice",
            scene_id=None,
            kind=PendingKind.CLARIFICATION,
            prompt="What do you do?",
            payload={},
        )
    )
    app = MessageApplication(
        store=store,
        context=ContextAssembler(Path(__file__).parents[1] / "prompts"),
        state_router=StateDecisionRouter(PendingAnswerCompletion()),
    )

    asyncio.run(
        app(
            IncomingMessage(
                event_id="clarification-answer",
                channel_id="channel",
                author_id="alice",
                content="I take the narrow passage.",
                created_at=start + timedelta(minutes=5),
            )
        )
    )

    assert store.activity_state("game")["active_seconds"] == 5 * 60


def test_cancelling_a_pending_interaction_does_not_record_activity(tmp_path) -> None:
    store = SQLiteStore(tmp_path / "db.sqlite3")
    store.initialize()
    store.create_world(WorldState("world", "World"))
    store.create_game(GameState("game", "world", GameLifecycle.ACTIVE))
    store.bind_channel(channel_id="channel", game_id="game")
    start = datetime(2026, 1, 1, tzinfo=UTC)
    store.start_activity_clock(game_id="game", started_at=start)
    store.put_pending(
        PendingInteraction(
            interaction_id="clarification",
            game_id="game",
            player_id="alice",
            scene_id=None,
            kind=PendingKind.CLARIFICATION,
            prompt="What do you do?",
            payload={},
        )
    )
    app = MessageApplication(
        store=store,
        context=ContextAssembler(Path(__file__).parents[1] / "prompts"),
        state_router=StateDecisionRouter(FakeCompletion()),
    )

    asyncio.run(
        app(
            IncomingMessage(
                event_id="clarification-cancel",
                channel_id="channel",
                author_id="alice",
                content="cancel",
                created_at=start + timedelta(minutes=5),
            )
        )
    )

    assert store.activity_state("game")["active_seconds"] == 0


def test_roll_confirmation_at_xp_boundary_does_not_invalidate_its_pool(tmp_path) -> None:
    store = SQLiteStore(tmp_path / "db.sqlite3")
    store.initialize()
    store.create_world(WorldState("world", "World"))
    store.create_game(GameState("game", "world", GameLifecycle.ACTIVE))
    store.bind_channel(channel_id="channel", game_id="game")
    _add_playable_character(store)
    start = datetime(2026, 1, 1, tzinfo=UTC)
    store.start_activity_clock(game_id="game", started_at=start)
    for minutes in (5, 10, 15, 20, 25):
        store.record_activity(game_id="game", occurred_at=start + timedelta(minutes=minutes))
    pending = ActionService(store).propose_roll(
        game_id="game",
        player_id="alice",
        scene_id="scene",
        proposal=PoolProposal(trait_names=("Trait 0",), difficulty=2),
        prompt="Confirm?",
    )
    app = MessageApplication(
        store=store,
        context=ContextAssembler(Path(__file__).parents[1] / "prompts"),
        state_router=StateDecisionRouter(FakeCompletion()),
        die=lambda: 4,
    )

    response = asyncio.run(
        app(
            IncomingMessage(
                event_id="boundary-confirmation",
                channel_id="channel",
                author_id="alice",
                content="confirm",
                created_at=start + timedelta(minutes=30),
            )
        )
    )

    assert store.roll_for_interaction(pending.interaction_id) is not None
    assert "1" in response
    assert store.activity_state("game")["active_seconds"] == 25 * 60
    character = store.character_for_player(game_id="game", player_id="alice")
    assert character is not None
    assert character.experience_earned == 0


def test_accepted_roll_declaration_at_xp_boundary_captures_the_new_revision(tmp_path) -> None:
    store = SQLiteStore(tmp_path / "db.sqlite3")
    store.initialize()
    store.create_world(WorldState("world", "World"))
    store.create_game(
        GameState(
            "game",
            "world",
            GameLifecycle.ACTIVE,
            progression_enabled=True,
        )
    )
    store.bind_channel(channel_id="channel", game_id="game")
    _add_playable_character(store)
    start = datetime(2026, 1, 1, tzinfo=UTC)
    store.start_activity_clock(game_id="game", started_at=start)
    for minutes in (5, 10, 15, 20, 25):
        store.record_activity(game_id="game", occurred_at=start + timedelta(minutes=minutes))
    app = MessageApplication(
        store=store,
        context=ContextAssembler(Path(__file__).parents[1] / "prompts"),
        state_router=StateDecisionRouter(
            StaticCompletion(
                '{"command":"declare_action","argument":null,"confidence":1,'
                '"evidence":"action declaration"}'
            )
        ),
        action_pipeline=create_action_pipeline(
            StaticCompletion(
                '{"resolution":"roll","trait_names":["Trait 0"],"aspect_names":[],'
                '"flag":null,"bonus_ids":[],"difficulty":2,'
                '"evidence":["uncertain opposition"],"clarification_question":null,'
                '"rejection_reason":null}'
            )
        ),
        die=lambda: 4,
    )

    asyncio.run(
        app(
            IncomingMessage(
                event_id="boundary-declaration",
                channel_id="channel",
                author_id="alice",
                content="I force the jammed hatch.",
                created_at=start + timedelta(minutes=30),
            )
        )
    )
    pending = store.open_pending(game_id="game", player_id="alice")
    character = store.character_for_player(game_id="game", player_id="alice")
    assert pending is not None and character is not None
    assert character.experience_earned == 1
    assert pending.payload["character_revision"] == character.revision

    asyncio.run(
        app(
            IncomingMessage(
                event_id="boundary-declaration-confirm",
                channel_id="channel",
                author_id="alice",
                content="confirm",
                created_at=start + timedelta(minutes=30),
            )
        )
    )

    assert store.roll_for_interaction(pending.interaction_id) is not None


def test_rejected_action_does_not_record_gameplay_activity(tmp_path) -> None:
    store = SQLiteStore(tmp_path / "db.sqlite3")
    store.initialize()
    store.create_world(WorldState("world", "World"))
    store.create_game(GameState("game", "world", GameLifecycle.ACTIVE))
    store.bind_channel(channel_id="channel", game_id="game")
    _add_playable_character(store)
    start = datetime(2026, 1, 1, tzinfo=UTC)
    store.start_activity_clock(game_id="game", started_at=start)
    app = MessageApplication(
        store=store,
        context=ContextAssembler(Path(__file__).parents[1] / "prompts"),
        state_router=StateDecisionRouter(
            StaticCompletion(
                '{"command":"declare_action","argument":null,"confidence":1,'
                '"evidence":"action declaration"}'
            )
        ),
        action_pipeline=create_action_pipeline(
            StaticCompletion(
                '{"resolution":"rejected","trait_names":[],"aspect_names":[],'
                '"flag":null,"bonus_ids":[],"difficulty":null,'
                '"evidence":["the Moon is unreachable"],"clarification_question":null,'
                '"rejection_reason":"That is impossible from this scene."}'
            )
        ),
    )

    asyncio.run(
        app(
            IncomingMessage(
                event_id="rejected-action",
                channel_id="channel",
                author_id="alice",
                content="I jump to the Moon.",
                created_at=start + timedelta(minutes=5),
            )
        )
    )

    assert store.activity_state("game")["active_seconds"] == 0


def test_llm_call_is_bound_to_the_channel_game_for_telemetry(tmp_path) -> None:
    store = SQLiteStore(tmp_path / "db.sqlite3")
    store.initialize()
    store.create_world(WorldState("world", "World"))
    store.create_game(GameState("game", "world", GameLifecycle.ACTIVE))
    store.bind_channel(channel_id="channel", game_id="game")

    class SessionAwareCompletion:
        async def complete(self, **kwargs) -> CompletionResult:
            assert current_game_id() == "game"
            return CompletionResult(
                '{"command":"clarify","argument":null,"confidence":1,"evidence":"test"}',
                used_tool=True,
            )

    app = MessageApplication(
        store=store,
        context=ContextAssembler(Path(__file__).parents[1] / "prompts"),
        state_router=StateDecisionRouter(SessionAwareCompletion()),
    )
    asyncio.run(
        app(
            IncomingMessage.now(
                event_id="telemetry",
                channel_id="channel",
                author_id="alice",
                content="Осматриваюсь",
            )
        )
    )
    assert current_game_id() is None


def test_invalid_state_decision_uses_scenario_fallback(tmp_path) -> None:
    class InvalidStateCompletion:
        async def complete(self, **kwargs) -> CompletionResult:
            return CompletionResult("not-json", used_tool=True)

    store = SQLiteStore(tmp_path / "db.sqlite3")
    store.initialize()
    app = MessageApplication(
        store=store,
        context=ContextAssembler(Path(__file__).parents[1] / "prompts"),
        state_router=StateDecisionRouter(InvalidStateCompletion()),
    )

    response = asyncio.run(
        app(
            IncomingMessage.now(
                event_id="invalid-state",
                channel_id="channel",
                author_id="alice",
                content="Не вполне понимаю, куда двигаться дальше",
            )
        )
    )

    assert "не уверен" in response


def test_invalid_collecting_decision_clarifies_without_revising_world(tmp_path) -> None:
    class InvalidStateCompletion:
        async def complete(self, **kwargs) -> CompletionResult:
            return CompletionResult("not-json", used_tool=True)

    store = SQLiteStore(tmp_path / "db.sqlite3")
    store.initialize()
    store.create_world(WorldState("world", "World"))
    store.save_world_workspace(
        channel_id="channel",
        world_id="world",
        stage="collecting",
        brief="Original brief",
        settings={},
        sources={},
    )
    app = MessageApplication(
        store=store,
        context=ContextAssembler(Path(__file__).parents[1] / "prompts"),
        state_router=StateDecisionRouter(InvalidStateCompletion()),
    )

    response = asyncio.run(
        app(
            IncomingMessage.now(
                event_id="invalid-collecting",
                channel_id="channel",
                author_id="alice",
                content="Не вполне понимаю, что делать дальше",
            )
        )
    )

    assert "не уверен" in response
    assert store.world_workspace("channel")["brief"] == "Original brief"


def test_low_confidence_world_revision_clarifies_without_mutation(tmp_path) -> None:
    class LowConfidenceRevision:
        async def complete(self, **kwargs) -> CompletionResult:
            return CompletionResult(
                '{"command":"revise_world","argument":null,"confidence":0.01,'
                '"evidence":"uncertain revision"}',
                used_tool=True,
            )

    store = SQLiteStore(tmp_path / "db.sqlite3")
    store.initialize()
    store.create_world(WorldState("world", "World"))
    store.save_world_workspace(
        channel_id="channel",
        world_id="world",
        stage="collecting",
        brief="Original brief",
        settings={},
        sources={},
    )
    app = MessageApplication(
        store=store,
        context=ContextAssembler(Path(__file__).parents[1] / "prompts"),
        state_router=StateDecisionRouter(LowConfidenceRevision()),
    )

    response = asyncio.run(
        app(
            IncomingMessage.now(
                event_id="uncertain-revision",
                channel_id="channel",
                author_id="alice",
                content="Может быть, стоит что-то поменять",
            )
        )
    )

    assert "не уверен" in response
    assert store.world_workspace("channel")["brief"] == "Original brief"


@pytest.mark.parametrize("confidence", [0.01, 0.99])
def test_llm_decision_cannot_publish_world_without_explicit_phrase(tmp_path, confidence) -> None:
    class ImplicitWorldApproval:
        def __init__(self, value: float) -> None:
            self.value = value

        async def complete(self, **kwargs) -> CompletionResult:
            return CompletionResult(
                '{"command":"confirm_world","argument":null,'
                f'"confidence":{self.value},"evidence":"positive review"}}',
                used_tool=True,
            )

    store = SQLiteStore(tmp_path / "db.sqlite3")
    store.initialize()
    store.create_world(WorldState("world", "World"))
    world = store.world_state("world")
    store.update_world_content(
        world_id="world",
        expected_revision=world.revision,
        content={"locations": [{"location_id": "start", "name": "Start"}]},
    )
    store.save_world_workspace(
        channel_id="channel",
        world_id="world",
        stage="review",
        brief="Review brief",
        settings={},
        sources={},
    )
    app = MessageApplication(
        store=store,
        context=ContextAssembler(Path(__file__).parents[1] / "prompts"),
        state_router=StateDecisionRouter(ImplicitWorldApproval(confidence)),
    )

    response = asyncio.run(
        app(
            IncomingMessage.now(
                event_id="implicit-approval",
                channel_id="channel",
                author_id="alice",
                content="Выглядит здорово, мне нравится",
            )
        )
    )

    assert "не уверен" in response
    assert store.world_state("world").status == "draft"
    assert store.world_workspace("channel") is not None


def test_confidence_point_zero_one_does_not_start_game(tmp_path) -> None:
    class LowConfidenceStart:
        async def complete(self, **kwargs) -> CompletionResult:
            return CompletionResult(
                '{"command":"start_game","argument":null,"confidence":0.01,'
                '"evidence":"uncertain start"}',
                used_tool=True,
            )

    store = SQLiteStore(tmp_path / "db.sqlite3")
    store.initialize()
    store.create_world(WorldState("world", "World"))
    store.create_game(GameState("game", "world", GameLifecycle.PREPARING))
    store.bind_channel(channel_id="channel", game_id="game")
    app = MessageApplication(
        store=store,
        context=ContextAssembler(Path(__file__).parents[1] / "prompts"),
        state_router=StateDecisionRouter(LowConfidenceStart()),
    )

    response = asyncio.run(
        app(
            IncomingMessage.now(
                event_id="uncertain-start",
                channel_id="channel",
                author_id="alice",
                content="Наверное, уже можно переходить дальше",
            )
        )
    )

    assert "не уверен" in response
    assert store.game_state("game").lifecycle is GameLifecycle.PREPARING


def test_low_confidence_read_only_intent_also_clarifies(tmp_path) -> None:
    class LowConfidenceStatus:
        async def complete(self, **kwargs) -> CompletionResult:
            return CompletionResult(
                '{"command":"show_game_status","argument":null,"confidence":0.4,'
                '"evidence":"possibly asking for status"}',
                used_tool=True,
            )

    store = SQLiteStore(tmp_path / "db.sqlite3")
    store.initialize()
    store.create_world(WorldState("world", "World"))
    store.create_game(GameState("game", "world", GameLifecycle.ACTIVE))
    store.bind_channel(channel_id="channel", game_id="game")
    app = MessageApplication(
        store=store,
        context=ContextAssembler(Path(__file__).parents[1] / "prompts"),
        state_router=StateDecisionRouter(LowConfidenceStatus()),
    )

    response = asyncio.run(
        app(
            IncomingMessage.now(
                event_id="uncertain-status",
                channel_id="channel",
                author_id="alice",
                content="Ну и как там вообще?",
            )
        )
    )

    assert "Я не уверен" in response


def test_oversized_message_is_rejected_before_any_model_call(tmp_path) -> None:
    class NeverCompletion:
        async def complete(self, **kwargs):
            raise AssertionError("oversized input must not reach a model")

    store = SQLiteStore(tmp_path / "db.sqlite3")
    store.initialize()
    app = MessageApplication(
        store=store,
        context=ContextAssembler(Path(__file__).parents[1] / "prompts"),
        state_router=StateDecisionRouter(NeverCompletion()),
    )
    response = asyncio.run(
        app(
            IncomingMessage.now(
                event_id="too-long",
                channel_id="channel",
                author_id="alice",
                content="x" * 12_001,
            )
        )
    )
    assert "too long" in response


def test_stale_pool_confirmation_is_cancelled_with_a_reminder(tmp_path) -> None:
    class NeverCompletion:
        async def complete(self, **kwargs):
            raise AssertionError("expired pending must not reach a model")

    store = SQLiteStore(tmp_path / "db.sqlite3")
    store.initialize()
    store.create_world(WorldState("world", "World"))
    store.create_game(GameState("game", "world", GameLifecycle.ACTIVE))
    store.bind_channel(channel_id="channel", game_id="game")
    store.put_pending(
        PendingInteraction(
            "old-pool",
            "game",
            "alice",
            None,
            PendingKind.POOL_CONFIRMATION,
            "Подтвердите пул",
        )
    )
    with store.transaction() as connection:
        connection.execute(
            "UPDATE pending_interactions SET created_at = ? WHERE interaction_id = ?",
            ("2026-01-01T00:00:00+00:00", "old-pool"),
        )
    app = MessageApplication(
        store=store,
        context=ContextAssembler(Path(__file__).parents[1] / "prompts"),
        state_router=StateDecisionRouter(NeverCompletion()),
    )
    response = asyncio.run(
        app(
            IncomingMessage(
                event_id="after-a-day",
                channel_id="channel",
                author_id="alice",
                content="Что происходит?",
                created_at=datetime(2026, 1, 2, 1, tzinfo=UTC),
            )
        )
    )
    assert "подтверждение пула отменено" in response
    assert store.open_pending(game_id="game", player_id="alice") is None
    assert store.pending_by_id("old-pool").status is PendingStatus.EXPIRED


def test_open_pending_is_a_pure_read_for_stale_choice(tmp_path) -> None:
    store = SQLiteStore(tmp_path / "db.sqlite3")
    store.initialize()
    store.create_world(WorldState("world", "World"))
    store.create_game(GameState("game", "world", GameLifecycle.ACTIVE))
    store.bind_channel(channel_id="channel", game_id="game")
    store.put_pending(
        PendingInteraction(
            "old-choice",
            "game",
            "alice",
            None,
            PendingKind.CHOICE,
            "Какую дверь выбрать?",
            payload={"options": ["левая", "правая"]},
        )
    )
    with store.transaction() as connection:
        connection.execute(
            "UPDATE pending_interactions SET created_at = ? WHERE interaction_id = ?",
            ("2026-01-01T00:00:00+00:00", "old-choice"),
        )
    stale = store.open_pending(game_id="game", player_id="alice")

    assert stale is not None
    assert stale.interaction_id == "old-choice"
    assert store.pending_by_id("old-choice").status is PendingStatus.OPEN
