import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

from masterclaw.app.message_handler import MessageApplication
from masterclaw.context.assembler import ContextAssembler
from masterclaw.domain.characters import CharacterState
from masterclaw.domain.mechanics import CharacterSheet, Flag, FlagType, Trait
from masterclaw.domain.models import GameLifecycle, IncomingMessage
from masterclaw.domain.state import GameState, WorldState
from masterclaw.pipelines.state_decision import StateDecisionRouter
from masterclaw.storage.sqlite import SQLiteStore


class NeverCompletion:
    async def complete(self, **kwargs):
        raise AssertionError("commands must not call an LLM")


def setup_app(tmp_path):
    store = SQLiteStore(tmp_path / "db.sqlite3")
    store.initialize()
    store.create_world(WorldState("world", "World"))
    store.create_game(GameState("game", "world", GameLifecycle.ACTIVE, progression_enabled=True))
    store.bind_channel(channel_id="channel", game_id="game")
    sheet = CharacterSheet(
        "Hero",
        tuple(Trait(f"T{i}", 3, tuple(f"A{i}.{n}" for n in range(3))) for i in range(6)),
        (
            Flag("Friend", FlagType.RELATIONSHIP),
            Flag("Goal", FlagType.GOAL),
            Flag("Belief", FlagType.BELIEF),
        ),
    )
    store.create_character(CharacterState("hero", "game", "player", "Bio", sheet))
    start = datetime(2026, 1, 1, tzinfo=UTC)
    store.start_activity_clock(game_id="game", started_at=start)
    app = MessageApplication(
        store=store,
        context=ContextAssembler(Path(__file__).parents[1] / "prompts"),
        state_router=StateDecisionRouter(NeverCompletion()),
    )
    return store, app, start


def send(app, *, event_id, author, content, at):
    return asyncio.run(
        app(
            IncomingMessage(
                event_id=event_id,
                channel_id="channel",
                author_id=author,
                content=content,
                created_at=at,
            )
        )
    )


def test_read_only_status_does_not_farm_xp_and_reports_character_xp_without_llm(tmp_path) -> None:
    store, app, current = setup_app(tmp_path)
    for index in range(6):
        current += timedelta(minutes=5)
        send(app, event_id=f"play-{index}", author="player", content="/game status", at=current)
    status = send(app, event_id="status", author="player", content="/xp status", at=current)
    assert "Прогрессия: включена" in status
    assert "начислено полных интервалов: 0" in status
    assert "XP персонажа: доступно 0, заработано 0, потрачено 0" in status
    assert store.character_for_player(game_id="game", player_id="player").experience_earned == 0
