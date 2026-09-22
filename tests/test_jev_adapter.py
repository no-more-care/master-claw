import asyncio
import json

import httpx
import pytest
from pydantic import SecretStr

from masterclaw.adapters.jev import DECISIONS_URL, JevClassifier, parse_response
from masterclaw.classifiers.base import (
    ChoiceQuestion,
    ClassificationRequest,
    ClassifierAuthenticationError,
    ClassifierConfigurationError,
    ClassifierNetworkError,
    ClassifierRateLimitError,
    ClassifierRequestError,
    ClassifierResponseError,
    ClassifierServerError,
    ClassifierTimeoutError,
    NoulQuestion,
    ScoreQuestion,
)


def request():
    return ClassificationRequest(
        taxonomy_version="test.v1",
        state={"message": "Привет"},
        questions={
            "route": ChoiceQuestion(instructions="Route", criteria={"a": "A", "b": "B"}),
            "valid": NoulQuestion(instructions="Valid?"),
            "risk": ScoreQuestion(instructions="Risk?", criteria=["low", "high"]),
        },
    )


def wire_response():
    return {
        "model": "typesafe/jev-1.13",
        "version": "1.13",
        "provider": "TypeSafe",
        "id": "request-123",
        "usage": {"input_tokens": 100, "cost": 0.001},
        "answers": {
            "route": {
                "type": "choice",
                "choice": "a",
                "confidence": 0.98,
                "probabilities": {"a": 0.98, "b": 0.02},
            },
            "valid": {"type": "noul", "noul": 0.5},
            "risk": {
                "type": "score",
                "score": 0.75,
                "confidence": 0.8,
                "legend": {"0": "low", "1": "high"},
                "probabilities": {"0": 0.25, "1": 0.75},
            },
        },
    }


def test_wire_payload_and_all_answer_types():
    def handler(http_request):
        assert str(http_request.url) == DECISIONS_URL
        assert http_request.method == "POST"
        assert http_request.headers["Authorization"] == "Bearer test-key"
        payload = json.loads(http_request.content)
        assert set(payload) == {"model", "state", "questions"}
        assert payload["model"] == "~typesafe/jev-latest"
        assert payload["state"] == {"message": "Привет"}
        assert payload["questions"]["valid"] == {"type": "noul", "instructions": "Valid?"}
        return httpx.Response(200, json=wire_response())

    classifier = JevClassifier(
        api_key=SecretStr("test-key"), transport=httpx.MockTransport(handler)
    )
    response = asyncio.run(classifier.classify(request()))
    assert response.model == "typesafe/jev-1.13"
    assert response.provider == "openrouter"
    assert response.upstream_provider == "TypeSafe"
    assert response.version == "1.13"
    assert response.request_id == "request-123"
    assert response.usage == {"input_tokens": 100, "cost": 0.001}
    assert response.cost == 0.001
    assert response.answers["valid"].confidence is None
    assert response.answers["valid"].probabilities == {"true": 0.5, "false": 0.5}
    assert response.answers["route"].probabilities == {"a": 0.98, "b": 0.02}
    assert response.answers["risk"].score == 0.75


def test_optional_metadata_is_not_invented():
    wire = wire_response()
    for key in ("version", "id", "usage", "provider"):
        del wire[key]
    response = parse_response(wire, request())
    assert (
        response.version
        is response.request_id
        is response.cost
        is response.upstream_provider
        is None
    )
    assert response.usage == {}


@pytest.mark.parametrize(
    ("status", "kind", "transient"),
    [
        (301, ClassifierRequestError, False),
        (400, ClassifierRequestError, False),
        (401, ClassifierAuthenticationError, False),
        (403, ClassifierAuthenticationError, False),
        (408, ClassifierTimeoutError, True),
        (429, ClassifierRateLimitError, True),
        (500, ClassifierServerError, True),
        (503, ClassifierServerError, True),
    ],
)
def test_http_errors_are_sanitized_and_not_retried(status, kind, transient):
    calls = []

    def handler(http_request):
        calls.append(http_request)
        return httpx.Response(status, text="secret player state or credentials")

    classifier = JevClassifier(api_key=SecretStr("secret"), transport=httpx.MockTransport(handler))
    with pytest.raises(kind, match=f"HTTP status {status}") as caught:
        asyncio.run(classifier.classify(request()))
    assert "secret" not in str(caught.value)
    assert len(calls) == 1
    assert caught.value.transient is transient


