import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest

from masterclaw.classifier_doctor import (
    ClassifierDoctorSettings,
    check_classifier,
    run_classifier_doctor,
    smoke_request,
)
from masterclaw.classifiers.policy import SystemOneHttpConfig
from masterclaw.cli import build_parser, main


def synthetic_wire(payload):
    answers = {}
    for key, question in payload["questions"].items():
        if question["type"] == "noul":
            answers[key] = {"type": "noul", "noul": 0.99}
        elif question["type"] == "choice":
            first = next(iter(question["criteria"]))
            answers[key] = {
                "type": "choice",
                "choice": first,
                "confidence": 1.0,
                "probabilities": {label: float(label == first) for label in question["criteria"]},
            }
        else:
            answers[key] = {
                "type": "score",
                "score": 0.0,
                "confidence": 1.0,
                "legend": {str(i): label for i, label in enumerate(question["criteria"])},
                "probabilities": {str(i): float(i == 0) for i in range(len(question["criteria"]))},
            }
    return {"model": payload["model"], "version": "rev-1", "answers": answers}


@pytest.mark.parametrize("smoke", [False, True])
def test_doctor_default_only_get_smoke_only_synthetic(smoke):
    methods = []

    def handler(req):
        methods.append(req.method)
        if req.method == "GET":
            assert req.url.path == "/v1/models"
            assert req.content == b""
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "id": "local/system-one",
                            "revision": "rev-1",
                            "checkpoint": "/PRIVATE/path",
                        }
                    ]
                },
            )
        assert req.url.path == "/v1/systemone"
        payload = json.loads(req.content)
        assert payload["state"] == smoke_request().state
        assert payload["state"]["synthetic"] is True
        assert {question["type"] for question in payload["questions"].values()} == {
            "noul",
            "choice",
            "score",
        }
        return httpx.Response(200, json=synthetic_wire(payload))

    result = asyncio.run(
        check_classifier(
            SystemOneHttpConfig(expected_revision="rev-1"),
            smoke=smoke,
            transport=httpx.MockTransport(handler),
        )
    )
    assert result.ok and result.category == "ok"
    assert methods == ["GET"] + (["POST"] if smoke else [])
    assert "PRIVATE" not in repr(result)


@pytest.mark.parametrize(
    "case", ["missing", "bad_json", "missing_alias", "revision", "oversized", "redirect"]
)
def test_doctor_actionable_sanitized_failure_without_inference(case):
    methods = []

    def handler(req):
        methods.append(req.method)
        if case == "missing":
            raise httpx.ConnectError("PRIVATE checkpoint path and secret")
        if case == "bad_json":
            return httpx.Response(200, content=b"PRIVATE not JSON")
        if case == "oversized":
            return httpx.Response(200, content=b"x" * 300_000)
        if case == "redirect":
            return httpx.Response(302, headers={"Location": "https://PRIVATE.example"})
        return httpx.Response(
            200,
            json={
                "data": [
                    {
                        "id": "wrong" if case == "missing_alias" else "local/system-one",
                        "revision": "wrong",
                    }
                ]
            },
        )

    result = asyncio.run(
        check_classifier(
            SystemOneHttpConfig(expected_revision="rev-1"),
            smoke=True,
            transport=httpx.MockTransport(handler),
        )
    )
    assert not result.ok and result.category in {"network", "response", "request"}
    assert methods == ["GET"]
    assert "PRIVATE" not in repr(result)


def test_doctor_settings_need_no_discord_openrouter_or_gameplay_config(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MASTERCLAW_CLASSIFIER__PROVIDER", "system_one_http")
    configured = ClassifierDoctorSettings(_env_file=None)
    assert configured.classifier.provider == "system_one_http"
    assert configured.classifier.system_one_http.token is None


@pytest.mark.parametrize("smoke", [False, True])
def test_cli_dispatch_is_separate_and_explicit(monkeypatch, smoke):
    calls = []
    monkeypatch.setattr(
        "masterclaw.classifier_doctor.run_classifier_doctor",
        lambda **kwargs: calls.append(kwargs) or 0,
    )
    argv = ["classifier-doctor"] + (["--smoke"] if smoke else [])
    assert build_parser().parse_args(argv).smoke is smoke
    assert main(argv) == 0
    assert calls == [{"smoke": smoke}]


def test_doctor_cli_never_prints_server_details(monkeypatch, capsys):
    monkeypatch.setattr(
        "masterclaw.classifier_doctor.ClassifierDoctorSettings",
        lambda: SimpleNamespace(
            classifier=SimpleNamespace(
                provider="system_one_http", system_one_http=SystemOneHttpConfig()
            )
        ),
    )

    async def check(*args, **kwargs):
        return await check_classifier(
            SystemOneHttpConfig(),
            transport=httpx.MockTransport(
                lambda _: (_ for _ in ()).throw(httpx.ConnectError("PRIVATE /checkpoint"))
            ),
        )

    monkeypatch.setattr("masterclaw.classifier_doctor.check_classifier", check)
    assert run_classifier_doctor() == 1
    output = capsys.readouterr().out
    assert "network" in output and "externally supervised" in output
    assert "PRIVATE" not in output and "checkpoint" not in output
