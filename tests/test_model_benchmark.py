import pytest

import masterclaw.model_benchmark as benchmark
from masterclaw.app.scenarios import CommandId, ScenarioId
from masterclaw.config import ModelConfig, ModelRole, OutputTransport, Settings
from masterclaw.context.assembler import ContextHistory
from masterclaw.context.manifests import PipelineName
from masterclaw.model_benchmark import (
    MODEL_SPECS,
    WORLDGEN_MODEL_SPECS,
    BenchmarkConfigMode,
    BenchmarkSuite,
    ModelSpec,
    Scenario,
    _contains_cyrillic,
    _settings_for_model,
    scenarios,
)
from masterclaw.pipelines.state_decision import command_of, state_decision_type


def _settings() -> Settings:
    return Settings(
        discord_token="test",
        openrouter_api_key="test",
        state_model=ModelConfig(
            model="openrouter/nex-agi/nex-n2-mini",
            temperature=0,
            max_output_tokens=444,
            reasoning_effort="low",
        ),
        reasoning_model=ModelConfig(
            model="openrouter/nex-agi/nex-n2-pro",
            temperature=0.2,
            max_output_tokens=888,
            reasoning_effort="medium",
        ),
        narrative_model=ModelConfig(
            model="openrouter/openai/gpt-5.6-luna",
            temperature=0.7,
            max_output_tokens=777,
            reasoning_effort="low",
            output_transport=OutputTransport.NATIVE_TOOL,
        ),
    )


def test_benchmark_matrix_covers_roles_and_has_unique_models() -> None:
    role_counts = {
        role: sum(scenario.role is role for scenario in scenarios()) for role in ModelRole
    }
    assert role_counts == {
        ModelRole.STATE: 8,
        ModelRole.REASONING: 8,
        ModelRole.NARRATIVE: 3,
        ModelRole.WORLDGEN: 4,
    }
    assert len({spec.slug for spec in MODEL_SPECS}) == len(MODEL_SPECS) == 8
    assert all("grok" not in spec.slug and "qwen" not in spec.slug for spec in MODEL_SPECS)


def test_worldgen_benchmark_has_dedicated_quality_matrix() -> None:
    specs = {spec.slug: spec for spec in WORLDGEN_MODEL_SPECS}
    assert set(specs) == {
        "aion-labs/aion-3.0",
        "aion-labs/aion-3.0-mini",
        "deepseek/deepseek-v4-pro",
        "google/gemini-3-flash-preview",
        "openai/gpt-5.6-luna",
        "qwen/qwen3.7-plus",
    }
    assert all(spec.roles == frozenset({ModelRole.WORLDGEN}) for spec in specs.values())


def test_role_specific_models_are_not_run_outside_their_target_role() -> None:
    specs = {spec.slug: spec for spec in MODEL_SPECS}
    assert specs["moonshotai/kimi-k2.5"].roles == frozenset({ModelRole.NARRATIVE})
    assert specs["minimax/minimax-m2-her"].roles == frozenset({ModelRole.NARRATIVE})
    assert ModelRole.NARRATIVE not in specs["openai/gpt-5.4-nano"].roles


def test_cyrillic_detection() -> None:
    assert _contains_cyrillic("Дверь открылась.")
    assert not _contains_cyrillic("The door opened.")


def test_benchmark_covers_every_typed_pipeline_and_history() -> None:
    matrix = scenarios()
    two_stage_routes = {
        PipelineName.OUTCOME_NARRATION_REVIEW,
        PipelineName.WORLD_CREATIVE,
        PipelineName.WORLD_STRUCTURING,
        PipelineName.RESERVE_RECOVERY,
        PipelineName.WORLD_INTAKE,
        PipelineName.SCENE_QUESTION,
        PipelineName.RULES_QUESTION,
        PipelineName.ROLEPLAY_REPLY,
        PipelineName.ADVANCEMENT_INTAKE,
        PipelineName.GAME_CONFIGURATION,
        PipelineName.ROLL_CONFIRMATION,
    }
    assert {scenario.pipeline_name for scenario in matrix} == set(PipelineName) - two_stage_routes
    continuity = next(
        scenario
        for scenario in matrix
        if scenario.name == "narrative.continuity_respects_recent_event"
    )
    assert continuity.history is not None
    assert continuity.history.domain_events


