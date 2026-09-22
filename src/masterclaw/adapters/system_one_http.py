"""Externally supervised System One sidecar; never starts, downloads or imports models."""

from __future__ import annotations

import asyncio
import ipaddress
import json
import math
import re
from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit

import httpx

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
from masterclaw.classifiers.observations import numeric_usage
from masterclaw.classifiers.policy import SystemOneHttpConfig


def validated_endpoint(endpoint: str, *, allow_non_loopback: bool = False) -> str:
    try:
        url = urlsplit(endpoint)
        host = url.hostname
        port = url.port
        if (
            url.scheme not in {"http", "https"}
            or not host
            or url.username is not None
            or url.password is not None
            or url.query
            or url.fragment
            or url.path != "/v1/systemone"
            or (port is not None and not 1 <= port <= 65535)
        ):
            raise ValueError()
        try:
            loopback = ipaddress.ip_address(host).is_loopback
        except ValueError:
            loopback = host.lower() == "localhost"
        if not loopback and (not allow_non_loopback or url.scheme != "https"):
            raise ValueError()
        return urlunsplit((url.scheme, url.netloc, url.path, "", ""))
    except (ValueError, TypeError):
        raise ClassifierConfigurationError(
            "System One requires loopback /v1/systemone; remote needs HTTPS and explicit opt-in"
        ) from None


@dataclass(frozen=True, slots=True)
class CatalogVerification:
    configured_model: str
    resolved_model: str
    deployment_verified: bool
    version: str | None = None


def _safe_model_id(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) <= 128
        and bool(
            re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._+-]*(?:/[A-Za-z0-9][A-Za-z0-9._+-]*)*", value)
        )
    )


