import asyncio
import json

import httpx
import pytest
from pydantic import SecretStr
from test_classifier_wiring import settings
from test_jev_adapter import request, wire_response

from masterclaw.adapters.jev import parse_response
from masterclaw.adapters.system_one_http import SystemOneHttpClassifier, validated_endpoint
from masterclaw.adapters.system_one_wire import parse_system_one_response
from masterclaw.classifiers.base import (
    MAX_RESPONSE_BYTES,
    ClassifierAuthenticationError,
    ClassifierConfigurationError,
    ClassifierNetworkError,
    ClassifierRateLimitError,
    ClassifierRequestError,
    ClassifierResponseError,
    ClassifierServerError,
    ClassifierTimeoutError,
)
from masterclaw.classifiers.executor import SemanticClassifierExecutor, SemanticEvaluationContext
from masterclaw.classifiers.policy import SemanticEvaluationPolicy, SystemOneHttpConfig
from masterclaw.runtime.composition import create_semantic_classifier


def local_wire(*, laya=False):
    wire = wire_response()
    wire.update(
        model="local/system-one",
        version="rev-1",
        provider="/private/provider/path",
        id="C:/private/checkpoint",
        usage={"input_tokens": 12, "checkpoint_path": "PRIVATE"},
    )
    if laya:
        for answer in wire["answers"].values():
            answer["rl_agent"] = {"checkpoint": "/private/laya/path", "arbitrary": ["PRIVATE"]}
    return wire


def local_catalog(*, revision="rev-1"):
    return {"data": [{"id": "local/system-one", "revision": revision}]}


@pytest.mark.parametrize("laya", [False, True])
def test_kev_laya_primitives_payload_and_sanitized_metadata(laya):
    seen = []

    def handler(req):
        seen.append(req)
        if req.method == "GET":
            return httpx.Response(200, json=local_catalog())
        assert req.method == "POST" and req.url.path == "/v1/systemone"
        assert "authorization" not in req.headers
        payload = json.loads(req.content)
        assert set(payload) == {"model", "state", "questions"}
        assert payload["model"] == "local/system-one"
        assert payload["state"] == request().state
        return httpx.Response(200, json=local_wire(laya=laya))

    adapter = SystemOneHttpClassifier(
        transport=httpx.MockTransport(handler),
        config=SystemOneHttpConfig(expected_revision="rev-1"),
    )

    async def run():
        result = await adapter.classify(request())
        await adapter.aclose()
        return result

    result = asyncio.run(run())
    assert [req.method for req in seen] == ["GET", "POST"]
    assert result.provider == "local_system_one"
    assert result.version == "rev-1" and result.model == "local/system-one"
    assert result.request_id is result.upstream_provider is result.cost is None
    assert result.answers["valid"].probabilities == {"true": 0.5, "false": 0.5}
    assert result.answers["valid"].confidence is None
    assert result.answers["risk"].legend == {"0": "low", "1": "high"}
    assert result.usage == {"input_tokens": 12}
    assert "private" not in result.model_dump_json() and "rl_agent" not in result.model_dump_json()


def test_laya_allowlist_does_not_weaken_jev_or_unknown_answer_validation():
    wire = local_wire(laya=True)
    with pytest.raises(ClassifierResponseError):
        parse_response(wire, request())
    parse_system_one_response(wire, request(), provider="local_system_one", allow_rl_agent=True)
    wire["answers"]["valid"]["explanation"] = "PRIVATE"
    with pytest.raises(ClassifierResponseError):
        parse_system_one_response(wire, request(), provider="local_system_one", allow_rl_agent=True)


