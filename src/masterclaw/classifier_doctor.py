"""Bounded sidecar health check; model loading and process supervision are external."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

import httpx
from pydantic import ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict

from masterclaw.adapters.system_one_http import SystemOneHttpClassifier
from masterclaw.classifiers.base import (
    ChoiceQuestion,
    ClassificationRequest,
    ClassifierError,
    NoulQuestion,
    ScoreQuestion,
)
from masterclaw.classifiers.policy import ClassifierConfig, SystemOneHttpConfig


class ClassifierDoctorSettings(BaseSettings):
    # Deliberately independent of Discord/OpenRouter credentials, DB and generative models.
    model_config = SettingsConfigDict(
        env_file=".env", env_prefix="MASTERCLAW_", env_nested_delimiter="__", extra="ignore"
    )
    classifier: ClassifierConfig = ClassifierConfig()


@dataclass(frozen=True, slots=True)
class ClassifierDoctorResult:
    ok: bool
    category: str
    smoke: bool
    configured_model: str | None = None
    resolved_model: str | None = None
    deployment_verified: bool = False


def smoke_request() -> ClassificationRequest:
    return ClassificationRequest(
        taxonomy_version="system_one_doctor.v1",
        state={"synthetic": True, "text": "A blue circle is visible. Синий круг виден."},
        questions={
            "shape": ChoiceQuestion(
                instructions="Which shape is described?",
                criteria={"circle": "A circle", "square": "A square"},
            ),
            "visible": NoulQuestion(
                instructions="Is the circle described as visible?",
                criteria={"true": "It is visible", "false": "It is not visible"},
            ),
            "visibility": ScoreQuestion(
                instructions="Rate the described visibility.",
                criteria=["Not visible", "Clearly visible"],
            ),
        },
    )


async def check_classifier(
    config: SystemOneHttpConfig,
    *,
    smoke: bool = False,
    transport: httpx.AsyncBaseTransport | None = None,
) -> ClassifierDoctorResult:
    adapter = None
    try:
        adapter = SystemOneHttpClassifier(config=config, timeout_seconds=3, transport=transport)
        catalog = await adapter.check_models()
        if smoke:
            await adapter.classify(smoke_request())
        return ClassifierDoctorResult(
            ok=True,
            category="ok",
            smoke=smoke,
            configured_model=catalog.configured_model,
            resolved_model=catalog.resolved_model,
            deployment_verified=catalog.deployment_verified,
        )
    except ClassifierError as error:
        return ClassifierDoctorResult(ok=False, category=error.category.value, smoke=smoke)
    finally:
        if adapter is not None:
            await adapter.aclose()


def run_classifier_doctor(*, smoke: bool = False) -> int:
    try:
        settings = ClassifierDoctorSettings()
        if settings.classifier.provider != "system_one_http":
            print("classifier-doctor: configure provider=system_one_http for this local check")
            return 1
        result = asyncio.run(check_classifier(settings.classifier.system_one_http, smoke=smoke))
    except (ValidationError, ValueError, OSError):
        print(
            "classifier-doctor: invalid local configuration; check endpoint/alias/revision settings"
        )
        return 1
    except Exception:
        # Third-party client/cleanup failures must not leak paths, bodies or credentials.
        print("classifier-doctor: local check failed; inspect the externally supervised sidecar")
        return 1
    if result.ok:
        print(
            "classifier-doctor: OK; "
            + ("synthetic smoke passed" if smoke else "no inference requested")
        )
        print(
            f"configured={result.configured_model} resolved={result.resolved_model} "
            f"deployment_verified={result.deployment_verified}"
        )
        return 0
    print(
        f"classifier-doctor: {result.category}; check the externally supervised sidecar, "
        "configured alias/revision and access settings. Nothing was started or downloaded."
    )
    return 1
