from __future__ import annotations

import asyncio
import json
import os
from typing import Annotated, Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field, JsonValue, SecretStr, ValidationError

from masterclaw.classifiers.base import (
    MAX_REQUEST_BYTES,
    MAX_RESPONSE_BYTES,
    ChoiceAnswer,
    ClassificationRequest,
    ClassificationResponse,
    ClassifierAuthenticationError,
    ClassifierConfigurationError,
    ClassifierNetworkError,
    ClassifierRateLimitError,
    ClassifierRequestError,
    ClassifierResponseError,
    ClassifierServerError,
    ClassifierTimeoutError,
    NoulAnswer,
    Probability,
    ScoreAnswer,
    Text,
)

DECISIONS_URL = "https://openrouter.ai/api/alpha/decisions"


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


def parse_response(payload: object, request: ClassificationRequest) -> ClassificationResponse:
    """Translate the provider wire format, then validate it against the exact request."""
    try:
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
            provider="openrouter",
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


class _BorrowedTransport(httpx.AsyncBaseTransport):
    """Let an owned client borrow a caller-owned transport without closing it."""

    def __init__(self, transport: httpx.AsyncBaseTransport) -> None:
        self._transport = transport

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        return await self._transport.handle_async_request(request)


def _http_error(status: int):
    message = f"classifier HTTP status {status}"
    if status in {401, 403}:
        return ClassifierAuthenticationError(message)
    if status == 408:
        return ClassifierTimeoutError(message)
    if status == 429:
        return ClassifierRateLimitError(message)
    if status >= 500:
        return ClassifierServerError(message)
    return ClassifierRequestError(message)


class JevClassifier:
    """Bounded asynchronous Jev adapter; no generative CompletionPort dependency.

    Reuses one lazy HTTP pool on the application's event loop. Call aclose during shutdown.
    Injected clients/transports belong to their caller and are never closed here.
    No retries or redirects.
    """

    def __init__(
        self,
        *,
        model: str = "~typesafe/jev-latest",
        timeout_seconds: float = 5,
        max_concurrency: int = 4,
        api_key: SecretStr | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if max_concurrency < 1 or not 0 < timeout_seconds <= 60:
            raise ClassifierConfigurationError("invalid classifier limits")
        if client is not None and transport is not None:
            raise ClassifierConfigurationError("choose classifier client or transport")
        key = api_key.get_secret_value() if api_key is not None else os.getenv("OPENROUTER_API_KEY")
        if not key or not key.strip():
            raise ClassifierConfigurationError("classifier credentials unavailable")
        self._model = model
        self._timeout = timeout_seconds
        self._key = SecretStr(key)
        self._transport = None if transport is None else _BorrowedTransport(transport)
        self._client = client
        self._owns_client = client is None
        self._closed = False
        self._semaphore = asyncio.Semaphore(max_concurrency)

    async def classify(self, request: ClassificationRequest) -> ClassificationResponse:
        if self._closed:
            raise ClassifierConfigurationError("classifier is closed")
        key = self._key.get_secret_value()
        # Revalidate at the boundary because Pydantic's frozen models can contain mutable maps.
        try:
            request = ClassificationRequest.model_validate(request.model_dump(mode="json"))
            payload = json.dumps(
                {
                    "model": self._model,
                    "state": request.state,
                    "questions": {
                        key: question.model_dump(mode="json", exclude_none=True)
                        for key, question in request.questions.items()
                    },
                },
                ensure_ascii=False,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
            if len(payload) > MAX_REQUEST_BYTES:
                raise ValueError("request too large")
        except (ValidationError, ValueError, TypeError):
            raise ClassifierRequestError("invalid classifier request") from None
        try:
            async with asyncio.timeout(self._timeout), self._semaphore:
                if self._closed:
                    raise ClassifierConfigurationError("classifier is closed")
                if self._client is None:
                    self._client = httpx.AsyncClient(
                        timeout=self._timeout,
                        transport=self._transport,
                        follow_redirects=False,
                    )
                async with self._client.stream(
                    "POST",
                    DECISIONS_URL,
                    headers={
                        "Authorization": f"Bearer {key}",
                        "Content-Type": "application/json",
                    },
                    content=payload,
                    follow_redirects=False,
                    timeout=self._timeout,
                ) as response:
                    if not 200 <= response.status_code < 300:
                        raise _http_error(response.status_code)
                    data = bytearray()
                    async for chunk in response.aiter_bytes():
                        data.extend(chunk)
                        if len(data) > MAX_RESPONSE_BYTES:
                            raise ClassifierResponseError("classifier response exceeds size limit")
            return parse_response(json.loads(data), request)
        except (httpx.TimeoutException, TimeoutError):
            raise ClassifierTimeoutError("classifier timed out") from None
        except httpx.HTTPError:
            raise ClassifierNetworkError("classifier transport failed") from None
        except (json.JSONDecodeError, UnicodeDecodeError, RecursionError):
            raise ClassifierResponseError("invalid classifier response JSON") from None

    async def aclose(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._owns_client and self._client is not None:
            await self._client.aclose()
