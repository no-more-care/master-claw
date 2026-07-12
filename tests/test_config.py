import pytest

from masterclaw.config import ModelConfig


def test_only_openrouter_models_are_accepted_in_v2() -> None:
    assert ModelConfig(model="openrouter/vendor/model").model == "openrouter/vendor/model"
    with pytest.raises(ValueError, match="only openrouter"):
        ModelConfig(model="openai/model")
