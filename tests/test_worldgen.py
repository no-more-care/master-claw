import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from masterclaw.app.game_service import GameService
from masterclaw.app.message_handler import MessageApplication
from masterclaw.app.worldgen_service import (
    WorldGenerationError,
    WorldGenerationService,
    create_world_generation_service,
)
from masterclaw.context.assembler import ContextAssembler
from masterclaw.domain.models import IncomingMessage
from masterclaw.domain.state import WorldState
from masterclaw.pipelines.base import CompletionResult, PipelineValidationError
from masterclaw.pipelines.state_decision import StateDecisionRouter
from masterclaw.pipelines.world_intake import WorldCreationBrief, create_world_intake_pipeline
from masterclaw.pipelines.worldgen import WorldDraft, WorldLocation
from masterclaw.storage.sqlite import SQLiteStore


def pregen(name: str, target: str, *, faction: str | None = "Chain Keepers") -> dict:
    return {
        "name": name,
        "concept": f"{name} is an investigator grounded in the world.",
        "hook": f"{name} must decide whether to trust {target}.",
        "biography": (
            f"{name} has lived through the city's crises and has personal stakes in its future."
        ),
        "faction_affiliations": [] if faction is None else [faction],
        "connections": [
            {"character_name": target, "relationship": "Trusted but strained colleague"}
        ],
        "traits": [
            {
                "name": f"Trait {index}",
                "level": 3,
                "aspects": [f"A{index}.1", f"A{index}.2", f"A{index}.3"],
            }
            for index in range(6)
        ],
        "flags": [
            {
                "text": f"I rely on {target}",
                "type": "relationship",
                "is_positive": True,
            },
            {"text": "Find the truth", "type": "goal", "is_positive": False},
            {
                "text": "Secrets always cost someone",
                "type": "belief",
                "is_positive": False,
            },
        ],
    }


def pregens(*, faction: str | None = "Chain Keepers") -> list[dict]:
    return [
        pregen("Mira", "Orin", faction=faction),
        pregen("Orin", "Vale", faction=faction),
        pregen("Vale", "Mira", faction=faction),
    ]


def valid_world_payload() -> dict:
    return {
        "premise": "A long enough premise about a city above an endless storm.",
        "themes": ["memory"],
        "locations": [
            {
                "id": "sky_city",
                "name": "Sky City",
                "description": "A city suspended from ancient chains.",
            }
        ],
        "factions": ["Chain Keepers"],
        "tensions": ["The chains are failing"],
        "secret_plot": "The storm is a sleeping intelligence.",
        "character_templates": pregens(),
    }


class WorldCompletion:
    def __init__(self, responses: list[str] | None = None) -> None:
        self.responses = iter(
            responses
            or [
                (
                    '{"module_plot":"A city hangs above an endless storm on ancient chains. '
                    "The Chain Keepers ration repairs while memory is traded as currency. "
                    "Players can expose the rationing fraud, bargain with the intelligence in "
                    "the storm, or seize the failing lift network. Every path saves one district "
                    "at a cost to another and reveals clues that the storm is waking beneath "
                    'them."}'
                ),
                json.dumps(
                    {
                        "premise": "A long enough premise about a city above an endless storm.",
                        "themes": ["memory"],
                        "locations": [
                            {
                                "id": "sky_city",
                                "name": "Sky City",
                                "description": "A city suspended from ancient chains.",
                            }
                        ],
                        "factions": ["Chain Keepers"],
                        "tensions": ["The chains are failing"],
                        "secret_plot": "The storm is a sleeping intelligence.",
                        "character_templates": pregens(),
                    }
                ),
            ]
        )
        self.calls = 0

    async def complete(self, **kwargs) -> CompletionResult:
        assert "world_generation" in kwargs["system"]
        self.calls += 1
        return CompletionResult(next(self.responses), used_tool=True)


class WorldIntakeCompletion:
    async def complete(self, **kwargs) -> CompletionResult:
        return CompletionResult(
            '{"title":"Ash Detectives","brief":"Create a hostile post-apocalyptic fantasy '
            "world after magical wars, where investigators handle paranormal cases while chaos "
            'presses into reality.","genre":"post-apocalyptic fantasy",'
            '"tone":"grimdark investigation","themes":["memory"],'
            '"specified_fields":["genre","tone","themes"]}',
            used_tool=True,
        )


