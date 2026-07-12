from dataclasses import replace
from pathlib import Path

import pytest

from masterclaw.context.assembler import ContextAssembler, ContextAssemblyError
from masterclaw.context.manifests import PipelineName, manifest_for

PROMPTS = Path(__file__).parents[1] / "prompts"


def test_context_contains_only_declared_fragments_and_projections() -> None:
    manifest = manifest_for(PipelineName.ACTION_INTERPRETATION)
    context = ContextAssembler(PROMPTS).assemble(
        manifest,
        {
            "session_brief": {"scene": "gate"},
            "actor_character": {"name": "Hero"},
            "current_scene": {"facts": ["closed door"]},
            "secret_plot": {"killer": "hidden"},
        },
    )
    assert "closed door" in context.text
    assert "killer" not in context.text
    assert {fragment for fragment, _ in context.fragment_versions} == {
        "declaration_validation",
        "difficulty",
        "equipment",
    }


def test_missing_projection_fails_closed() -> None:
    with pytest.raises(ContextAssemblyError, match="missing state projections"):
        ContextAssembler(PROMPTS).assemble(
            manifest_for(PipelineName.INTENT_CLASSIFICATION), {"mode": "play"}
        )


def test_token_budget_is_enforced_before_llm_call() -> None:
    manifest = replace(manifest_for(PipelineName.INTENT_CLASSIFICATION), input_token_budget=1)
    with pytest.raises(ContextAssemblyError, match="budget exceeded"):
        ContextAssembler(PROMPTS).assemble(
            manifest,
            {
                "mode": "play",
                "pending_interaction": None,
            },
        )
