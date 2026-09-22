from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum

from masterclaw.config import ModelRole


class PipelineName(StrEnum):
    INTENT_CLASSIFICATION = "intent_classification"
    ACTION_INTERPRETATION = "action_interpretation"
    OUTCOME_NARRATION = "outcome_narration"
    OUTCOME_NARRATION_REVIEW = "outcome_narration_review"
    WORLD_CREATIVE = "world_creative"
    WORLD_INTAKE = "world_intake"
    WORLD_STRUCTURING = "world_structuring"
    WORLD_SECTION = "world_section"
    ADVANCEMENT_SAFETY = "advancement_safety"
    PLAYER_NARRATION_REVIEW = "player_narration_review"
    CHARACTER_CREATION = "character_creation"
    CONSEQUENCE_PLANNING = "consequence_planning"
    RESERVE_RECOVERY = "reserve_recovery"
    SCENE_QUESTION = "scene_question"
    RULES_QUESTION = "rules_question"
    ROLEPLAY_REPLY = "roleplay_reply"
    COMPOUND_PLAY = "compound_play"
    ADVANCEMENT_INTAKE = "advancement_intake"
    GAME_CONFIGURATION = "game_configuration"
    ROLL_CONFIRMATION = "roll_confirmation"


class FallbackAction(StrEnum):
    SYSTEM_FAILURE = "system_failure"
    CLARIFY_ACTION = "clarify_action"
    RETRY_CHARACTER_BRIEF = "character_brief_retry"
    RETRY_WORLD_BRIEF = "world_brief_retry"
    SHOW_ADVANCEMENT_USAGE = "advance_usage"
    SHOW_RESERVE_USAGE = "reserve_usage"
    RETRY_PLAYER_NARRATION = "player_narration_retry"
    RETRY_GAME_CONFIGURATION = "game_configuration_retry"
    CONVERSATION_CLARIFICATION = "conversation_clarification"
    NARRATIVE_FALLBACK = "narrative_fallback"
    CONSEQUENCE_FALLBACK = "consequence_unavailable"
    RESERVE_RECOVERY_SKIP = "reserve_recovery_skip"


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
    on_invalid: FallbackAction = FallbackAction.SYSTEM_FAILURE


