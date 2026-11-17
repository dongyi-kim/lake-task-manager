"""Single-instance identity remains available when business admission is full."""
import io
import json
from types import SimpleNamespace
from urllib.error import HTTPError

import pytest

from app.launcher import instance


LIVE = {"status": "ok", "pid": 123, "instance": "worker-generation",
        "resources": {"busy": True, "supported": True}}
LEGACY = {"status": "ok", "env": "prod", "projectKey": "DL", "rev": "old"}


def response(payload):
    return io.BytesIO(json.dumps(payload).encode())


@pytest.mark.parametrize("status", [200, 503])
def test_existing_instance_is_focused_when_business_health_and_revision_are_overloaded(monkeypatch, status):
    calls = []
    payload = {**LIVE, "status": "ok" if status == 200 else "unresponsive"}
    def open_url(request, timeout):
        url = request if isinstance(request, str) else request.full_url
        calls.append(url)
        if url.endswith("/api/health/live"):
            if status == 503:
                raise HTTPError(url, 503, "Unavailable", {}, response(payload))
            return response(payload)
        if url.endswith(("/api/health", "/api/app/rev")):
            raise HTTPError(url, 429, "Busy", {}, response({"executed": False}))
        if url.endswith("/api/app/open"):
            return response({"action": "focus"})
        raise AssertionError("overload must never cause quit: " + url)
    monkeypatch.setattr(instance.urllib.request, "urlopen", open_url)
    monkeypatch.setattr(instance, "disk_rev", lambda: "new")
    assert instance.signal_existing_instance(SimpleNamespace(app_port=4457)) is True
    assert calls == ["http://127.0.0.1:4457/api/health/live",
                     "http://127.0.0.1:4457/api/app/rev", "http://127.0.0.1:4457/api/app/open"]


def test_older_instance_without_live_contract_retains_legacy_health_detection(monkeypatch):
    calls = []
    def open_url(url, timeout):
        calls.append(url)
        if url.endswith("/live"):
            raise HTTPError(url, 404, "Not found", {}, response({"detail": "Not Found"}))
        return response(LEGACY)
    monkeypatch.setattr(instance.urllib.request, "urlopen", open_url)
    assert instance.ltm_health("http://127.0.0.1:4457") == LEGACY
    assert calls == ["http://127.0.0.1:4457/api/health/live", "http://127.0.0.1:4457/api/health"]


@pytest.mark.parametrize("payload", [
    {"status": "ok"}, {**LIVE, "pid": True}, {**LIVE, "instance": 123},
    {**LIVE, "resources": {"busy": "yes", "supported": True}},
    {**LIVE, "status": "unrelated"}, {**LIVE, "status": []}, [], None,
])
def test_unrelated_or_malformed_live_contract_never_claims_an_ltm_instance(monkeypatch, payload):
    def open_url(url, timeout):
        return response(payload if url.endswith("/live") else {"status": "ok"})
    monkeypatch.setattr(instance.urllib.request, "urlopen", open_url)
    assert instance.ltm_health("http://127.0.0.1:4457") is None


def test_malformed_legacy_environment_is_rejected_without_crashing_launcher(monkeypatch):
    monkeypatch.setattr(instance.urllib.request, "urlopen",
                        lambda url, timeout: response({**LEGACY, "env": []}))
    assert instance.ltm_health("http://127.0.0.1:4457") is None
