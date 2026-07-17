from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from masterclaw.context.assembler import ContextAssembler
from masterclaw.context.manifests import PipelineName, manifest_for
from masterclaw.domain.mechanics import (
    STARTING_CHARACTER_RULES,
    CharacterSheet,
    Flag,
    Trait,
    validate_character,
)
from masterclaw.domain.state import WorldState
from masterclaw.pipelines.base import BoundedJsonPipeline, CompletionPort
from masterclaw.pipelines.worldgen import (
    CreativeWorldDraft,
    WorldDraft,
    create_world_creative_pipeline,
    create_world_structuring_pipeline,
)


class WorldGenerationError(ValueError):
    pass


@dataclass(slots=True)
class WorldGenerationService:
    context: ContextAssembler
    creative_pipeline: BoundedJsonPipeline[CreativeWorldDraft]
    creative_fallback_pipeline: BoundedJsonPipeline[CreativeWorldDraft]
    structuring_pipeline: BoundedJsonPipeline[WorldDraft]
    structuring_fallback_pipeline: BoundedJsonPipeline[WorldDraft]

    async def generate(
        self,
        *,
        world: WorldState,
        brief: str,
        settings: Mapping[str, object] | None = None,
    ) -> WorldDraft:
        confirmed_settings = dict(settings or {})
        constraints = {
            "brief": brief,
            "world_id": world.world_id,
            "title": world.title,
            "confirmed_settings": confirmed_settings,
        }
        creative_context = self.context.assemble(
            manifest_for(PipelineName.WORLD_CREATIVE),
            {"world_constraints": constraints},
        )
        try:
            creative = await self.creative_pipeline.run(
                task="Write the raw creative plot for this module.", context=creative_context
            )
        except Exception:
            creative = await self.creative_fallback_pipeline.run(
                task="Write the raw creative plot for this module.", context=creative_context
            )

        structuring_context = self.context.assemble(
            manifest_for(PipelineName.WORLD_STRUCTURING),
            {
                "world_constraints": constraints,
                "creative_draft": creative.module_plot,
            },
        )
        try:
            draft = await self.structuring_pipeline.run(
                task=(
                    "Check the raw plot for consistency and convert it into the complete world "
                    "JSON contract."
                ),
                context=structuring_context,
            )
        except Exception:
            draft = await self.structuring_fallback_pipeline.run(
                task=(
                    "Check the raw plot for consistency and convert it into the complete world "
                    "JSON contract."
                ),
                context=structuring_context,
            )
        self._validate_draft(draft, settings=confirmed_settings)
        return draft

    @staticmethod
    def _validate_draft(
        draft: WorldDraft,
        *,
        settings: Mapping[str, object] | None = None,
    ) -> None:
        location_ids = [location.id for location in draft.locations]
        if len(set(location_ids)) != len(location_ids):
            raise WorldGenerationError("structured world contains duplicate location ids")
        normalized_factions = [faction.strip().casefold() for faction in draft.factions]
        if len(set(normalized_factions)) != len(normalized_factions):
            raise WorldGenerationError("structured world contains duplicate factions")
        faction_names = {item.strip().casefold() for item in draft.factions}
        character_names = {item.name.strip().casefold() for item in draft.character_templates}
        if len(character_names) != len(draft.character_templates):
            raise WorldGenerationError("structured world contains duplicate pregen names")
        for character in draft.character_templates:
            if any(
                item.strip().casefold() not in faction_names
                for item in character.faction_affiliations
            ):
                raise WorldGenerationError("pregen references an unknown faction")
            for connection in character.connections:
                target = connection.character_name.strip().casefold()
                if target == character.name.strip().casefold() or target not in character_names:
                    raise WorldGenerationError("pregen connection must reference another pregen")
            sheet = CharacterSheet(
                name=character.name,
                traits=tuple(
                    Trait(item.name, item.level, tuple(item.aspects)) for item in character.traits
                ),
                flags=tuple(Flag(item.text, item.type, locked=True) for item in character.flags),
            )
            validate_character(sheet, STARTING_CHARACTER_RULES)
        settings = settings or {}
        requested_count = settings.get("pregenerated_character_count")
        if isinstance(requested_count, int) and len(draft.character_templates) != requested_count:
            raise WorldGenerationError(
                "structured world does not contain the requested pregen count"
            )
        requested_themes = settings.get("themes")
        if isinstance(requested_themes, (list, tuple)):
            generated_themes = {item.strip().casefold() for item in draft.themes}
            missing_themes = [
                str(item)
                for item in requested_themes
                if str(item).strip().casefold() not in generated_themes
            ]
            if missing_themes:
                raise WorldGenerationError("structured world is missing a requested theme")


def create_world_generation_service(
    *,
    context: ContextAssembler,
    creative_completion: CompletionPort,
    creative_fallback_completion: CompletionPort,
    structuring_completion: CompletionPort,
    structuring_fallback_completion: CompletionPort,
) -> WorldGenerationService:
    return WorldGenerationService(
        context=context,
        creative_pipeline=create_world_creative_pipeline(creative_completion),
        creative_fallback_pipeline=create_world_creative_pipeline(creative_fallback_completion),
        structuring_pipeline=create_world_structuring_pipeline(structuring_completion),
        structuring_fallback_pipeline=create_world_structuring_pipeline(
            structuring_fallback_completion
        ),
    )