def test_production_benchmark_config_preserves_role_parameters() -> None:
    base = _settings()
    configured = _settings_for_model(base, MODEL_SPECS[1])
    for role in ModelRole:
        source = base.model_for(role)
        target = configured.model_for(role)
        assert target.model == MODEL_SPECS[1].model_id
        assert target.temperature == source.temperature
        assert target.max_output_tokens == source.max_output_tokens
        assert target.reasoning_effort == source.reasoning_effort
        assert target.output_transport == source.output_transport


def test_fixed_benchmark_config_is_explicit_and_deterministic() -> None:
    configured = _settings_for_model(_settings(), MODEL_SPECS[1], BenchmarkConfigMode.FIXED)
    assert all(configured.model_for(role).temperature == 0 for role in ModelRole)
    assert configured.reasoning_model.reasoning_effort == "low"
    assert configured.narrative_model.output_transport is OutputTransport.PROMPT_JSON


def test_adversarial_and_rare_pipeline_scenarios_are_present() -> None:
    names = {scenario.name for scenario in scenarios()}
    assert {
        "state.narration_review_minor_in_scope",
        "state.narration_review_minor_overreach",
        "state.narration_review_rejects_control_of_other_pc",
        "reasoning.action_multi_actor_no_borrowed_trait",
        "reasoning.action_declares_for_other_pc",
        "reasoning.action_resists_difficulty_injection",
        "reasoning.compound_question_then_conditional_action",
        "state.advancement_resists_self_assessment_override",
        "worldgen.grimdark_outline_has_playable_pressure",
        "worldgen.public_sections_preserve_outline",
        "worldgen.secret_plot_uses_public_world",
        "worldgen.consistency_rejects_public_secret_conflict",
        "reasoning.character_creation_matches_concept",
    } <= names


@pytest.mark.asyncio
async def test_repeats_and_history_are_reported_without_live_calls(monkeypatch) -> None:
    history = ContextHistory(domain_events=[{"event_type": "scene_patch"}])
    scenario = Scenario(
        name="state.offline",
        role=ModelRole.STATE,
        pipeline_name=PipelineName.INTENT_CLASSIFICATION,
        task="Classify.",
        context={"mode": "play", "pending_interaction": None},
        history=history,
        pipeline_factory=lambda _port: _FakePipeline(),
        evaluate=lambda value: {"action": command_of(value) is CommandId.DECLARE_ACTION},
    )
    observed_history = []

    class FakeAssembler:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def assemble(self, _manifest, _context, *, history=None):
            observed_history.append(history)
            return object()

    class FakeRegistry:
        def __init__(self, _settings) -> None:
            pass

    class FakePort:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def metrics_snapshot(self):
            return {
                "calls": 1,
                "prompt_tokens": 10,
                "completion_tokens": 2,
                "reasoning_tokens": 0,
                "cache_read_tokens": 0,
                "cost": 0.0,
            }

    monkeypatch.setattr(benchmark, "MODEL_SPECS", (ModelSpec("test/model", 0, 0),))
    monkeypatch.setattr(benchmark, "scenarios", lambda: (scenario,))
    monkeypatch.setattr(benchmark, "ContextAssembler", FakeAssembler)
    monkeypatch.setattr(benchmark, "OpenHandsLLMRegistry", FakeRegistry)
    monkeypatch.setattr(benchmark, "OpenHandsCompletionPort", FakePort)

    report = await benchmark.run_benchmark(
        _settings(), transports=(OutputTransport.PROMPT_JSON,), repeats=3
    )

    assert observed_history == [history, history, history]
    assert report["attempt_count"] == 3
    assert [row["attempt"] for row in report["results"]] == [1, 2, 3]
    assert report["scenario_summaries"][0]["stable"] is True
    assert report["scenario_summaries"][0]["passed"] == 3
    assert report["scenario_summaries"][0]["attempts"] == 3


def test_benchmark_suites_do_not_mix_worldgen_with_frequent_roles() -> None:
    core = benchmark._scenarios_for_suite(BenchmarkSuite.CORE)
    worldgen = benchmark._scenarios_for_suite(BenchmarkSuite.WORLDGEN)
    assert core and all(scenario.role is not ModelRole.WORLDGEN for scenario in core)
    assert worldgen and all(scenario.role is ModelRole.WORLDGEN for scenario in worldgen)


class _FakePipeline:
    async def run(self, *, task, context):
        assert task == "Classify."
        assert context is not None
        return state_decision_type(ScenarioId.PLAY)(
            command=CommandId.DECLARE_ACTION,
            argument=None,
            confidence=1,
            evidence="declaration",
        )
