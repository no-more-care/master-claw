from __future__ import annotations

import logging
from collections.abc import Callable

from masterclaw.app.action_service import ActionService
from masterclaw.app.advancement_coordinator import AdvancementCoordinator
from masterclaw.app.game_service import GameService
from masterclaw.app.handlers.commands import CommandHandlers
from masterclaw.app.handlers.compound import CompoundPlayHandlers
from masterclaw.app.handlers.dispatching import DispatchExecution
from masterclaw.app.handlers.information import InformationHandlers
from masterclaw.app.handlers.play import PlayHandlers
from masterclaw.app.handlers.preparation import PreparationHandlers
from masterclaw.app.handlers.support import HandlerSupport
from masterclaw.app.handlers.world_management import WorldManagementHandlers
from masterclaw.app.i18n import tr
from masterclaw.app.progression_service import ProgressionService
from masterclaw.app.status_panels import render_status_panel
from masterclaw.app.worldgen_service import WorldGenerationService
from masterclaw.context.assembler import ContextAssembler
from masterclaw.domain.models import HandlerResponse, IncomingMessage
from masterclaw.domain.routing import ModeRouter
from masterclaw.pipelines.action import ActionInterpretation
from masterclaw.pipelines.base import BoundedJsonPipeline
from masterclaw.pipelines.character_creation import CharacterDraft
from masterclaw.pipelines.compound_play import CompoundPlayPlan
from masterclaw.pipelines.consequence import SceneConsequencePlan
from masterclaw.pipelines.conversation import ConversationReply
from masterclaw.pipelines.conversation_actions import (
    AdvancementRequest,
    GameConfigurationRequest,
    RollConfirmationRequest,
)
from masterclaw.pipelines.narrative import NarrativeResult, ReviewedNarrativePipeline
from masterclaw.pipelines.player_narration import PlayerNarrationReview
from masterclaw.pipelines.reserve_recovery import ReserveRecoveryDecision
from masterclaw.pipelines.state_decision import StateDecisionRouter
from masterclaw.pipelines.world_intake import WorldCreationBrief
from masterclaw.storage.sqlite import SQLiteStore
from masterclaw.telemetry import (
    bind_game,
    bind_trace,
    reset_game,
    reset_trace,
    stage_span,
)

logger = logging.getLogger(__name__)

MAX_MESSAGE_CHARACTERS = 12_000


