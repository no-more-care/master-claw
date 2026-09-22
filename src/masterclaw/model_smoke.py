from __future__ import annotations

import asyncio

from masterclaw.adapters.openhands import OpenHandsCompletionPort, OpenHandsLLMRegistry
from masterclaw.app.scenarios import SCENARIOS, CommandId, ScenarioId
from masterclaw.config import ModelRole, Settings
from masterclaw.context.assembler import AssembledContext, ContextAssembler
from masterclaw.context.manifests import PipelineName, manifest_for, state_decision_manifest
from masterclaw.domain.mechanics import OutcomeAuthority
from masterclaw.pipelines.action import create_action_pipeline
from masterclaw.pipelines.compound_play import create_compound_play_pipeline
from masterclaw.pipelines.consequence import create_consequence_pipeline
from masterclaw.pipelines.narrative import create_narrative_pipeline
from masterclaw.pipelines.state_decision import command_of, create_state_decision_pipeline
from masterclaw.pipelines.world_intake import WorldCreationBrief, create_world_intake_pipeline
from masterclaw.pipelines.worldgen import (
    create_world_creative_pipeline,
    create_world_structuring_pipeline,
)


class ModelSmokeValidationError(RuntimeError):
    """A typed provider response passed schema validation but missed the smoke semantics."""


def validate_world_intake_smoke(result: WorldCreationBrief) -> None:
    """Reject a schema-valid intake that drops an explicit smoke-request setting."""

    specified = set(result.specified_fields)
    required_fields = {
        "genre",
        "tone",
        "scale",
        "player_role",
        "locale",
        "pregenerated_character_count",
    }
    missing_provenance = sorted(required_fields - specified)
    if missing_provenance:
        raise ModelSmokeValidationError(
            f"world_intake dropped explicit specified_fields: {', '.join(missing_provenance)}"
        )
    normalized_role = (result.player_role or "").casefold()
    if "storm" not in normalized_role or "courier" not in normalized_role:
        raise ModelSmokeValidationError("world_intake dropped explicit player_role")
    if result.locale != "en":
        raise ModelSmokeValidationError("world_intake did not preserve explicit locale")
    if result.pregenerated_character_count != 3:
        raise ModelSmokeValidationError("world_intake did not preserve explicit pregen count")