MANIFESTS: dict[PipelineName, ContextManifest] = {
    PipelineName.INTENT_CLASSIFICATION: ContextManifest(
        pipeline=PipelineName.INTENT_CLASSIFICATION,
        model_role=ModelRole.STATE,
        rule_fragments=(),
        state_projections=("mode", "scenario", "pending_interaction", "world_workspace"),
        recent_domain_events=2,
        recent_chat_messages=6,
        input_token_budget=2500,
        output_token_budget=300,
    ),
    PipelineName.ACTION_INTERPRETATION: ContextManifest(
        pipeline=PipelineName.ACTION_INTERPRETATION,
        model_role=ModelRole.REASONING,
        rule_fragments=(
            "core_mechanics",
            "declaration_validation",
            "difficulty",
            "equipment",
        ),
        state_projections=(
            "session_brief",
            "actor_character",
            "current_scene",
            "public_world_context",
        ),
        recent_domain_events=6,
        recent_chat_messages=2,
        input_token_budget=12000,
        output_token_budget=1400,
    ),
    PipelineName.OUTCOME_NARRATION: ContextManifest(
        pipeline=PipelineName.OUTCOME_NARRATION,
        model_role=ModelRole.NARRATIVE,
        rule_fragments=("narrator_rights", "narrative_style"),
        state_projections=(
            "session_brief",
            "actor_character",
            "current_scene",
            "public_world_context",
            "roll_result",
        ),
        recent_domain_events=4,
        recent_chat_messages=1,
        input_token_budget=10000,
        output_token_budget=1800,
    ),
    PipelineName.OUTCOME_NARRATION_REVIEW: ContextManifest(
        pipeline=PipelineName.OUTCOME_NARRATION_REVIEW,
        model_role=ModelRole.REASONING,
        rule_fragments=("narrator_rights", "narrative_style"),
        state_projections=("immutable_roll_result", "source_context", "raw_narrative"),
        recent_domain_events=0,
        recent_chat_messages=0,
        input_token_budget=12000,
        output_token_budget=2200,
    ),
    PipelineName.WORLD_CREATIVE: ContextManifest(
        pipeline=PipelineName.WORLD_CREATIVE,
        model_role=ModelRole.WORLDGEN,
        rule_fragments=("world_generation",),
        state_projections=("world_constraints",),
        recent_domain_events=0,
        recent_chat_messages=0,
        input_token_budget=12000,
        output_token_budget=6000,
    ),
    PipelineName.WORLD_INTAKE: ContextManifest(
        pipeline=PipelineName.WORLD_INTAKE,
        model_role=ModelRole.REASONING,
        rule_fragments=("world_generation",),
        state_projections=("player_request",),
        recent_domain_events=0,
        recent_chat_messages=1,
        input_token_budget=10000,
        output_token_budget=1800,
    ),
    PipelineName.WORLD_STRUCTURING: ContextManifest(
        pipeline=PipelineName.WORLD_STRUCTURING,
        model_role=ModelRole.WORLDGEN,
        rule_fragments=("world_generation",),
        state_projections=("world_constraints", "creative_draft"),
        recent_domain_events=0,
        recent_chat_messages=0,
        input_token_budget=20000,
        output_token_budget=6000,
    ),
    PipelineName.WORLD_SECTION: ContextManifest(
        pipeline=PipelineName.WORLD_SECTION,
        model_role=ModelRole.WORLDGEN,
        rule_fragments=("world_generation",),
        state_projections=(
            "world_outline",
            "world_sections",
            "world_constraints",
            "target_section",
        ),
        recent_domain_events=0,
        recent_chat_messages=0,
        input_token_budget=16000,
        output_token_budget=4000,
    ),
    PipelineName.ADVANCEMENT_SAFETY: ContextManifest(
        pipeline=PipelineName.ADVANCEMENT_SAFETY,
        model_role=ModelRole.STATE,
        rule_fragments=("advancement",),
        state_projections=(
            "session_brief",
            "current_scene",
            "actor_character",
            "advancement_request",
        ),
        recent_domain_events=4,
        recent_chat_messages=1,
        input_token_budget=7000,
        output_token_budget=500,
    ),
    PipelineName.PLAYER_NARRATION_REVIEW: ContextManifest(
        pipeline=PipelineName.PLAYER_NARRATION_REVIEW,
        model_role=ModelRole.STATE,
        rule_fragments=("narrator_rights",),
        state_projections=(
            "session_brief",
            "actor_character",
            "current_scene",
            "public_world_context",
            "roll_result",
            "submitted_narration",
        ),
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
        state_projections=(
            "current_scene",
            "gm_world_context",
            "actor_character",
            "allowed_scenes",
            "outcome_source",
            "narrator_rights",
            "narrator_rights_policy",
        ),
        recent_domain_events=4,
        recent_chat_messages=1,
        input_token_budget=9000,
        output_token_budget=1000,
    ),
    PipelineName.COMPOUND_PLAY: ContextManifest(
        pipeline=PipelineName.COMPOUND_PLAY,
        model_role=ModelRole.REASONING,
        rule_fragments=("declaration_validation",),
        state_projections=(
            "session_brief",
            "current_scene",
            "actor_character",
            "player_request",
        ),
        recent_domain_events=2,
        recent_chat_messages=2,
        input_token_budget=7000,
        output_token_budget=900,
    ),
    PipelineName.RESERVE_RECOVERY: ContextManifest(
        pipeline=PipelineName.RESERVE_RECOVERY,
        model_role=ModelRole.REASONING,
        rule_fragments=(),
        state_projections=(
            "current_scene",
            "outcome_source",
            "reserve_policy",
            "actor_character",
            "characters",
        ),
        recent_domain_events=4,
        recent_chat_messages=2,
        input_token_budget=9000,
        output_token_budget=700,
    ),
    PipelineName.SCENE_QUESTION: ContextManifest(
        pipeline=PipelineName.SCENE_QUESTION,
        model_role=ModelRole.REASONING,
        rule_fragments=(),
        state_projections=(
            "session_brief",
            "actor_character",
            "current_scene",
            "public_world_context",
            "player_question",
        ),
        recent_domain_events=4,
        recent_chat_messages=2,
        input_token_budget=9000,
        output_token_budget=1400,
    ),
    PipelineName.RULES_QUESTION: ContextManifest(
        pipeline=PipelineName.RULES_QUESTION,
        model_role=ModelRole.REASONING,
        rule_fragments=(
            "core_mechanics",
            "declaration_validation",
            "difficulty",
            "equipment",
            "narrator_rights",
            "advancement",
            "character_creation",
        ),
        state_projections=("session_brief", "player_question"),
        recent_domain_events=0,
        recent_chat_messages=1,
        input_token_budget=14000,
        output_token_budget=1400,
    ),
    PipelineName.ROLEPLAY_REPLY: ContextManifest(
        pipeline=PipelineName.ROLEPLAY_REPLY,
        model_role=ModelRole.NARRATIVE,
        rule_fragments=("narrative_style",),
        state_projections=(
            "session_brief",
            "actor_character",
            "current_scene",
            "public_world_context",
            "player_narration",
        ),
        recent_domain_events=4,
        recent_chat_messages=3,
        input_token_budget=10000,
        output_token_budget=1800,
    ),
    PipelineName.ADVANCEMENT_INTAKE: ContextManifest(
        pipeline=PipelineName.ADVANCEMENT_INTAKE,
        model_role=ModelRole.REASONING,
        rule_fragments=("advancement",),
        state_projections=("actor_character", "player_request"),
        recent_domain_events=2,
        recent_chat_messages=1,
        input_token_budget=8000,
        output_token_budget=800,
    ),
    PipelineName.GAME_CONFIGURATION: ContextManifest(
        pipeline=PipelineName.GAME_CONFIGURATION,
        model_role=ModelRole.STATE,
        rule_fragments=(),
        state_projections=("game", "player_request"),
        recent_domain_events=0,
        recent_chat_messages=1,
        input_token_budget=4000,
        output_token_budget=600,
    ),
    PipelineName.ROLL_CONFIRMATION: ContextManifest(
        pipeline=PipelineName.ROLL_CONFIRMATION,
        model_role=ModelRole.STATE,
        rule_fragments=(),
        state_projections=("pending_roll", "player_response"),
        recent_domain_events=0,
        recent_chat_messages=1,
        input_token_budget=3000,
        output_token_budget=300,
    ),
}

