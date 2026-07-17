from __future__ import annotations

import logging

from masterclaw.app.handlers.types import WorldWorkspaceStage
from masterclaw.app.i18n import tr
from masterclaw.app.scenarios import normalize_phrase
from masterclaw.app.world_settings import (
    WORLD_SETTING_DEFAULTS,
    complete_world_settings,
    game_defaults,
)
from masterclaw.app.worldgen_service import WorldGenerationError
from masterclaw.context.manifests import PipelineName, manifest_for
from masterclaw.domain.models import (
    IncomingMessage,
)
from masterclaw.domain.state import NarratorRightsLevel, ReserveRecoveryMode
from masterclaw.pipelines.base import PipelineValidationError
from masterclaw.pipelines.world_intake import WorldCreationBrief

logger = logging.getLogger(__name__)


class WorldManagementHandlers:
    async def _handle_natural_world_creation(self, message: IncomingMessage) -> str:
        locale = self._locale(None)
        if self._store.world_workspace(message.channel_id) is not None:
            return tr(locale, "world_workspace_active")
        if self._world_intake_pipeline is None:
            return tr(locale, "worldgen_unavailable")
        manifest = manifest_for(PipelineName.WORLD_INTAKE)
        assembled = self._assemble_context(
            manifest,
            {"player_request": message.content},
            channel_id=message.channel_id,
            player_id=message.author_id,
        )
        try:
            intake = await self._world_intake_pipeline.run(
                task="Extract the title and complete generation brief from this request.",
                context=assembled,
            )
        except PipelineValidationError:
            logger.warning("world_intake_invalid event_id=%s", message.event_id, exc_info=True)
            return tr(locale, manifest.on_invalid.value)
        world_id = f"world_{message.event_id}"
        world = self._store.world_state(world_id)
        if world is None:
            world = self._games.create_world(world_id=world_id, title=intake.title)
        settings, sources = self._world_settings(intake)
        self._store.save_world_workspace(
            channel_id=message.channel_id,
            world_id=world.world_id,
            stage=WorldWorkspaceStage.COLLECTING.value,
            brief=intake.brief,
            settings=settings,
            sources=sources,
        )
        return tr(locale, "world_inputs_collected")

    @staticmethod
    def _world_settings(
        intake: WorldCreationBrief,
    ) -> tuple[dict[str, object], dict[str, str]]:
        specified = set(intake.specified_fields)
        settings: dict[str, object] = {"title": intake.title}
        sources: dict[str, str] = {"title": "player" if "title" in specified else "default"}
        for key, default in WORLD_SETTING_DEFAULTS.items():
            value = getattr(intake, key)
            if key in specified and value is not None and value != "" and value != []:
                settings[key] = value
                sources[key] = "player"
            else:
                settings[key] = default
                sources[key] = "default"
        return settings, sources

    async def _handle_world_revision(
        self, *, message: IncomingMessage, workspace: dict[str, object]
    ) -> str:
        locale = self._locale(None)
        if self._world_intake_pipeline is None:
            return tr(locale, "worldgen_unavailable")
        manifest = manifest_for(PipelineName.WORLD_INTAKE)
        assembled = self._assemble_context(
            manifest,
            {
                "player_request": {
                    "prior_settings": workspace["settings"],
                    "new_message": message.content,
                }
            },
            channel_id=message.channel_id,
            player_id=message.author_id,
        )
        try:
            intake = await self._world_intake_pipeline.run(
                task=(
                    "Extract only the changes stated in the new message. "
                    "Return a compact revision; "
                    "do not reproduce the existing world brief."
                ),
                context=assembled,
            )
        except PipelineValidationError:
            logger.warning("world_revision_invalid event_id=%s", message.event_id, exc_info=True)
            return tr(locale, manifest.on_invalid.value)
        logger.info(
            "world_revision event_id=%s channel_id=%s world_id=%s stage=%s specified_fields=%s",
            message.event_id,
            message.channel_id,
            workspace["world_id"],
            workspace["stage"],
            sorted(set(intake.specified_fields)),
        )
        settings, sources = complete_world_settings(
            dict(workspace["settings"]), dict(workspace["sources"])
        )
        defaults, _ = self._world_settings(intake)
        for key in set(intake.specified_fields):
            if key == "title":
                settings["title"] = intake.title
                sources["title"] = "player"
                self._store.set_world_title(world_id=str(workspace["world_id"]), title=intake.title)
                continue
            value = getattr(intake, key, None)
            present = value is not None and value != "" and value != []
            settings[key] = value if present else defaults[key]
            sources[key] = "player" if present else "default"
        self._store.save_world_workspace(
            channel_id=message.channel_id,
            world_id=str(workspace["world_id"]),
            stage=WorldWorkspaceStage.COLLECTING.value,
            brief=(
                f"{str(workspace['brief']).rstrip()}\n\n"
                "Later player revision (takes precedence over conflicting earlier text):\n"
                f"{intake.brief.strip()}"
            ),
            settings=settings,
            sources=sources,
        )
        return tr(locale, "world_inputs_updated")

    async def _handle_world_generation(
        self, *, message: IncomingMessage, workspace: dict[str, object]
    ) -> str:
        locale = self._locale(None)
        if self._worldgen is None:
            return tr(locale, "worldgen_unavailable")
        world = self._store.world_state(str(workspace["world_id"]))
        if world is None:
            return tr(locale, "world_draft_required")
        settings, sources = complete_world_settings(
            dict(workspace["settings"]), dict(workspace["sources"])
        )
        setting_lines = [
            f"{key}: {', '.join(value) if isinstance(value, list) else value}"
            for key, value in settings.items()
            if key != "title"
        ]
        generation_brief = (
            f"{workspace['brief']}\n\nConfirmed generation parameters:\n" + "\n".join(setting_lines)
        )
        try:
            draft = await self._worldgen.generate(
                world=world,
                brief=generation_brief,
                settings=settings,
            )
        except (PipelineValidationError, WorldGenerationError):
            logger.warning(
                "world_generation_invalid event_id=%s world_id=%s",
                message.event_id,
                world.world_id,
                exc_info=True,
            )
            return tr(locale, "world_workspace_clarification")
        world_content = draft.model_dump(mode="json")
        world_content["game_defaults"] = game_defaults(settings)
        self._store.update_world_content(
            world_id=world.world_id,
            expected_revision=world.revision,
            content=world_content,
        )
        self._store.save_world_workspace(
            channel_id=message.channel_id,
            world_id=world.world_id,
            stage=WorldWorkspaceStage.REVIEW.value,
            brief=str(workspace["brief"]),
            settings=settings,
            sources=sources,
        )
        return tr(locale, "world_draft_generated")

    def _handle_world_confirmation(
        self, *, message: IncomingMessage, workspace: dict[str, object]
    ) -> str:
        locale = self._locale(None)
        if WorldWorkspaceStage(str(workspace["stage"])) is not WorldWorkspaceStage.REVIEW:
            return tr(locale, "world_generate_before_confirm")
        world = self._store.world_state(str(workspace["world_id"]))
        content = self._store.world_content(str(workspace["world_id"])) or {}
        if world is None or not content.get("locations"):
            return tr(locale, "world_generate_before_confirm")
        world = self._store.set_world_status(
            world_id=world.world_id, status="approved", expected_revision=world.revision
        )
        self._store.clear_world_workspace(message.channel_id)
        return (
            tr(locale, "world_added_to_catalog", title=world.title) + "\n\n" + self._world_catalog()
        )

    def _handle_world_exit(self, *, message: IncomingMessage, workspace: dict[str, object]) -> str:
        self._store.pause_world_workspace(
            channel_id=message.channel_id,
            world_id=str(workspace["world_id"]),
        )
        return tr(self._locale(None), "world_workspace_paused") + "\n\n" + self._world_catalog()

    def _prepare_selected_world(self, *, message: IncomingMessage, world) -> str:
        locale = self._locale(None)
        content = self._store.world_content(world.world_id) or {}
        if world.status == "draft":
            project = self._store.world_project(world.world_id)
            if project is None or project["channel_id"] is not None:
                return tr(locale, "world_not_ready", title=world.title)
            self._store.resume_world_workspace(
                channel_id=message.channel_id,
                world_id=world.world_id,
            )
            return tr(locale, "world_project_resumed", title=world.title)
        if world.status != "approved" or not content.get("locations"):
            return tr(locale, "world_not_ready", title=world.title)
        game_id = f"game_{message.event_id}"
        game = self._store.game_state(game_id)
        if game is None:
            defaults = dict(content.get("game_defaults") or {})
            game = self._games.prepare_game(
                game_id=game_id,
                world_id=world.world_id,
                channel_id=message.channel_id,
                locale=str(defaults.get("locale", locale)),
                progression_enabled=bool(defaults.get("progression_enabled", False)),
                narrator_rights_level=NarratorRightsLevel(
                    str(defaults.get("narrator_rights_level", "minor"))
                ),
                reserve_recovery_mode=ReserveRecoveryMode(
                    str(defaults.get("reserve_recovery_mode", "both"))
                ),
            )
        if game.narrative_channel_id is None:
            self._store.set_narrative_channel(
                game_id=game_id,
                channel_id=message.channel_id,
                expected_revision=game.revision,
            )
        return tr(locale, "world_selected_preparing", title=world.title)

    def _world_catalog(self) -> str:
        worlds = self._store.list_worlds()
        if not worlds:
            return tr(self._locale(None), "world_catalog_empty")
        rows = ["## 🌐 ДОСТУПНЫЕ МИРЫ"]
        for index, world in enumerate(worlds, start=1):
            content = self._store.world_content(world.world_id) or {}
            project = self._store.world_project(world.world_id)
            project_settings = dict(project["settings"]) if project is not None else {}
            premise = " ".join(
                str(
                    content.get("premise")
                    or (project["brief"] if project is not None else None)
                    or "Черновик без описания"
                ).split()
            )
            if len(premise) > 260:
                premise = premise[:259].rstrip() + "…"
            themes = (
                ", ".join(content.get("themes") or project_settings.get("themes") or [])
                or "не указаны"
            )
            status = "готов к игре" if world.status == "approved" else "черновик"
            rows.extend(
                (
                    f"### {index}. {world.title} · _{status}_",
                    f"> {premise}",
                    f"**Темы:** {themes}",
                )
            )
            if index != len(worlds):
                rows.append("━━━━━━━━━━━━━━━━━━━━━━━━")
        rows.extend(
            (
                "━━━━━━━━━━━━━━━━━━━━━━━━",
                "Напишите, например: **«выбираем Мир теней»**, или опишите новый мир.",
            )
        )
        return "\n".join(rows)

    def _match_world_selection(self, content: str):
        normalized = normalize_phrase(content)
        worlds = self._store.list_worlds()
        markers = (
            "выбираем ",
            "выбрать мир ",
            "выбираю мир ",
            "берем ",
            "хочу мир ",
            "хочу играть в ",
            "давайте играть в ",
            "хотим играть в ",
            "играем в ",
            "choose world ",
            "play in ",
        )
        tail = next(
            (normalized[len(marker) :] for marker in markers if normalized.startswith(marker)),
            None,
        )
        candidate = tail if tail else normalized
        word_ordinals = {"первый": 0, "второй": 1, "третий": 2, "first": 0, "second": 1, "third": 2}
        ordinal = word_ordinals.get(candidate)
        if ordinal is None and candidate.isdecimal():
            ordinal = int(candidate) - 1
        if ordinal is not None:
            return worlds[ordinal] if 0 <= ordinal < len(worlds) else None
        exact_titles = [world for world in worlds if normalize_phrase(world.title) == candidate]
        if len(exact_titles) == 1:
            return exact_titles[0]
        if exact_titles or tail is None or not tail:
            # Duplicate titles or no explicit selection marker: ask instead of guessing.
            return None
        contained = [
            world
            for world in worlds
            if normalize_phrase(world.title) and normalize_phrase(world.title) in tail
        ]
        return contained[0] if len(contained) == 1 else None
