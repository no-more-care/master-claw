from __future__ import annotations

import asyncio

from masterclaw.adapters.openhands import OpenHandsCompletionPort, OpenHandsLLMRegistry
from masterclaw.config import ModelRole, Settings
from masterclaw.pipelines.consequence import create_consequence_pipeline
from masterclaw.pipelines.intent import create_intent_pipeline
from masterclaw.pipelines.narrative import create_narrative_pipeline


async def run_model_smoke(settings: Settings) -> dict[str, object]:
    registry = OpenHandsLLMRegistry(settings)
    intent = await create_intent_pipeline(OpenHandsCompletionPort(registry, ModelRole.STATE)).run(
        task="Classify: I inspect the locked door.",
        dynamic_context='{"mode":"play","pending_interaction":null}',
    )
    consequence = await create_consequence_pipeline(
        OpenHandsCompletionPort(registry, ModelRole.REASONING)
    ).run(
        task="Produce no state change for a character merely looking at a door.",
        dynamic_context=(
            '{"current_scene":{"facts":["door is locked"]},'
            '"outcome_source":{"kind":"automatic","declaration":"look"},'
            '"narrator_rights":"gm_automatic"}'
        ),
    )
    narrative = await create_narrative_pipeline(
        OpenHandsCompletionPort(registry, ModelRole.NARRATIVE)
    ).run(
        task="Narrate a character noticing a locked door without opening it.",
        dynamic_context='{"locale":"en","facts":["door is locked"]}',
    )
    return {
        "state": intent.model_dump(mode="json"),
        "reasoning": consequence.model_dump(mode="json"),
        "narrative": narrative.model_dump(mode="json"),
    }


def run(settings: Settings) -> dict[str, object]:
    return asyncio.run(run_model_smoke(settings))