class SequenceCompletion:
    def __init__(self, *responses: str) -> None:
        self.responses = iter(responses)
        self.calls = 0

    async def complete(self, **kwargs) -> CompletionResult:
        self.calls += 1
        return CompletionResult(next(self.responses), used_tool=True)


class FakeWorldgen:
    def __init__(self) -> None:
        self.calls = 0

    async def generate(self, *, world, brief, settings=None) -> WorldDraft:
        self.calls += 1
        return WorldDraft(
            premise=f"Public premise revision {self.calls} with enough detail for players.",
            themes=["memory"],
            locations=[
                WorldLocation(
                    id="ash_harbor",
                    name="Ash Harbor",
                    description="A storm-beaten refuge for investigators.",
                )
            ],
            factions=["Lantern Wardens"],
            tensions=["The harbor wards are failing"],
            secret_plot="The wards are feeding the storm.",
            character_templates=pregens(faction="Lantern Wardens"),
        )


def worldgen_service(context, completion):
    return create_world_generation_service(
        context=context,
        creative_completion=completion,
        creative_fallback_completion=completion,
        structuring_completion=completion,
        structuring_fallback_completion=completion,
    )


def test_world_generation_is_typed_and_persisted(tmp_path) -> None:
    store = SQLiteStore(tmp_path / "db.sqlite3")
    store.initialize()
    completion = WorldCompletion()
    context = ContextAssembler(Path(__file__).parents[1] / "prompts")
    world = GameService(store).create_world(world_id="storm", title="Storm World")
    draft = asyncio.run(
        worldgen_service(context, completion).generate(
            world=world,
            brief="A city above an endless storm",
        )
    )
    store.update_world_content(
        world_id=world.world_id,
        expected_revision=world.revision,
        content=draft.model_dump(mode="json"),
    )
    assert store.world_state("storm").revision == 1
    assert store.world_content("storm")["locations"][0]["id"] == "sky_city"
    assert completion.calls == 2


def test_invalid_pregen_sheet_is_repaired_inside_structuring_pipeline(tmp_path) -> None:
    invalid_pregens = pregens()
    invalid_pregens[0]["traits"][1]["name"] = "trait 0"
    valid_world = valid_world_payload()
    invalid_world = {**valid_world, "character_templates": invalid_pregens}
    completion = WorldCompletion(
        [
            (
                '{"module_plot":"A city hangs above an endless storm on ancient chains. '
                "The Chain Keepers conceal failing anchors and trade memories for repairs. "
                "Players can reveal the lie, negotiate with rival districts, or descend into "
                'the storm, with every path exposing clues and demanding a costly choice."}'
            ),
            json.dumps(invalid_world),
            json.dumps(valid_world),
        ]
    )
    context = ContextAssembler(Path(__file__).parents[1] / "prompts")

    draft = asyncio.run(
        worldgen_service(context, completion).generate(
            world=WorldState("storm", "Storm World"),
            brief="A city above a storm",
        )
    )

    assert [trait.name for trait in draft.character_templates[0].traits[:2]] == [
        "Trait 0",
        "Trait 1",
    ]
    assert completion.calls == 3


def test_world_intake_rejects_unknown_specified_field() -> None:
    with pytest.raises(ValidationError, match="invented_setting"):
        WorldCreationBrief.model_validate(
            {
                "title": "Storm World",
                "brief": "A sufficiently detailed world brief about a city above a storm.",
                "specified_fields": ["invented_setting"],
            }
        )


