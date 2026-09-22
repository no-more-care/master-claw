from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass

from masterclaw.classifiers.base import ClassifierConfigurationError, ClassifierPort
from masterclaw.classifiers.executor import SemanticClassifierExecutor
from masterclaw.config import Settings
from masterclaw.runtime.resources import RuntimeResources


@dataclass(frozen=True, slots=True)
class ClassifierRuntime:
    port: ClassifierPort
    executor: SemanticClassifierExecutor
    resources: RuntimeResources

    async def aclose(self) -> None:
        await self.resources.aclose()


def _jev_runtime(settings: Settings) -> ClassifierRuntime:
    # Concrete backends are imported only at composition, never by domain/application code.
    from masterclaw.adapters.jev import JevClassifier

    config = settings.classifier
    adapter = JevClassifier(
        model=config.model,
        timeout_seconds=max(policy.timeout_seconds for policy in config.enabled_use_cases()),
        max_concurrency=config.max_concurrency,
        api_key=(
            settings.classifier_api_key
            if settings.classifier_api_key is not None
            else settings.openrouter_api_key
        ),
    )
    return ClassifierRuntime(
        port=adapter,
        executor=SemanticClassifierExecutor(adapter, requested_model=config.model),
        resources=RuntimeResources(adapter),
    )


BackendFactory = Callable[[Settings], ClassifierRuntime]


def _system_one_http_runtime(settings: Settings) -> ClassifierRuntime:
    from masterclaw.adapters.system_one_http import SystemOneHttpClassifier

    config = settings.classifier
    adapter = SystemOneHttpClassifier(
        config=config.system_one_http,
        timeout_seconds=max(policy.timeout_seconds for policy in config.enabled_use_cases()),
    )
    return ClassifierRuntime(
        port=adapter,
        executor=SemanticClassifierExecutor(adapter, requested_model=config.system_one_http.model),
        resources=RuntimeResources(adapter),
    )


_BACKEND_FACTORIES: Mapping[str, BackendFactory] = {
    "jev": _jev_runtime,
    "system_one_http": _system_one_http_runtime,
}


def create_semantic_classifier(settings: Settings) -> ClassifierRuntime | None:
    if not settings.classifier.enabled_use_cases():
        return None
    factory = _BACKEND_FACTORIES.get(settings.classifier.provider)
    if factory is None:
        raise ClassifierConfigurationError("unsupported classifier provider")
    return factory(settings)
