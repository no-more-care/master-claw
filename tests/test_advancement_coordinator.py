import asyncio
from datetime import UTC, datetime
from pathlib import Path

import pytest

from masterclaw.app.advancement_coordinator import AdvancementCoordinator
from masterclaw.app.i18n import tr
from masterclaw.app.message_handler import MessageApplication
from masterclaw.context.assembler import ContextAssembler
from masterclaw.context.manifests import FallbackAction, PipelineName, manifest_for
from masterclaw.domain.characters import CharacterState
from masterclaw.domain.mechanics import CharacterSheet, Flag, FlagType, Trait
from masterclaw.domain.models import GameLifecycle, IncomingMessage
from masterclaw.domain.state import GameState, WorldState
from masterclaw.pipelines.advancement import create_advancement_safety_pipeline
from masterclaw.pipelines.base import CompletionResult
from masterclaw.pipelines.conversation_actions import create_advancement_intake_pipeline
from masterclaw.pipelines.state_decision import StateDecisionRouter
from masterclaw.storage.sqlite import SQLiteStore


class FakeCompletion:
    def __init__(self, allowed: bool) -> None:
        self.allowed = allowed
        self.calls = 0

    async def complete(self, **kwargs) -> CompletionResult:
        self.calls += 1
        value = "true" if self.allowed else "false"
        return CompletionResult(
            f'{{"allowed":{value},"reason":"scene assessment","evidence":["current scene"]}}',
            used_tool=True,
        )


class StaticCompletion:
    def __init__(self, response: str) -> None:
        self.response = response
        self.calls = 0

    async def complete(self, **kwargs) -> CompletionResult:
        self.calls += 1
        return CompletionResult(self.response, used_tool=True)


def setup(tmp_path, *, enabled=True):
    store = SQLiteStore(tmp_path / "db.sqlite3")
    store.initialize()
    store.create_world(WorldState("world", "World"))
    store.create_game(GameState("game", "world", GameLifecycle.ACTIVE, progression_enabled=enabled))
    store.create_scene(scene_id="camp", game_id="game", title="Camp", state={"danger": "nearby"})
    store.place_player(game_id="game", player_id="alice", scene_id="camp")
    sheet = CharacterSheet(
        "Hero",
        tuple(Trait(f"T{i}", 3, tuple(f"A{i}.{n}" for n in range(3))) for i in range(6)),
        (
            Flag("Friend", FlagType.RELATIONSHIP),
            Flag("Goal", FlagType.GOAL),
            Flag("Belief", FlagType.BELIEF),
        ),
    )
    store.create_character(
        CharacterState("hero", "game", "alice", "Bio", sheet, experience_earned=4)
    )
    return store


def coordinator(store, allowed):
    return AdvancementCoordinator(
        store=store,
        context=ContextAssembler(Path(__file__).parents[1] / "prompts"),
        safety_pipeline=create_advancement_safety_pipeline(FakeCompletion(allowed)),
    )


def test_gm_pipeline_can_deny_advancement_for_current_scene(tmp_path) -> None:
    store = setup(tmp_path)
    with pytest.raises(ValueError, match="not allowed now"):
        asyncio.run(
            coordinator(store, False).raise_trait(
                game_id="game", player_id="alice", trait_name="T0", new_aspect="New"
            )
        )


def test_allowed_decision_becomes_revision_bound_permit(tmp_path) -> None:
    store = setup(tmp_path)
    updated = asyncio.run(
        coordinator(store, True).raise_trait(
            game_id="game", player_id="alice", trait_name="T0", new_aspect="New"
        )
    )
    assert updated.sheet.traits[0].level == 4
    assert updated.experience_available == 0


def test_disabled_progression_skips_gm_pipeline_and_rejects(tmp_path) -> None:
    store = setup(tmp_path, enabled=False)
    with pytest.raises(ValueError, match="disabled"):
        asyncio.run(
            coordinator(store, True).raise_trait(
                game_id="game", player_id="alice", trait_name="T0", new_aspect="New"
            )
        )


