import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from masterclaw.app.action_service import ActionService
from masterclaw.app.game_service import GameService
from masterclaw.app.message_handler import MessageApplication
from masterclaw.app.orchestrator import ChannelOrchestrator
from masterclaw.context.assembler import ContextAssembler
from masterclaw.domain.characters import CharacterState
from masterclaw.domain.mechanics import CharacterSheet, Flag, FlagType, PoolProposal, Trait
from masterclaw.domain.models import GameLifecycle, HandlerResponse, IncomingMessage
from masterclaw.domain.state import GameState, PendingKind, PendingStatus, WorldState
from masterclaw.pipelines.base import CompletionResult
from masterclaw.pipelines.state_decision import StateDecisionRouter
from masterclaw.storage.sqlite import SQLiteStore


class NeverCompletion:
    async def complete(self, **kwargs):
        raise AssertionError("deterministic session control must not call an LLM")


def playable_sheet(name: str) -> CharacterSheet:
    return CharacterSheet(
        name,
        tuple(Trait(f"{name} Trait {index}", 3, ()) for index in range(6)),
        (
            Flag("Friend", FlagType.RELATIONSHIP),
            Flag("Goal", FlagType.GOAL),
            Flag("Belief", FlagType.BELIEF),
        ),
    )


def lifecycle_store(tmp_path, lifecycle: GameLifecycle) -> SQLiteStore:
    store = SQLiteStore(tmp_path / "db.sqlite3")
    store.initialize()
    store.create_world(WorldState("world", "World", status="approved"))
    store.update_world_content(
        world_id="world",
        expected_revision=0,
        status="approved",
        content={
            "locations": [
                {
                    "id": "crossroads",
                    "name": "Crossroads",
                    "description": "A new beginning.",
                }
            ]
        },
    )
    store.create_game(
        GameState(
            "game",
            "world",
            lifecycle,
            locale="en",
            narrative_channel_id="channel",
        )
    )
    store.bind_channel(channel_id="channel", game_id="game")
    store.create_character(
        CharacterState("alice-hero", "game", "alice", "Hero", playable_sheet("Alice"))
    )
    if lifecycle is not GameLifecycle.PREPARING:
        store.create_scene(scene_id="scene", game_id="game", title="Crossroads")
        store.place_player(game_id="game", player_id="alice", scene_id="scene")
    if lifecycle is GameLifecycle.ACTIVE:
        store.start_activity_clock(
            game_id="game",
            started_at=datetime(2026, 1, 1, tzinfo=UTC),
        )
    return store


def crash_after_handler_then_retry(
    *,
    store: SQLiteStore,
    event: IncomingMessage,
    monkeypatch,
    completion=None,
) -> tuple[list[HandlerResponse], object]:
    assert store.enqueue(event)
    app = MessageApplication(
        store=store,
        context=ContextAssembler(Path(__file__).parents[1] / "prompts"),
        state_router=StateDecisionRouter(completion or NeverCompletion()),
    )
    original_complete = store.complete_batch
    completion_attempts = 0
    responses: list[HandlerResponse] = []

    def fail_first_completion(**kwargs):
        nonlocal completion_attempts
        completion_attempts += 1
        if completion_attempts == 1:
            raise RuntimeError("simulated failure after handler")
        return original_complete(**kwargs)

    monkeypatch.setattr(store, "complete_batch", fail_first_completion)

    async def recording_handler(message: IncomingMessage) -> str | HandlerResponse:
        response = await app(message)
        assert isinstance(response, HandlerResponse)
        responses.append(response)
        return response

    orchestrator = ChannelOrchestrator(store, recording_handler)
    with pytest.raises(RuntimeError, match="simulated failure after handler"):
        asyncio.run(orchestrator.process_available("channel"))
    replayed = asyncio.run(orchestrator.process_available("channel"))

    assert completion_attempts == 2
    assert replayed is not None
    assert len(responses) == 1
    assert replayed.items[0].text == responses[0].text
    return responses, replayed


