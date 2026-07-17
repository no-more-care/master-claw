from __future__ import annotations

import logging
import uuid
from dataclasses import replace

from masterclaw.app.i18n import tr
from masterclaw.app.scenarios import SCENARIOS, ScenarioId
from masterclaw.context.manifests import PipelineName, manifest_for
from masterclaw.domain.models import HandlerResponse, IncomingMessage, OutboundDelivery
from masterclaw.domain.state import PendingInteraction, PendingKind
from masterclaw.pipelines.base import PipelineValidationError
from masterclaw.pipelines.compound_play import PlayRequestPartKind

logger = logging.getLogger(__name__)


class CompoundPlayHandlers:
    async def _handle_compound_play(
        self,
        *,
        message: IncomingMessage,
        game_id: str,
    ) -> str | HandlerResponse:
        locale = self._locale(game_id)
        if self._compound_play_pipeline is None:
            return tr(locale, "conversation_clarification")
        projections = self._scenario_context_projections(
            SCENARIOS[ScenarioId.PLAY],
            game_id=game_id,
            channel_id=message.channel_id,
            player_id=message.author_id,
            workspace=None,
            pending=None,
        )
        projections["player_request"] = message.content
        manifest = manifest_for(PipelineName.COMPOUND_PLAY)
        assembled = self._assemble_context(
            manifest,
            projections,
            game_id=game_id,
            channel_id=message.channel_id,
            player_id=message.author_id,
        )
        try:
            plan = await self._compound_play_pipeline.run(
                task="Decompose this compound play request without resolving it.",
                context=assembled,
            )
        except PipelineValidationError:
            logger.warning("compound_play_invalid event_id=%s", message.event_id, exc_info=True)
            return tr(locale, manifest.on_invalid.value)
        if plan.clarification_question is not None:
            return plan.clarification_question

        response_parts: list[str] = []
        deliveries: list[OutboundDelivery] = []
        for index, part in enumerate(plan.parts):
            if part.conditional_on_previous:
                prompt = tr(locale, "compound_action_confirmation", action=part.text)
                scene = self._store.scene_projection(game_id=game_id, player_id=message.author_id)
                self._store.put_pending(
                    PendingInteraction(
                        interaction_id=str(uuid.uuid4()),
                        game_id=game_id,
                        player_id=message.author_id,
                        scene_id=None if scene is None else str(scene["scene_id"]),
                        kind=PendingKind.CHOICE,
                        prompt=prompt,
                        payload={
                            "compound_action": part.text,
                            "source_event_id": message.event_id,
                        },
                    )
                )
                response_parts.append(prompt)
                continue
            part_message = replace(
                message,
                event_id=f"{message.event_id}:part:{index}",
                content=part.text,
            )
            try:
                if part.kind is PlayRequestPartKind.ROLEPLAY:
                    result = await self._handle_free_roleplay(
                        message=part_message,
                        game_id=game_id,
                    )
                elif part.kind is PlayRequestPartKind.SCENE_QUESTION:
                    result = await self._handle_scene_question(
                        message=part_message,
                        game_id=game_id,
                    )
                else:
                    result = await self._handle_action_declaration(
                        message=part_message,
                        game_id=game_id,
                    )
            except Exception:
                logger.exception(
                    "compound_part_failed event_id=%s part=%d", message.event_id, index
                )
                response_parts.append(tr(locale, "compound_part_failed", part=part.text))
                if index + 1 < len(plan.parts):
                    response_parts.append(tr(locale, "compound_parts_skipped"))
                break
            if isinstance(result, HandlerResponse):
                if result.text.strip():
                    response_parts.append(result.text)
                deliveries.extend(result.deliveries)
            elif result.strip():
                response_parts.append(result)

        text = "\n\n".join(response_parts) or tr(locale, "conversation_clarification")
        if deliveries:
            return HandlerResponse(text=text, deliveries=tuple(deliveries))
        return text