@pytest.mark.parametrize(
    ("invalid_case", "expected_error"),
    [
        ("duplicate_location", "duplicate location ids"),
        ("duplicate_faction", "duplicate factions"),
        ("duplicate_pregen", "duplicate pregen names"),
        ("unknown_faction", "unknown faction"),
        ("invalid_connection", "another existing pregen"),
        ("duplicate_trait", "trait names must be unique"),
        ("duplicate_aspect", "trait aspects must be unique"),
    ],
)
def test_world_draft_rejects_semantically_inconsistent_structuring_output(
    invalid_case: str, expected_error: str
) -> None:
    payload = valid_world_payload()
    if invalid_case == "duplicate_location":
        payload["locations"].append(
            {"id": "sky_city", "name": "Lower City", "description": "A lower district."}
        )
    elif invalid_case == "duplicate_faction":
        payload["factions"].append("chain keepers")
    elif invalid_case == "duplicate_pregen":
        payload["character_templates"][1]["name"] = "mira"
    elif invalid_case == "unknown_faction":
        payload["character_templates"][0]["faction_affiliations"] = ["Unknown Circle"]
    elif invalid_case == "invalid_connection":
        payload["character_templates"][0]["connections"][0]["character_name"] = "Mira"
    elif invalid_case == "duplicate_trait":
        payload["character_templates"][0]["traits"][1]["name"] = "trait 0"
    elif invalid_case == "duplicate_aspect":
        payload["character_templates"][0]["traits"][0]["aspects"][1] = "a0.1"
    else:
        raise AssertionError(f"unhandled invalid case: {invalid_case}")

    with pytest.raises(ValidationError, match=expected_error):
        WorldDraft.model_validate(payload)


def test_pregen_relationship_flag_is_derived_from_existing_connection() -> None:
    raw = pregen("Mira", "Orin")
    raw["flags"] = [
        {"text": "Find the truth", "type": "goal", "is_positive": False},
        {"text": "Secrets cost someone", "type": "belief", "is_positive": False},
        {
            "text": "Never leave evidence behind",
            "type": "personality",
            "is_positive": False,
        },
    ]

    draft = WorldDraft.model_validate(
        {
            "premise": "A sufficiently detailed premise for a world above a storm.",
            "themes": ["memory"],
            "locations": [{"id": "sky_city", "name": "Sky City", "description": "A chained city."}],
            "factions": ["Chain Keepers"],
            "tensions": [],
            "secret_plot": "The storm is awake.",
            "character_templates": [raw, pregen("Orin", "Vale"), pregen("Vale", "Mira")],
        }
    )

    relationship_flags = [
        flag for flag in draft.character_templates[0].flags if flag.type.value == "relationship"
    ]
    assert len(relationship_flags) == 1
    assert relationship_flags[0].text == "Orin: Trusted but strained colleague"
    assert relationship_flags[0].is_positive is True


def test_world_validation_binds_exact_pregen_count_and_requested_themes() -> None:
    draft = WorldDraft.model_validate(valid_world_payload())
    WorldGenerationService._validate_draft(
        draft,
        settings={"pregenerated_character_count": 3, "themes": ["MEMORY"]},
    )
    with pytest.raises(WorldGenerationError, match="requested pregen count"):
        WorldGenerationService._validate_draft(
            draft,
            settings={"pregenerated_character_count": 4},
        )
    with pytest.raises(WorldGenerationError, match="requested theme"):
        WorldGenerationService._validate_draft(
            draft,
            settings={"themes": ["sacrifice"]},
        )


def test_invalid_world_intake_returns_scenario_specific_retry(tmp_path) -> None:
    class InvalidCompletion:
        async def complete(self, **kwargs) -> CompletionResult:
            return CompletionResult("not-json", used_tool=True)

    class NeverStateCompletion:
        async def complete(self, **kwargs) -> CompletionResult:
            raise AssertionError("explicit create-world phrase must not call the state model")

    store = SQLiteStore(tmp_path / "db.sqlite3")
    store.initialize()
    app = MessageApplication(
        store=store,
        context=ContextAssembler(Path(__file__).parents[1] / "prompts"),
        state_router=StateDecisionRouter(NeverStateCompletion()),
        world_intake_pipeline=create_world_intake_pipeline(InvalidCompletion()),
    )

    response = asyncio.run(
        app(
            IncomingMessage(
                event_id="invalid-world",
                channel_id="channel",
                author_id="alice",
                content="Создай мир",
                created_at=datetime(2026, 1, 1, tzinfo=UTC),
            )
        )
    )

    assert "Опишите мир ещё раз короче" in response
    assert store.world_workspace("channel") is None


