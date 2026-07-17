from __future__ import annotations

import logging
from datetime import timedelta

from masterclaw.app.dispatch import (
    ClassifyWithLlm,
    DecisionSource,
    DispatchSnapshot,
    HandlerKind,
    PendingView,
    Reject,
    RespondFromState,
    RunHandler,
    StateResponseKind,
    decide,
    decision_for_scenario_command,
)
from masterclaw.app.i18n import tr
from masterclaw.app.scenarios import (
    INFERRED_COMMAND_MIN_CONFIDENCE,
    CommandId,
    CommandSafety,
    ScenarioId,
    normalize_phrase,
    resolve_scenario,
)
from masterclaw.app.world_settings import render_world_settings
from masterclaw.context.manifests import state_decision_manifest
from masterclaw.domain.models import (
    IncomingMessage,
    OperatingMode,
)
from masterclaw.domain.state import PendingKind
from masterclaw.pipelines.base import PipelineValidationError
from masterclaw.pipelines.state_decision import command_of

logger = logging.getLogger(__name__)


class DispatchExecution:
    async def _dispatch(self, message: IncomingMessage, channel) -> str:
        command = self._command(message.content)
        route = self._router.route(channel=channel)
        workspace = self._store.world_workspace(message.channel_id)
        resumed_roll = self._store.roll_for_confirmation_event(message.event_id)
        pending = None
        if channel.game_id:
            pending = self._store.open_pending(game_id=channel.game_id, player_id=message.author_id)
        if (
            pending is not None
            and pending.created_at is not None
            and message.created_at - pending.created_at >= timedelta(hours=24)
        ):
            if pending.kind is PendingKind.POOL_CONFIRMATION:
                self._store.expire_pending(
                    interaction_id=pending.interaction_id,
                    player_id=message.author_id,
                    expected_revision=pending.revision,
                )
                return tr(self._locale(channel.game_id), "pending_pool_expired")
            logger.info(
                "stale_pending_continues event_id=%s interaction_id=%s kind=%s",
                message.event_id,
                pending.interaction_id,
                pending.kind.value,
            )
        snapshot = DispatchSnapshot(
            mode=route.mode,
            mode_reason=route.reason,
            game_id=channel.game_id,
            workspace_stage=None if workspace is None else str(workspace["stage"]),
            pending=(
                None
                if pending is None
                else PendingView(kind=pending.kind.value, scene_id=pending.scene_id)
            ),
            resumed_roll=resumed_roll is not None,
            locale=self._locale(channel.game_id),
            content=message.content,
            author_id=message.author_id,
            command=command,
        )
        scenario = resolve_scenario(
            mode=snapshot.mode,
            workspace_stage=snapshot.workspace_stage,
            pending_kind=None if snapshot.pending is None else snapshot.pending.kind,
            resumed_roll=snapshot.resumed_roll,
        )
        decision = decide(snapshot)
        projections = {
            "mode": {"value": route.mode.value, "reason": route.reason},
            "scenario": {
                "id": scenario.id.value,
                "allowed_commands": sorted(command.value for command in scenario.llm_commands),
                "fallback": scenario.fallback.value,
            },
        }
        projections.update(
            self._scenario_context_projections(
                scenario,
                game_id=channel.game_id,
                channel_id=message.channel_id,
                player_id=message.author_id,
                workspace=workspace,
                pending=pending,
            )
        )
        decision_evidence = "deterministic rule"
        decision_command = None
        if isinstance(decision, ClassifyWithLlm):
            assembled = self._assemble_context(
                state_decision_manifest(
                    context_projections=scenario.context_projections,
                    recent_chat_messages=scenario.recent_chat_messages,
                ),
                projections,
                game_id=channel.game_id,
                channel_id=message.channel_id,
                player_id=message.author_id,
            )
            try:
                result = await self._state_router.pipeline_for(scenario).run(
                    task=f"Choose exactly one scenario command for:\n{message.content}",
                    context=assembled,
                )
                decision_command = command_of(result)
                argument = result.argument
                decision_evidence = result.evidence
                if result.confidence >= 0.9 and decision_command in {
                    CommandId.SHOW_CHARACTER_SHEET,
                    CommandId.SHOW_GAME_STATUS,
                    CommandId.SHOW_SCENE,
                    CommandId.SHOW_WORLD_CATALOG,
                    CommandId.SHOW_XP,
                    CommandId.ANSWER_PENDING,
                }:
                    logger.info(
                        "lexicon_candidate scenario=%s command=%s confidence=%.3f phrase=%r",
                        scenario.id.value,
                        decision_command.value,
                        result.confidence,
                        normalize_phrase(message.content),
                    )
                safety = scenario.command_safety(decision_command)
                if safety is CommandSafety.EXPLICIT_ONLY:
                    blocked_command = decision_command
                    decision_command = CommandId.CLARIFY
                    argument = None
                    decision_evidence = (
                        f"blocked inferred {blocked_command.value}: explicit confirmation required"
                    )
                elif (
                    safety is CommandSafety.INFERRED
                    and result.confidence < INFERRED_COMMAND_MIN_CONFIDENCE
                ):
                    blocked_command = decision_command
                    decision_command = CommandId.CLARIFY
                    argument = None
                    decision_evidence = (
                        f"blocked low-confidence {blocked_command.value}: "
                        f"{result.confidence:.3f} < {INFERRED_COMMAND_MIN_CONFIDENCE:.3f}"
                    )
            except PipelineValidationError as error:
                decision_command = scenario.fallback
                argument = None
                decision_evidence = f"scenario fallback after invalid state decision: {error}"
                logger.warning(
                    "state_decision_fallback event_id=%s scenario=%s command=%s error=%s",
                    message.event_id,
                    scenario.id.value,
                    decision_command.value,
                    error,
                )
            decision = decision_for_scenario_command(
                decision_command,
                source=DecisionSource.LLM,
                argument=argument,
            )
        routed_command = getattr(decision, "command", None)
        source = decision.source
        gate = "rejected" if isinstance(decision, Reject) else "allowed"
        logger.info(
            "scenario_decision event_id=%s channel_id=%s scenario=%s command=%s "
            "source=%s gate=%s evidence=%r",
            message.event_id,
            message.channel_id,
            scenario.id.value,
            None if routed_command is None else routed_command.value,
            source.value,
            gate,
            decision_evidence,
        )
        if workspace is not None and source is DecisionSource.WORKSPACE:
            logger.info(
                "world_workspace_transition event_id=%s channel_id=%s world_id=%s "
                "stage=%s action=%s source=%s",
                message.event_id,
                message.channel_id,
                workspace["world_id"],
                workspace["stage"],
                (
                    decision.handler.value
                    if isinstance(decision, RunHandler)
                    else decision.kind.value
                    if isinstance(decision, RespondFromState)
                    else "clarify"
                ),
                source.value,
            )
        # Activity is a consequence of the chosen PLAY route, never an input to it.
        if route.mode is OperatingMode.PLAY and channel.game_id is not None:
            self._store.record_activity(game_id=channel.game_id, occurred_at=message.created_at)

        if isinstance(decision, Reject):
            response = tr(snapshot.locale, decision.reason)
            if (
                decision.command is CommandId.CLARIFY
                or decision.reason == "conversation_clarification"
            ):
                response += "\n\n" + self._scenario_help(snapshot.locale, scenario.id, route.mode)
            return response
        if isinstance(decision, RespondFromState):
            if decision.kind is StateResponseKind.WORLD_CATALOG:
                return self._world_catalog()
            if decision.kind is StateResponseKind.WORLD_STATUS:
                if workspace is None:
                    return tr(self._locale(None), "world_settings_missing")
                return render_world_settings(
                    workspace["settings"],
                    workspace["sources"],
                    locale=self._locale(None),
                )
            if decision.kind is StateResponseKind.WORLD_SELECTION_CLARIFICATION:
                return (
                    tr(self._locale(None), "world_selection_clarify")
                    + "\n\n"
                    + self._world_catalog()
                )
            if decision.kind is StateResponseKind.HELP:
                return self._scenario_help(snapshot.locale, scenario.id, route.mode)
            if channel.game_id is None:
                return tr(self._locale(None), "game_unbound")
            if decision.kind is StateResponseKind.CHARACTER_STATUS:
                return self._character_status(game_id=channel.game_id, player_id=message.author_id)
            if decision.kind is StateResponseKind.GAME_STATUS:
                return self._game_status(channel.game_id)
            if decision.kind is StateResponseKind.XP_STATUS:
                return self._xp_status(channel.game_id)
            return self._scene_status(game_id=channel.game_id, player_id=message.author_id)

        handler = decision.handler
        if handler is HandlerKind.COMMAND:
            response = await self._handle_command(
                message=message,
                game_id=channel.game_id,
                command=command,
                routed_command=decision.command,
            )
            return response or tr(snapshot.locale, "conversation_clarification")
        if handler is HandlerKind.RESUMED_ROLL:
            return await self._render_roll_outcome(message=message, roll=resumed_roll)
        if handler is HandlerKind.WORKSPACE_REVISE:
            return await self._handle_world_revision(message=message, workspace=workspace)
        if handler is HandlerKind.WORKSPACE_GENERATE:
            return await self._handle_world_generation(message=message, workspace=workspace)
        if handler is HandlerKind.WORKSPACE_CONFIRM:
            return self._handle_world_confirmation(message=message, workspace=workspace)
        if handler is HandlerKind.WORKSPACE_EXIT:
            return self._handle_world_exit(message=message, workspace=workspace)
        if handler is HandlerKind.WORLD_CREATE:
            return await self._handle_natural_world_creation(message)
        if handler is HandlerKind.WORLD_SELECT:
            selected_world = (
                self._match_world_selection(decision.argument) if decision.argument else None
            )
            if selected_world is None:
                return (
                    tr(self._locale(None), "world_selection_clarify")
                    + "\n\n"
                    + self._world_catalog()
                )
            return self._prepare_selected_world(message=message, world=selected_world)
        if handler is HandlerKind.CHARACTER_SELECT:
            pregen = (
                self._match_pregenerated_character(
                    game_id=channel.game_id, content=decision.argument
                )
                if decision.argument
                else None
            )
            if pregen is None:
                return tr(snapshot.locale, "conversation_clarification")
            return self._select_pregenerated_character(
                message=message, game_id=channel.game_id, pregen=pregen
            )
        if handler is HandlerKind.CHARACTER_CREATE:
            return await self._handle_natural_character_creation(
                message=message, game_id=channel.game_id
            )
        if handler is HandlerKind.GAME_START:
            return self._handle_natural_game_start(
                game_id=channel.game_id, started_at=message.created_at
            )
        if handler is HandlerKind.GAME_CONFIGURE:
            return await self._handle_natural_game_configuration(
                message=message, game_id=channel.game_id
            )
        if handler is HandlerKind.HELP:
            return self._handle_natural_help(message=message, game_id=channel.game_id)
        if handler is HandlerKind.ADVANCEMENT:
            return await self._handle_natural_advancement(message=message, game_id=channel.game_id)
        if handler is HandlerKind.ACTION:
            return await self._handle_action_declaration(message=message, game_id=channel.game_id)
        if handler is HandlerKind.RULES_QUESTION:
            return await self._handle_rules_question(message=message, game_id=channel.game_id)
        if handler is HandlerKind.SCENE_QUESTION:
            return await self._handle_scene_question(message=message, game_id=channel.game_id)
        if handler is HandlerKind.COMPOUND:
            return await self._handle_compound_play(message=message, game_id=channel.game_id)
        if handler is HandlerKind.ROLEPLAY:
            return await self._handle_free_roleplay(message=message, game_id=channel.game_id)
        if handler is HandlerKind.PENDING_CANCEL:
            return self._cancel_pending_interaction(message=message, pending=pending)
        if handler is HandlerKind.PENDING:
            if pending.kind.value == "player_narration":
                return await self._handle_player_narration(message=message, pending=pending)
            return await self._handle_pending_response(message=message, pending=pending)
        raise AssertionError(f"unhandled dispatch decision: {decision!r}")

    @staticmethod
    def _scenario_help(locale: str, scenario_id: ScenarioId, mode: OperatingMode) -> str:
        if scenario_id in {
            ScenarioId.PLAY_PENDING_POOL,
            ScenarioId.PLAY_PENDING_NARRATION,
            ScenarioId.PLAY_PENDING_OTHER,
        }:
            return tr(locale, f"scenario_help_{scenario_id.value}")
        return tr(locale, f"scenario_help_{mode.value}")

    @staticmethod
    def _command(content: str) -> str | None:
        stripped = content.strip()
        return stripped.split(maxsplit=1)[0] if stripped.startswith("/") else None
