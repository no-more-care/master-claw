from __future__ import annotations

import logging
import re

from masterclaw.app.decision_checkpoints import run_checkpointed_decision
from masterclaw.app.handlers.types import WorldWorkspaceStage
from masterclaw.app.i18n import tr
from masterclaw.app.scenarios import normalize_phrase
from masterclaw.app.world_semantic_observer import capture_public_world_semantics
from masterclaw.app.world_settings import (
    WORLD_SETTING_DEFAULTS,
    complete_world_settings,
    game_defaults,
)
from masterclaw.app.worldgen_service import (
    WorldGenerationError,
    validate_confirmed_world_settings,
)
from masterclaw.context.manifests import PipelineName, manifest_for
from masterclaw.domain.models import (
    HandlerResponse,
    IncomingMessage,
)
from masterclaw.domain.state import NarratorRightsLevel, ReserveRecoveryMode, WorldState
from masterclaw.pipelines.base import PipelineValidationError
from masterclaw.pipelines.world_intake import WorldCreationBrief

logger = logging.getLogger(__name__)


_NEW_WORLD_REQUEST_PATTERNS = (
    re.compile(
        r"\b(?:создай|создать|создадим|создам)\s+(?:мне\s+)?"
        r"(?:(?:новый|другой)\s+|еще\s+один\s+)?(?:мир|сеттинг)\b"
    ),
    re.compile(
        r"\b(?:сделай|начни)\s+(?:мне\s+)?"
        r"(?:(?:новый|другой)\s+|еще\s+один\s+)(?:мир|сеттинг)\b"
    ),
    re.compile(
        r"\b(?:create|design)\s+(?:(?:a|the)\s+)?"
        r"(?:(?:new|another)\s+)?(?:world|setting)\b"
    ),
    re.compile(r"\bmake\s+(?:(?:a|the)\s+)?(?:new|another)\s+(?:world|setting)\b"),
    re.compile(r"\bstart\s+(?:(?:a|the)\s+)?(?:new|another)\s+(?:world|setting)\b"),
)


def _is_detailed_new_world_request(content: str) -> bool:
    """Recognize only anchored creation requests, not ordinary edits of the current world."""

    normalized = normalize_phrase(content)
    return any(pattern.search(normalized) is not None for pattern in _NEW_WORLD_REQUEST_PATTERNS)