def assemble_model_smoke_contexts(settings: Settings) -> dict[str, AssembledContext]:
    """Assemble every live-smoke context without constructing an API client."""
    assembler = ContextAssembler(
        settings.prompt_path,
        model_ids={role: settings.model_for(role).model for role in ModelRole},
    )
    actor_character = {
        "character_id": "smoke-character",
        "revision": 0,
        "name": "Mara",
        "traits": [
            {
                "name": "Force",
                "level": 2,
                "aspects": ["Shoulder charge", "Leverage"],
            }
        ],
        "conditions": [],
        "plot_items": [],
        "temporary_bonuses": [],
    }
    current_scene = {
        "scene_id": "smoke-scene",
        "revision": 0,
        "facts": ["door is locked"],
    }
    session_brief = {
        "locale": "en",
        "narrator_rights_level": "minor",
    }
    public_world_context = {
        "premise": "Storm couriers keep a city alive above an endless storm.",
    }
    world_constraints = {
        "brief": (
            "A mysterious fantasy city suspended above an endless storm. "
            "Players are storm couriers protecting its fragile districts."
        ),
        "world_id": "smoke-world",
        "title": "Stormglass",
        "confirmed_settings": {
            "locale": "en",
            "genre": "fantasy",
            "tone": "mysterious",
            "scale": "one city",
            "player_role": "storm couriers",
            "pregenerated_character_count": 3,
        },
    }
    authority = OutcomeAuthority.GM_AUTOMATIC
    return {
        "state": assembler.assemble(
            state_decision_manifest(
                context_projections=SCENARIOS[ScenarioId.PLAY].context_projections,
                recent_chat_messages=SCENARIOS[ScenarioId.PLAY].recent_chat_messages,
            ),
            {
                "mode": {"value": "play"},
                "scenario": {
                    "id": ScenarioId.PLAY.value,
                    "allowed_commands": sorted(
                        command.value for command in SCENARIOS[ScenarioId.PLAY].llm_commands
                    ),
                },
                "session_brief": session_brief,
                "current_scene": current_scene,
                "actor_character": actor_character,
            },
        ),
        "action": assembler.assemble(
            manifest_for(PipelineName.ACTION_INTERPRETATION),
            {
                "session_brief": session_brief,
                "actor_character": actor_character,
                "current_scene": current_scene,
                "public_world_context": public_world_context,
            },
        ),
        "compound": assembler.assemble(
            manifest_for(PipelineName.COMPOUND_PLAY),
            {
                "session_brief": session_brief,
                "actor_character": actor_character,
                "current_scene": current_scene,
                "player_request": (
                    "I ask whether the door bears a guild mark, then I inspect its lock."
                ),
            },
        ),
        "reasoning": assembler.assemble(
            manifest_for(PipelineName.CONSEQUENCE_PLANNING),
            {
                "current_scene": current_scene,
                "gm_world_context": {},
                "actor_character": actor_character,
                "allowed_scenes": [current_scene],
                "outcome_source": {"kind": "automatic", "declaration": "look"},
                "narrator_rights": authority.value,
                "narrator_rights_policy": {
                    "level": "minor",
                    "authority": authority.value,
                    "actor_only": True,
                    "minor_fact_changes": 1,
                    "minor_rewards": 1,
                    "significant_fact_changes": 2,
                    "significant_npc_changes": 1,
                    "significant_plot_item_changes": 1,
                    "significant_thread_changes": 1,
                    "significant_can_move_actor": False,
                    "significant_can_remove_npc": False,
                    "failure_can_grant_positive_bonus": False,
                },
            },
        ),
        "narrative": assembler.assemble(
            manifest_for(PipelineName.OUTCOME_NARRATION),
            {
                "session_brief": session_brief,
                "actor_character": actor_character,
                "current_scene": current_scene,
                "public_world_context": public_world_context,
                "roll_result": {"resolution": "automatic", "declaration": "look"},
            },
        ),
        "world_intake": assembler.assemble(
            manifest_for(PipelineName.WORLD_INTAKE),
            {
                "player_request": (
                    "Create a world with locale English; genre fantasy; tone mysterious; "
                    "scale one floating city; player role storm couriers; and exactly three "
                    "pregenerated characters."
                ),
            },
        ),
        "world_creative": assembler.assemble(
            manifest_for(PipelineName.WORLD_CREATIVE),
            {
                "world_constraints": world_constraints,
            },
        ),
    }


