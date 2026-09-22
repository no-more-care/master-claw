import asyncio

import pytest
from pydantic import ValidationError

from masterclaw.adapters.discord_bot import DiscordIngressClient
from masterclaw.adapters.jev import JevClassifier
from masterclaw.classifiers.base import ClassifierConfigurationError
from masterclaw.classifiers.policy import ClassifierMode
from masterclaw.cli import _state_classifier
from masterclaw.config import Settings


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
    assert _state_classifier(configured) is None


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
    assert isinstance(_state_classifier(configured), JevClassifier)


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
    assert _state_classifier(configured) is None


def test_wiring_uses_existing_key_with_explicit_prefixed_override(monkeypatch):
    captured = {}

    def factory(**kwargs):
        captured.update(kwargs)
        return object()

    monkeypatch.setattr("masterclaw.cli.JevClassifier", factory)
    _state_classifier(settings(classifier={"mode": "shadow"}))
    assert captured["api_key"].get_secret_value() == "test"
    monkeypatch.setenv("MASTERCLAW_CLASSIFIER_API_KEY", "dedicated")
    _state_classifier(settings(classifier={"mode": "shadow"}))
    assert captured["api_key"].get_secret_value() == "dedicated"


@pytest.mark.parametrize("override", [None, ""])
def test_shadow_credentials_fail_at_wiring(override):
    with pytest.raises(ClassifierConfigurationError, match="credentials unavailable"):
        _state_classifier(
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