@pytest.mark.parametrize(
    "body", [b"invalid secret JSON", b"x" * 256_001], ids=["invalid", "oversized"]
)
def test_invalid_or_oversized_body_is_rejected(body):
    classifier = JevClassifier(
        api_key=SecretStr("secret"),
        transport=httpx.MockTransport(lambda _: httpx.Response(200, content=body)),
    )
    with pytest.raises(ClassifierResponseError) as caught:
        asyncio.run(classifier.classify(request()))
    assert "secret" not in str(caught.value)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda wire: wire["answers"].pop("route"),
        lambda wire: wire["answers"]["route"].update(type="unknown"),
        lambda wire: wire["answers"]["route"].update(probabilities={"a": 0.9, "alien": 0.1}),
        lambda wire: wire["answers"]["risk"].update(legend={"0": "high", "1": "low"}),
        lambda wire: wire["answers"]["valid"].update(noul=1.5),
        lambda wire: wire["answers"]["valid"].update(confidence=0.99),
    ],
)
def test_response_schema_and_question_validation(mutation):
    wire = wire_response()
    mutation(wire)
    with pytest.raises(ClassifierResponseError, match="invalid classifier response"):
        parse_response(wire, request())


def test_network_timeout_and_transport_error_are_sanitized():
    for error, expected, kind in [
        (httpx.ReadTimeout("secret request"), "timed out", ClassifierTimeoutError),
        (httpx.ConnectError("secret request"), "transport failed", ClassifierNetworkError),
    ]:

        def handler(_, error=error):
            raise error

        classifier = JevClassifier(
            api_key=SecretStr("secret"), transport=httpx.MockTransport(handler)
        )
        with pytest.raises(kind, match=expected) as caught:
            asyncio.run(classifier.classify(request()))
        assert caught.value.transient


def test_missing_credentials_and_environment_key(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    with pytest.raises(ClassifierConfigurationError, match="credentials unavailable"):
        JevClassifier()
    monkeypatch.setenv("OPENROUTER_API_KEY", "env-key")

    def handler(req):
        assert req.headers["Authorization"] == "Bearer env-key"
        return httpx.Response(200, json=wire_response())

    asyncio.run(JevClassifier(transport=httpx.MockTransport(handler)).classify(request()))


def test_total_timeout_bounds_slow_transport():
    async def handler(_):
        await asyncio.sleep(60)

    classifier = JevClassifier(
        api_key=SecretStr("key"),
        timeout_seconds=0.001,
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(ClassifierTimeoutError, match="timed out"):
        asyncio.run(classifier.classify(request()))


def test_concurrency_is_bounded_pool_reused_and_owned_client_closed_once(monkeypatch):
    active = 0
    peak = 0

    async def handler(_):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        try:
            await asyncio.sleep(0.001)
            return httpx.Response(200, json=wire_response())
        finally:
            active -= 1

    class Transport(httpx.MockTransport):
        closed = 0

        async def aclose(self):
            self.closed += 1
            await super().aclose()

    transport = Transport(handler)
    instances = []

    class Client(httpx.AsyncClient):
        close_calls = 0

        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            instances.append(self)

        async def aclose(self):
            self.close_calls += 1
            await super().aclose()

    monkeypatch.setattr(httpx, "AsyncClient", Client)
    classifier = JevClassifier(
        api_key=SecretStr("key"),
        max_concurrency=2,
        transport=transport,
    )

    async def run():
        await asyncio.gather(*(classifier.classify(request()) for _ in range(5)))
        assert len(instances) == 1
        assert not instances[0].is_closed
        await classifier.aclose()
        await classifier.aclose()
        with pytest.raises(ClassifierConfigurationError, match="closed"):
            await classifier.classify(request())

    asyncio.run(run())
    assert peak == 2
    assert active == 0
    assert transport.closed == 0  # Caller owns injected transport.
    assert instances[0].close_calls == 1
    assert instances[0].is_closed


def test_external_client_remains_open_after_classifier_shutdown():
    async def run():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda _: httpx.Response(200, json=wire_response()))
        ) as client:
            classifier = JevClassifier(api_key=SecretStr("key"), client=client)
            await classifier.classify(request())
            await classifier.classify(request())
            await classifier.aclose()
            await classifier.aclose()
            assert not client.is_closed
        assert client.is_closed

    asyncio.run(run())


def test_adapter_cancellation_propagates_and_releases_semaphore():
    async def handler(_):
        raise asyncio.CancelledError

    async def run():
        classifier = JevClassifier(
            api_key=SecretStr("key"),
            max_concurrency=1,
            transport=httpx.MockTransport(handler),
        )
        try:
            for _ in range(2):
                with pytest.raises(asyncio.CancelledError):
                    await classifier.classify(request())
        finally:
            await classifier.aclose()

    asyncio.run(run())


def test_invalid_request_is_permanent_without_http_call():
    classifier = JevClassifier(api_key=SecretStr("key"))
    invalid = request()
    invalid.questions.clear()
    with pytest.raises(ClassifierRequestError) as caught:
        asyncio.run(classifier.classify(invalid))
    assert not caught.value.transient
