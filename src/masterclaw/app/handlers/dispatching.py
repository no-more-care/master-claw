from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import replace
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
    is_assertive_mutation,
    normalize_phrase,
    resolve_scenario,
)
from masterclaw.app.world_settings import render_world_settings
from masterclaw.context.manifests import state_decision_manifest
from masterclaw.domain.models import (
    HandlerResponse,
    IncomingMessage,
    OperatingMode,
)
from masterclaw.domain.state import PendingKind
from masterclaw.pipelines.base import PipelineValidationError

logger = logging.getLogger(__name__)


class DispatchExecution:
    def _replay_event_operation(
        self,
        *,
        message: IncomingMessage,
        operation: Mapping[str, object],
    ) -> str | HandlerResponse:
        """Render the original committed result without consulting mutable live state."""

        if operation.get("channel_id") != message.channel_id:
            raise RuntimeError("event operation replay channel mismatch")
        operation_type = str(operation.get("operation_type") or "")
        result = operation.get("result")
        if not isinstance(result, Mapping):
            raise RuntimeError("event operation replay result is invalid")
        game_id = operation.get("game_id")
        fallback_locale = self._locale(None if game_id is None else str(game_id))
        locale = str(result.get("locale") or fallback_locale)
        detail: str
        if operation_type == "configure_game:progression":
            detail = tr(
                locale,
                "progression_changed",
                progression=tr(
                    locale,
                    "enabled" if bool(result["progression_enabled"]) else "disabled",
                ),
            )
        elif operation_type == "configure_game:narrator_rights":
            detail = tr(
                locale,
                "rights_changed",
                rights=str(result["narrator_rights_level"]),
            )
        elif operation_type == "configure_game:reserve_recovery":
            detail = tr(
                locale,
                "reserve_recovery_changed",
                mode=str(result["reserve_recovery_mode"]),
            )
        elif operation_type == "configure_game:narrative_channel":
            detail = tr(
                locale,
                "narrative_channel_changed",
                channel_id=str(result["narrative_channel_id"]),
            )
        elif operation_type == "start_game":
            detail = tr(locale, "game_started", game_id=str(result["game_id"]))
        elif operation_type == "pause_game":
            detail = tr(locale, "game_paused")
        elif operation_type == "resume_game":
            detail = tr(locale, "game_resumed")
        elif operation_type == "finish_game":
            detail = tr(locale, "game_finished")
        elif operation_type == "unbind_game":
            detail = tr(locale, "game_unbound_complete")
        elif operation_type == "new_session":
            detail = tr(locale, "new_session_applied_historically")
        elif operation_type == "offer_help":
            detail = tr(
                locale,
                "help_added",
                player_id=str(result["target_player_id"]),
                interaction_id=str(result["interaction_id"]),
            )
        else:
            raise RuntimeError(f"unsupported event operation replay: {operation_type}")
        return HandlerResponse(
            tr(locale, "event_operation_replayed", result=detail),
            render_live_status=True,
        )

    async def _dispatch(self, message: IncomingMessage, channel) -> str:
        event_operation = self._store.event_operation(message.event_id)
        if event_operation is not None:
            return self._replay_event_operation(
                message=message,
                operation=event_operation,
            )
        command = self._command(message.content)
        route = self._router.route(channel=channel)
        workspace = self._store.world_workspace(message.channel_id)
        world_operation = self._store.world_operation_for_event(message.event_id)
        if world_operation is not None:
            return self._replay_world_operation(
                message=message,
                operation=world_operation,
            )
        replayed_world_id = f"world_{message.event_id}"
        replayed_world = self._store.world_state(replayed_world_id)
        replayed_project = self._store.world_project(replayed_world_id)
        if (
            replayed_world is not None
            and replayed_project is not None
            and replayed_project["channel_id"] == message.channel_id
        ):
            return tr(self._locale(None), "world_inputs_collected")
        replayed_game = self._store.game_state(f"game_{message.event_id}")
        if replayed_game is not None:
            live_binding = self._store.channel_state(message.channel_id)
            if live_binding.game_id not in {None, replayed_game.game_id}:
                return tr(self._locale(None), "channel_context_changed_retry")
            selected_world = self._store.world_state(replayed_game.world_id)
            if selected_world is None:
                raise RuntimeError("selected game references a missing world")
            return self._prepare_selected_world(
                message=message,
                world=selected_world,
            )
        replayed_character_id = f"character_{message.event_id}"
        replayed_character_owner = self._store.character_owner(replayed_character_id)
        if replayed_character_owner is not None:
            if (
                channel.game_id != replayed_character_owner["game_id"]
                or message.author_id != replayed_character_owner["player_id"]
            ):
                return tr(self._locale(channel.game_id), "channel_context_changed_retry")
            character = self._store.character_for_player(
                game_id=replayed_character_owner["game_id"],
                player_id=message.author_id,
            )
            if character is None or character.character_id != replayed_character_id:
                raise RuntimeError("committed character replay lost its owner")
            game = self._store.game_state(replayed_character_owner["game_id"])
            if (
                game is not None
                and game.lifecycle.value != "preparing"
                and self._store.scene_projection(
                    game_id=game.game_id,
                    player_id=message.author_id,
                )
                is None
                and self._place_joining_character(
                    game_id=game.game_id,
                    player_id=message.author_id,
                )
            ):
                return tr(
                    self._locale(game.game_id),
                    "character_created_joined",
                    character_id=character.character_id,
                    name=character.sheet.name,
                )
            return tr(
                self._locale(replayed_character_owner["game_id"]),
                "character_created_and_placed",
                character_id=character.character_id,
                name=character.sheet.name,
            )
        resumed_roll = self._store.roll_for_confirmation_event(message.event_id)
        replayed_scene_id = f"scene_{message.event_id}"
        if (
            channel.game_id is not None
            and self._store.scene_by_id(
                game_id=channel.game_id,
                scene_id=replayed_scene_id,
            )
            is not None
        ):
            # Natural scene configuration commits under a deterministic event-derived id.
            # A retried inbox event must reconstruct that success before either classifier runs;
            # otherwise a stochastic title could turn an already-committed success into failure.
            return tr(
                self._locale(channel.game_id),
                "scene_created",
                scene_id=replayed_scene_id,
            )
        pending = None
        if channel.game_id:
            pending = self._store.open_pending(
                game_id=channel.game_id,
                player_id=message.author_id,
            )
            if (
                pending is not None
                and pending.origin_channel_id is not None
                and pending.origin_channel_id != message.channel_id
            ):
                return tr(
                    self._locale(channel.game_id),
                    "pending_other_channel",
                    channel_id=pending.origin_channel_id,
                )
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
                    closed_by_event_id=message.event_id,
                )
                return tr(self._locale(channel.game_id), "pending_pool_expired")
            self._store.expire_pending(
                interaction_id=pending.interaction_id,
                player_id=message.author_id,
                expected_revision=pending.revision,
                closed_by_event_id=message.event_id,
            )
            logger.info(
                "stale_pending_expired event_id=%s interaction_id=%s kind=%s",
                message.event_id,
                pending.interaction_id,
                pending.kind.value,
            )
            return await self._dispatch(
                replace(
                    message,
                    event_id=f"{message.event_id}:after-expire",
                ),
                channel,
            )
        if pending is not None and message.event_id in {
            pending.payload.get("prompt_source_event_id"),
            pending.payload.get("root_source_event_id"),
        }:
            # The handler installed or revised this pending step, but durable inbox/outbox
            # completion was interrupted. Re-render the committed prompt instead of treating
            # the source message as its own answer.
            if pending.kind is PendingKind.POOL_CONFIRMATION:
                return await self._handle_pending_response(message=message, pending=pending)
            return pending.prompt
        closed_pending = (
            None
            if channel.game_id is None
            else self._store.closed_pending_for_event(
                game_id=channel.game_id,
                player_id=message.author_id,
                event_id=message.event_id,
            )
        )
        if closed_pending is not None:
            return await self._replay_closed_pending(
                message=message,
                pending=closed_pending,
            )
        if pending is not None and message.reply_to_event_id is not None:
            expected_prompt_source = pending.payload.get("prompt_source_event_id")
            if expected_prompt_source is not None:
                expected_sources = {
                    str(expected_prompt_source),
                    *(
                        (str(pending.payload["root_source_event_id"]),)
                        if pending.payload.get("root_source_event_id") is not None
                        else ()
                    ),
                }
                replied_delivery = self._store.outbox_delivery_for_discord_message(
                    channel_id=message.channel_id,
                    discord_message_id=message.reply_to_event_id,
                )
                if (
                    replied_delivery is None
                    or replied_delivery["kind"] != "response"
                    or replied_delivery["source_event_id"] not in expected_sources
                ):
                    return tr(self._locale(channel.game_id), "pending_reply_mismatch")
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
        if isinstance(decision, ClassifyWithLlm) and is_assertive_mutation(message.content):
            if scenario.id is ScenarioId.WORLD_SELECTION:
                selected_world = self._match_world_selection(message.content)
                if selected_world is not None:
                    decision = RunHandler(
                        HandlerKind.WORLD_SELECT,
                        DecisionSource.STRUCTURAL,
                        CommandId.SELECT_WORLD,
                        selected_world.title,
                    )
            elif scenario.id is ScenarioId.PREPARATION and channel.game_id is not None:
                pregen = self._match_pregenerated_character(
                    game_id=channel.game_id, content=message.content
                )
                if pregen is not None:
                    decision = RunHandler(
                        HandlerKind.CHARACTER_SELECT,
                        DecisionSource.STRUCTURAL,
                        CommandId.SELECT_CHARACTER,
                        str(pregen["name"]),
                    )
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
                result = await self._state_decisions.decide(
                    message=message,
                    scenario=scenario,
                    context=assembled,
                    game_id=channel.game_id,
                    pending_kind=None if pending is None else pending.kind.value,
                    workspace_stage=snapshot.workspace_stage,
                )
                decision_command = result.command
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
                    normalized_phrase = normalize_phrase(message.content)
                    if normalized_phrase and len(normalized_phrase) <= 512:
                        self._store.record_lexicon_candidate(
                            event_id=message.event_id,
                            scenario=scenario.id.value,
                            command=decision_command.value,
                            normalized_phrase=normalized_phrase,
                            confidence=result.confidence,
                        )
                        logger.info(
                            "lexicon_candidate scenario=%s command=%s confidence=%.3f phrase=%r",
                            scenario.id.value,
                            decision_command.value,
                            result.confidence,
                            normalized_phrase,
                        )
                safety = scenario.command_safety(decision_command)
                if (
                    decision_command is not CommandId.CLARIFY
                    and result.confidence < INFERRED_COMMAND_MIN_CONFIDENCE
                ):
                    blocked_command = decision_command
                    decision_command = CommandId.CLARIFY
                    argument = None
                    decision_evidence = (
                        f"blocked low-confidence {blocked_command.value}: "
                        f"{result.confidence:.3f} < {INFERRED_COMMAND_MIN_CONFIDENCE:.3f}"
                    )
                elif safety is CommandSafety.EXPLICIT_ONLY:
                    blocked_command = decision_command
                    decision_command = CommandId.CLARIFY
                    argument = None
                    decision_evidence = (
                        f"blocked inferred {blocked_command.value}: explicit confirmation required"
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
                return self._xp_status(channel.game_id, player_id=message.author_id)
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
                message=message,
                game_id=channel.game_id,
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
