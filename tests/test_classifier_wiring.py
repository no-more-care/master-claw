import asyncio

import pytest
from pydantic import ValidationError

from masterclaw.adapters.discord_bot import DiscordIngressClient
from masterclaw.adapters.jev import JevClassifier
from masterclaw.classifiers.base import ClassifierConfigurationError
from masterclaw.classifiers.policy import ClassifierMode, ClassifierUseCase
from masterclaw.config import Settings
from masterclaw.runtime.composition import create_semantic_classifier
from masterclaw.runtime.resources import RuntimeResources


def settings(**kwargs):
    model = {"model": "openrouter/test/model"}
    return Settings(
        **(
            {
                "_env_file": None,
                "discord_token": "test",
                "openrouter_api_key": "test",
                "state_model": model,
                "reasoning_model": model,
                "narrative_model": model,
            }
            | kwargs
        )
    )


def test_classifier_off_needs_no_additional_credentials(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    configured = settings()
    assert configured.classifier.mode is ClassifierMode.OFF
    assert create_semantic_classifier(configured) is None


def test_nested_classifier_environment_config_and_wiring(monkeypatch):
    monkeypatch.setenv("MASTERCLAW_CLASSIFIER__MODE", "shadow")
    monkeypatch.setenv("MASTERCLAW_CLASSIFIER__PROVIDER", "jev")
    monkeypatch.setenv("MASTERCLAW_CLASSIFIER__MODEL", "typesafe/jev-1.13")
    monkeypatch.setenv("MASTERCLAW_CLASSIFIER__THRESHOLD", "0.98")
    monkeypatch.setenv("MASTERCLAW_CLASSIFIER__TIMEOUT_SECONDS", "2.5")
    configured = settings()
    assert configured.classifier.mode is ClassifierMode.SHADOW
    assert configured.classifier.model == "typesafe/jev-1.13"
    assert configured.classifier.threshold == 0.98
    assert configured.classifier.timeout_seconds == 2.5
    runtime = create_semantic_classifier(configured)
    assert isinstance(runtime.port, JevClassifier)


def test_classifier_key_alias_supports_dotenv_without_changing_default_mode(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text("MASTERCLAW_CLASSIFIER_API_KEY=dotenv-test-key\n", encoding="utf-8")
    model = {"model": "openrouter/test/model"}
    configured = Settings(
        _env_file=env_file,
        discord_token="test",
        openrouter_api_key="test",
        state_model=model,
        reasoning_model=model,
        narrative_model=model,
    )
    assert configured.classifier_api_key.get_secret_value() == "dotenv-test-key"
    assert "dotenv-test-key" not in repr(configured)
    assert create_semantic_classifier(configured) is None


def test_wiring_uses_existing_key_with_explicit_prefixed_override(monkeypatch):
    captured = {}

    def factory(**kwargs):
        captured.update(kwargs)
        return object()

    monkeypatch.setattr("masterclaw.adapters.jev.JevClassifier", factory)
    create_semantic_classifier(settings(classifier={"mode": "shadow"}))
    assert captured["api_key"].get_secret_value() == "test"
    monkeypatch.setenv("MASTERCLAW_CLASSIFIER_API_KEY", "dedicated")
    create_semantic_classifier(settings(classifier={"mode": "shadow"}))
    assert captured["api_key"].get_secret_value() == "dedicated"


@pytest.mark.parametrize("override", [None, ""])
def test_shadow_credentials_fail_at_wiring(override):
    with pytest.raises(ClassifierConfigurationError, match="credentials unavailable"):
        create_semantic_classifier(
            settings(
                openrouter_api_key="   ",
                classifier_api_key=override,
                classifier={"mode": "shadow"},
            )
        )


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("MODE", "active"),
        ("PROVIDER", "unknown"),
        ("TIMEOUT_SECONDS", "0"),
        ("THRESHOLD", "1.1"),
        ("THRESHOLD", "nan"),
    ],
)
def test_bad_nested_config_is_rejected(monkeypatch, key, value):
    monkeypatch.setenv(f"MASTERCLAW_CLASSIFIER__{key}", value)
    with pytest.raises(ValidationError):
        settings()


