from __future__ import annotations

import logging
from collections.abc import Mapping
from contextvars import ContextVar, Token

from masterclaw.app.i18n import locale_for_text
from masterclaw.app.scenarios import Scenario
from masterclaw.context.assembler import AssembledContext, ContextHistory
from masterclaw.context.manifests import ContextManifest
from masterclaw.domain.models import ChannelState, IncomingMessage
from masterclaw.domain.text_safety import hidden_secret_plot, secret_fact_catalog
from masterclaw.telemetry import (
    stage_span,
)

logger = logging.getLogger(__name__)
_MESSAGE_LOCALE: ContextVar[str] = ContextVar("masterclaw_message_locale", default="ru")


class HandlerSupport:
    def _has_durable_inbox_event(self, event_id: str) -> bool:
        """Keep direct unit/service calls compatible while production uses event UoWs."""

        try:
            self._store.inbox_provider_attempts(event_id)
        except ValueError as error:
            if str(error) == "inbox event not found":
                return False
            raise
        return True

    def _channel_for_message(self, message: IncomingMessage) -> ChannelState:
        """Recover the immutable ingress route for nested deterministic dispatches."""
        if message.has_routing_snapshot:
            return ChannelState(
                channel_id=message.channel_id,
                game_id=message.routing_game_id,
                lifecycle=message.routing_lifecycle,
            )
        return self._store.channel_state(message.channel_id)

    @staticmethod
    def _bind_message_locale(content: str) -> Token[str]:
        return _MESSAGE_LOCALE.set(locale_for_text(content))

    @staticmethod
    def _reset_message_locale(token: Token[str]) -> None:
        _MESSAGE_LOCALE.reset(token)

    @staticmethod
    def _projection_items(value: object, *, limit: int = 12) -> list[object]:
        if value is None:
            return []
        if isinstance(value, (list, tuple)):
            return list(value[:limit])
        return [value]

    @staticmethod
    def _first_canonical_value(
        sources: tuple[Mapping[str, object], ...],
        keys: tuple[str, ...],
    ) -> object:
        for source in sources:
            for key in keys:
                if key in source:
                    return source[key]
        return None

    @classmethod
    def _merged_canonical_items(
        cls,
        sources: tuple[Mapping[str, object], ...],
        keys: tuple[str, ...],
        *,
        limit: int = 12,
    ) -> list[object]:
        merged: list[object] = []
        for source in sources:
            value = cls._first_canonical_value((source,), keys)
            for item in cls._projection_items(value, limit=limit):
                if item not in merged:
                    merged.append(item)
                if len(merged) >= limit:
                    return merged
        return merged

    def _world_context_projection(
        self,
        *,
        game_id: str,
        scene: object,
        include_secret: bool,
    ) -> dict[str, object]:
        game = self._store.game_state(game_id)
        if game is None:
            return {}
        content = self._store.world_content(game.world_id) or {}
        scene_mapping = scene if isinstance(scene, Mapping) else {}
        raw_state = scene_mapping.get("state")
        scene_state = raw_state if isinstance(raw_state, Mapping) else {}
        raw_locations = content.get("locations")
        locations = raw_locations if isinstance(raw_locations, list) else []
        world_location_id = scene_state.get("world_location_id")
        scene_title = scene_mapping.get("title")
        current_location: Mapping[str, object] | None = None
        for raw_location in locations:
            if not isinstance(raw_location, Mapping):
                continue
            if world_location_id is not None and raw_location.get("id") == world_location_id:
                current_location = raw_location
                break
            if (
                world_location_id is None
                and scene_title
                and raw_location.get("name") == scene_title
            ):
                current_location = raw_location
                break

        location_projection = {
            "scene_id": scene_mapping.get("scene_id"),
            "world_location_id": (
                current_location.get("id") if current_location is not None else world_location_id
            ),
            "name": (current_location.get("name") if current_location is not None else scene_title),
            "description": (
                current_location.get("description")
                if current_location is not None
                else scene_state.get("description")
            ),
        }
        location_source = current_location or {}
        available_directions = self._first_canonical_value(
            (scene_state, location_source),
            ("available_directions", "exits", "directions", "links"),
        )
        active_threads = self._merged_canonical_items(
            (scene_state, location_source, content),
            ("active_threads", "threads", "open_threads"),
        )
        active_threats = self._merged_canonical_items(
            (scene_state, location_source, content),
            ("active_threats", "threats"),
        )
        projection: dict[str, object] = {
            "premise": content.get("premise"),
            "themes": self._projection_items(content.get("themes"), limit=8),
            "factions": self._projection_items(content.get("factions")),
            "tensions": self._projection_items(content.get("tensions")),
            "current_location": location_projection,
            "available_directions": self._projection_items(available_directions),
            "active_threads": active_threads,
            "active_threats": active_threats,
        }
        if include_secret:
            raw_secret = content.get("secret_plot")
            hidden_secret = hidden_secret_plot(
                raw_secret if isinstance(raw_secret, str) else None,
                self._store.revealed_secret_ids(game_id),
            )
            projection["secret_plot"] = hidden_secret
            projection["secret_catalog"] = [
                {"secret_id": fact.secret_id, "text": fact.text}
                for fact in secret_fact_catalog(hidden_secret)
            ]
        return projection

    def _scene_with_participant_characters(
        self,
        *,
        game_id: str,
        scene: object,
    ) -> object:
        if not isinstance(scene, Mapping):
            return scene
        if "participant_characters" in scene:
            return scene
        participants = scene.get("participants")
        if not isinstance(participants, (list, tuple)):
            return scene
        names_by_player = {
            str(character["player_id"]): str(character["name"])
            for character in self._store.character_roster(game_id)
        }
        enriched = dict(scene)
        enriched["participant_characters"] = [
            {"player_id": player_id, "name": names_by_player[player_id]}
            for participant in participants
            if (player_id := str(participant)) in names_by_player
        ]
        return enriched

    def _actor_character_projection(
        self, *, game_id: str, player_id: str
    ) -> dict[str, object] | None:
        character = self._store.character_for_player(game_id=game_id, player_id=player_id)
        if character is None:
            return None
        return {
            "player_id": player_id,
            "character_id": character.character_id,
            "revision": character.revision,
            "name": character.sheet.name,
            "traits": [
                {
                    "name": trait.name,
                    "level": trait.level,
                    "aspects": list(trait.aspects),
                }
                for trait in character.sheet.traits
            ],
            "flags": [flag.text for flag in character.sheet.flags],
            "reserve": character.sheet.reserve_current,
            "conditions": [item.text for item in character.conditions],
            "plot_items": [
                {"name": item.name, "description": item.description}
                for item in character.plot_items
            ],
            "temporary_bonuses": [
                {
                    "bonus_id": bonus.bonus_id,
                    "type": bonus.type.value,
                    "trigger": bonus.trigger,
                }
                for bonus in character.sheet.temporary_bonuses
            ],
        }

    def _scenario_context_projections(
        self,
        scenario: Scenario,
        *,
        game_id: str | None,
        channel_id: str,
        player_id: str,
        workspace: dict[str, object] | None,
        pending,
    ) -> dict[str, object]:
        projections: dict[str, object] = {}
        requested = set(scenario.context_projections)
        if "world_catalog" in requested:
            projections["world_catalog"] = [
                {
                    "ordinal": index,
                    "world_id": world.world_id,
                    "title": world.title,
                    "status": world.status,
                    "premise": str(
                        (self._store.world_content(world.world_id) or {}).get("premise", "")
                    )[:500],
                    "themes": self._projection_items(
                        (self._store.world_content(world.world_id) or {}).get("themes"),
                        limit=8,
                    ),
                }
                for index, world in enumerate(self._store.list_worlds(), start=1)
            ]
        if "world_workspace" in requested:
            projections["world_workspace"] = (
                None
                if workspace is None
                else {
                    "stage": workspace["stage"],
                    "brief": workspace["brief"],
                    "settings": workspace["settings"],
                    "sources": workspace["sources"],
                }
            )
        if "pending_interaction" in requested:
            projections["pending_interaction"] = (
                None
                if pending is None
                else {
                    "id": pending.interaction_id,
                    "kind": pending.kind.value,
                    "prompt": pending.prompt,
                    "scene_id": pending.scene_id,
                }
            )
        if "current_scene" in requested:
            scene = (
                None
                if game_id is None
                else self._store.scene_projection(game_id=game_id, player_id=player_id)
            )
            projections["current_scene"] = (
                scene
                if game_id is None
                else self._scene_with_participant_characters(game_id=game_id, scene=scene)
            )
        if "actor_character" in requested:
            projections["actor_character"] = (
                None
                if game_id is None
                else self._actor_character_projection(game_id=game_id, player_id=player_id)
            )
        return projections

    def _assemble_context(
        self,
        manifest: ContextManifest,
        projections: dict[str, object],
        *,
        game_id: str | None = None,
        channel_id: str | None = None,
        player_id: str | None = None,
    ) -> AssembledContext:
        if (
            game_id is not None
            and "session_brief" in manifest.state_projections
            and "session_brief" not in projections
        ):
            game = self._store.game_state(game_id)
            if game is not None:
                projections = {
                    **projections,
                    "session_brief": self._narrative_session_brief(game),
                }
        if (
            game_id is not None
            and player_id is not None
            and "actor_character" in manifest.state_projections
            and "actor_character" not in projections
        ):
            projections = {
                **projections,
                "actor_character": self._actor_character_projection(
                    game_id=game_id, player_id=player_id
                ),
            }
        if game_id is not None and "current_scene" in projections:
            projections = {
                **projections,
                "current_scene": self._scene_with_participant_characters(
                    game_id=game_id,
                    scene=projections["current_scene"],
                ),
            }
        if (
            game_id is not None
            and "public_world_context" in manifest.state_projections
            and "public_world_context" not in projections
        ):
            projections = {
                **projections,
                "public_world_context": self._world_context_projection(
                    game_id=game_id,
                    scene=projections.get("current_scene"),
                    include_secret=False,
                ),
            }
        if (
            game_id is not None
            and "gm_world_context" in manifest.state_projections
            and "gm_world_context" not in projections
        ):
            projections = {
                **projections,
                "gm_world_context": self._world_context_projection(
                    game_id=game_id,
                    scene=projections.get("current_scene"),
                    include_secret=True,
                ),
            }
        with stage_span(
            "db.context_history",
            component="sqlite",
            operation=manifest.pipeline.value,
        ):
            domain_events = (
                self._store.recent_domain_events(
                    game_id=game_id, limit=manifest.recent_domain_events
                )
                if game_id is not None
                else []
            )
            chat_messages = self._store.recent_chat_messages(
                game_id=game_id,
                channel_id=channel_id,
                player_id=player_id,
                limit=manifest.recent_chat_messages,
            )
        with stage_span(
            "context.assembly",
            component="context_assembler",
            operation=manifest.pipeline.value,
            attributes={"pipeline": manifest.pipeline.value},
        ):
            return self._context.assemble(
                manifest,
                projections,
                history=ContextHistory(domain_events, chat_messages),
            )

    def _locale(self, game_id: str | None) -> str:
        if game_id is None:
            return _MESSAGE_LOCALE.get()
        game = self._store.game_state(game_id)
        return game.locale if game is not None else "ru"

    def _secret_plot_for_game(self, game_id: str) -> str | None:
        game = self._store.game_state(game_id)
        if game is None:
            return None
        content = self._store.world_content(game.world_id) or {}
        secret_plot = content.get("secret_plot")
        raw_secret = secret_plot if isinstance(secret_plot, str) and secret_plot.strip() else None
        return hidden_secret_plot(raw_secret, self._store.revealed_secret_ids(game_id))

    def _narrative_session_brief(self, game) -> dict[str, object]:
        content = self._store.world_content(game.world_id) or {}
        defaults = dict(content.get("game_defaults") or {})
        return {
            "game_id": game.game_id,
            "locale": game.locale,
            "narrative_style": defaults.get("narrative_style"),
            "narrative_perspective": defaults.get("narrative_perspective"),
            "narrative_detail": defaults.get("narrative_detail"),
        }
