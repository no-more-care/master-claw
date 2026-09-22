from __future__ import annotations

import asyncio
import json
import os

import httpx
from pydantic import SecretStr, ValidationError

from masterclaw.adapters.classifier_http import _BorrowedTransport, _http_error
from masterclaw.adapters.system_one_wire import parse_system_one_response
from masterclaw.classifiers.base import (
    MAX_REQUEST_BYTES,
    MAX_RESPONSE_BYTES,
    ClassificationRequest,
    ClassificationResponse,
    ClassifierConfigurationError,
    ClassifierNetworkError,
    ClassifierRequestError,
    ClassifierResponseError,
    ClassifierTimeoutError,
)

DECISIONS_URL = "https://openrouter.ai/api/alpha/decisions"


def parse_response(payload: object, request: ClassificationRequest) -> ClassificationResponse:
    return parse_system_one_response(payload, request, provider="openrouter")


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
