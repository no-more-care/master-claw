import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from masterclaw.app.game_service import GameService
from masterclaw.app.message_handler import MessageApplication
from masterclaw.context.assembler import ContextAssembler
from masterclaw.domain.characters import CharacterState
from masterclaw.domain.mechanics import CharacterSheet, Flag, FlagType, Trait
from masterclaw.domain.models import HandlerResponse, IncomingMessage
from masterclaw.pipelines.base import CompletionResult
from masterclaw.pipelines.character_creation import create_character_pipeline
from masterclaw.pipelines.conversation_actions import create_game_configuration_pipeline
from masterclaw.pipelines.state_decision import StateDecisionRouter
from masterclaw.storage.sqlite import SQLiteStore


def prepare_game(store: SQLiteStore, *, world_id: str, game_id: str, locale: str = "ru") -> None:
    service = GameService(store)
    service.create_world(world_id=world_id, title="World")
    service.prepare_game(
        game_id=game_id,
        world_id=world_id,
        channel_id="game-channel",
        locale=locale,
    )


class NeverCompletion:
    async def complete(self, **kwargs) -> CompletionResult:
        raise AssertionError("preparation commands must not call an LLM")


class CharacterCompletion:
    def __init__(self) -> None:
        self.calls = 0

    async def complete(self, **kwargs) -> CompletionResult:
        self.calls += 1
        return CompletionResult(
            json.dumps(
                {
                    "name": "Hero",
                    "biography": "A traveller",
                    "traits": [
                        {
                            "name": f"Trait {i}",
                            "level": 3,
                            "aspects": [f"Aspect {i}.{n}" for n in range(3)],
                        }
                        for i in range(6)
                    ],
                    "flags": [
                        {
                            "text": "Friend of Mira",
                            "type": "relationship",
                            "is_positive": True,
                        },
                        {
                            "text": "Never surrender",
                            "type": "belief",
                            "is_positive": False,
                        },
                        {"text": "Find home", "type": "goal", "is_positive": False},
                    ],
                }
            ),
            used_tool=True,
        )


class NaturalPreparationIntent:
    def __init__(self) -> None:
        self.responses = iter(
            (
                "create_character",
                "show_game_status",
            )
        )

    async def complete(self, **kwargs) -> CompletionResult:
        command = next(self.responses)
        return CompletionResult(
            json.dumps(
                {
                    "command": command,
                    "argument": None,
                    "confidence": 1,
                    "evidence": "natural request",
                }
            ),
            used_tool=True,
        )


class SceneConfigurationIntent:
    def __init__(self) -> None:
        self.calls = 0

    async def complete(self, **kwargs) -> CompletionResult:
        self.calls += 1
        return CompletionResult(
            json.dumps(
                {
                    "command": "configure_game",
                    "argument": None,
                    "confidence": 1,
                    "evidence": "scene configuration request",
                }
            ),
            used_tool=True,
        )


class SceneConfigurationCompletion:
    def __init__(self) -> None:
        self.calls = 0

    async def complete(self, **kwargs) -> CompletionResult:
        self.calls += 1
        return CompletionResult(
            json.dumps(
                {
                    "kind": "create_scene",
                    "scene_title": "The Glass Archive",
                }
            ),
            used_tool=True,
        )


def send(app, event_id, content):
    response = asyncio.run(
        app(
            IncomingMessage(
                event_id=event_id,
                channel_id="game-channel",
                author_id="alice",
                content=content,
                created_at=datetime(2026, 1, 1, tzinfo=UTC),
            )
        )
    )
    return response.text if isinstance(response, HandlerResponse) else response


def test_equal_player_can_prepare_and_start_game_with_progression_setting(tmp_path) -> None:
    store = SQLiteStore(tmp_path / "db.sqlite3")
    store.initialize()
    app = MessageApplication(
        store=store,
        context=ContextAssembler(Path(__file__).parents[1] / "prompts"),
        state_router=StateDecisionRouter(NeverCompletion()),
        character_pipeline=create_character_pipeline(CharacterCompletion()),
    )
    prepare_game(store, world_id="world", game_id="game")
    send(app, "bad-channel", "/game narrative not-a-channel")
    assert store.game_state("game").narrative_channel_id is None
    assert "включена" in send(app, "3", "/game progression on")
    assert "Нарративный канал" in send(app, "4", "/game narrative 999")
    assert "Создан персонаж" in send(app, "6", '/character create hero "A travelling hero"')
    status = send(app, "6a", "/character status")
    assert "Персонаж **Hero**" in status
    assert "Опыт: 0" in status
    assert "Резерв: 7/7" in status
    assert "началась" in send(app, "8", "/game start")
    game = store.game_state("game")
    assert game.progression_enabled is True
    assert game.narrative_channel_id == "999"


def test_narrative_command_reports_non_messageable_channel_error(tmp_path) -> None:
    store = SQLiteStore(tmp_path / "db.sqlite3")
    store.initialize()
    app = MessageApplication(
        store=store,
        context=ContextAssembler(Path(__file__).parents[1] / "prompts"),
        state_router=StateDecisionRouter(NeverCompletion()),
        character_pipeline=create_character_pipeline(CharacterCompletion()),
    )
    prepare_game(store, world_id="world", game_id="game", locale="en")
    store.record_discord_channel(
        channel_id="game-channel",
        guild_id="guild",
        parent_channel_id=None,
        kind="text",
    )
    store.record_discord_channel(
        channel_id="999",
        guild_id="guild",
        parent_channel_id=None,
        kind="voice",
    )

    response = send(app, "voice-narrative", "/game narrative 999")

    assert "Narrative channel was not changed" in response
    assert "not messageable" in response
    assert store.game_state("game").narrative_channel_id is None