class MessageApplication(
    DispatchExecution,
    WorldManagementHandlers,
    PreparationHandlers,
    InformationHandlers,
    CompoundPlayHandlers,
    PlayHandlers,
    CommandHandlers,
    HandlerSupport,
):
    """Initial message boundary: deterministic mode, pending lookup, bounded intent."""

    def __init__(
        self,
        *,
        store: SQLiteStore,
        context: ContextAssembler,
        state_router: StateDecisionRouter,
        action_pipeline: BoundedJsonPipeline[ActionInterpretation] | None = None,
        narrative_pipeline: (
            BoundedJsonPipeline[NarrativeResult] | ReviewedNarrativePipeline | None
        ) = None,
        advancement: AdvancementCoordinator | None = None,
        player_narration_pipeline: BoundedJsonPipeline[PlayerNarrationReview] | None = None,
        die: Callable[[], int] | None = None,
        worldgen: WorldGenerationService | None = None,
        character_pipeline: BoundedJsonPipeline[CharacterDraft] | None = None,
        consequence_pipeline: BoundedJsonPipeline[SceneConsequencePlan] | None = None,
        reserve_recovery_pipeline: BoundedJsonPipeline[ReserveRecoveryDecision] | None = None,
        world_intake_pipeline: BoundedJsonPipeline[WorldCreationBrief] | None = None,
        scene_question_pipeline: BoundedJsonPipeline[ConversationReply] | None = None,
        rules_question_pipeline: BoundedJsonPipeline[ConversationReply] | None = None,
        roleplay_reply_pipeline: BoundedJsonPipeline[ConversationReply] | None = None,
        compound_play_pipeline: BoundedJsonPipeline[CompoundPlayPlan] | None = None,
        advancement_intake_pipeline: BoundedJsonPipeline[AdvancementRequest] | None = None,
        game_configuration_pipeline: BoundedJsonPipeline[GameConfigurationRequest] | None = None,
        roll_confirmation_pipeline: BoundedJsonPipeline[RollConfirmationRequest] | None = None,
    ) -> None:
        self._store = store
        self._context = context
        self._state_router = state_router
        self._router = ModeRouter()
        self._progression = ProgressionService(store)
        self._actions = ActionService(store)
        self._action_pipeline = action_pipeline
        self._narrative_pipeline = narrative_pipeline
        self._advancement = advancement
        self._games = GameService(store)
        self._player_narration_pipeline = player_narration_pipeline
        self._die = die
        self._worldgen = worldgen
        self._character_pipeline = character_pipeline
        self._consequence_pipeline = consequence_pipeline
        self._reserve_recovery_pipeline = reserve_recovery_pipeline
        self._world_intake_pipeline = world_intake_pipeline
        self._scene_question_pipeline = scene_question_pipeline
        self._rules_question_pipeline = rules_question_pipeline
        self._roleplay_reply_pipeline = roleplay_reply_pipeline
        self._compound_play_pipeline = compound_play_pipeline
        self._advancement_intake_pipeline = advancement_intake_pipeline
        self._game_configuration_pipeline = game_configuration_pipeline
        self._roll_confirmation_pipeline = roll_confirmation_pipeline

    async def __call__(self, message: IncomingMessage) -> str:
        locale_token = self._bind_message_locale(message.content)
        trace_token = bind_trace(
            self._store,
            trace_id=f"message:{message.event_id}",
            channel_id=message.channel_id,
            event_id=message.event_id,
        )
        game_token = None
        try:
            with stage_span("message.total", component="application", operation="handle_message"):
                with stage_span("db.initial_state", component="sqlite", operation="channel_state"):
                    live_channel = self._store.channel_state(message.channel_id)
                    channel = self._channel_for_message(message)
                game_token = bind_game(channel.game_id)
                with stage_span(
                    "application.dispatch", component="message_handler", operation="dispatch"
                ):
                    if len(message.content) > MAX_MESSAGE_CHARACTERS:
                        response = tr(self._locale(channel.game_id), "message_too_long")
                    else:
                        response = await self._dispatch(message, channel)
                    if (
                        message.has_routing_snapshot
                        and channel.game_id is not None
                        and self._store.cancel_source_pending_if_origin_unbound(
                            game_id=channel.game_id,
                            player_id=message.author_id,
                            origin_channel_id=message.channel_id,
                            source_event_id=message.event_id,
                        )
                    ):
                        notice = tr(
                            self._locale(channel.game_id),
                            "stale_route_pending_cancelled",
                        )
                        if isinstance(response, HandlerResponse):
                            response = HandlerResponse(
                                f"{response.text}\n\n{notice}".strip(),
                                response.deliveries,
                                response.completion_game_id,
                                response.render_live_status,
                            )
                        else:
                            response = f"{response}\n\n{notice}".strip()
                with stage_span(
                    "response.status_panel",
                    component="response_format",
                    operation="render_status_panel",
                ):
                    # Preserve an ingress snapshot only when the live binding had already diverged
                    # before this turn ran. If both matched at dispatch time, render the state
                    # produced by this very message (for example world selection or `/game new`).
                    panel_channel_state = (
                        channel
                        if (
                            message.has_routing_snapshot
                            and channel != live_channel
                            and not (
                                isinstance(response, HandlerResponse)
                                and response.render_live_status
                            )
                        )
                        else None
                    )
                    panel = render_status_panel(
                        self._store,
                        channel_id=message.channel_id,
                        player_id=message.author_id,
                        locale=self._locale(channel.game_id),
                        channel_state=panel_channel_state,
                    )
                if isinstance(response, HandlerResponse):
                    return HandlerResponse(
                        f"{panel}\n\n{response.text}",
                        response.deliveries,
                        response.completion_game_id,
                        response.render_live_status,
                    )
                return f"{panel}\n\n{response}"
        finally:
            if game_token is not None:
                reset_game(game_token)
            reset_trace(trace_token)
            self._reset_message_locale(locale_token)
