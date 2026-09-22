import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from masterclaw.app.game_service import GameService
from masterclaw.app.handlers.world_management import WorldManagementHandlers
from masterclaw.app.i18n import tr
from masterclaw.app.message_handler import MessageApplication
from masterclaw.app.world_settings import WORLD_SETTING_DEFAULTS
from masterclaw.app.worldgen_service import (
    WorldGenerationError,
    WorldGenerationService,
    create_world_generation_service,
    validate_confirmed_world_settings,
)
from masterclaw.context.assembler import AssembledContext, ContextAssembler
from masterclaw.domain.models import HandlerResponse, IncomingMessage
from masterclaw.domain.state import GameLifecycle, GameState, WorldState
from masterclaw.pipelines.base import CompletionResult, PipelineValidationError
from masterclaw.pipelines.state_decision import StateDecisionRouter
from masterclaw.pipelines.world_intake import WorldCreationBrief, create_world_intake_pipeline
from masterclaw.pipelines.worldgen import (
    WorldDraft,
    WorldLocation,
    create_world_structuring_pipeline,
)
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


def adherent_english_world_payload() -> dict:
    payload = valid_world_payload()
    premise = "Serious fantasy investigators defend one storm city while its ancient chains fail."
    payload["premise"] = premise
    payload["setting_adherence"] = {
        "locale": {
            "confirmed_value": "en",
            "applied_terms": ["Serious fantasy"],
            "evidence": premise,
        },
        "genre": {
            "confirmed_value": "fantasy",
            "applied_terms": ["fantasy"],
            "evidence": premise,
        },
        "tone": {
            "confirmed_value": "serious",
            "applied_terms": ["Serious"],
            "evidence": premise,
        },
        "scale": {
            "confirmed_value": "one city",
            "applied_terms": ["one storm city"],
            "evidence": premise,
        },
        "player_role": {
            "confirmed_value": "investigators",
            "applied_terms": ["investigators"],
            "evidence": premise,
        },
    }
    return payload


