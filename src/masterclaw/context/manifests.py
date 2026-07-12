from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from masterclaw.config import ModelRole


class PipelineName(StrEnum):
    INTENT_CLASSIFICATION = "intent_classification"
    ACTION_INTERPRETATION = "action_interpretation"
    OUTCOME_NARRATION = "outcome_narration"
    WORLD_SECTION = "world_section"
    ADVANCEMENT_SAFETY = "advancement_safety"
    PLAYER_NARRATION_REVIEW = "player_narration_review"
    CHARACTER_CREATION = "character_creation"
    CONSEQUENCE_PLANNING = "consequence_planning"


@dataclass(frozen=True, slots=True)
class ContextManifest:
    pipeline: PipelineName
    model_role: ModelRole
    rule_fragments: tuple[str, ...]
    state_projections: tuple[str, ...]
    recent_domain_events: int
    recent_chat_messages: int
    input_token_budget: int
    output_token_budget: int


MANIFESTS: dict[PipelineName, ContextManifest] = {
    PipelineName.INTENT_CLASSIFICATION: ContextManifest(
        pipeline=PipelineName.INTENT_CLASSIFICATION,
        model_role=ModelRole.STATE,
        rule_fragments=(),
        state_projections=("mode", "pending_interaction"),
        recent_domain_events=2,
        recent_chat_messages=2,
        input_token_budget=2500,
        output_token_budget=300,
    ),
    PipelineName.ACTION_INTERPRETATION: ContextManifest(
        pipeline=PipelineName.ACTION_INTERPRETATION,
        model_role=ModelRole.REASONING,
        rule_fragments=("declaration_validation", "difficulty", "equipment"),
        state_projections=("session_brief", "actor_character", "current_scene"),
        recent_domain_events=6,
        recent_chat_messages=2,
        input_token_budget=12000,
        output_token_budget=1400,
    ),
    PipelineName.OUTCOME_NARRATION: ContextManifest(
        pipeline=PipelineName.OUTCOME_NARRATION,
        model_role=ModelRole.NARRATIVE,
        rule_fragments=("narrator_rights", "narrative_style"),
        state_projections=("session_brief", "current_scene", "roll_result"),
        recent_domain_events=4,
        recent_chat_messages=1,
        input_token_budget=10000,
        output_token_budget=1800,
    ),
    PipelineName.WORLD_SECTION: ContextManifest(
        pipeline=PipelineName.WORLD_SECTION,
        model_role=ModelRole.REASONING,
        rule_fragments=("world_generation",),
        state_projections=("world_outline", "world_constraints", "target_section"),
        recent_domain_events=4,
        recent_chat_messages=2,
        input_token_budget=16000,
        output_token_budget=2400,
    ),
    PipelineName.ADVANCEMENT_SAFETY: ContextManifest(
        pipeline=PipelineName.ADVANCEMENT_SAFETY,
        model_role=ModelRole.STATE,
        rule_fragments=("advancement",),
        state_projections=("current_scene", "actor_character", "advancement_request"),
        recent_domain_events=4,
        recent_chat_messages=1,
        input_token_budget=7000,
        output_token_budget=500,
    ),
    PipelineName.PLAYER_NARRATION_REVIEW: ContextManifest(
        pipeline=PipelineName.PLAYER_NARRATION_REVIEW,
        model_role=ModelRole.STATE,
        rule_fragments=("narrator_rights",),
        state_projections=("current_scene", "roll_result", "submitted_narration"),
        recent_domain_events=3,
        recent_chat_messages=1,
        input_token_budget=7000,
        output_token_budget=500,
    ),
    PipelineName.CHARACTER_CREATION: ContextManifest(
        pipeline=PipelineName.CHARACTER_CREATION,
        model_role=ModelRole.REASONING,
        rule_fragments=("character_creation",),
        state_projections=("public_world", "player_brief"),
        recent_domain_events=0,
        recent_chat_messages=1,
        input_token_budget=10000,
        output_token_budget=3000,
    ),
    PipelineName.CONSEQUENCE_PLANNING: ContextManifest(
        pipeline=PipelineName.CONSEQUENCE_PLANNING,
        model_role=ModelRole.REASONING,
        rule_fragments=("narrator_rights",),
        state_projections=("current_scene", "outcome_source", "narrator_rights"),
        recent_domain_events=4,
        recent_chat_messages=1,
        input_token_budget=9000,
        output_token_budget=1000,
    ),
}


def manifest_for(pipeline: PipelineName) -> ContextManifest:
    return MANIFESTS[pipeline]