def test_natural_world_requires_generation_and_publication_confirmations(tmp_path) -> None:
    store = SQLiteStore(tmp_path / "db.sqlite3")
    store.initialize()
    context = ContextAssembler(Path(__file__).parents[1] / "prompts")
    completion = WorldCompletion()
    state_route = SequenceCompletion(
        '{"command":"create_world","argument":null,"confidence":1,'
        '"evidence":"detailed new world request"}',
        '{"command":"select_world","argument":"Ash Detectives","confidence":1,'
        '"evidence":"catalogue selection"}',
        '{"command":"select_character","argument":"Mira","confidence":1,'
        '"evidence":"pregenerated character selection"}',
    )
    app = MessageApplication(
        store=store,
        context=context,
        state_router=StateDecisionRouter(state_route),
        world_intake_pipeline=create_world_intake_pipeline(WorldIntakeCompletion()),
        worldgen=worldgen_service(context, completion),
    )
    response = asyncio.run(
        app(
            IncomingMessage(
                event_id="1526253124296245278",
                channel_id="channel",
                author_id="alice",
                content=(
                    "Давай создадим мрачный постапокалиптический мир после магических войн. "
                    "Один большой разрушенный город стоит над древними катакомбами, вокруг "
                    "возникают зоны дикой магии, а герои работают в детективном агентстве и "
                    "расследуют паранормальные происшествия для частных клиентов, бизнеса и "
                    "правительства. Нужны неоднозначные решения, хтонь и тяжёлая атмосфера."
                ),
                created_at=datetime(2026, 1, 1, tzinfo=UTC),
            )
        )
    )
    world_id = "world_1526253124296245278"
    assert "генерация ещё не запускалась" in response
    assert store.world_state(world_id).title == "Ash Detectives"
    assert store.world_content(world_id) == {}
    assert store.world_workspace("channel")["stage"] == "collecting"
    assert "**Жанр:** post-apocalyptic fantasy" in response
    assert "_[задано игроками]_" in response
    assert "Масштаб:** один регион" in response
    assert "### Нарратив" in response
    assert "**Права рассказчика:** малые права игрока (`minor`)" in response
    assert "**Количество прегенов:** 3" in response
    assert "**Заданные концепты:** не заданы — будут предложены" in response
    assert "│" not in response and "╭" not in response and "╰" not in response
    assert store.channel_state("channel").game_id is None
    assert completion.calls == 0

    generated = asyncio.run(
        app(
            IncomingMessage(
                event_id="generate",
                channel_id="channel",
                author_id="alice",
                content="Можно генерировать",
                created_at=datetime(2026, 1, 1, tzinfo=UTC),
            )
        )
    )
    assert "Публичная вводная" in generated
    assert store.world_content(world_id)["locations"][0]["id"] == "sky_city"
    assert store.world_content(world_id)["game_defaults"]["narrative_detail"] == "balanced"
    assert store.world_workspace("channel")["stage"] == "review"
    assert store.channel_state("channel").game_id is None
    assert completion.calls == 2

    confirmed = asyncio.run(
        app(
            IncomingMessage(
                event_id="confirm",
                channel_id="channel",
                author_id="alice",
                content="Подтверждаю мир",
                created_at=datetime(2026, 1, 1, tzinfo=UTC),
            )
        )
    )
    assert store.channel_state("channel").game_id is None
    assert store.world_workspace("channel") is None
    assert "ДОСТУПНЫЕ МИРЫ" in confirmed
    assert "Ash Detectives" in confirmed
    assert world_id not in confirmed

    selected = asyncio.run(
        app(
            IncomingMessage(
                event_id="select",
                channel_id="channel",
                author_id="alice",
                content="Выбираем Ash Detectives",
                created_at=datetime(2026, 1, 1, tzinfo=UTC),
            )
        )
    )
    game_id = "game_select"
    assert store.channel_state("channel").game_id == game_id
    assert store.first_scene_id(game_id) is None
    assert store.game_state(game_id).narrative_channel_id == "channel"
    assert "ПОДГОТОВКА" in selected

    chosen = asyncio.run(
        app(
            IncomingMessage(
                event_id="choose-mira",
                channel_id="channel",
                author_id="alice",
                content="Выбираю Mira",
                created_at=datetime(2026, 1, 1, tzinfo=UTC),
            )
        )
    )
    character = store.character_for_player(game_id=game_id, player_id="alice")
    assert character is not None and character.sheet.name == "Mira"
    assert store.scene_projection(game_id=game_id, player_id="alice") is None
    assert "Стартовая сцена" in chosen

    GameService(store).start_game(game_id)
    assert store.scene_projection(game_id=game_id, player_id="alice") is not None