async def run_model_smoke(settings: Settings) -> dict[str, object]:
    contexts = assemble_model_smoke_contexts(settings)
    registry = OpenHandsLLMRegistry(settings)
    state_decision = await create_state_decision_pipeline(
        OpenHandsCompletionPort(registry, ModelRole.STATE), SCENARIOS[ScenarioId.PLAY]
    ).run(
        task="Choose the scenario command: I inspect the locked door.",
        context=contexts["state"],
    )
    reasoning_port = OpenHandsCompletionPort(registry, ModelRole.REASONING)
    action = await create_action_pipeline(reasoning_port).run(
        task="Classify this declaration: I carefully force the locked door before the guard comes.",
        context=contexts["action"],
    )
    compound = await create_compound_play_pipeline(reasoning_port).run(
        task="Decompose the supplied compound request without resolving it.",
        context=contexts["compound"],
    )
    consequence = await create_consequence_pipeline(reasoning_port).run(
        task="Produce no state change for a character merely looking at a door.",
        context=contexts["reasoning"],
    )
    narrative = await create_narrative_pipeline(
        OpenHandsCompletionPort(registry, ModelRole.NARRATIVE)
    ).run(
        task="Narrate a character noticing a locked door without opening it.",
        context=contexts["narrative"],
    )
    world_intake = await create_world_intake_pipeline(reasoning_port).run(
        task="Normalize the supplied current world-creation request without inventing settings.",
        context=contexts["world_intake"],
    )
    creative = await create_world_creative_pipeline(
        OpenHandsCompletionPort(
            registry,
            ModelRole.WORLDGEN,
            model_config=settings.worldgen_creative_model,
        )
    ).run(
        task="Write the raw creative plot for this module.",
        context=contexts["world_creative"],
    )
    structuring_context = ContextAssembler(
        settings.prompt_path,
        model_ids={role: settings.model_for(role).model for role in ModelRole},
    ).assemble(
        manifest_for(PipelineName.WORLD_STRUCTURING),
        {
            "world_constraints": {
                "brief": (
                    "A mysterious fantasy city suspended above an endless storm. "
                    "Players are storm couriers protecting its fragile districts."
                ),
                "world_id": "smoke-world",
                "title": "Stormglass",
                "confirmed_settings": {
                    "locale": "en",
                    "genre": "fantasy",
                    "tone": "mysterious",
                    "scale": "one city",
                    "player_role": "storm couriers",
                    "pregenerated_character_count": 3,
                },
            },
            "creative_draft": creative.module_plot,
        },
    )
    worldgen = await create_world_structuring_pipeline(
        OpenHandsCompletionPort(registry, ModelRole.WORLDGEN)
    ).run(
        task=(
            "Check the raw plot for consistency and convert it into the complete world "
            "JSON contract."
        ),
        context=structuring_context,
    )
    validate_world_intake_smoke(world_intake)
    state_command = command_of(state_decision)
    if state_command is not CommandId.DECLARE_ACTION:
        raise ModelSmokeValidationError(
            f"state role misclassified the smoke action as {state_command.value}"
        )
    if action.resolution != "roll" or action.difficulty is None:
        raise ModelSmokeValidationError("reasoning role did not produce the expected roll")
    compound_kinds = [part.kind.value for part in compound.parts]
    if compound_kinds != ["scene_question", "action"]:
        raise ModelSmokeValidationError("compound role lost or reordered the smoke parts")
    if any(
        (
            consequence.add_facts,
            consequence.remove_facts,
            consequence.add_actor_conditions,
            consequence.remove_actor_conditions,
            consequence.add_actor_plot_items,
            consequence.remove_actor_plot_items,
            consequence.move_actor_to_scene_id,
            consequence.upsert_scene_npcs,
            consequence.remove_scene_npc_ids,
            consequence.open_threads,
            consequence.close_threads,
            consequence.grant_temporary_bonus,
            consequence.reveal_secret_ids,
        )
    ):
        raise ModelSmokeValidationError("consequence role invented state for a passive look")
    if not narrative.narrative.strip():
        raise ModelSmokeValidationError("narrative role returned blank prose")
    if len(worldgen.locations) < 1 or len(worldgen.character_templates) != 3:
        raise ModelSmokeValidationError("worldgen role missed the requested world shape")
    return {
        "state": state_decision.model_dump(mode="json"),
        "action": action.model_dump(mode="json"),
        "compound": compound.model_dump(mode="json"),
        "reasoning": consequence.model_dump(mode="json"),
        "narrative": narrative.model_dump(mode="json"),
        "world_intake": world_intake.model_dump(mode="json"),
        "worldgen": {
            "creative_pitch_characters": len(creative.module_plot),
            "locations": len(worldgen.locations),
            "factions": len(worldgen.factions),
            "pregenerated_characters": len(worldgen.character_templates),
        },
    }


def run(settings: Settings) -> dict[str, object]:
    return asyncio.run(run_model_smoke(settings))