class SystemOneHttpClassifier:
    """Owns its lazy no-proxy/no-redirect client; injected transport remains caller-owned."""

    def __init__(
        self,
        *,
        config: SystemOneHttpConfig | None = None,
        timeout_seconds: float = 5,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self._config = config or SystemOneHttpConfig()
        self._endpoint = validated_endpoint(
            self._config.endpoint, allow_non_loopback=self._config.allow_non_loopback
        )
        if not math.isfinite(timeout_seconds) or not 0 < timeout_seconds <= 60:
            raise ClassifierConfigurationError("invalid classifier timeout")
        self._timeout = timeout_seconds
        self._transport = _BorrowedTransport(transport) if transport is not None else None
        self._client: httpx.AsyncClient | None = None
        self._semaphore = asyncio.Semaphore(self._config.max_concurrency)
        self._closed = False
        self._close_task: asyncio.Task | None = None
        self._catalog_lock = asyncio.Lock()
        self._catalog: CatalogVerification | None = None
        if not _safe_model_id(self._config.model):
            raise ClassifierConfigurationError("invalid classifier model alias")
        token = self._config.token.get_secret_value() if self._config.token is not None else None
        if token is not None and (
            not token.strip() or any(ord(char) < 32 or ord(char) > 126 for char in token)
        ):
            raise ClassifierConfigurationError("invalid classifier credentials")

    async def _request_json(self, method: str, url: str, payload: bytes | None = None) -> object:
        if self._closed:
            raise ClassifierConfigurationError("classifier is closed")
        headers = {"Content-Type": "application/json"}
        if self._config.token is not None:
            token = self._config.token.get_secret_value()
            headers[self._config.auth_header] = (
                f"Bearer {token}" if self._config.auth_header == "Authorization" else token
            )
        try:
            async with asyncio.timeout(self._timeout), self._semaphore:
                if self._closed:
                    raise ClassifierConfigurationError("classifier is closed")
                if self._client is None:
                    self._client = httpx.AsyncClient(
                        timeout=self._timeout,
                        transport=self._transport,
                        follow_redirects=False,
                        trust_env=False,
                    )
                async with self._client.stream(
                    method,
                    url,
                    content=payload,
                    headers=headers,
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
            return json.loads(data)
        except (httpx.TimeoutException, TimeoutError):
            raise ClassifierTimeoutError("classifier timed out") from None
        except httpx.HTTPError:
            raise ClassifierNetworkError("classifier transport failed") from None
        except (ValueError, UnicodeDecodeError, RecursionError):
            raise ClassifierResponseError("invalid classifier response JSON") from None

    async def classify(self, request: ClassificationRequest) -> ClassificationResponse:
        try:
            request = ClassificationRequest.model_validate(request.model_dump(mode="json"))
            payload = json.dumps(
                {
                    "model": self._config.model,
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
                raise ValueError()
        except (ValueError, TypeError):
            raise ClassifierRequestError("invalid classifier request") from None
        catalog = await self.check_models()
        raw = await self._request_json("POST", self._endpoint, payload)
        if not isinstance(raw, dict) or raw.get("model") not in {
            catalog.configured_model,
            catalog.resolved_model,
        }:
            raise ClassifierResponseError("classifier model alias mismatch")
        # Paths and arbitrary sidecar metadata never escape via identifiers or usage.
        try:
            usage = (
                numeric_usage(raw.get("usage", {}))
                if isinstance(raw.get("usage", {}), dict)
                else {}
            )
        except (OverflowError, TypeError, ValueError):
            raise ClassifierResponseError("invalid classifier usage") from None
        if "cost" in raw:
            cost = raw["cost"]
            if type(cost) not in {int, float} or not math.isfinite(cost) or cost < 0:
                raise ClassifierResponseError("invalid classifier billed cost")
            usage["cost"] = cost
        return parse_system_one_response(
            {
                **raw,
                "model": catalog.resolved_model,
                "version": catalog.version,
                "provider": None,
                "id": None,
                "usage": usage,
            },
            request,
            provider="local_system_one",
            allow_rl_agent=True,
        )

    async def check_models(self) -> CatalogVerification:
        # Serialize the first check: concurrent inference requests share one verified catalog.
        # Cache only successful verification, not transient failures or raw catalog/path values.
        try:
            async with asyncio.timeout(self._timeout), self._catalog_lock:
                if self._closed:
                    raise ClassifierConfigurationError("classifier is closed")
                if self._catalog is None:
                    self._catalog = await self._verify_models()
                return self._catalog
        except TimeoutError:
            raise ClassifierTimeoutError("classifier catalog check timed out") from None

    async def _verify_models(self) -> CatalogVerification:
        url = urlsplit(self._endpoint)
        raw = await self._request_json(
            "GET", urlunsplit((url.scheme, url.netloc, "/v1/models", "", ""))
        )
        if not isinstance(raw, dict) or ("models" in raw) == ("data" in raw):
            raise ClassifierResponseError("invalid classifier model catalog")
        entries = raw.get("models", raw.get("data"))
        if not isinstance(entries, list) or not 1 <= len(entries) <= 128:
            raise ClassifierResponseError("invalid classifier model catalog")
        matches = []
        for item in entries:
            if not isinstance(item, dict) or not isinstance(item.get("id"), str):
                raise ClassifierResponseError("invalid classifier catalog entry")
            aliases = item.get("aliases", [])
            if (
                not isinstance(aliases, list)
                or len(aliases) > 128
                or not all(isinstance(alias, str) and 0 < len(alias) <= 512 for alias in aliases)
            ):
                raise ClassifierResponseError("invalid classifier catalog aliases")
            if self._config.model == item["id"] or self._config.model in aliases:
                matches.append(item)
        if len(matches) != 1:
            raise ClassifierResponseError("configured classifier alias is not uniquely available")
        selected = matches[0]
        if not _safe_model_id(selected["id"]):
            raise ClassifierResponseError("unsafe classifier canonical model id")
        private_identifiers = {
            selected[key] for key in ("run", "base") if isinstance(selected.get(key), str)
        }
        if selected["id"] in private_identifiers or self._config.model in private_identifiers:
            raise ClassifierResponseError(
                "classifier aliases must not expose run or base identifiers"
            )
        # Explicit sidecar revision/version wins over direct Kev's best-available run tag.
        field = next(
            (key for key in ("revision", "version", "run") if selected.get(key) is not None), None
        )
        revision = selected.get(field) if field is not None else None
        expected = self._config.expected_revision
        if expected is not None and revision != expected.get_secret_value():
            raise ClassifierResponseError("configured classifier revision is unavailable")
        public_version = (
            revision
            if field != "run"
            and isinstance(revision, str)
            and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._+-]{0,127}", revision)
            else None
        )
        return CatalogVerification(
            configured_model=self._config.model,
            resolved_model=selected["id"],
            deployment_verified=expected is not None,
            version=public_version,
        )

    async def aclose(self) -> None:
        self._closed = True
        if self._client is not None:
            if self._close_task is None:
                self._close_task = asyncio.create_task(self._client.aclose())
            await asyncio.shield(self._close_task)
