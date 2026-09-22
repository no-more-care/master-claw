"""System One wire normalization shared by hosted Jev and local Kev/Laya sidecars."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, ValidationError

from masterclaw.classifiers.base import (
    ChoiceAnswer,
    ClassificationRequest,
    ClassificationResponse,
    ClassifierResponseError,
    NoulAnswer,
    Probability,
    ScoreAnswer,
    Text,
)


class _WireNoul(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["noul"]
    noul: Probability


class _WireResponse(BaseModel):
    # Provider metadata can grow independently of the typed answer contract.
    model_config = ConfigDict(extra="ignore")
    model: Text
    answers: dict[
        str, Annotated[ChoiceAnswer | ScoreAnswer | _WireNoul, Field(discriminator="type")]
    ] = Field(min_length=1, max_length=32)
    provider: Text | None = None
    version: Text | None = None
    id: Text | None = None
    usage: dict[str, JsonValue] = Field(default_factory=dict)


def parse_system_one_response(
    payload: object,
    request: ClassificationRequest,
    *,
    provider: str,
    allow_rl_agent: bool = False,
) -> ClassificationResponse:
    """Translate the provider wire format, then validate it against the exact request."""
    try:
        if (
            allow_rl_agent
            and isinstance(payload, dict)
            and isinstance(payload.get("answers"), dict)
        ):
            payload = {
                **payload,
                "answers": {
                    key: {field: value for field, value in answer.items() if field != "rl_agent"}
                    if isinstance(answer, dict)
                    else answer
                    for key, answer in payload["answers"].items()
                },
            }
        wire = _WireResponse.model_validate(payload)
        answers: dict[str, ChoiceAnswer | ScoreAnswer | NoulAnswer] = {}
        for key, answer in wire.answers.items():
            if isinstance(answer, _WireNoul):
                answers[key] = NoulAnswer(
                    noul=answer.noul,
                    probabilities={"true": answer.noul, "false": 1 - answer.noul},
                )
            else:
                # The response contract's discriminated union validates choice/score and
                # rejects unknown types. Noul confidence is absent on the wire, not invented.
                answers[key] = answer
        return ClassificationResponse(
            request_key=request.request_key,
            taxonomy_version=request.taxonomy_version,
            provider=provider,
            upstream_provider=wire.provider,
            model=wire.model,
            version=wire.version,
            request_id=wire.id,
            answers=answers,
            usage=wire.usage,
            cost=wire.usage.get("cost"),
        ).validate_for(request)
    except (ValidationError, ValueError, TypeError):
        raise ClassifierResponseError("invalid classifier response") from None