@pytest.mark.parametrize(
    ("initial", "command", "expected", "success", "panel_mode"),
    (
        (
            GameLifecycle.PREPARING,
            "/game start",
            GameLifecycle.ACTIVE,
            "The game has started.",
            "**Mode:** play",
        ),
        (
            GameLifecycle.ACTIVE,
            "/game pause",
            GameLifecycle.PAUSED,
            "The game is paused",
            "**Mode:** game paused",
        ),
        (
            GameLifecycle.PAUSED,
            "/game resume",
            GameLifecycle.ACTIVE,
            "The game has resumed.",
            "**Mode:** play",
        ),
    ),
)
def test_lifecycle_replay_uses_frozen_branch_but_live_result_panel(
    tmp_path,
    monkeypatch,
    initial,
    command,
    expected,
    success,
    panel_mode,
) -> None:
    store = lifecycle_store(tmp_path, initial)
    game_before = store.game_state("game")
    activity_before = store.activity_state("game")
    event = IncomingMessage(
        event_id=f"lifecycle-{initial.value}",
        channel_id="channel",
        author_id="alice",
        content=command,
        created_at=datetime(2026, 1, 1, 1, tzinfo=UTC),
    )

    responses, _ = crash_after_handler_then_retry(
        store=store,
        event=event,
        monkeypatch=monkeypatch,
    )

    game_after = store.game_state("game")
    activity_after = store.activity_state("game")
    assert game_before is not None
    assert game_after is not None
    assert game_after.lifecycle is expected
    assert game_after.revision == game_before.revision + 1
    assert activity_after["revision"] == activity_before["revision"] + 1
    assert success in responses[0].text
    assert panel_mode in responses[0].text
    assert responses[0].render_live_status is True


def test_natural_start_replay_renders_live_play_panel(tmp_path, monkeypatch) -> None:
    store = lifecycle_store(tmp_path, GameLifecycle.PREPARING)
    before = store.game_state("game")
    event = IncomingMessage(
        event_id="natural-lifecycle-start",
        channel_id="channel",
        author_id="alice",
        content="everyone is ready, start the game",
        created_at=datetime(2026, 1, 1, 1, tzinfo=UTC),
    )

    responses, _ = crash_after_handler_then_retry(
        store=store,
        event=event,
        monkeypatch=monkeypatch,
    )

    after = store.game_state("game")
    assert before is not None
    assert after is not None
    assert after.lifecycle is GameLifecycle.ACTIVE
    assert after.revision == before.revision + 1
    assert "The game has started." in responses[0].text
    assert "**Mode:** play" in responses[0].text
    assert "**Mode:** game preparation" not in responses[0].text


def test_finish_replay_atomically_cancels_pool_refunds_helper_and_pauses_clock(
    tmp_path,
    monkeypatch,
) -> None:
    store = lifecycle_store(tmp_path, GameLifecycle.ACTIVE)
    store.create_character(
        CharacterState("bob-hero", "game", "bob", "Helper", playable_sheet("Bob"))
    )
    store.place_player(game_id="game", player_id="bob", scene_id="scene")
    pending = ActionService(store).propose_roll(
        game_id="game",
        player_id="alice",
        scene_id="scene",
        proposal=PoolProposal(trait_names=("Alice Trait 0",), difficulty=2),
        prompt="Confirm the pool.",
        source_event_id="declaration",
        origin_channel_id="channel",
    )
    store.offer_help(
        game_id="game",
        helper_player_id="bob",
        target_player_id="alice",
    )
    helper_before = store.character_for_player(game_id="game", player_id="bob")
    game_before = store.game_state("game")
    activity_before = store.activity_state("game")
    event = IncomingMessage(
        event_id="finish-with-pending",
        channel_id="channel",
        author_id="alice",
        content="/game finish",
        created_at=datetime(2026, 1, 1, 1, tzinfo=UTC),
    )

    responses, _ = crash_after_handler_then_retry(
        store=store,
        event=event,
        monkeypatch=monkeypatch,
    )

    game_after = store.game_state("game")
    activity_after = store.activity_state("game")
    helper_after = store.character_for_player(game_id="game", player_id="bob")
    closed = store.pending_by_id(pending.interaction_id)
    assert helper_before is not None
    assert helper_after is not None
    assert game_before is not None
    assert game_after is not None
    assert closed is not None
    assert game_after.lifecycle is GameLifecycle.FINISHED
    assert game_after.revision == game_before.revision + 1
    assert activity_before["last_event_at"] is not None
    assert activity_after["last_event_at"] is None
    assert activity_after["revision"] == activity_before["revision"] + 1
    assert closed.status is PendingStatus.CANCELLED
    assert closed.kind is PendingKind.POOL_CONFIRMATION
    assert closed.revision == pending.revision + 1
    assert helper_before.sheet.reserve_current == 6
    assert helper_after.sheet.reserve_current == 7
    assert "The game is finished." in responses[0].text
    assert "**Mode:** game finished" in responses[0].text


