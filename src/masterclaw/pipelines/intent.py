from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from masterclaw.pipelines.base import BoundedJsonPipeline, CompletionPort


class MessageIntent(StrEnum):
    COMMAND = "command"
    SCENE_QUESTION = "scene_question"
    RULES_QUESTION = "rules_question"
    PLAYER_NARRATION = "player_narration"
    ACTION_DECLARATION = "action_declaration"
    MIXED_NARRATION_ACTION = "mixed_narration_action"
    PENDING_RESPONSE = "pending_response"
    AMBIGUOUS = "ambiguous"


class IntentResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intent: MessageIntent
    confidence: float = Field(ge=0, le=1)
    evidence: str = Field(min_length=1, max_length=300)


def create_intent_pipeline(completion: CompletionPort) -> BoundedJsonPipeline[IntentResult]:
    return BoundedJsonPipeline(
        completion=completion,
        output_type=IntentResult,
        static_system=(
            "You classify one tabletop game message. You cannot change game state, "
            "invent facts, resolve actions, or answer the player. Use only the supplied "
            "mode and pending interaction."
        ),
    )