def test_world_revision_marks_draft_dirty_and_requires_explicit_regeneration(tmp_path) -> None:
    store = SQLiteStore(tmp_path / "db.sqlite3")
    store.initialize()
    context = ContextAssembler(Path(__file__).parents[1] / "prompts")
    intent = SequenceCompletion(
        '{"command":"create_world","argument":null,"confidence":1,"evidence":"new world"}',
        '{"command":"revise_world","argument":null,"confidence":1,"evidence":"world revision"}',
    )
    intake = SequenceCompletion(
        '{"title":"Ash Harbor","brief":"A sufficiently detailed fantasy investigation '
        'brief set in a dangerous coastal refuge.","specified_fields":[]}',
        '{"title":"Ash Harbor","brief":"A sufficiently detailed hopeful fantasy '
        'investigation brief set in a dangerous coastal refuge.","tone":"hopeful dark '
        'fantasy","specified_fields":["tone"]}',
    )
    worldgen = FakeWorldgen()
    app = MessageApplication(
        store=store,
        context=context,
        state_router=StateDecisionRouter(intent),
        world_intake_pipeline=create_world_intake_pipeline(intake),
        worldgen=worldgen,
    )
    now = datetime(2026, 1, 1, tzinfo=UTC)

    def send(event_id: str, content: str) -> str:
        return asyncio.run(
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

    send("create", "Создадим мир про расследования в портовом городе")
    send("generate", "Можно генерировать")
    revised = send("revise", "Сделай тон более обнадёживающим")

    workspace = store.world_workspace("channel")
    assert workspace["stage"] == "collecting"
    assert workspace["settings"]["tone"] == "hopeful dark fantasy"
    assert workspace["sources"]["tone"] == "player"
    assert "**Тон:** hopeful dark fantasy" in revised
    assert "_[задано игроками]_" in revised
    assert "Public premise revision 2" not in revised
    assert worldgen.calls == 1

    regenerated = send("regenerate", "Перегенерируй мир")
    assert store.world_workspace("channel")["stage"] == "review"
    assert "Public premise revision 2" in regenerated
    assert worldgen.calls == 2


def test_explicit_world_exit_pauses_temporary_project_and_allows_resume(tmp_path) -> None:
    store = SQLiteStore(tmp_path / "db.sqlite3")
    store.initialize()
    store.create_world(WorldState("temporary", "Temporary"))
    store.save_world_workspace(
        channel_id="channel",
        world_id="temporary",
        stage="collecting",
        brief="A sufficiently detailed temporary world brief for cancellation.",
        settings={},
        sources={},
    )
    app = MessageApplication(
        store=store,
        context=ContextAssembler(Path(__file__).parents[1] / "prompts"),
        state_router=StateDecisionRouter(
            SequenceCompletion(
                '{"command":"select_world","argument":"Temporary","confidence":1,'
                '"evidence":"resume selected draft"}'
            )
        ),
    )

    response = asyncio.run(
        app(
            IncomingMessage(
                event_id="exit",
                channel_id="channel",
                author_id="alice",
                content="Отмени создание мира",
                created_at=datetime(2026, 1, 1, tzinfo=UTC),
            )
        )
    )

    assert store.world_workspace("channel") is None
    assert store.world_state("temporary") is not None
    assert store.world_project("temporary")["channel_id"] is None
    assert "Драфт сохранён" in response

    resumed = asyncio.run(
        app(
            IncomingMessage(
                event_id="resume",
                channel_id="channel",
                author_id="alice",
                content="Выбираем Temporary",
                created_at=datetime(2026, 1, 1, tzinfo=UTC),
            )
        )
    )
    assert store.world_workspace("channel")["world_id"] == "temporary"
    assert "Возобновлено редактирование" in resumed


def test_cross_reference_error_is_repaired_inside_structuring_pipeline() -> None:
    valid_world = valid_world_payload()
    invalid_world = {
        **valid_world,
        "locations": [
            {"id": "sky_city", "name": "Sky City", "description": "Upper city."},
            {"id": "sky_city", "name": "Lower City", "description": "Lower city."},
        ],
    }
    completion = WorldCompletion(
        [
            (
                '{"module_plot":"A city hangs above an endless storm on ancient chains. '
                "The Chain Keepers conceal failing anchors while memory is traded for repairs. "
                "Players can reveal the lie, negotiate with rival districts, or descend into the "
                "storm. Each path exposes clues to a waking intelligence and forces a costly "
                'choice."}'
            ),
            json.dumps(invalid_world),
            json.dumps(valid_world),
        ]
    )
    context = ContextAssembler(Path(__file__).parents[1] / "prompts")
    draft = asyncio.run(
        worldgen_service(context, completion).generate(
            world=WorldState("storm", "Storm World"),
            brief="A city above a storm",
        )
    )

    assert len(draft.locations) == 1
    assert completion.calls == 3


def test_cross_reference_error_uses_structuring_fallback_after_repair_exhausted() -> None:
    creative = SequenceCompletion(
        '{"module_plot":"A city hangs above an endless storm on ancient chains. '
        "The Chain Keepers conceal failing anchors while memory is traded for repairs. "
        "Players can reveal the lie, negotiate between districts, or descend into the storm. "
        "Each path exposes clues to a waking intelligence, saves one district at another's cost, "
        'and forces the city to decide what memory is worth."}'
    )
    invalid_world = valid_world_payload()
    invalid_world["factions"] = ["Chain Keepers", "chain keepers"]
    structuring = SequenceCompletion(json.dumps(invalid_world), json.dumps(invalid_world))
    structuring_fallback = SequenceCompletion(json.dumps(valid_world_payload()))
    context = ContextAssembler(Path(__file__).parents[1] / "prompts")
    service = create_world_generation_service(
        context=context,
        creative_completion=creative,
        creative_fallback_completion=creative,
        structuring_completion=structuring,
        structuring_fallback_completion=structuring_fallback,
    )

    draft = asyncio.run(
        service.generate(
            world=WorldState("storm", "Storm World"),
            brief="A city above a storm",
        )
    )

    assert draft.factions == ["Chain Keepers"]
    assert structuring.calls == 2
    assert structuring_fallback.calls == 1


def test_exhausted_world_generation_keeps_review_draft_and_returns_editor_fallback(
    tmp_path,
) -> None:
    class NeverStateCompletion:
        async def complete(self, **kwargs) -> CompletionResult:
            raise AssertionError("exact generation phrase must not call the state model")

    class ExhaustedWorldgen:
        async def generate(self, *, world, brief, settings=None) -> WorldDraft:
            raise PipelineValidationError("structuring exhausted")

    store = SQLiteStore(tmp_path / "db.sqlite3")
    store.initialize()
    world = GameService(store).create_world(world_id="storm", title="Storm World")
    prior_content = valid_world_payload()
    store.update_world_content(
        world_id=world.world_id,
        expected_revision=world.revision,
        content=prior_content,
    )
    store.save_world_workspace(
        channel_id="channel",
        world_id="storm",
        stage="review",
        brief="A sufficiently detailed world brief about a city above a storm.",
        settings={},
        sources={},
    )
    app = MessageApplication(
        store=store,
        context=ContextAssembler(Path(__file__).parents[1] / "prompts"),
        state_router=StateDecisionRouter(NeverStateCompletion()),
        worldgen=ExhaustedWorldgen(),
    )

    response = asyncio.run(
        app(
            IncomingMessage(
                event_id="generate-invalid",
                channel_id="channel",
                author_id="alice",
                content="Перегенерируй мир",
                created_at=datetime(2026, 1, 1, tzinfo=UTC),
            )
        )
    )

    workspace = store.world_workspace("channel")
    assert workspace is not None
    assert workspace["stage"] == "review"
    assert store.world_state("storm").revision == 1
    assert store.world_content("storm") == prior_content
    assert "перегенерировать" in response