def test_ingress_shutdown_cancels_work_then_closes_classifier_resources_once():
    order = []

    async def close_resources():
        order.append("resources_closed")

    async def work():
        try:
            await asyncio.sleep(60)
        finally:
            order.append("work_cancelled")

    async def run():
        client = DiscordIngressClient(
            store=object(),
            orchestrator=object(),
            close_resources=close_resources,
        )
        client._scheduled["channel"] = asyncio.create_task(work())
        await asyncio.sleep(0)
        await client.close()
        await client.close()

    asyncio.run(run())
    assert order == ["work_cancelled", "resources_closed"]


def test_nested_use_case_overrides_and_independent_concurrency(monkeypatch):
    monkeypatch.setenv("MASTERCLAW_CLASSIFIER__MODE", "shadow")
    monkeypatch.setenv("MASTERCLAW_CLASSIFIER__THRESHOLD", "0.98")
    monkeypatch.setenv("MASTERCLAW_CLASSIFIER__STATE_DISPATCH__TIMEOUT_SECONDS", "7")
    monkeypatch.setenv("MASTERCLAW_CLASSIFIER__MAX_CONCURRENCY", "2")
    configured = settings(llm_max_concurrency=17)
    state = configured.classifier.for_use_case(ClassifierUseCase.STATE_DISPATCH)
    assert state.mode is ClassifierMode.SHADOW
    assert state.threshold == 0.98
    assert state.timeout_seconds == 7
    assert configured.classifier.for_use_case(ClassifierUseCase.ACTION).mode is ClassifierMode.OFF
    assert (
        configured.classifier.for_use_case(ClassifierUseCase.ADVANCEMENT).mode is ClassifierMode.OFF
    )
    captured = {}

    def factory(**kwargs):
        captured.update(kwargs)
        return object()

    monkeypatch.setattr("masterclaw.adapters.jev.JevClassifier", factory)
    create_semantic_classifier(configured)
    assert captured["max_concurrency"] == 2
    assert captured["timeout_seconds"] == 7


def test_explicit_state_off_overrides_flat_shadow_without_creating_backend(monkeypatch):
    def forbidden_factory(**kwargs):
        raise AssertionError("off must not construct a backend")

    monkeypatch.setattr("masterclaw.adapters.jev.JevClassifier", forbidden_factory)
    configured = settings(
        openrouter_api_key="",
        classifier={
            "mode": "shadow",
            "state_dispatch": {"mode": "off"},
        },
    )
    assert create_semantic_classifier(configured) is None


def test_ingress_closes_resource_bundle_once():
    closed = []

    class Resource:
        async def aclose(self):
            closed.append("closed")

    async def run():
        client = DiscordIngressClient(
            store=object(),
            orchestrator=object(),
            resources=RuntimeResources(Resource()),
        )
        await client.close()
        await client.close()

    asyncio.run(run())
    assert closed == ["closed"]


def test_advancement_thresholds_are_independent_nested_settings(monkeypatch):
    monkeypatch.setenv("MASTERCLAW_CLASSIFIER__THRESHOLD", "0.99")
    monkeypatch.setenv("MASTERCLAW_CLASSIFIER__ADVANCEMENT__MODE", "shadow")
    monkeypatch.setenv("MASTERCLAW_CLASSIFIER__ADVANCEMENT__ALLOW_THRESHOLD", "0.85")
    monkeypatch.setenv("MASTERCLAW_CLASSIFIER__ADVANCEMENT__DENY_THRESHOLD", "0.15")
    configured = settings()
    assert configured.classifier.advancement.mode is ClassifierMode.SHADOW
    assert configured.classifier.advancement.allow_threshold == 0.85
    assert configured.classifier.advancement.deny_threshold == 0.15
    assert configured.classifier.for_use_case(ClassifierUseCase.STATE_DISPATCH).threshold == 0.99


