from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from masterclaw.pipelines.base import BoundedJsonPipeline, CompletionPort


class PlayerNarrationReview(BaseModel):
    model_config = ConfigDict(extra="forbid")

    accepted: bool
    reason: str = Field(min_length=1, max_length=500)
    scale_back_request: str | None = Field(default=None, max_length=500)


def create_player_narration_pipeline(
    completion: CompletionPort,
) -> BoundedJsonPipeline[PlayerNarrationReview]:
    return BoundedJsonPipeline(
        completion=completion,
        output_type=PlayerNarrationReview,
        static_system=(
            "Review player-authored outcome narration against the supplied immutable roll, "
            "narrator-rights category, scene facts, and limits. Accept only narration about "
            "the allowed outcome scale. Reject control of other player characters, unrelated "
            "world changes, hidden knowledge, or contradictions. Do not rewrite or publish it."
        ),
    )