@pytest.mark.parametrize(
    "change",
    [
        lambda w: w["answers"].pop("valid"),
        lambda w: w["answers"]["route"]["probabilities"].update(extra=0.0),
        lambda w: w["answers"]["route"].update(confidence=float("nan")),
        lambda w: w["answers"]["risk"]["legend"].update({"0": "wrong"}),
        lambda w: w["answers"]["valid"].update(noul=2.0),
        lambda w: w["answers"]["valid"].update(type="unknown"),
    ],
)
def test_shared_normalizer_remains_strict(change):
    wire = local_wire(laya=True)
    change(wire)
    with pytest.raises(ClassifierResponseError, match="^invalid classifier response$"):
        parse_system_one_response(wire, request(), provider="local_system_one", allow_rl_agent=True)


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://127.0.0.1:8081/v1/systemone",
        "http://[::1]:8081/v1/systemone",
        "https://localhost/v1/systemone",
    ],
)
def test_loopback_allowlist(endpoint):
    assert validated_endpoint(endpoint) == endpoint


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://remote.example/v1/systemone",
        "http://127.0.0.1.evil.test/v1/systemone",
        "http://user:PRIVATE@localhost/v1/systemone",
        "http://localhost/private/checkpoint",
        "http://localhost/v1/systemone?token=PRIVATE",
        "file:///private/checkpoint",
    ],
)
def test_bad_endpoints_fail_closed_without_echo(endpoint):
    with pytest.raises(ClassifierConfigurationError) as error:
        validated_endpoint(endpoint)
    assert "PRIVATE" not in str(error.value) and "checkpoint" not in str(error.value)


def test_remote_requires_explicit_opt_in():
    endpoint = "https://remote.example/v1/systemone"
    assert validated_endpoint(endpoint, allow_non_loopback=True) == endpoint


def test_request_cap_is_enforced_before_network():
    def forbidden(req):
        raise AssertionError("oversized request must not reach transport")

    adapter = SystemOneHttpClassifier(transport=httpx.MockTransport(forbidden))
    oversized = request().model_copy(update={"state": "x" * 128_001})
    with pytest.raises(ClassifierRequestError):
        asyncio.run(adapter.classify(oversized))
    assert adapter._client is None
    asyncio.run(adapter.aclose())


def test_local_deadline_bounds_slow_sidecar():
    async def handler(req):
        await asyncio.Event().wait()

    adapter = SystemOneHttpClassifier(timeout_seconds=0.01, transport=httpx.MockTransport(handler))

    async def run():
        try:
            with pytest.raises(ClassifierTimeoutError):
                await adapter.classify(request())
        finally:
            await adapter.aclose()

    asyncio.run(run())


@pytest.mark.parametrize(
    ("status", "error"),
    [
        (302, ClassifierRequestError),
        (400, ClassifierRequestError),
        (401, ClassifierAuthenticationError),
        (403, ClassifierAuthenticationError),
        (408, ClassifierTimeoutError),
        (429, ClassifierRateLimitError),
        (503, ClassifierServerError),
    ],
)
def test_status_errors_are_sanitized_and_never_redirect(status, error):
    calls = []

    def handler(req):
        calls.append(req)
        return httpx.Response(
            status, text="PRIVATE body /checkpoint", headers={"Location": "https://remote.example"}
        )

    adapter = SystemOneHttpClassifier(transport=httpx.MockTransport(handler))

    async def run():
        try:
            with pytest.raises(error) as raised:
                await adapter.classify(request())
            assert "PRIVATE" not in str(raised.value) and "checkpoint" not in str(raised.value)
        finally:
            await adapter.aclose()

    asyncio.run(run())
    assert len(calls) == 1


@pytest.mark.parametrize(
    ("failure", "expected"),
    [
        (httpx.ConnectError("PRIVATE address"), ClassifierNetworkError),
        (httpx.ReadTimeout("PRIVATE address"), ClassifierTimeoutError),
        (asyncio.CancelledError(), asyncio.CancelledError),
    ],
)
def test_transport_and_cancellation(failure, expected):
    def handler(req):
        raise failure

    adapter = SystemOneHttpClassifier(transport=httpx.MockTransport(handler))

    async def run():
        try:
            with pytest.raises(expected) as raised:
                await adapter.classify(request())
            assert "PRIVATE" not in str(raised.value)
        finally:
            await adapter.aclose()

    asyncio.run(run())