class WorldManagementHandlers:
    def _replay_world_operation(
        self,
        *,
        message: IncomingMessage,
        operation: dict[str, object],
    ) -> str:
        if operation.get("channel_id") != message.channel_id:
            raise RuntimeError("world operation replay belongs to another channel")
        locale = self._locale(None)
        kind = str(operation.get("operation") or "")
        if kind == "generation":
            return tr(locale, "world_draft_generated")
        if kind == "revision":
            return tr(locale, "world_inputs_updated")
        if kind == "pause":
            return tr(locale, "world_workspace_paused") + "\n\n" + self._world_catalog()
        world_id = str(operation.get("world_id") or "")
        world = self._store.world_state(world_id)
        if world is None:
            raise RuntimeError("committed world operation references a missing world")
        if kind == "approval":
            return (
                tr(locale, "world_added_to_catalog", title=world.title)
                + "\n\n"
                + self._world_catalog()
            )
        if kind == "resume":
            return tr(locale, "world_project_resumed", title=world.title)
        raise RuntimeError(f"unsupported world replay operation: {kind}")

    async def _handle_natural_world_creation(self, message: IncomingMessage) -> str:
        locale = self._locale(None)
        world_id = f"world_{message.event_id}"
        replayed_world = self._store.world_state(world_id)
        replayed_project = self._store.world_project(world_id)
        if (
            replayed_world is not None
            and replayed_project is not None
            and replayed_project["channel_id"] == message.channel_id
        ):
            return tr(locale, "world_inputs_collected")
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
            intake = await run_checkpointed_decision(
                store=self._store,
                event_id=message.event_id,
                pipeline_key="world_intake",
                pipeline=self._world_intake_pipeline,
                task="Extract the title and complete generation brief from this request.",
                context=assembled,
                game_id=None,
            )
        except PipelineValidationError:
            logger.warning("world_intake_invalid event_id=%s", message.event_id, exc_info=True)
            return tr(locale, manifest.on_invalid.value)
        if self._unsupported_pregen_count(intake):
            return tr(
                locale,
                "world_pregen_count_unsupported",
                requested=intake.pregenerated_character_count,
                minimum=3,
                maximum=6,
            )
        world = WorldState(world_id=world_id, title=intake.title.strip())
        settings, sources = self._world_settings(intake, inferred_locale=locale)
        try:
            self._store.save_world_workspace(
                channel_id=message.channel_id,
                world_id=world.world_id,
                stage=WorldWorkspaceStage.COLLECTING.value,
                brief=intake.brief,
                settings=settings,
                sources=sources,
                world=world,
            )
        except RuntimeError:
            logger.info(
                "world_creation_stale_channel event_id=%s channel_id=%s",
                message.event_id,
                message.channel_id,
            )
            return tr(locale, "channel_context_changed_retry")
        return tr(locale, "world_inputs_collected")

    @staticmethod
    def _world_settings(
        intake: WorldCreationBrief,
        *,
        inferred_locale: str | None = None,
    ) -> tuple[dict[str, object], dict[str, str]]:
        specified = set(intake.specified_fields)
        settings: dict[str, object] = {"title": intake.title}
        sources: dict[str, str] = {"title": "player" if "title" in specified else "default"}
        for key, default in WORLD_SETTING_DEFAULTS.items():
            value = getattr(intake, key)
            if key in specified:
                if value is None:
                    settings[key] = default
                    sources[key] = "default"
                else:
                    settings[key] = value
                    sources[key] = "player"
            elif key == "locale" and inferred_locale in {"ru", "en"}:
                # The intake model must not claim inferred values as player-specified. For a new
                # project, however, replying to an English request with a Russian-by-default
                # world is surprising, so retain message-language provenance explicitly.
                settings[key] = inferred_locale
                sources[key] = "inferred"
            else:
                settings[key] = default
                sources[key] = "default"
        return settings, sources

    @staticmethod
    def _unsupported_pregen_count(intake: WorldCreationBrief) -> bool:
        count = intake.pregenerated_character_count
        return count is not None and not 3 <= count <= 6

    async def _handle_world_revision(
        self, *, message: IncomingMessage, workspace: dict[str, object]
    ) -> str:
        locale = self._locale(None)
        replayed = self._store.world_revision_for_event(message.event_id)
        if replayed is not None:
            if (
                replayed.get("channel_id") != message.channel_id
                or replayed.get("world_id") != workspace["world_id"]
            ):
                raise RuntimeError("world revision replay belongs to another workspace")
            return tr(locale, "world_inputs_updated")
        if _is_detailed_new_world_request(message.content):
            return tr(locale, "world_workspace_active")
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
            intake = await run_checkpointed_decision(
                store=self._store,
                event_id=message.event_id,
                pipeline_key="world_intake",
                pipeline=self._world_intake_pipeline,
                task=(
                    "Extract only the changes stated in the new message. "
                    "Return a compact revision; "
                    "do not reproduce the existing world brief."
                ),
                context=assembled,
                game_id=None,
            )
        except PipelineValidationError:
            logger.warning("world_revision_invalid event_id=%s", message.event_id, exc_info=True)
            return tr(locale, manifest.on_invalid.value)
        if self._unsupported_pregen_count(intake):
            return tr(
                locale,
                "world_pregen_count_unsupported",
                requested=intake.pregenerated_character_count,
                minimum=3,
                maximum=6,
            )
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
        world = self._store.world_state(str(workspace["world_id"]))
        if world is None:
            return tr(locale, "world_draft_required")
        title = world.title
        for key in set(intake.specified_fields):
            if key == "title":
                settings["title"] = intake.title
                sources["title"] = "player"
                title = intake.title
                continue
            value = getattr(intake, key, None)
            if value is None:
                settings[key] = WORLD_SETTING_DEFAULTS[key]
                sources[key] = "default"
            else:
                # Empty lists and False are intentional player values, not missing values.
                settings[key] = value
                sources[key] = "player"
        self._store.apply_world_revision(
            event_id=message.event_id,
            channel_id=message.channel_id,
            world_id=str(workspace["world_id"]),
            expected_world_revision=world.revision,
            expected_workspace_revision=int(workspace["revision"]),
            title=title,
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
        replayed = self._store.world_generation_for_event(message.event_id)
        if replayed is not None:
            if (
                replayed.get("channel_id") != message.channel_id
                or replayed.get("world_id") != workspace["world_id"]
            ):
                raise RuntimeError("world generation replay belongs to another workspace")
            return tr(locale, "world_draft_generated")
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
            validate_confirmed_world_settings(settings)
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
        committed = self._store.commit_world_generation(
            event_id=message.event_id,
            channel_id=message.channel_id,
            world_id=world.world_id,
            expected_world_revision=world.revision,
            expected_workspace_revision=int(workspace["revision"]),
            content=world_content,
            brief=str(workspace["brief"]),
            settings=settings,
            sources=sources,
        )
        observer = self._world_semantic_observer
        if committed and observer is not None:
            try:
                await observer.observe(
                    capture_public_world_semantics(settings=settings, content=world_content)
                )
            except Exception:
                # Durable gameplay succeeded. Do not expose provider/state/error details.
                # CancelledError still propagates; committed replay never repeats observation.
                logger.warning("world_semantic_observation_failed")
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
        world = self._store.approve_world_and_clear_workspace(
            event_id=message.event_id,
            channel_id=message.channel_id,
            world_id=world.world_id,
            expected_world_revision=world.revision,
            expected_workspace_revision=int(workspace["revision"]),
        )
        return (
            tr(locale, "world_added_to_catalog", title=world.title) + "\n\n" + self._world_catalog()
        )

    def _handle_world_exit(self, *, message: IncomingMessage, workspace: dict[str, object]) -> str:
        self._store.pause_world_workspace_for_event(
            event_id=message.event_id,
            channel_id=message.channel_id,
            world_id=str(workspace["world_id"]),
            expected_workspace_revision=int(workspace["revision"]),
        )
        return tr(self._locale(None), "world_workspace_paused") + "\n\n" + self._world_catalog()

    def _prepare_selected_world(
        self,
        *,
        message: IncomingMessage,
        world,
    ) -> str | HandlerResponse:
        locale = self._locale(None)
        content = self._store.world_content(world.world_id) or {}
        if world.status == "draft":
            project = self._store.world_project(world.world_id)
            if project is None or project["channel_id"] is not None:
                return tr(locale, "world_not_ready", title=world.title)
            self._store.resume_world_workspace_for_event(
                event_id=message.event_id,
                channel_id=message.channel_id,
                world_id=world.world_id,
                expected_workspace_revision=int(project["revision"]),
            )
            return tr(locale, "world_project_resumed", title=world.title)
        if world.status != "approved" or not content.get("locations"):
            return tr(locale, "world_not_ready", title=world.title)
        game_id = f"game_{message.event_id}"
        defaults = dict(content.get("game_defaults") or {})
        try:
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
        except RuntimeError:
            logger.info(
                "world_selection_stale_channel event_id=%s channel_id=%s world_id=%s",
                message.event_id,
                message.channel_id,
                world.world_id,
            )
            return tr(locale, "channel_context_changed_retry")
        if game.narrative_channel_id is None:
            self._store.set_narrative_channel(
                game_id=game_id,
                channel_id=message.channel_id,
                expected_revision=game.revision,
            )
        return HandlerResponse(
            tr(locale, "world_selected_preparing", title=world.title),
            completion_game_id=game_id,
            render_live_status=True,
        )

    def _world_catalog(self) -> str:
        locale = self._locale(None)
        worlds = self._store.list_worlds()
        if not worlds:
            return tr(locale, "world_catalog_empty")
        rows = ["## 🌐 AVAILABLE WORLDS" if locale == "en" else "## 🌐 ДОСТУПНЫЕ МИРЫ"]
        for index, world in enumerate(worlds, start=1):
            content = self._store.world_content(world.world_id) or {}
            project = self._store.world_project(world.world_id)
            project_settings = dict(project["settings"]) if project is not None else {}
            premise = " ".join(
                str(
                    content.get("premise")
                    or (project["brief"] if project is not None else None)
                    or (
                        "Draft without a description" if locale == "en" else "Черновик без описания"
                    )
                ).split()
            )
            if len(premise) > 260:
                premise = premise[:259].rstrip() + "…"
            themes = ", ".join(content.get("themes") or project_settings.get("themes") or []) or (
                "not specified" if locale == "en" else "не указаны"
            )
            status = (
                ("ready to play" if world.status == "approved" else "draft")
                if locale == "en"
                else ("готов к игре" if world.status == "approved" else "черновик")
            )
            rows.extend(
                (
                    f"### {index}. {world.title} · _{status}_",
                    f"> {premise}",
                    f"**{'Themes' if locale == 'en' else 'Темы'}:** {themes}",
                )
            )
            if index != len(worlds):
                rows.append("━━━━━━━━━━━━━━━━━━━━━━━━")
        rows.extend(
            (
                "━━━━━━━━━━━━━━━━━━━━━━━━",
                (
                    "Write, for example, **“choose Shadow World”**, or describe a new world."
                    if locale == "en"
                    else "Напишите, например: **«выбираем Мир теней»**, или опишите новый мир."
                ),
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