def test_world_selection_replay_renders_own_live_preparation_panel(
    tmp_path,
    monkeypatch,
) -> None:
    store = SQLiteStore(tmp_path / "db.sqlite3")
    store.initialize()
    store.create_world(WorldState("world", "World", status="approved"))
    store.update_world_content(
        world_id="world",
        expected_revision=0,
        status="approved",
        content={
            "locations": [
                {
                    "id": "crossroads",
                    "name": "Crossroads",
                    "description": "A new beginning.",
                }
            ],
            "game_defaults": {"locale": "en"},
        },
    )
    event = IncomingMessage(
        event_id="select-own-world",
        channel_id="channel",
        author_id="alice",
        content="choose World",
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )

    class SelectWorldCompletion:
        def __init__(self) -> None:
            self.calls = 0

        async def complete(self, **kwargs) -> CompletionResult:
            self.calls += 1
            return CompletionResult(
                '{"command":"select_world","argument":"World","confidence":1,'
                '"evidence":"catalog selection"}',
                used_tool=True,
            )

    completion = SelectWorldCompletion()
    responses, _ = crash_after_handler_then_retry(
        store=store,
        event=event,
        monkeypatch=monkeypatch,
        completion=completion,
    )

    assert store.channel_state("channel").game_id == "game_select-own-world"
    assert responses[0].completion_game_id == "game_select-own-world"
    assert responses[0].render_live_status is True
    assert "PREPARATION" in responses[0].text
    assert "WORLD MANAGEMENT" not in responses[0].text
    assert completion.calls == 1


def send(app: MessageApplication, content: str, *, event_id: str, at: datetime) -> str:
    response = asyncio.run(
        app(
            IncomingMessage(
                event_id=event_id,
                channel_id="channel",
                author_id="alice",
                content=content,
                created_at=at,
            )
        )
    )
    return response.text if isinstance(response, HandlerResponse) else response


def test_session_lifecycle_commands_and_new_session_are_deterministic(tmp_path) -> None:
    store = SQLiteStore(tmp_path / "db.sqlite3")
    store.initialize()
    store.create_world(WorldState("world", "World", status="approved"))
    store.update_world_content(
        world_id="world",
        expected_revision=0,
        content={
            "locations": [
                {"id": "crossroads", "name": "Crossroads", "description": "A new beginning."}
            ]
        },
        status="approved",
    )
    store.create_game(
        GameState(
            "game",
            "world",
            GameLifecycle.ACTIVE,
            narrative_channel_id="channel",
        )
    )
    store.bind_channel(channel_id="channel", game_id="game")
    started = datetime(2026, 1, 1, tzinfo=UTC)
    store.start_activity_clock(game_id="game", started_at=started)
    app = MessageApplication(
        store=store,
        context=ContextAssembler(Path(__file__).parents[1] / "prompts"),
        state_router=StateDecisionRouter(NeverCompletion()),
    )

    paused = send(app, "/game pause", event_id="pause1", at=started + timedelta(minutes=1))
    assert "Режим:** игра на паузе" in paused
    assert store.game_state("game").lifecycle is GameLifecycle.PAUSED

    resumed = send(app, "/game resume", event_id="resume1", at=started + timedelta(minutes=2))
    assert "Игра продолжена" in resumed
    assert store.game_state("game").lifecycle is GameLifecycle.ACTIVE

    finished = send(app, "/game finish", event_id="finish1", at=started + timedelta(minutes=3))
    assert "Режим:** игра завершена" in finished
    assert store.game_state("game").lifecycle is GameLifecycle.FINISHED

    new_session = send(app, "/game new", event_id="new1", at=started + timedelta(minutes=4))
    assert "УПРАВЛЕНИЕ МИРАМИ" in new_session
    assert "Предыдущая игра сохранена и отвязана" in new_session
    assert store.channel_state("channel").game_id is None
    assert store.game_state("game").lifecycle is GameLifecycle.FINISHED
    assert store.game_state("game").narrative_channel_id is None

    selected = send(
        app,
        "выбираем World",
        event_id="select2",
        at=started + timedelta(minutes=5),
    )
    new_game = store.game_state("game_select2")
    assert "ПОДГОТОВКА" in selected
    assert store.channel_state("channel").game_id == "game_select2"
    assert new_game is not None
    assert new_game.lifecycle is GameLifecycle.PREPARING
    assert new_game.narrative_channel_id == "channel"


