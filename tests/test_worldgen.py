import asyncio
from datetime import UTC, datetime
from pathlib import Path

from masterclaw.app.message_handler import MessageApplication
from masterclaw.context.assembler import ContextAssembler
from masterclaw.domain.models import IncomingMessage
from masterclaw.pipelines.intent import create_intent_pipeline
from masterclaw.pipelines.worldgen import create_worldgen_pipeline
from masterclaw.storage.sqlite import SQLiteStore


class NeverIntent:
    async def complete(self, *, system: str, user: str) -> str:
        raise AssertionError("world commands do not use intent classification")


class WorldCompletion:
    async def complete(self, *, system: str, user: str) -> str:
        assert "world_generation" in user
        return (
            '{"premise":"A long enough premise about a city above an endless storm.",'
            '"themes":["memory"],"locations":[{"id":"sky_city","name":"Sky City",'
            '"description":"A city suspended from ancient chains."}],'
            '"factions":["Chain Keepers"],"tensions":["The chains are failing"],'
            '"secret_plot":"The storm is a sleeping intelligence."}'
        )


def test_world_generation_is_typed_versioned_and_persisted(tmp_path) -> None:
    store = SQLiteStore(tmp_path / "db.sqlite3")
    store.initialize()
    app = MessageApplication(
        store=store,
        context=ContextAssembler(Path(__file__).parents[1] / "prompts"),
        intent_pipeline=create_intent_pipeline(NeverIntent()),
        worldgen_pipeline=create_worldgen_pipeline(WorldCompletion()),
    )
    now = datetime(2026, 1, 1, tzinfo=UTC)
    for event_id, content in [
        ("1", '/world create storm "Storm World"'),
        ("2", '/world generate storm "A city above an endless storm"'),
    ]:
        response = asyncio.run(
            app(
                IncomingMessage(
                    event_id=event_id,
                    channel_id="channel",
                    author_id="alice",
                    content=content,
                    created_at=now,
                )
            )
        )
    assert "1 локаций" in response
    assert store.world_state("storm").revision == 1
    assert store.world_content("storm")["locations"][0]["id"] == "sky_city"