_INVALID_FALLBACKS = {
    PipelineName.ACTION_INTERPRETATION: FallbackAction.CLARIFY_ACTION,
    PipelineName.CHARACTER_CREATION: FallbackAction.RETRY_CHARACTER_BRIEF,
    PipelineName.WORLD_INTAKE: FallbackAction.RETRY_WORLD_BRIEF,
    PipelineName.ADVANCEMENT_INTAKE: FallbackAction.SHOW_ADVANCEMENT_USAGE,
    PipelineName.ADVANCEMENT_SAFETY: FallbackAction.SHOW_ADVANCEMENT_USAGE,
    PipelineName.ROLL_CONFIRMATION: FallbackAction.SHOW_RESERVE_USAGE,
    PipelineName.PLAYER_NARRATION_REVIEW: FallbackAction.RETRY_PLAYER_NARRATION,
    PipelineName.GAME_CONFIGURATION: FallbackAction.RETRY_GAME_CONFIGURATION,
    PipelineName.SCENE_QUESTION: FallbackAction.CONVERSATION_CLARIFICATION,
    PipelineName.RULES_QUESTION: FallbackAction.CONVERSATION_CLARIFICATION,
    PipelineName.ROLEPLAY_REPLY: FallbackAction.CONVERSATION_CLARIFICATION,
    PipelineName.COMPOUND_PLAY: FallbackAction.CONVERSATION_CLARIFICATION,
    PipelineName.OUTCOME_NARRATION: FallbackAction.NARRATIVE_FALLBACK,
    PipelineName.CONSEQUENCE_PLANNING: FallbackAction.CONSEQUENCE_FALLBACK,
    PipelineName.RESERVE_RECOVERY: FallbackAction.RESERVE_RECOVERY_SKIP,
}
for _pipeline, _fallback in _INVALID_FALLBACKS.items():
    MANIFESTS[_pipeline] = replace(MANIFESTS[_pipeline], on_invalid=_fallback)


def manifest_for(pipeline: PipelineName) -> ContextManifest:
    return MANIFESTS[pipeline]


def state_decision_manifest(
    *, context_projections: tuple[str, ...], recent_chat_messages: int
) -> ContextManifest:
    """Specialize the state-router context contract for one resolved scenario."""
    base = manifest_for(PipelineName.INTENT_CLASSIFICATION)
    projections = tuple(dict.fromkeys(("mode", "scenario", *context_projections)))
    return replace(
        base,
        state_projections=projections,
        recent_chat_messages=recent_chat_messages,
    )