@pytest.mark.parametrize("state_mode", ["off", "shadow"])
@pytest.mark.parametrize("action_mode", ["off", "shadow"])
@pytest.mark.parametrize("narration_mode", ["off", "shadow"])
@pytest.mark.parametrize("reserve_mode", ["off", "shadow"])
@pytest.mark.parametrize("outcome_mode", ["off", "shadow"])
def test_cli_state_and_advancement_share_executor_and_close_one_backend(
    tmp_path, monkeypatch, state_mode, action_mode, narration_mode, reserve_mode, outcome_mode
):
    import masterclaw.cli as cli

    captured = {}
    closes = []

    class Backend:
        async def classify(self, request):
            raise AssertionError("composition must not invoke a classifier")

        async def aclose(self):
            closes.append("backend")

    monkeypatch.setattr("masterclaw.adapters.jev.JevClassifier", lambda **kwargs: Backend())
    monkeypatch.setattr(cli, "OpenHandsLLMRegistry", lambda settings: object())
    monkeypatch.setattr(cli, "_service_completion", lambda *args, **kwargs: object())

    def application(**kwargs):
        captured.update(kwargs)
        return object()

    class Client:
        def __init__(self, **kwargs):
            self.resources = kwargs["resources"]

        def run(self, *args, **kwargs):
            async def close():
                await self.resources.aclose()
                await self.resources.aclose()

            asyncio.run(close())

    monkeypatch.setattr(cli, "MessageApplication", application)
    monkeypatch.setattr(cli, "DiscordIngressClient", Client)
    configured = settings(
        database_path=str(tmp_path / "db.sqlite3"),
        classifier={
            "mode": state_mode,
            "advancement": {"mode": "shadow"},
            "action_capability": {"mode": action_mode},
            "player_narration_rights": {"mode": narration_mode},
            "reserve_recovery": {"mode": reserve_mode},
            "outcome_narrative_review": {"mode": outcome_mode},
        },
    )
    assert cli._serve(configured) == 0
    from masterclaw.app.legacy_compound_planning import LegacyCompoundPlanDecider
    from masterclaw.app.legacy_outcome_narrative_review import LegacyAlwaysReviewDecider
    from masterclaw.app.outcome_narrative_review import OutcomeNarrativePipeline

    assert isinstance(captured["compound_plan_decider"], LegacyCompoundPlanDecider)
    assert "compound_play_pipeline" not in captured
    assert isinstance(captured["narrative_pipeline"], OutcomeNarrativePipeline)
    assert isinstance(captured["narrative_pipeline"]._decider, LegacyAlwaysReviewDecider)
    state_executor = captured["state_decisions"]._classifier._executor
    advancement_executor = captured["advancement"]._decider._classifier._executor
    assert state_executor is advancement_executor
    outcome_observer = captured["narrative_pipeline"]._observer
    if outcome_mode == "shadow":
        assert outcome_observer._executor is state_executor
    else:
        assert outcome_observer is None
    observer = captured["action_capability_observer"]
    if action_mode == "shadow":
        assert observer._executor is state_executor
    else:
        assert observer is None
    narration_decider = captured["narration_rights_decider"]
    if narration_mode == "shadow":
        assert narration_decider._classifier._executor is state_executor
        assert narration_decider._baseline is captured["narration_text_port"]
    else:
        assert narration_decider is captured["narration_text_port"]
    reserve_observer = captured["reserve_recovery_observer"]
    if reserve_mode == "shadow":
        assert reserve_observer._executor is state_executor
    else:
        assert reserve_observer is None
    assert closes == ["backend"]


def test_reserve_recovery_nested_config_alone_enables_shared_runtime(monkeypatch):
    assert settings().classifier.reserve_recovery.mode is ClassifierMode.OFF
    monkeypatch.setenv("MASTERCLAW_CLASSIFIER__RESERVE_RECOVERY__MODE", "shadow")
    monkeypatch.setenv("MASTERCLAW_CLASSIFIER__RESERVE_RECOVERY__ALLOW_THRESHOLD", "0.9")
    monkeypatch.setenv("MASTERCLAW_CLASSIFIER__RESERVE_RECOVERY__DENY_THRESHOLD", "0.2")
    monkeypatch.setenv("MASTERCLAW_CLASSIFIER__RESERVE_RECOVERY__MAX_CANDIDATES", "12")
    configured = settings()
    policy = configured.classifier.for_use_case(ClassifierUseCase.RESERVE_RECOVERY)
    assert policy.allow_threshold == 0.9 and policy.deny_threshold == 0.2
    assert policy.max_candidates == 12
    assert configured.classifier.mode is ClassifierMode.OFF
    runtime = create_semantic_classifier(configured)
    assert runtime is not None
    asyncio.run(runtime.aclose())


