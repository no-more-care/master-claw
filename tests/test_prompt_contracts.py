from pathlib import Path

import pytest
from pydantic import ValidationError

from masterclaw.domain.state import NarratorRightsLevel, ReserveRecoveryMode
from masterclaw.pipelines.action import ActionInterpretation
from masterclaw.pipelines.character_creation import CharacterDraft
from masterclaw.pipelines.conversation import ConversationReply
from masterclaw.pipelines.conversation_actions import GameConfigurationRequest
from masterclaw.pipelines.narrative import NarrativeResult
from masterclaw.pipelines.reserve_recovery import ReserveAward, ReserveRecoveryDecision

PROJECT_ROOT = Path(__file__).parents[1]


def valid_character_payload() -> dict[str, object]:
    return {
        "name": "Mara",
        "biography": "A careful investigator with ties to the city.",
        "traits": [
            {
                "name": f"Trait {index}",
                "level": 3,
                "aspects": [f"Aspect {index}.{part}" for part in range(3)],
            }
            for index in range(6)
        ],
        "flags": [
            {"text": "Mira is my trusted friend", "type": "relationship", "is_positive": True},
            {"text": "Find the truth", "type": "goal", "is_positive": False},
            {"text": "Never abandon a witness", "type": "belief", "is_positive": False},
        ],
    }


def test_character_draft_requires_normalized_unique_trait_and_aspect_names() -> None:
    duplicate_trait = valid_character_payload()
    duplicate_trait["traits"][1]["name"] = " trait 0 "
    with pytest.raises(ValidationError, match="trait names must be unique"):
        CharacterDraft.model_validate(duplicate_trait)

    duplicate_aspect = valid_character_payload()
    duplicate_aspect["traits"][1]["aspects"][0] = " ASPECT 0.0 "
    with pytest.raises(ValidationError, match="unique across the character"):
        CharacterDraft.model_validate(duplicate_aspect)


def test_character_draft_requires_explicit_positive_relationship() -> None:
    payload = valid_character_payload()
    payload["flags"][0]["is_positive"] = False
    with pytest.raises(ValidationError, match="positive relationship"):
        CharacterDraft.model_validate(payload)

    invalid_positive = valid_character_payload()
    invalid_positive["flags"][1]["is_positive"] = True
    with pytest.raises(ValidationError, match="only a relationship"):
        CharacterDraft.model_validate(invalid_positive)


@pytest.mark.parametrize("resolution", ["roll", "automatic"])
def test_action_interpretation_requires_evidence_for_resolved_paths(resolution: str) -> None:
    payload = {
        "resolution": resolution,
        "trait_names": ["Body"] if resolution == "roll" else [],
        "aspect_names": [],
        "flag": None,
        "bonus_ids": [],
        "difficulty": 2 if resolution == "roll" else None,
        "evidence": [],
        "clarification_question": None,
    }
    with pytest.raises(ValidationError, match="requires evidence"):
        ActionInterpretation.model_validate(payload)


def test_game_configuration_is_typed_and_cross_field_closed() -> None:
    rights = GameConfigurationRequest.model_validate(
        {"kind": "narrator_rights", "narrator_rights": "significant"}
    )
    assert rights.narrator_rights is NarratorRightsLevel.SIGNIFICANT

    recovery = GameConfigurationRequest.model_validate(
        {"kind": "reserve_recovery", "reserve_recovery_mode": "safe_rest"}
    )
    assert recovery.reserve_recovery_mode is ReserveRecoveryMode.SAFE_REST

    with pytest.raises(ValidationError):
        GameConfigurationRequest.model_validate({"kind": "narrative_channel", "channel_id": "abc"})
    with pytest.raises(ValidationError, match="only accepts"):
        GameConfigurationRequest.model_validate(
            {"kind": "progression", "enabled": True, "scene_title": "Injected"}
        )


def test_player_facing_text_contracts_reject_whitespace_only_values() -> None:
    with pytest.raises(ValidationError, match="reply cannot be blank"):
        ConversationReply(reply="   ")
    with pytest.raises(ValidationError, match="narrative cannot be blank"):
        NarrativeResult(narrative="   ")
    with pytest.raises(ValidationError, match="value cannot be blank"):
        ReserveAward(player_id="alice", reason="          ")


def test_action_contract_rejects_blank_evidence_entry() -> None:
    with pytest.raises(ValidationError, match="evidence entries cannot be blank"):
        ActionInterpretation.model_validate(
            {
                "resolution": "automatic",
                "evidence": ["   "],
            }
        )


def test_reserve_recovery_cross_field_errors_are_specific() -> None:
    with pytest.raises(ValidationError) as raised:
        ReserveRecoveryDecision(safe_rest_completed=True)
    assert raised.value.errors()[0]["type"] == "reserve_safe_rest_missing_reason"


@pytest.mark.parametrize(
    "relative_path",
    (
        "skills/rules/SKILL.md",
        "skills/characters/SKILL.md",
        "skills/worldgen/SKILL.md",
        "docs/mechanics-reference.md",
    ),
)
def test_live_character_guides_use_canonical_three_to_nine_trait_range(
    relative_path: str,
) -> None:
    normalized = (PROJECT_ROOT / relative_path).read_text(encoding="utf-8").replace("–", "-")

    assert "3-9" in normalized
    assert "3-7" not in normalized


@pytest.mark.parametrize(
    "relative_path",
    ("prompts/rules/narrator_rights.md", "skills/rules/SKILL.md"),
)
def test_narrator_rights_guides_offer_only_one_use_future_roll_bonuses(
    relative_path: str,
) -> None:
    guidance = (PROJECT_ROOT / relative_path).read_text(encoding="utf-8").casefold()

    assert "one-use" in guidance
    assert "session-long" not in guidance
