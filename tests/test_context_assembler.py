from dataclasses import replace
from pathlib import Path

import pytest

from masterclaw.config import ModelRole
from masterclaw.context.assembler import ContextAssembler, ContextAssemblyError, ContextHistory
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
            "public_world_context": {"premise": "a public truth"},
            "undeclared_state": {"killer": "hidden"},
        },
    )
    assert "closed door" in context.dynamic_context
    assert "killer" not in context.dynamic_context
    assert "a public truth" in context.dynamic_context
    assert "secret_plot" not in context.dynamic_context
    assert "Classify whether" in context.static_rules
    assert context.output_token_budget == manifest.output_token_budget
    assert "closed door" not in context.static_rules
    assert set(context.fragments) == {
        "declaration_validation",
        "difficulty",
        "equipment",
        "core_mechanics",
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
                "scenario": {"id": "play", "allowed_commands": ["clarify"]},
                "pending_interaction": None,
                "world_workspace": None,
            },
        )


def test_context_includes_only_manifest_bounded_recent_history() -> None:
    manifest = manifest_for(PipelineName.OUTCOME_NARRATION)
    context = ContextAssembler(PROMPTS).assemble(
        manifest,
        {
            "session_brief": {},
            "actor_character": {"player_id": "alice", "name": "Hero"},
            "current_scene": {},
            "public_world_context": {},
            "roll_result": {},
        },
        history=ContextHistory(
            domain_events=[{"sequence": number} for number in range(6)],
            chat_messages=[{"content": "old"}, {"content": "latest"}],
        ),
    )
    assert '"sequence":2' in context.dynamic_context
    assert '"sequence":1' not in context.dynamic_context
    assert "latest" in context.dynamic_context
    assert "old" not in context.dynamic_context


def test_live_narrator_rights_fragment_defines_every_configured_level() -> None:
    context = ContextAssembler(PROMPTS).assemble(
        manifest_for(PipelineName.PLAYER_NARRATION_REVIEW),
        {
            "session_brief": {"locale": "en"},
            "actor_character": {"player_id": "alice", "name": "Hero"},
            "current_scene": {},
            "public_world_context": {},
            "roll_result": {},
            "submitted_narration": "",
        },
    )
    for level in ("disabled", "minor", "significant", "madness"):
        assert f"`{level}`" in context.static_rules
    assert "`gm_automatic`" in context.static_rules


def test_rules_question_receives_complete_canonical_core_mechanics() -> None:
    context = ContextAssembler(PROMPTS).assemble(
        manifest_for(PipelineName.RULES_QUESTION),
        {
            "session_brief": {"locale": "en"},
            "player_question": "How do pools, hits, reserve, and help work?",
        },
    )

    rules = context.static_rules
    assert "Each applicable trait contributes exactly one die" in rules
    assert "Every d6 result of 4, 5, or 6 is one hit" in rules
    assert "Reserve starts at 7 and cannot exceed 7" in rules
    assert "explicitly contributes exactly one die" in rules
    assert "unreduced base difficulty is an integer from 2 through 7" in rules


def test_context_budget_uses_configured_model_tokenizer(monkeypatch) -> None:
    import litellm

    monkeypatch.setattr(litellm, "token_counter", lambda **kwargs: 37)
    context = ContextAssembler(
        PROMPTS,
        model_ids={ModelRole.STATE: "openrouter/example/model"},
    ).assemble(
        manifest_for(PipelineName.INTENT_CLASSIFICATION),
        {
            "mode": "play",
            "scenario": {"id": "play", "allowed_commands": ["clarify"]},
            "pending_interaction": None,
            "world_workspace": None,
        },
    )
    assert context.estimated_tokens == 37


def test_context_budget_degrades_history_and_long_projection_before_failing() -> None:
    manifest = replace(
        manifest_for(PipelineName.INTENT_CLASSIFICATION),
        state_projections=("mode",),
        recent_domain_events=5,
        recent_chat_messages=5,
        input_token_budget=800,
    )
    context = ContextAssembler(PROMPTS).assemble(
        manifest,
        {"mode": {"description": "x" * 5000}},
        history=ContextHistory(
            domain_events=[{"sequence": index, "text": "e" * 500} for index in range(5)],
            chat_messages=[{"sequence": index, "text": "c" * 500} for index in range(5)],
        ),
    )
    assert context.degradations == (
        "chat_messages:1",
        "domain_events:1",
        "projection_strings:1000",
    )
    assert '"sequence":4' in context.dynamic_context
    assert '"sequence":3' not in context.dynamic_context


def test_projection_truncation_preserves_latest_later_wins_revision() -> None:
    value = (
        "Original premise that must remain recognizable.\n"
        + "middle " * 500
        + "\nLater player revision (takes precedence): latest tone is hopeful."
    )

    truncated = ContextAssembler._truncate_projection(value, max_string_chars=1000)

    assert isinstance(truncated, str)
    assert len(truncated) <= 1000
    assert "Original premise" in truncated
    assert "latest tone is hopeful" in truncated
    assert "[middle omitted]" in truncated
