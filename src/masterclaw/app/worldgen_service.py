from __future__ import annotations

import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from masterclaw.app.legacy_world_semantic_guard import LegacyWorldSemanticGuard, _text_values
from masterclaw.app.world_semantic_guard import (
    WorldSemanticGuard,
    WorldSemanticGuardError,
    WorldSemanticSnapshot,
)
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
from masterclaw.domain.text_safety import SecretLeakError, ensure_no_secret_fragments
from masterclaw.pipelines.base import BoundedJsonPipeline, CompletionPort
from masterclaw.pipelines.worldgen import (
    CreativeWorldDraft,
    WorldDraft,
    create_world_creative_pipeline,
    create_world_structuring_pipeline,
)


class WorldGenerationError(ValueError):
    pass


_ALLOWED_WORLD_SETTINGS = frozenset(
    {
        "title",
        "genre",
        "tone",
        "scale",
        "player_role",
        "themes",
        "content_constraints",
        "narrative_style",
        "narrative_perspective",
        "narrative_detail",
        "narrator_rights_level",
        "reserve_recovery_mode",
        "progression_enabled",
        "locale",
        "pregenerated_character_count",
        "pregenerated_character_briefs",
    }
)
_TEXT_WORLD_SETTINGS = frozenset(
    {
        "title",
        "genre",
        "tone",
        "scale",
        "player_role",
        "narrative_style",
        "narrative_perspective",
    }
)
_ENUM_WORLD_SETTINGS: dict[str, frozenset[str]] = {
    "narrative_detail": frozenset({"concise", "balanced", "detailed"}),
    "narrator_rights_level": frozenset({"disabled", "minor", "significant", "madness"}),
    "reserve_recovery_mode": frozenset({"safe_rest", "roleplay_award", "both"}),
    "locale": frozenset({"ru", "en"}),
}
_LIST_WORLD_SETTINGS: dict[str, int] = {
    "themes": 8,
    "content_constraints": 12,
    "pregenerated_character_briefs": 6,
}


def validate_confirmed_world_settings(settings: Mapping[str, object]) -> None:
    """Fail before generation when persisted world settings violate their typed contract."""

    unexpected = set(settings) - _ALLOWED_WORLD_SETTINGS
    if unexpected:
        raise WorldGenerationError("confirmed settings contain an unsupported field")

    for key in _TEXT_WORLD_SETTINGS & settings.keys():
        value = settings[key]
        if not isinstance(value, str) or not value.strip():
            raise WorldGenerationError(f"confirmed {key} must be non-empty text")

    for key, allowed in _ENUM_WORLD_SETTINGS.items():
        if key in settings and settings[key] not in allowed:
            raise WorldGenerationError(f"confirmed {key} is unsupported")

    if "progression_enabled" in settings and not isinstance(settings["progression_enabled"], bool):
        raise WorldGenerationError("confirmed progression_enabled must be boolean")

    for key, maximum in _LIST_WORLD_SETTINGS.items():
        if key not in settings:
            continue
        value = settings[key]
        if not isinstance(value, (list, tuple)) or len(value) > maximum:
            raise WorldGenerationError(f"confirmed {key} must be a bounded list")
        if any(not isinstance(item, str) or not item.strip() for item in value):
            raise WorldGenerationError(f"confirmed {key} contains a blank item")

    requested_count = settings.get("pregenerated_character_count")
    if requested_count is not None:
        if (
            isinstance(requested_count, bool)
            or not isinstance(requested_count, int)
            or not 3 <= requested_count <= 6
        ):
            raise WorldGenerationError("confirmed pregen count must be between three and six")
        requested_concepts = settings.get("pregenerated_character_briefs", ())
        if (
            isinstance(requested_concepts, (list, tuple))
            and len(requested_concepts) > requested_count
        ):
            raise WorldGenerationError("requested pregen concepts exceed the confirmed count")


def _script_letter_counts(texts: Sequence[str]) -> tuple[int, int]:
    cyrillic = 0
    latin = 0
    for character in " ".join(texts):
        if not character.isalpha():
            continue
        name = unicodedata.name(character, "")
        if "CYRILLIC" in name:
            cyrillic += 1
        elif "LATIN" in name:
            latin += 1
    return cyrillic, latin


def _validate_setting_locale(draft: WorldDraft, settings: Mapping[str, object]) -> None:
    public_texts = _text_values(
        draft.model_dump(mode="json", exclude={"secret_plot", "setting_adherence"})
    )
    locale = settings.get("locale")
    if locale in {"ru", "en"}:
        cyrillic, latin = _script_letter_counts(public_texts)
        total = cyrillic + latin
        if total < 40:
            raise WorldGenerationError("structured world has too little prose to verify locale")
        expected = cyrillic if locale == "ru" else latin
        minimum_ratio = 0.55 if locale == "ru" else 0.75
        if expected / total < minimum_ratio:
            raise WorldGenerationError("structured world prose does not match confirmed locale")


@dataclass(slots=True)
class WorldGenerationService:
    context: ContextAssembler
    creative_pipeline: BoundedJsonPipeline[CreativeWorldDraft]
    creative_fallback_pipeline: BoundedJsonPipeline[CreativeWorldDraft]
    structuring_pipeline: BoundedJsonPipeline[WorldDraft]
    structuring_fallback_pipeline: BoundedJsonPipeline[WorldDraft]
    semantic_guard: WorldSemanticGuard = field(default_factory=LegacyWorldSemanticGuard)

    async def generate(
        self,
        *,
        world: WorldState,
        brief: str,
        settings: Mapping[str, object] | None = None,
    ) -> WorldDraft:
        confirmed_settings = dict(settings or {})
        validate_confirmed_world_settings(confirmed_settings)
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
            self._validate_draft(
                draft, settings=confirmed_settings, semantic_guard=self.semantic_guard
            )
        except Exception:
            draft = await self.structuring_fallback_pipeline.run(
                task=(
                    "Check the raw plot for consistency and convert it into the complete world "
                    "JSON contract."
                ),
                context=structuring_context,
            )
            self._validate_draft(
                draft, settings=confirmed_settings, semantic_guard=self.semantic_guard
            )
        return draft

    @staticmethod
    def _validate_draft(
        draft: WorldDraft,
        *,
        settings: Mapping[str, object] | None = None,
        semantic_guard: WorldSemanticGuard | None = None,
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
        validate_confirmed_world_settings(settings)
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

        snapshot = WorldSemanticSnapshot.capture(
            settings=settings,
            draft=draft.model_dump(mode="json", exclude={"setting_adherence"}),
            # This excluded validation metadata must not disappear during detachment.
            adherence=draft.setting_adherence.model_dump(mode="json"),
        )
        guard = semantic_guard if semantic_guard is not None else LegacyWorldSemanticGuard()
        try:
            guard.validate(snapshot)
        except WorldSemanticGuardError as error:
            raise WorldGenerationError(str(error)) from None
        _validate_setting_locale(draft, settings)

        public_content = draft.model_dump(mode="json", exclude={"secret_plot"})
        try:
            ensure_no_secret_fragments(public_content, draft.secret_plot)
        except SecretLeakError as error:
            raise WorldGenerationError("public world material overlaps the secret plot") from error


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