def test_ooc_escape_never_reaches_state_router_or_changes_lifecycle(tmp_path) -> None:
    store = SQLiteStore(tmp_path / "db.sqlite3")
    store.initialize()
    store.create_world(WorldState("world", "World"))
    store.create_game(GameState("game", "world", GameLifecycle.ACTIVE))
    store.bind_channel(channel_id="channel", game_id="game")
    app = MessageApplication(
        store=store,
        context=ContextAssembler(Path(__file__).parents[1] / "prompts"),
        state_router=StateDecisionRouter(NeverCompletion()),
    )

    response = send(
        app,
        "// не ставь игру на паузу, обсуждаем расписание",
        event_id="ooc1",
        at=datetime(2026, 1, 1, tzinfo=UTC),
    )

    assert "не изменяет состояние игры" in response
    assert store.game_state("game").lifecycle is GameLifecycle.ACTIVE


def test_new_session_replay_is_safe_after_handler_succeeds_but_completion_fails(
    tmp_path,
    monkeypatch,
) -> None:
    store = SQLiteStore(tmp_path / "db.sqlite3")
    store.initialize()
    store.create_world(WorldState("world", "World", status="approved"))
    store.create_game(GameState("game", "world", GameLifecycle.ACTIVE, locale="en"))
    store.bind_channel(channel_id="channel", game_id="game")
    event = IncomingMessage(
        event_id="new-session",
        channel_id="channel",
        author_id="alice",
        content="/game new",
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    store.enqueue(event)
    app = MessageApplication(
        store=store,
        context=ContextAssembler(Path(__file__).parents[1] / "prompts"),
        state_router=StateDecisionRouter(NeverCompletion()),
    )
    original_complete = store.complete_batch
    completion_attempts = 0

    def fail_first_completion(**kwargs):
        nonlocal completion_attempts
        completion_attempts += 1
        if completion_attempts == 1:
            raise RuntimeError("simulated failure after handler")
        return original_complete(**kwargs)

    monkeypatch.setattr(store, "complete_batch", fail_first_completion)
    handled: list[IncomingMessage] = []
    responses: list[HandlerResponse] = []

    async def recording_handler(message: IncomingMessage) -> str | HandlerResponse:
        handled.append(message)
        response = await app(message)
        assert isinstance(response, HandlerResponse)
        responses.append(response)
        return response

    orchestrator = ChannelOrchestrator(store, recording_handler)

    try:
        asyncio.run(orchestrator.process_available("channel"))
    except RuntimeError as error:
        assert str(error) == "simulated failure after handler"
    else:
        raise AssertionError("expected the first completion to fail")

    assert store.channel_state("channel").game_id is None
    assert handled[0].has_routing_snapshot
    assert handled[0].routing_game_id == "game"

    result = asyncio.run(orchestrator.process_available("channel"))

    assert result is not None
    assert len(handled) == 1
    assert len(responses) == 1
    assert result.items[0].text == responses[0].text
    assert "WORLD MANAGEMENT" in responses[0].text
    assert "PLAY" not in responses[0].text
    assert responses[0].render_live_status is True
    assert completion_attempts == 2
    assert store.pending(channel_id="channel") == []
    assert len(store.pending_outbox(channel_id="channel")) == 1


def test_pause_commit_crash_does_not_overwrite_an_external_resume(
    tmp_path,
    monkeypatch,
) -> None:
    store = lifecycle_store(tmp_path, GameLifecycle.ACTIVE)
    event = IncomingMessage(
        event_id="pause-operation-crash",
        channel_id="channel",
        author_id="alice",
        content="/game pause",
        created_at=datetime(2026, 1, 1, 1, tzinfo=UTC),
    )
    assert store.enqueue(event)
    app = MessageApplication(
        store=store,
        context=ContextAssembler(Path(__file__).parents[1] / "prompts"),
        state_router=StateDecisionRouter(NeverCompletion()),
    )
    original = store.transition_game_for_event
    calls = 0

    def commit_then_crash(**kwargs):
        nonlocal calls
        calls += 1
        result = original(**kwargs)
        if calls == 1:
            raise OSError("simulated crash after pause commit")
        return result

    monkeypatch.setattr(store, "transition_game_for_event", commit_then_crash)
    orchestrator = ChannelOrchestrator(store, app)

    with pytest.raises(OSError, match="after pause commit"):
        asyncio.run(orchestrator.process_available("channel"))
    assert store.game_state("game").lifecycle is GameLifecycle.PAUSED
    GameService(store).resume_game(
        "game",
        resumed_at=datetime(2026, 1, 1, 2, tzinfo=UTC),
    )

    replay = asyncio.run(orchestrator.process_available("channel"))

    assert replay is not None
    assert calls == 1
    assert store.game_state("game").lifecycle is GameLifecycle.ACTIVE
    assert "already applied earlier" in replay.items[0].text
    assert "The result recorded then was: The game is paused" in replay.items[0].text
    assert "**Mode:** play" in replay.items[0].text


def test_configuration_commit_crash_keeps_newer_admin_value(
    tmp_path,
    monkeypatch,
) -> None:
    store = lifecycle_store(tmp_path, GameLifecycle.PREPARING)
    event = IncomingMessage(
        event_id="configuration-operation-crash",
        channel_id="channel",
        author_id="alice",
        content="/game progression on",
        created_at=datetime(2026, 1, 1, 1, tzinfo=UTC),
    )
    assert store.enqueue(event)
    app = MessageApplication(
        store=store,
        context=ContextAssembler(Path(__file__).parents[1] / "prompts"),
        state_router=StateDecisionRouter(NeverCompletion()),
    )
    original = store.configure_game_for_event
    calls = 0

    def commit_then_crash(**kwargs):
        nonlocal calls
        calls += 1
        result = original(**kwargs)
        if calls == 1:
            raise OSError("simulated crash after configuration commit")
        return result

    monkeypatch.setattr(store, "configure_game_for_event", commit_then_crash)
    orchestrator = ChannelOrchestrator(store, app)

    with pytest.raises(OSError, match="after configuration commit"):
        asyncio.run(orchestrator.process_available("channel"))
    assert store.game_state("game").progression_enabled is True
    store.set_progression_enabled(game_id="game", enabled=False, expected_revision=1)

    replay = asyncio.run(orchestrator.process_available("channel"))

    assert replay is not None
    assert calls == 1
    assert store.game_state("game").progression_enabled is False
    assert "already applied earlier" in replay.items[0].text
    assert "The result recorded then was: Progression enabled" in replay.items[0].text


def test_new_session_commit_crash_does_not_detach_external_rebind(
    tmp_path,
    monkeypatch,
) -> None:
    store = lifecycle_store(tmp_path, GameLifecycle.ACTIVE)
    store.create_world(WorldState("world-b", "World B"))
    store.create_game(GameState("game-b", "world-b", GameLifecycle.ACTIVE, locale="en"))
    event = IncomingMessage(
        event_id="new-session-operation-crash",
        channel_id="channel",
        author_id="alice",
        content="/game new",
        created_at=datetime(2026, 1, 1, 1, tzinfo=UTC),
    )
    assert store.enqueue(event)
    app = MessageApplication(
        store=store,
        context=ContextAssembler(Path(__file__).parents[1] / "prompts"),
        state_router=StateDecisionRouter(NeverCompletion()),
    )
    original = store.unbind_channel_for_event
    calls = 0

    def commit_then_crash(**kwargs):
        nonlocal calls
        calls += 1
        result = original(**kwargs)
        if calls == 1:
            raise OSError("simulated crash after new-session commit")
        return result

    monkeypatch.setattr(store, "unbind_channel_for_event", commit_then_crash)
    orchestrator = ChannelOrchestrator(store, app)

    with pytest.raises(OSError, match="after new-session commit"):
        asyncio.run(orchestrator.process_available("channel"))
    assert store.channel_state("channel").game_id is None
    store.bind_channel(channel_id="channel", game_id="game-b")

    replay = asyncio.run(orchestrator.process_available("channel"))

    assert replay is not None
    assert calls == 1
    assert store.channel_state("channel").game_id == "game-b"
    assert "already applied earlier" in replay.items[0].text
    assert (
        "The previous game was preserved and detached; at that time the channel "
        "became ready for a new world selection."
    ) in replay.items[0].text
    assert "The panel shows the current state." in replay.items[0].text
    assert "World B" in replay.items[0].text
    assert "Choose a ready world" not in replay.items[0].text
    assert [row["kind"] for row in store.pending_outbox(channel_id="channel")] == ["system_notice"]
