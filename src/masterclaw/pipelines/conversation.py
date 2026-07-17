from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from masterclaw.pipelines.base import BoundedJsonPipeline, CompletionPort


class ConversationReply(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reply: str = Field(min_length=1, max_length=6000)


def create_scene_question_pipeline(
    completion: CompletionPort,
) -> BoundedJsonPipeline[ConversationReply]:
    return BoundedJsonPipeline(
        completion=completion,
        output_type=ConversationReply,
        static_system=(
            "Answer a player's question about the current scene using only public canonical facts "
            "and what their character can perceive. Never reveal the secret plot, hidden state, "
            "another scene, or internal pipeline terminology. If the answer is not established or "
            "perceivable, say so naturally rather than inventing it. If multiple participants are "
            "plausible addressees and the addressee is genuinely ambiguous, distinguish them by "
            "character name. Never insert a Discord user mention, and do not prefix a routine "
            "single-recipient reply with a name."
            " Reply in the language specified by session_brief.locale."
        ),
    )


def create_rules_question_pipeline(
    completion: CompletionPort,
) -> BoundedJsonPipeline[ConversationReply]:
    return BoundedJsonPipeline(
        completion=completion,
        output_type=ConversationReply,
        static_system=(
            "Answer the player's BlackBirdPie rules question accurately and concisely from the "
            "supplied rule fragments. Do not invent a rule or mutate game state. Explain the "
            "practical next step in ordinary language; slash commands are never required."
            " Reply in the language specified by session_brief.locale."
        ),
    )


def create_roleplay_reply_pipeline(
    completion: CompletionPort,
) -> BoundedJsonPipeline[ConversationReply]:
    return BoundedJsonPipeline(
        completion=completion,
        output_type=ConversationReply,
        static_system=(
            "Continue the current scene in response to a player's in-character speech or harmless "
            "roleplay. Preserve canonical facts and player agency. You may portray the environment "
            "and NPC reactions, but must not resolve an uncertain action, roll dice, decide "
            "another player character's actions, or create persistent mechanical changes. If "
            "multiple participants are plausible addressees and the addressee is genuinely "
            "ambiguous, distinguish them by character name. Never insert a Discord user mention, "
            "and do not prefix a routine single-recipient reply with a name. Treat secret_plot as "
            "GM-only causal context and never reveal it unless current public facts establish that "
            "the characters have actually discovered the relevant information."
            " Reply in session_brief.locale and follow the supplied narrative style, perspective, "
            "and detail settings when they are present."
        ),
    )
