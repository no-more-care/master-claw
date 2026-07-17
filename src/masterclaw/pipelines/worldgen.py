from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator

from masterclaw.domain.mechanics import FlagType
from masterclaw.pipelines.base import BoundedJsonPipeline, CompletionPort
from masterclaw.pipelines.character_creation import FlagDraft, TraitDraft


def _normalized_identity(value: str) -> str:
    return value.strip().casefold()


class WorldLocationSeed(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{1,63}$")
    name: str = Field(min_length=1, max_length=120)
    purpose: str = Field(min_length=1, max_length=500)


class WorldOutline(BaseModel):
    model_config = ConfigDict(extra="forbid")

    premise: str = Field(min_length=20, max_length=3000)
    themes: list[str] = Field(min_length=1, max_length=8)
    location_seeds: list[WorldLocationSeed] = Field(min_length=1, max_length=20)
    faction_seeds: list[str] = Field(default_factory=list, max_length=12)
    tensions: list[str] = Field(default_factory=list, max_length=12)


class WorldLocation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{1,63}$")
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(min_length=1, max_length=1000)


class WorldPublicSections(BaseModel):
    model_config = ConfigDict(extra="forbid")

    locations: list[WorldLocation] = Field(min_length=1, max_length=20)
    factions: list[str] = Field(default_factory=list, max_length=12)
    tensions: list[str] = Field(default_factory=list, max_length=12)


class WorldSecretSection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    secret_plot: str = Field(min_length=1, max_length=4000)


class PregeneratedConnection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    character_name: str = Field(min_length=1, max_length=100)
    relationship: str = Field(min_length=1, max_length=300)


class PregeneratedCharacter(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=100)
    concept: str = Field(min_length=1, max_length=500)
    hook: str = Field(min_length=1, max_length=500)
    biography: str = Field(min_length=20, max_length=3000)
    faction_affiliations: list[str] = Field(default_factory=list, max_length=4)
    connections: list[PregeneratedConnection] = Field(min_length=1, max_length=5)
    traits: list[TraitDraft] = Field(min_length=3, max_length=9)
    flags: list[FlagDraft] = Field(min_length=3, max_length=12)

    @model_validator(mode="after")
    def require_playable_starting_sheet(self) -> PregeneratedCharacter:
        trait_names = [_normalized_identity(trait.name) for trait in self.traits]
        if len(set(trait_names)) != len(trait_names):
            raise ValueError("pregen trait names must be unique")
        aspects = [
            _normalized_identity(aspect) for trait in self.traits for aspect in trait.aspects
        ]
        if len(set(aspects)) != len(aspects):
            raise ValueError("pregen trait aspects must be globally unique")
        if sum(trait.level for trait in self.traits) != 18:
            raise ValueError("pregen trait levels must total exactly 18")
        if not any(flag.type is FlagType.RELATIONSHIP and flag.is_positive for flag in self.flags):
            connection = self.connections[0]
            relationship_flag = FlagDraft(
                text=f"{connection.character_name}: {connection.relationship}",
                type=FlagType.RELATIONSHIP,
                is_positive=True,
            )
            self.flags = (
                [*self.flags, relationship_flag]
                if len(self.flags) < 12
                else [*self.flags[:11], relationship_flag]
            )
        return self


class WorldConsistencyReview(BaseModel):
    model_config = ConfigDict(extra="forbid")

    consistent: bool
    issues: list[str] = Field(default_factory=list, max_length=20)


class WorldDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    premise: str = Field(min_length=20, max_length=3000)
    themes: list[str] = Field(min_length=1, max_length=8)
    locations: list[WorldLocation] = Field(min_length=1, max_length=20)
    factions: list[str] = Field(default_factory=list, max_length=12)
    tensions: list[str] = Field(default_factory=list, max_length=12)
    secret_plot: str = Field(min_length=1, max_length=4000)
    character_templates: list[PregeneratedCharacter] = Field(min_length=3, max_length=6)

    @model_validator(mode="after")
    def require_consistent_cross_references(self) -> WorldDraft:
        location_ids = [location.id for location in self.locations]
        if len(set(location_ids)) != len(location_ids):
            raise ValueError("structured world contains duplicate location ids")

        normalized_factions = [_normalized_identity(faction) for faction in self.factions]
        if len(set(normalized_factions)) != len(normalized_factions):
            raise ValueError("structured world contains duplicate factions")
        faction_names = set(normalized_factions)

        normalized_character_names = [
            _normalized_identity(character.name) for character in self.character_templates
        ]
        if len(set(normalized_character_names)) != len(normalized_character_names):
            raise ValueError("structured world contains duplicate pregen names")
        character_names = set(normalized_character_names)

        for character in self.character_templates:
            unknown_factions = [
                affiliation
                for affiliation in character.faction_affiliations
                if _normalized_identity(affiliation) not in faction_names
            ]
            if unknown_factions:
                raise ValueError(
                    f"pregen {character.name!r} references an unknown faction: "
                    f"{unknown_factions[0]!r}"
                )

            own_name = _normalized_identity(character.name)
            connection_targets = [
                _normalized_identity(connection.character_name)
                for connection in character.connections
            ]
            invalid_target = next(
                (
                    connection.character_name
                    for connection, target in zip(
                        character.connections, connection_targets, strict=True
                    )
                    if target == own_name or target not in character_names
                ),
                None,
            )
            if invalid_target is not None:
                raise ValueError(
                    f"pregen {character.name!r} connection must reference another existing "
                    f"pregen: {invalid_target!r}"
                )
            if len(set(connection_targets)) != len(connection_targets):
                raise ValueError(f"pregen {character.name!r} contains duplicate connections")
        return self


class CreativeWorldDraft(BaseModel):
    """A deliberately loose story pitch passed to the structural worldgen stage."""

    model_config = ConfigDict(extra="forbid")

    module_plot: str = Field(min_length=200, max_length=12000)


def create_world_creative_pipeline(
    completion: CompletionPort,
) -> BoundedJsonPipeline[CreativeWorldDraft]:
    return BoundedJsonPipeline(
        completion=completion,
        output_type=CreativeWorldDraft,
        static_system=(
            "Act as a strong tabletop module author. Invent a compelling playable plot from the "
            "brief, with escalating conflicts, difficult choices, active factions, secrets, and "
            "multiple player-driven paths. Grimdark requests may be harsh and morally difficult, "
            "but must preserve player agency and avoid empty shock for its own sake. Write a raw "
            "creative pitch; do not spend effort matching the final world schema."
        ),
    )


def create_world_structuring_pipeline(
    completion: CompletionPort,
) -> BoundedJsonPipeline[WorldDraft]:
    return BoundedJsonPipeline(
        completion=completion,
        output_type=WorldDraft,
        static_system=(
            "Act as the consistency editor for a tabletop module. Check the supplied raw creative "
            "draft against the original constraints, repair contradictions and dangling "
            "references, then distribute the result into the exact typed world schema. Keep the "
            "strongest ideas while making location ids unique and stable, faction names "
            "consistent, public material usable without exposing the secret plot, and every "
            "secret tied to public clues. Propose the requested number (three to six) of public "
            "fully playable pregenerated characters, honoring any supplied concepts; otherwise "
            "create distinct concepts grounded in the premise. Each pregen must have a biography, "
            "a valid 18-point starting trait sheet with unique trait and aspect names, flags, only "
            "existing faction affiliations, and at least one named positive relationship to "
            "another existing pregen. Mark every flag with is_positive; only positive relationship "
            "flags may set it true. Location ids, faction identities, pregen names, and connection "
            "targets must be internally consistent. Do not assign per-character starting "
            "placement: application code places every character together in the shared opening "
            "scene."
        ),
    )


def create_world_outline_pipeline(
    completion: CompletionPort,
) -> BoundedJsonPipeline[WorldOutline]:
    return BoundedJsonPipeline(
        completion=completion,
        output_type=WorldOutline,
        static_system=(
            "Design a compact tabletop-world outline from the supplied brief. Establish premise, "
            "themes, stable snake-case location ids, faction seeds, and central tensions. Do not "
            "write detailed sections or reveal a secret plot yet."
        ),
    )


def create_world_public_sections_pipeline(
    completion: CompletionPort,
) -> BoundedJsonPipeline[WorldPublicSections]:
    return BoundedJsonPipeline(
        completion=completion,
        output_type=WorldPublicSections,
        static_system=(
            "Expand exactly the supplied outline into public world sections. Preserve every "
            "location id and faction identity. Do not create a secret plot, game session, player "
            "character, or Discord configuration."
        ),
    )


def create_world_secret_pipeline(
    completion: CompletionPort,
) -> BoundedJsonPipeline[WorldSecretSection]:
    return BoundedJsonPipeline(
        completion=completion,
        output_type=WorldSecretSection,
        static_system=(
            "Create only the hidden plot that connects the supplied public world sections. It must "
            "respect their named locations, factions, themes, and tensions."
        ),
    )


def create_world_consistency_pipeline(
    completion: CompletionPort,
) -> BoundedJsonPipeline[WorldConsistencyReview]:
    return BoundedJsonPipeline(
        completion=completion,
        output_type=WorldConsistencyReview,
        static_system=(
            "Critique the complete candidate world for contradictions, dangling references, "
            "identity drift, and conflict between public facts and the secret plot. Mark it "
            "consistent only when it is safe to publish without correction."
        ),
    )
