import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

from masterclaw.app.message_handler import MessageApplication
from masterclaw.context.assembler import ContextAssembler
from masterclaw.domain.models import IncomingMessage
from masterclaw.pipelines.character_creation import create_character_pipeline
from masterclaw.pipelines.intent import create_intent_pipeline
from masterclaw.storage.sqlite import SQLiteStore


class NeverCompletion:
    async def complete(self, *, system: str, user: str) -> str:
        raise AssertionError("preparation commands must not call an LLM")


class CharacterCompletion:
    async def complete(self, *, system: str, user: str) -> str:
        return json.dumps(
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
                    {"text": "Friend of Mira", "type": "relationship"},
                    {"text": "Never surrender", "type": "belief"},
                    {"text": "Find home", "type": "goal"},
                ],
            }
        )


def send(app, event_id, content):
    return asyncio.run(
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


def test_equal_player_can_prepare_and_start_game_with_progression_setting(tmp_path) -> None:
    store = SQLiteStore(tmp_path / "db.sqlite3")
    store.initialize()
    app = MessageApplication(
        store=store,
        context=ContextAssembler(Path(__file__).parents[1] / "prompts"),
        intent_pipeline=create_intent_pipeline(NeverCompletion()),
        character_pipeline=create_character_pipeline(CharacterCompletion()),
    )
    assert "Создан черновик" in send(app, "1", '/world create world "World"')
    assert "режиме подготовки" in send(app, "2", "/game prepare game world ru")
    assert "включена" in send(app, "3", "/game progression on")
    assert "Нарративный канал" in send(app, "4", "/game narrative 999")
    assert "Создана сцена" in send(app, "5", '/game scene opening "Opening"')
    assert "Создан персонаж" in send(app, "6", '/character create hero "A travelling hero"')
    status = send(app, "6a", "/character status")
    assert "Персонаж `hero` — Hero" in status
    assert "Опыт: 0" in status
    assert "Резерв: 7/7" in status
    assert "размещён" in send(app, "7", "/character place opening")
    assert "запущена" in send(app, "8", "/game start")
    game = store.game_state("game")
    assert game.progression_enabled is True
    assert game.narrative_channel_id == "999"
