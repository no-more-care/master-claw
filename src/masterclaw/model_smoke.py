from __future__ import annotations

import asyncio

from masterclaw.adapters.openhands import OpenHandsCompletionPort, OpenHandsLLMRegistry
from masterclaw.app.scenarios import SCENARIOS, ScenarioId
from masterclaw.config import ModelRole, Settings
from masterclaw.context.assembler import ContextAssembler
from masterclaw.context.manifests import PipelineName, manifest_for, state_decision_manifest
from masterclaw.domain.mechanics import OutcomeAuthority
from masterclaw.pipelines.consequence import create_consequence_pipeline
from masterclaw.pipelines.narrative import create_narrative_pipeline
from masterclaw.pipelines.state_decision import create_state_decision_pipeline
from masterclaw.pipelines.worldgen import create_world_outline_pipeline


async def run_model_smoke(settings: Settings) -> dict[str, object]:
    registry = OpenHandsLLMRegistry(settings)
    assembler = ContextAssembler(
        settings.prompt_path,
        model_ids={role: settings.model_for(role).model for role in ModelRole},
    )
    state_decision = await create_state_decision_pipeline(
        OpenHandsCompletionPort(registry, ModelRole.STATE), SCENARIOS[ScenarioId.PLAY]
    ).run(
        task="Choose the scenario command: I inspect the locked door.",
        context=assembler.assemble(
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
                "current_scene": {"facts": ["door is locked"]},
                "actor_character": {"name": "Mara", "traits": []},
            },
        ),
    )
    consequence = await create_consequence_pipeline(
        OpenHandsCompletionPort(registry, ModelRole.REASONING)
    ).run(
        task="Produce no state change for a character merely looking at a door.",
        context=assembler.assemble(
            manifest_for(PipelineName.CONSEQUENCE_PLANNING),
            {
                "current_scene": {"facts": ["door is locked"]},
                "gm_world_context": {},
                "outcome_source": {"kind": "automatic", "declaration": "look"},
                "narrator_rights": OutcomeAuthority.GM_AUTOMATIC.value,
            },
        ),
    )
    narrative = await create_narrative_pipeline(
        OpenHandsCompletionPort(registry, ModelRole.NARRATIVE)
    ).run(
        task="Narrate a character noticing a locked door without opening it.",
        context=assembler.assemble(
            manifest_for(PipelineName.OUTCOME_NARRATION),
            {
                "session_brief": {"locale": "en"},
                "current_scene": {"facts": ["door is locked"]},
                "gm_world_context": {},
                "roll_result": {"resolution": "automatic", "declaration": "look"},
            },
        ),
    )
    worldgen = await create_world_outline_pipeline(
        OpenHandsCompletionPort(registry, ModelRole.WORLDGEN)
    ).run(
        task="Create a compact world outline for a city above an endless storm.",
        context=assembler.assemble(
            manifest_for(PipelineName.WORLD_SECTION),
            {
                "world_outline": {},
                "world_sections": {},
                "world_constraints": {"brief": "A city above an endless storm."},
                "target_section": "outline",
            },
        ),
    )
    return {
        "state": state_decision.model_dump(mode="json"),
        "reasoning": consequence.model_dump(mode="json"),
        "narrative": narrative.model_dump(mode="json"),
        "worldgen": worldgen.model_dump(mode="json"),
    }


def run(settings: Settings) -> dict[str, object]:
    return asyncio.run(run_model_smoke(settings))