def test_english_game_uses_english_deterministic_command_responses(tmp_path) -> None:
    store = SQLiteStore(tmp_path / "db.sqlite3")
    store.initialize()
    app = MessageApplication(
        store=store,
        context=ContextAssembler(Path(__file__).parents[1] / "prompts"),
        state_router=StateDecisionRouter(NeverCompletion()),
        character_pipeline=create_character_pipeline(CharacterCompletion()),
    )
    prepare_game(store, world_id="english", game_id="english-game", locale="en")
    assert "Progression enabled" in send(app, "en-3", "/game progression on")
    assert "Created character" in send(app, "en-5", '/character create hero "A travelling hero"')
    status = send(app, "en-6", "/character status")
    assert "🧭 PREPARATION" in status
    assert "ПОДГОТОВКА" not in status
    assert "What the players know" in status
    assert "Traits:" in status
    assert "Reserve: 7/7" in status
    assert "XP: 0" in status


def test_natural_character_creation_start_and_status_need_no_commands(tmp_path) -> None:
    store = SQLiteStore(tmp_path / "db.sqlite3")
    store.initialize()
    app = MessageApplication(
        store=store,
        context=ContextAssembler(Path(__file__).parents[1] / "prompts"),
        state_router=StateDecisionRouter(NaturalPreparationIntent()),
        character_pipeline=create_character_pipeline(CharacterCompletion()),
    )
    prepare_game(store, world_id="world", game_id="game")
    send(app, "narrative", "/game narrative 999")

    created = send(
        app,
        "natural-character",
        "Я бывшая разведчица, которая ищет пропавшего брата и никому не доверяет",
    )
    assert "создан персонаж" in created.lower()
    assert store.character_for_player(game_id="game", player_id="alice") is not None
    assert store.scene_projection(game_id="game", player_id="alice") is None

    started = send(app, "natural-start", "Все готовы, начинаем игру")
    assert "началась" in started.lower()
    assert store.scene_projection(game_id="game", player_id="alice") is not None
    status = send(app, "natural-status", "Какой сейчас статус игры?")
    assert "состояние игры" in status.lower()


def test_slash_character_create_replay_places_character_after_crash(
    tmp_path,
    monkeypatch,
) -> None:
    store = SQLiteStore(tmp_path / "character-replay.sqlite3")
    store.initialize()
    prepare_game(store, world_id="world", game_id="game")
    veteran_sheet = CharacterSheet(
        "Veteran",
        tuple(
            Trait(
                f"Veteran Trait {index}",
                3,
                tuple(f"Veteran Aspect {index}.{aspect}" for aspect in range(3)),
            )
            for index in range(6)
        ),
        (
            Flag("Protect the newcomer", FlagType.RELATIONSHIP),
            Flag("Keep the group alive", FlagType.GOAL),
            Flag("Nobody gets left behind", FlagType.BELIEF),
        ),
    )
    store.create_character(
        CharacterState("veteran", "game", "bob", "An experienced guide.", veteran_sheet)
    )
    game = store.game_state("game")
    assert game is not None
    store.set_narrative_channel(
        game_id="game",
        channel_id="game-channel",
        expected_revision=game.revision,
    )
    GameService(store).start_game(
        "game",
        started_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    completion = CharacterCompletion()
    app = MessageApplication(
        store=store,
        context=ContextAssembler(Path(__file__).parents[1] / "prompts"),
        state_router=StateDecisionRouter(NeverCompletion()),
        character_pipeline=create_character_pipeline(completion),
    )
    original_place = app._place_joining_character

    def crash_after_character_commit(**_kwargs) -> bool:
        raise RuntimeError("simulated crash before placement")

    monkeypatch.setattr(app, "_place_joining_character", crash_after_character_commit)
    with pytest.raises(RuntimeError, match="simulated crash"):
        send(app, "join-event", '/character create newcomer "A travelling hero"')

    assert store.character_for_player(game_id="game", player_id="alice") is not None
    assert store.scene_projection(game_id="game", player_id="alice") is None
    assert completion.calls == 1

    monkeypatch.setattr(app, "_place_joining_character", original_place)
    replayed = send(app, "join-event", '/character create newcomer "A travelling hero"')

    assert store.scene_projection(game_id="game", player_id="alice") is not None
    assert completion.calls == 1
    assert "присоедин" in replayed.lower()


def test_natural_scene_creation_is_idempotent_for_same_event(tmp_path) -> None:
    store = SQLiteStore(tmp_path / "scene-replay.sqlite3")
    store.initialize()
    prepare_game(store, world_id="world", game_id="game")
    intent = SceneConfigurationIntent()
    configuration = SceneConfigurationCompletion()
    app = MessageApplication(
        store=store,
        context=ContextAssembler(Path(__file__).parents[1] / "prompts"),
        state_router=StateDecisionRouter(intent),
        game_configuration_pipeline=create_game_configuration_pipeline(configuration),
    )

    first = send(app, "scene-event", "Создай сцену «Стеклянный архив»")
    replayed = send(app, "scene-event", "Создай сцену «Стеклянный архив»")

    assert "scene_scene-event" in first
    assert "scene_scene-event" in replayed
    assert store.scene_count("game") == 1
    scene = store.scene_by_id(game_id="game", scene_id="scene_scene-event")
    assert scene is not None
    assert scene["title"] == "The Glass Archive"
    assert intent.calls == 1
    assert configuration.calls == 1