@pytest.mark.parametrize(
    "values",
    [
        {"max_candidates": 32},
        {"max_candidates": 0},
        {"allow_threshold": 0.1, "deny_threshold": 0.2},
        {"mode": "active"},
    ],
)
def test_reserve_classifier_config_rejects_unbounded_or_authoritative_modes(values):
    with pytest.raises(ValidationError):
        settings(classifier={"reserve_recovery": values})


def test_outcome_review_config_alone_enables_runtime_and_has_independent_thresholds(monkeypatch):
    assert settings().classifier.outcome_narrative_review.mode is ClassifierMode.OFF
    monkeypatch.setenv("MASTERCLAW_CLASSIFIER__OUTCOME_NARRATIVE_REVIEW__MODE", "shadow")
    monkeypatch.setenv("MASTERCLAW_CLASSIFIER__OUTCOME_NARRATIVE_REVIEW__ALLOW_THRESHOLD", "0.9")
    monkeypatch.setenv("MASTERCLAW_CLASSIFIER__OUTCOME_NARRATIVE_REVIEW__DENY_THRESHOLD", "0.2")
    configured = settings()
    policy = configured.classifier.for_use_case(ClassifierUseCase.OUTCOME_NARRATIVE_REVIEW)
    assert policy.allow_threshold == 0.9 and policy.deny_threshold == 0.2
    assert configured.classifier.mode is ClassifierMode.OFF
    assert configured.classifier.player_narration_rights.mode is ClassifierMode.OFF
    runtime = create_semantic_classifier(configured)
    assert runtime is not None
    asyncio.run(runtime.aclose())
    for values in ({"mode": "active"}, {"allow_threshold": 0.1, "deny_threshold": 0.2}):
        with pytest.raises(ValidationError):
            settings(classifier={"outcome_narrative_review": values})


def test_narration_rights_config_is_independent_and_alone_enables_runtime(monkeypatch):
    assert settings().classifier.player_narration_rights.mode is ClassifierMode.OFF
    monkeypatch.setenv("MASTERCLAW_CLASSIFIER__PLAYER_NARRATION_RIGHTS__MODE", "shadow")
    monkeypatch.setenv("MASTERCLAW_CLASSIFIER__PLAYER_NARRATION_RIGHTS__ALLOW_THRESHOLD", "0.85")
    monkeypatch.setenv("MASTERCLAW_CLASSIFIER__PLAYER_NARRATION_RIGHTS__DENY_THRESHOLD", "0.15")
    configured = settings()
    policy = configured.classifier.for_use_case(ClassifierUseCase.PLAYER_NARRATION_RIGHTS)
    assert policy.allow_threshold == 0.85 and policy.deny_threshold == 0.15
    assert configured.classifier.mode is ClassifierMode.OFF
    assert configured.classifier.advancement.mode is ClassifierMode.OFF
    assert configured.classifier.action_capability.mode is ClassifierMode.OFF
    runtime = create_semantic_classifier(configured)
    assert runtime is not None
    asyncio.run(runtime.aclose())


def test_action_capability_has_independent_config_and_can_enable_runtime_alone(monkeypatch):
    monkeypatch.setenv("MASTERCLAW_CLASSIFIER__ACTION_CAPABILITY__MODE", "shadow")
    monkeypatch.setenv("MASTERCLAW_CLASSIFIER__ACTION_CAPABILITY__CAPABLE_THRESHOLD", "0.88")
    monkeypatch.setenv("MASTERCLAW_CLASSIFIER__ACTION_CAPABILITY__BLOCKED_THRESHOLD", "0.97")
    configured = settings()
    assert configured.classifier.mode is ClassifierMode.OFF
    assert configured.classifier.action.mode is ClassifierMode.OFF
    policy = configured.classifier.for_use_case(ClassifierUseCase.ACTION_CAPABILITY)
    assert policy.capable_threshold == 0.88 and policy.blocked_threshold == 0.97
    assert create_semantic_classifier(configured) is not None