def test_owned_lazy_pool_bounded_concurrency_no_proxy_and_borrowed_transport(monkeypatch):
    constructions, closes, active, peak = [], [], 0, 0
    real_client = httpx.AsyncClient

    class Transport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, req):
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            await asyncio.sleep(0)
            active -= 1
            return httpx.Response(
                200, json=local_catalog() if req.method == "GET" else local_wire()
            )

        async def aclose(self):
            closes.append("transport")

    class Client(real_client):
        async def aclose(self):
            closes.append("client")
            await super().aclose()

    def factory(**kwargs):
        constructions.append(kwargs)
        assert kwargs["trust_env"] is False and kwargs["follow_redirects"] is False
        return Client(**kwargs)

    monkeypatch.setenv("HTTP_PROXY", "http://PRIVATE.proxy.invalid")
    monkeypatch.setattr(httpx, "AsyncClient", factory)
    adapter = SystemOneHttpClassifier(transport=Transport())
    assert constructions == []

    async def run():
        await asyncio.gather(*(adapter.classify(request()) for _ in range(4)))
        await asyncio.gather(adapter.aclose(), adapter.aclose())
        await adapter.aclose()
        with pytest.raises(ClassifierConfigurationError):
            await adapter.classify(request())

    asyncio.run(run())
    assert len(constructions) == peak == 1
    assert closes == ["client"]


@pytest.mark.parametrize("case", ["size", "json", "alias", "revision"])
def test_response_size_json_and_deployment_binding(case):
    wire = local_wire()
    if case == "alias":
        wire["model"] = "/private/checkpoint"
    response = (
        httpx.Response(200, content=b"x" * (MAX_RESPONSE_BYTES + 1))
        if case == "size"
        else (
            httpx.Response(200, content=b"not JSON")
            if case == "json"
            else httpx.Response(200, json=wire)
        )
    )
    adapter = SystemOneHttpClassifier(
        config=SystemOneHttpConfig(expected_revision="rev-1"),
        transport=httpx.MockTransport(
            lambda req: (
                httpx.Response(
                    200, json=local_catalog(revision="wrong" if case == "revision" else "rev-1")
                )
                if req.method == "GET"
                else response
            )
        ),
    )

    async def run():
        try:
            with pytest.raises(ClassifierResponseError) as raised:
                await adapter.classify(request())
            assert "checkpoint" not in str(raised.value)
        finally:
            await adapter.aclose()

    asyncio.run(run())


def test_local_off_constructs_nothing_and_enabled_needs_no_remote_secret(monkeypatch):
    configured = settings(openrouter_api_key="", classifier={"provider": "system_one_http"})
    assert create_semantic_classifier(configured) is None
    configured = configured.model_copy(
        update={"classifier": configured.classifier.model_copy(update={"mode": "shadow"})}
    )
    runtime = create_semantic_classifier(configured)
    assert isinstance(runtime.port, SystemOneHttpClassifier)
    assert runtime.port._client is None and runtime.port._config.max_concurrency == 1
    asyncio.run(runtime.aclose())


@pytest.mark.parametrize("auth_header", ["Authorization", "X-API-Key"])
def test_optional_local_auth_and_telemetry_do_not_expose_private_metadata(auth_header):
    def handler(req):
        assert req.headers[auth_header] == (
            "Bearer PRIVATE" if auth_header == "Authorization" else "PRIVATE"
        )
        if req.method == "GET":
            return httpx.Response(200, json=local_catalog())
        wire = local_wire(laya=True)
        wire["cost"] = 0.003
        return httpx.Response(200, json=wire)

    adapter = SystemOneHttpClassifier(
        config=SystemOneHttpConfig(token=SecretStr("PRIVATE"), auth_header=auth_header),
        transport=httpx.MockTransport(handler),
    )

    async def run():
        try:
            return await SemanticClassifierExecutor(
                adapter, requested_model="local/system-one"
            ).evaluate(
                request(),
                policy=SemanticEvaluationPolicy(mode="shadow"),
                context=SemanticEvaluationContext(use_case="test"),
            )
        finally:
            await adapter.aclose()

    result = asyncio.run(run())
    assert result.observation.cost == 0.003
    assert result.observation.provider == "local_system_one"
    assert "PRIVATE" not in result.observation.model_dump_json()
    assert "checkpoint" not in result.observation.model_dump_json()