def test_advancement_replay_uses_source_event_id_without_spending_or_authorizing_twice(
    tmp_path,
) -> None:
    store = setup(tmp_path)
    completion = FakeCompletion(True)
    advancement = AdvancementCoordinator(
        store=store,
        context=ContextAssembler(Path(__file__).parents[1] / "prompts"),
        safety_pipeline=create_advancement_safety_pipeline(completion),
    )

    first = asyncio.run(
        advancement.raise_trait(
            game_id="game",
            player_id="alice",
            trait_name="T0",
            new_aspect="New",
            causation_id="advancement:discord-event",
        )
    )
    replayed = asyncio.run(
        advancement.raise_trait(
            game_id="game",
            player_id="alice",
            trait_name="T0",
            new_aspect="New",
            causation_id="advancement:discord-event",
        )
    )

    assert replayed == first
    assert replayed.sheet.traits[0].level == 4
    assert replayed.experience_spent == 4
    assert completion.calls == 1


def test_checkpointed_advancement_authorization_rejects_changed_scene_revision(
    tmp_path,
) -> None:
    store = setup(tmp_path)
    completion = FakeCompletion(True)
    advancement = AdvancementCoordinator(
        store=store,
        context=ContextAssembler(Path(__file__).parents[1] / "prompts"),
        safety_pipeline=create_advancement_safety_pipeline(completion),
    )
    request = {"kind": "raise", "trait": "T0", "new_aspect": "New"}

    permit = asyncio.run(
        advancement.authorize_request(
            game_id="game",
            player_id="alice",
            request=request,
            checkpoint_event_id="advancement-interrupted-before-apply",
        )
    )
    assert permit.scene_revision == 0

    store.apply_scene_patch(
        game_id="game",
        scene_id="camp",
        expected_revision=0,
        causation_id="external:camp-changed",
        summary="Danger reaches the camp before the interrupted request resumes.",
        add_facts=["The camp is under attack"],
        remove_facts=[],
    )

    with pytest.raises(RuntimeError, match="input fingerprint mismatch"):
        asyncio.run(
            advancement.authorize_request(
                game_id="game",
                player_id="alice",
                request=request,
                checkpoint_event_id="advancement-interrupted-before-apply",
            )
        )

    character = store.character_for_player(game_id="game", player_id="alice")
    assert character is not None
    assert character.sheet.traits[0].level == 3
    assert character.experience_spent == 0
    assert completion.calls == 1


def test_invalid_advancement_safety_uses_actionable_fallback_without_mutation(
    tmp_path,
) -> None:
    store = setup(tmp_path)
    store.bind_channel(channel_id="game", game_id="game")
    safety = StaticCompletion("not valid JSON")
    app = MessageApplication(
        store=store,
        context=ContextAssembler(Path(__file__).parents[1] / "prompts"),
        state_router=StateDecisionRouter(
            StaticCompletion(
                '{"command":"request_advancement","argument":null,"confidence":1,'
                '"evidence":"explicit advancement request"}'
            )
        ),
        advancement=AdvancementCoordinator(
            store=store,
            context=ContextAssembler(Path(__file__).parents[1] / "prompts"),
            safety_pipeline=create_advancement_safety_pipeline(safety),
        ),
        advancement_intake_pipeline=create_advancement_intake_pipeline(
            StaticCompletion(
                '{"kind":"raise","trait_name":"T0","aspects":["New"],"justification":null}'
            )
        ),
    )
    before = store.character_for_player(game_id="game", player_id="alice")

    response = asyncio.run(
        app(
            IncomingMessage(
                event_id="invalid-advancement-safety",
                channel_id="game",
                author_id="alice",
                content="I spend my XP to raise T0 and add New.",
                created_at=datetime(2026, 1, 1, tzinfo=UTC),
            )
        )
    )

    after = store.character_for_player(game_id="game", player_id="alice")
    assert "`/advance raise" in response
    assert manifest_for(PipelineName.ADVANCEMENT_SAFETY).on_invalid is (
        FallbackAction.SHOW_ADVANCEMENT_USAGE
    )
    assert tr("ru", FallbackAction.SYSTEM_FAILURE.value) not in response
    assert tr("en", FallbackAction.SYSTEM_FAILURE.value) not in response
    assert before == after
    assert safety.calls == 2
    assert not [
        event
        for event in store.recent_domain_events(game_id="game", limit=20)
        if event["event_type"] == "character_advanced"
    ]
