import asyncio
import json
from pathlib import Path

import httpx
import pytest
from pydantic import SecretStr
from test_jev_adapter import request

from masterclaw.adapters.system_one_http import SystemOneHttpClassifier
from masterclaw.classifier_doctor import check_classifier
from masterclaw.classifiers.base import ClassifierConfigurationError, ClassifierResponseError
from masterclaw.classifiers.policy import SystemOneHttpConfig


def fixture():
    return json.loads(
        (Path(__file__).parent / "fixtures/kev_serve_system_one.json").read_text(encoding="utf-8")
    )


@pytest.mark.parametrize("response_model", ["jev-latest", "kev-latest"])
def test_official_catalog_alias_binding_without_inference_revision(response_model):
    values = fixture()
    values["response"]["model"] = response_model
    calls = []

    async def handler(req):
        calls.append(req.method)
        await asyncio.sleep(0)
        if req.method == "GET":
            assert req.url.path == "/v1/models"
            return httpx.Response(200, json=values["catalog"])
        assert json.loads(req.content)["model"] == "jev-latest"
        return httpx.Response(200, json=values["response"])

    adapter = SystemOneHttpClassifier(
        config=SystemOneHttpConfig(model="jev-latest", expected_revision="runs/synthetic-kev"),
        transport=httpx.MockTransport(handler),
    )

    async def run():
        try:
            results = await asyncio.gather(*(adapter.classify(request()) for _ in range(3)))
            catalog = await adapter.check_models()
            assert catalog.configured_model == "jev-latest"
            assert catalog.resolved_model == "kev-latest"
            assert catalog.deployment_verified is True
            assert "runs/" not in repr(catalog) and "synthetic/base" not in repr(catalog)
            return results
        finally:
            await adapter.aclose()

    results = asyncio.run(run())
    assert calls == ["GET", "POST", "POST", "POST"]
    for result in results:
        assert result.model == "kev-latest" and result.version is None
        assert result.cost is None and result.provider == "local_system_one"
        assert (
            "runs/" not in result.model_dump_json()
            and "synthetic/base" not in result.model_dump_json()
        )


@pytest.mark.parametrize("pin", [None, "runs/synthetic-kev"])
def test_official_doctor_exposes_only_safe_aliases_and_verified_boolean(pin):
    calls = []

    def handler(req):
        calls.append(req.method)
        return httpx.Response(200, json=fixture()["catalog"])

    result = asyncio.run(
        check_classifier(
            SystemOneHttpConfig(model="jev-latest", expected_revision=pin),
            transport=httpx.MockTransport(handler),
        )
    )
    assert (
        result.ok
        and result.configured_model == "jev-latest"
        and result.resolved_model == "kev-latest"
    )
    assert result.deployment_verified is (pin is not None)
    assert calls == ["GET"]
    assert "runs/" not in repr(result) and "synthetic/base" not in repr(result)


@pytest.mark.parametrize(
    ("metadata", "pin", "ok"),
    [
        ({"revision": "r1", "version": "v1", "run": "run1"}, "r1", True),
        ({"revision": "r1", "version": "v1", "run": "run1"}, "v1", False),
        ({"version": "v1", "run": "run1"}, "v1", True),
        ({"run": "run1"}, "run1", True),
        ({"base": "base1"}, "base1", False),
    ],
)
def test_catalog_pin_precedence_before_any_inference(metadata, pin, ok):
    calls = []
    values = fixture()
    entry = {"id": "kev-latest", "aliases": ["jev-latest"], **metadata}

    def handler(req):
        calls.append(req.method)
        return httpx.Response(
            200, json={"models": [entry]} if req.method == "GET" else values["response"]
        )

    adapter = SystemOneHttpClassifier(
        config=SystemOneHttpConfig(model="jev-latest", expected_revision=pin),
        transport=httpx.MockTransport(handler),
    )

    async def run():
        try:
            if ok:
                await adapter.classify(request())
            else:
                with pytest.raises(ClassifierResponseError):
                    await adapter.classify(request())
        finally:
            await adapter.aclose()

    asyncio.run(run())
    assert calls == ["GET"] + (["POST"] if ok else [])


@pytest.mark.parametrize(
    "case", ["ambiguous", "bad_aliases", "unsafe_id", "echoed_run", "wrong_response"]
)
def test_catalog_resolution_cannot_weaken_response_binding(case):
    values = fixture()
    entry = values["catalog"]["models"][0]
    if case == "ambiguous":
        values["catalog"]["models"].append({"id": "other", "aliases": ["jev-latest"]})
    elif case == "bad_aliases":
        entry["aliases"] = "jev-latest"
    elif case == "unsafe_id":
        entry["id"] = "C:/private/checkpoint"
    elif case == "echoed_run":
        entry["id"] = entry["run"] = "runs/private-checkpoint"
    else:
        values["response"]["model"] = "other-model"
    calls = []

    def handler(req):
        calls.append(req.method)
        return httpx.Response(
            200, json=values["catalog"] if req.method == "GET" else values["response"]
        )

    adapter = SystemOneHttpClassifier(
        config=SystemOneHttpConfig(model="jev-latest"), transport=httpx.MockTransport(handler)
    )

    async def run():
        try:
            with pytest.raises(ClassifierResponseError) as raised:
                await adapter.classify(request())
            assert "checkpoint" not in str(raised.value)
        finally:
            await adapter.aclose()

    asyncio.run(run())
    assert calls == ["GET"] + (["POST"] if case == "wrong_response" else [])


@pytest.mark.parametrize("token", [None, SecretStr("PRIVATE")])
def test_remote_http_rejected_before_client_even_with_opt_in(monkeypatch, token):
    def forbidden(**kwargs):
        raise AssertionError("remote HTTP must not construct a client")

    monkeypatch.setattr(httpx, "AsyncClient", forbidden)
    with pytest.raises(ClassifierConfigurationError, match="HTTPS"):
        SystemOneHttpClassifier(
            config=SystemOneHttpConfig(
                endpoint="http://remote.example/v1/systemone", allow_non_loopback=True, token=token
            )
        )