def natural_adherent_world_payload() -> dict:
    premise = (
        "Постапокалиптическое фэнтези переносит самостоятельную группу героев, связанную "
        "общей проблемой, в один регион с несколькими значимыми локациями. Здесь их ждёт "
        "мрачное расследование последствий магической войны."
    )

    def localized_pregen(name: str, target: str) -> dict:
        return {
            "name": name,
            "concept": f"{name} — следователь, ищущий правду о разрушенном городе.",
            "hook": f"{name} должен решить, можно ли доверять {target}.",
            "biography": (
                f"{name} пережил магическую войну и теперь расследует опасные аномалии города."
            ),
            "faction_affiliations": ["Хранители цепей"],
            "connections": [
                {
                    "character_name": target,
                    "relationship": "Надёжный, но непростой союзник",
                }
            ],
            "traits": [
                {
                    "name": f"Черта {index}",
                    "level": 3,
                    "aspects": [
                        f"Аспект {index}.1",
                        f"Аспект {index}.2",
                        f"Аспект {index}.3",
                    ],
                }
                for index in range(6)
            ],
            "flags": [
                {
                    "text": f"Я полагаюсь на {target}",
                    "type": "relationship",
                    "is_positive": True,
                },
                {"text": "Найти правду", "type": "goal", "is_positive": False},
                {
                    "text": "У каждой тайны есть цена",
                    "type": "belief",
                    "is_positive": False,
                },
            ],
        }

    return {
        "premise": premise,
        "themes": ["memory"],
        "locations": [
            {
                "id": "sky_city",
                "name": "Небесный город",
                "description": "Город висит над древними катакомбами на ржавых цепях.",
            }
        ],
        "factions": ["Хранители цепей"],
        "tensions": ["Цепи разрушаются, пока районы спорят о последних ресурсах."],
        "secret_plot": "The storm is a sleeping intelligence.",
        "character_templates": [
            localized_pregen("Mira", "Orin"),
            localized_pregen("Orin", "Vale"),
            localized_pregen("Vale", "Mira"),
        ],
        "setting_adherence": {
            "locale": {
                "confirmed_value": "ru",
                "applied_terms": ["Постапокалиптическое фэнтези"],
                "evidence": premise,
            },
            "genre": {
                "confirmed_value": "post-apocalyptic fantasy",
                "applied_terms": ["Постапокалиптическое фэнтези"],
                "evidence": premise,
            },
            "tone": {
                "confirmed_value": "grimdark investigation",
                "applied_terms": ["мрачное расследование"],
                "evidence": premise,
            },
            "scale": {
                "confirmed_value": "один регион с несколькими значимыми локациями",
                "applied_terms": ["один регион", "значимыми локациями"],
                "evidence": premise,
            },
            "player_role": {
                "confirmed_value": "самостоятельная группа героев, связанная общей проблемой",
                "applied_terms": ["самостоятельную группу героев", "общей проблемой"],
                "evidence": premise,
            },
        },
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
                json.dumps(natural_adherent_world_payload()),
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
        self.tasks: list[str] = []

    async def complete(self, **kwargs) -> CompletionResult:
        self.calls += 1
        self.tasks.append(kwargs["task"])
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


def test_world_structuring_repair_receives_safe_trait_aspect_error_code() -> None:
    invalid_world = valid_world_payload()
    invalid_world["character_templates"][0]["traits"][0] = {
        "name": "Mismatched trait",
        "level": 6,
        "aspects": ["One", "Two", "Three"],
    }
    completion = SequenceCompletion(
        json.dumps(invalid_world),
        json.dumps(valid_world_payload()),
    )

    draft = asyncio.run(
        create_world_structuring_pipeline(completion).run(
            task="Structure the supplied world.",
            context=AssembledContext(
                static_rules="",
                dynamic_context="{}",
                fragments=(),
                estimated_tokens=1,
                output_token_budget=6000,
            ),
        )
    )

    assert len(draft.character_templates) == 3
    assert completion.calls == 2
    assert "trait_aspect_count_mismatch" in completion.tasks[1]


def test_world_intake_rejects_unknown_specified_field() -> None:
    with pytest.raises(ValidationError, match="invented_setting"):
        WorldCreationBrief.model_validate(
            {
                "title": "Storm World",
                "brief": "A sufficiently detailed world brief about a city above a storm.",
                "specified_fields": ["invented_setting"],
            }
        )


def test_world_intake_requires_current_message_provenance() -> None:
    with pytest.raises(ValidationError, match="specified_fields"):
        WorldCreationBrief.model_validate(
            {
                "title": "Storm World",
                "brief": "A sufficiently detailed world brief about a city above a storm.",
                "tone": "hopeful",
                "specified_fields": [],
            }
        )


def test_world_intake_preserves_large_unsupported_pregen_count_for_friendly_rejection() -> None:
    intake = WorldCreationBrief.model_validate(
        {
            "title": "Large Party",
            "brief": "A complete world brief for a deliberately oversized adventuring party.",
            "pregenerated_character_count": 7,
            "specified_fields": ["pregenerated_character_count"],
        }
    )

    assert intake.pregenerated_character_count == 7


def test_explicit_empty_boundaries_and_false_progression_are_not_defaulted() -> None:
    intake = WorldCreationBrief.model_validate(
        {
            "title": "Storm World",
            "brief": "A sufficiently detailed world brief about a city above a storm.",
            "content_constraints": [],
            "progression_enabled": False,
            "specified_fields": ["content_constraints", "progression_enabled"],
        }
    )

    settings, sources = WorldManagementHandlers._world_settings(intake)

    assert settings["content_constraints"] == []
    assert sources["content_constraints"] == "player"
    assert settings["progression_enabled"] is False
    assert sources["progression_enabled"] == "player"


def test_new_english_world_infers_locale_without_claiming_player_provenance() -> None:
    intake = WorldCreationBrief.model_validate(
        {
            "title": "Storm City",
            "brief": "A sufficiently detailed English world brief about a city above a storm.",
            "specified_fields": [],
        }
    )

    settings, sources = WorldManagementHandlers._world_settings(
        intake,
        inferred_locale="en",
    )

    assert settings["locale"] == "en"
    assert sources["locale"] == "inferred"


def test_explicit_null_locale_restores_default_instead_of_using_message_inference() -> None:
    intake = WorldCreationBrief.model_validate(
        {
            "title": "Storm City",
            "brief": "A sufficiently detailed English request that explicitly resets locale.",
            "locale": None,
            "specified_fields": ["locale"],
        }
    )

    settings, sources = WorldManagementHandlers._world_settings(
        intake,
        inferred_locale="en",
    )

    assert settings["locale"] == WORLD_SETTING_DEFAULTS["locale"]
    assert sources["locale"] == "default"


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


@pytest.mark.parametrize(
    "settings",
    [
        {"tone": "   "},
        {"locale": "de"},
        {"narrative_detail": "verbose"},
        {"progression_enabled": "false"},
        {"content_constraints": [""]},
        {"pregenerated_character_count": 2},
        {
            "pregenerated_character_count": 3,
            "pregenerated_character_briefs": ["one", "two", "three", "four"],
        },
    ],
)
def test_confirmed_world_settings_fail_before_generation(settings: dict[str, object]) -> None:
    with pytest.raises(WorldGenerationError):
        validate_confirmed_world_settings(settings)


def test_world_validation_enforces_boundaries_concepts_and_secret_separation() -> None:
    boundary_payload = valid_world_payload()
    boundary_payload["tensions"] = ["Public torture chambers divide the city"]
    with pytest.raises(WorldGenerationError, match="content boundary"):
        WorldGenerationService._validate_draft(
            WorldDraft.model_validate(boundary_payload),
            settings={"content_constraints": ["no gore or torture"]},
        )

    draft = WorldDraft.model_validate(valid_world_payload())
    WorldGenerationService._validate_draft(
        draft,
        settings={
            "pregenerated_character_count": 3,
            "pregenerated_character_briefs": ["investigator"],
        },
    )
    with pytest.raises(WorldGenerationError, match="pregen concept"):
        WorldGenerationService._validate_draft(
            draft,
            settings={
                "pregenerated_character_count": 3,
                "pregenerated_character_briefs": ["blind oracle"],
            },
        )

    leaked_payload = valid_world_payload()
    leaked_payload["premise"] = (
        "The storm is a sleeping intelligence. Everyone in the city already knows this truth."
    )
    with pytest.raises(WorldGenerationError, match="overlaps the secret plot"):
        WorldGenerationService._validate_draft(WorldDraft.model_validate(leaked_payload))


@pytest.mark.parametrize(
    ("constraint", "generated_text"),
    [
        ("no torture", "Prisoners are tortured openly beneath the city."),
        ("no violence", "Violent raids decide which district receives food."),
        ("без пыток", "В подвале проводят пытки над пленниками."),
        ("без насилия", "Открытое насилие определяет власть в городе."),
    ],
)
def test_world_boundaries_reject_english_and_russian_inflections(
    constraint: str,
    generated_text: str,
) -> None:
    payload = valid_world_payload()
    payload["tensions"] = [generated_text]

    with pytest.raises(WorldGenerationError, match="content boundary"):
        WorldGenerationService._validate_draft(
            WorldDraft.model_validate(payload),
            settings={"content_constraints": [constraint]},
        )


@pytest.mark.parametrize(
    ("constraint", "safe_text"),
    [
        ("no torture", "Torture is absent and prohibited throughout the city."),
        ("no torture", "The setting contains no torture or depictions of it."),
        ("без пыток", "Пытки запрещены и отсутствуют в этом мире."),
        ("без насилия", "В сеттинге нет насилия и его описаний."),
    ],
)
def test_world_boundaries_allow_explicit_absence_statements(
    constraint: str,
    safe_text: str,
) -> None:
    payload = valid_world_payload()
    payload["tensions"] = [safe_text]

    WorldGenerationService._validate_draft(
        WorldDraft.model_validate(payload),
        settings={"content_constraints": [constraint]},
    )


@pytest.mark.parametrize(
    ("constraint", "unsafe_double_negative"),
    [
        ("no torture", "Torture is not prohibited in the lower district."),
        (
            "no torture",
            "Torture is prohibited in law and torture occurs in hidden prisons.",
        ),
        ("без пыток", "Пытки не запрещены в нижнем городе."),
    ],
)
def test_world_boundaries_do_not_treat_double_negation_as_safe(
    constraint: str,
    unsafe_double_negative: str,
) -> None:
    payload = valid_world_payload()
    payload["tensions"] = [unsafe_double_negative]

    with pytest.raises(WorldGenerationError, match="content boundary"):
        WorldGenerationService._validate_draft(
            WorldDraft.model_validate(payload),
            settings={"content_constraints": [constraint]},
        )


def test_multiword_boundary_matches_reordered_terms_but_allows_protective_context() -> None:
    unsafe = valid_world_payload()
    unsafe["tensions"] = ["Children are exposed to brutal violence in the lower wards."]
    with pytest.raises(WorldGenerationError, match="content boundary"):
        WorldGenerationService._validate_draft(
            WorldDraft.model_validate(unsafe),
            settings={"content_constraints": ["no violence against children"]},
        )

    safe = valid_world_payload()
    safe["tensions"] = ["Children are protected from violence throughout the city."]
    WorldGenerationService._validate_draft(
        WorldDraft.model_validate(safe),
        settings={"content_constraints": ["no violence against children"]},
    )


@pytest.mark.parametrize(
    ("constraint", "unsafe_text", "safe_text"),
    [
        (
            "violence only off-screen",
            "Graphic violence erupts openly in the market.",
            "Violence remains off-screen and is never depicted.",
        ),
        (
            "fade to black for torture",
            "The jailers graphically torture prisoners before the crowd.",
            "Any torture fades to black and remains off-screen.",
        ),
        (
            "пытки только за кадром",
            "Стража подробно показывает пытки пленников.",
            "Пытки остаются за кадром и не описываются.",
        ),
    ],
)
def test_veiled_content_boundaries_reject_direct_depiction_but_allow_the_veil(
    constraint: str,
    unsafe_text: str,
    safe_text: str,
) -> None:
    unsafe = valid_world_payload()
    unsafe["tensions"] = [unsafe_text]
    with pytest.raises(WorldGenerationError, match="content boundary"):
        WorldGenerationService._validate_draft(
            WorldDraft.model_validate(unsafe),
            settings={"content_constraints": [constraint]},
        )

    safe = valid_world_payload()
    safe["tensions"] = [safe_text]
    WorldGenerationService._validate_draft(
        WorldDraft.model_validate(safe),
        settings={"content_constraints": [constraint]},
    )


def test_multi_term_pregen_concept_requires_meaningful_coverage() -> None:
    one_word_payload = valid_world_payload()
    one_word_payload["character_templates"][0]["concept"] = (
        "Mira is a blind courier who distrusts every prophecy."
    )
    with pytest.raises(WorldGenerationError, match="pregen concept"):
        WorldGenerationService._validate_draft(
            WorldDraft.model_validate(one_word_payload),
            settings={"pregenerated_character_briefs": ["blind oracle"]},
        )

    full_payload = valid_world_payload()
    full_payload["character_templates"][0]["concept"] = (
        "Mira is a blind oracle whose visions arrive during storms."
    )
    WorldGenerationService._validate_draft(
        WorldDraft.model_validate(full_payload),
        settings={"pregenerated_character_briefs": ["blind oracle"]},
    )


def test_requested_pregen_concepts_require_distinct_templates() -> None:
    payload = valid_world_payload()
    payload["character_templates"][0]["concept"] = (
        "Mira is a blind oracle and courier serving the upper city."
    )

    with pytest.raises(WorldGenerationError, match="pregen concept"):
        WorldGenerationService._validate_draft(
            WorldDraft.model_validate(payload),
            settings={"pregenerated_character_briefs": ["blind oracle", "blind courier"]},
        )


def test_world_setting_adherence_is_auditable_and_not_persisted() -> None:
    draft = WorldDraft.model_validate(adherent_english_world_payload())

    WorldGenerationService._validate_draft(
        draft,
        settings={
            "locale": "en",
            "genre": "fantasy",
            "tone": "serious",
            "scale": "one city",
            "player_role": "investigators",
        },
    )

    assert "setting_adherence" not in draft.model_dump(mode="json")
    legacy = WorldDraft.model_validate(valid_world_payload())
    WorldGenerationService._validate_draft(legacy, settings={})


@pytest.mark.parametrize(
    ("setting_name", "confirmed_value"),
    [
        ("locale", "en"),
        ("genre", "fantasy"),
        ("tone", "serious"),
        ("scale", "one city"),
        ("player_role", "investigators"),
    ],
)
def test_each_confirmed_world_setting_requires_its_own_adherence_claim(
    setting_name: str,
    confirmed_value: str,
) -> None:
    payload = adherent_english_world_payload()
    payload["setting_adherence"].pop(setting_name)

    with pytest.raises(WorldGenerationError, match=f"lacks {setting_name} adherence evidence"):
        WorldGenerationService._validate_draft(
            WorldDraft.model_validate(payload),
            settings={setting_name: confirmed_value},
        )


@pytest.mark.parametrize(
    ("mutation", "expected_error"),
    [
        ("missing_claim", "lacks genre adherence evidence"),
        ("changed_value", "changed the confirmed genre"),
        ("invented_evidence", "evidence is not public draft text"),
        ("missing_term", "evidence omits its applied terms"),
        ("wrong_locale", "prose does not match confirmed locale"),
    ],
)
def test_world_setting_adherence_rejects_unverifiable_claims(
    mutation: str,
    expected_error: str,
) -> None:
    payload = adherent_english_world_payload()
    settings: dict[str, object] = {"genre": "fantasy"}
    if mutation == "missing_claim":
        payload["setting_adherence"].pop("genre")
    elif mutation == "changed_value":
        payload["setting_adherence"]["genre"]["confirmed_value"] = "horror"
    elif mutation == "invented_evidence":
        payload["setting_adherence"]["genre"]["evidence"] = (
            "This invented excerpt is nowhere in the public world."
        )
    elif mutation == "missing_term":
        payload["setting_adherence"]["genre"]["applied_terms"] = ["horror"]
    elif mutation == "wrong_locale":
        settings = {"locale": "ru"}
        payload["setting_adherence"]["locale"]["confirmed_value"] = "ru"
    else:
        raise AssertionError(f"unknown mutation: {mutation}")

    with pytest.raises(WorldGenerationError, match=expected_error):
        WorldGenerationService._validate_draft(
            WorldDraft.model_validate(payload),
            settings=settings,
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


@pytest.mark.parametrize("requested_count", [2, 7])
def test_unsupported_pregen_count_is_explained_without_creating_draft(
    tmp_path, requested_count: int
) -> None:
    store = SQLiteStore(tmp_path / "db.sqlite3")
    store.initialize()
    app = MessageApplication(
        store=store,
        context=ContextAssembler(Path(__file__).parents[1] / "prompts"),
        state_router=StateDecisionRouter(
            SequenceCompletion(
                '{"command":"create_world","argument":null,"confidence":1,'
                '"evidence":"explicit new world request"}'
            )
        ),
        world_intake_pipeline=create_world_intake_pipeline(
            SequenceCompletion(
                '{"title":"Unsupported Party","brief":"A complete world brief designed for an '
                f'unsupported party size.","pregenerated_character_count":{requested_count},'
                '"specified_fields":["pregenerated_character_count"]}'
            )
        ),
    )

    response = asyncio.run(
        app(
            IncomingMessage(
                event_id=f"unsupported-pregen-{requested_count}",
                channel_id="channel",
                author_id="alice",
                content=(
                    f"Please create a new world for exactly {requested_count} pregenerated heroes."
                ),
                created_at=datetime(2026, 1, 1, tzinfo=UTC),
            )
        )
    )

    assert (
        tr(
            "en",
            "world_pregen_count_unsupported",
            requested=requested_count,
            minimum=3,
            maximum=6,
        )
        in response
    )
    assert store.world_workspace("channel") is None
    assert store.world_state(f"world_unsupported-pregen-{requested_count}") is None


def test_detailed_new_world_request_cannot_mutate_active_workspace(tmp_path) -> None:
    class NeverIntakeCompletion:
        async def complete(self, **kwargs) -> CompletionResult:
            raise AssertionError("new-world guard must run before world intake")

    store = SQLiteStore(tmp_path / "db.sqlite3")
    store.initialize()
    store.create_world(WorldState("active", "Active Draft"))
    original_brief = "A sufficiently detailed existing world brief that must remain unchanged."
    store.save_world_workspace(
        channel_id="channel",
        world_id="active",
        stage="collecting",
        brief=original_brief,
        settings={},
        sources={},
    )
    app = MessageApplication(
        store=store,
        context=ContextAssembler(Path(__file__).parents[1] / "prompts"),
        state_router=StateDecisionRouter(
            SequenceCompletion(
                '{"command":"revise_world","argument":null,"confidence":1,'
                '"evidence":"free-form workspace message"}'
            )
        ),
        world_intake_pipeline=create_world_intake_pipeline(NeverIntakeCompletion()),
    )

    response = asyncio.run(
        app(
            IncomingMessage(
                event_id="replace-attempt",
                channel_id="channel",
                author_id="alice",
                content="Create a new world about pirates with floating islands and skyships.",
                created_at=datetime(2026, 1, 1, tzinfo=UTC),
            )
        )
    )

    assert tr("en", "world_workspace_active") in response
    assert store.world_workspace("channel")["brief"] == original_brief


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
    assert isinstance(selected, HandlerResponse)
    assert selected.completion_game_id == game_id
    assert store.channel_state("channel").game_id == game_id
    assert store.first_scene_id(game_id) is None
    assert store.game_state(game_id).narrative_channel_id == "channel"
    assert "ПОДГОТОВКА" in selected.text

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
    # Only the original free-form world brief needs the state LLM. Exact world and
    # pregenerated-character names are selected structurally.
    assert state_route.calls == 1
    assert store.scene_projection(game_id=game_id, player_id="alice") is None
    assert "Стартовая сцена" in chosen

    GameService(store).start_game(game_id)
    assert store.scene_projection(game_id=game_id, player_id="alice") is not None


def test_world_selection_retry_finishes_orphan_game_binding(tmp_path) -> None:
    store = SQLiteStore(tmp_path / "db.sqlite3")
    store.initialize()
    store.create_world(WorldState("storm", "Storm World", status="approved"))
    store.update_world_content(
        world_id="storm",
        expected_revision=0,
        content=valid_world_payload(),
        status="approved",
    )
    # Simulate a crash after GameService.prepare_game created the game but before bind_channel.
    store.create_game(GameState("game_retry", "storm", GameLifecycle.PREPARING))
    assert store.channel_state("channel").game_id is None

    app = MessageApplication(
        store=store,
        context=ContextAssembler(Path(__file__).parents[1] / "prompts"),
        state_router=StateDecisionRouter(
            SequenceCompletion(
                '{"command":"select_world","argument":"Storm World","confidence":1,'
                '"evidence":"catalog selection"}'
            )
        ),
    )
    response = asyncio.run(
        app(
            IncomingMessage(
                event_id="retry",
                channel_id="channel",
                author_id="alice",
                content="Choose Storm World",
                created_at=datetime(2026, 1, 1, tzinfo=UTC),
            )
        )
    )

    assert isinstance(response, HandlerResponse)
    assert response.completion_game_id == "game_retry"
    assert store.channel_state("channel").game_id == "game_retry", response
    assert store.game_state("game_retry").narrative_channel_id == "channel"
    assert store.first_scene_id("game_retry") is None
    assert "Storm World" in response.text


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


def test_deterministic_boundary_failure_uses_structuring_fallback() -> None:
    creative = SequenceCompletion(
        '{"module_plot":"A city hangs above an endless storm on ancient chains. '
        "The Chain Keepers ration repairs while memory is traded for safety. Players may expose "
        "the fraud, bargain with rivals, or descend into the storm. Each route changes who pays "
        'for the failing anchors and reveals clues about the intelligence below."}'
    )
    invalid_world = valid_world_payload()
    invalid_world["tensions"] = ["Torture chambers enforce the rationing order"]
    structuring = SequenceCompletion(json.dumps(invalid_world))
    structuring_fallback = SequenceCompletion(json.dumps(valid_world_payload()))
    service = create_world_generation_service(
        context=ContextAssembler(Path(__file__).parents[1] / "prompts"),
        creative_completion=creative,
        creative_fallback_completion=creative,
        structuring_completion=structuring,
        structuring_fallback_completion=structuring_fallback,
    )

    draft = asyncio.run(
        service.generate(
            world=WorldState("storm", "Storm World"),
            brief="A city above a storm without torture.",
            settings={"content_constraints": ["no torture"]},
        )
    )

    assert draft.tensions == ["The chains are failing"]
    assert structuring.calls == 1
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
