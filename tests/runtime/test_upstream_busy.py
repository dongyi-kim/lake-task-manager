"""HTTP busy responses must not trigger authentication or transport recovery."""
import json
import time
from types import SimpleNamespace

import pytest

import app.main as main
from app.auth.base import SessionExpired, UpstreamBusy
from app.infra.cache import Cache
from app.jira.jira_client import JiraClient


def test_queue_pressure_does_not_open_transport_circuit(monkeypatch):
    calls = []
    monkeypatch.setattr(main._client, "mark_upstream_down", lambda *args: calls.append(args))
    response = main._on_upstream_unavailable(None, UpstreamBusy("queue full"))
    payload = json.loads(response.body)
    assert response.status_code == 503
    assert response.headers["retry-after"] == "1"
    assert payload["code"] == "upstream_busy"
    assert payload["retryable"] and not payload["needLogin"]
    assert "executed" not in payload and "uncertain" not in payload
    assert not calls


@pytest.fixture
def client():
    cache = Cache(":memory:")
    result = JiraClient(SimpleNamespace(jira_env="prod", jira_base="https://jira.test", project_key="DL"), cache)
    yield result
    result.close()
    cache.close()


def test_busy_auth_confirmation_returns_busy_without_transport_or_login_side_effects(client, monkeypatch):
    def busy(*_args, **_kwargs):
        raise UpstreamBusy("queue full during auth confirmation")

    client._provider = SimpleNamespace(get_json=busy)
    client._provider_built = True
    monkeypatch.setattr(main, "_client", client)
    monkeypatch.setattr(main, "_settings", SimpleNamespace(jira_env="prod"))
    response = main._on_session_expired(None, SessionExpired("initial request denied"))
    payload = json.loads(response.body)
    assert response.status_code == 503
    assert response.headers["retry-after"] == "1"
    assert payload["code"] == "upstream_busy"
    assert payload["retryable"] and not payload["needLogin"]
    assert not client.upstream_down()
    assert not getattr(client, "_session_dead", False)


@pytest.mark.parametrize("busy_stage", ["initial_probe", "renewal", "confirmation"])
def test_busy_auth_probe_stays_pending_and_rechecks_immediately_after_recovery(client, busy_stage):
    calls, renewals = [], []
    outcomes = ([UpstreamBusy("queue full"), {"name": "tester"}]
                if busy_stage == "initial_probe" else
                [SessionExpired("expired"), {"name": "tester"}]
                if busy_stage == "renewal" else
                [SessionExpired("expired"), UpstreamBusy("queue full"), {"name": "tester"}])

    def probe(*_args, **_kwargs):
        calls.append(1)
        outcome = outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    def renew(_service):
        renewals.append(1)
        if busy_stage == "renewal":
            raise UpstreamBusy("renewal not admitted")
        return True

    client._provider = SimpleNamespace(get_json=probe)
    client._provider_built = True
    client._renew_service = renew
    # A forced recovery must not leave a previous degraded memo masking the next healthy probe.
    client._auth_probe_result = {"ok": False, "needLogin": False, "mode": "degraded"}
    client._auth_probe_completed_result = dict(client._auth_probe_result)
    client._auth_probe_at = time.monotonic()
    first = client.proactive_auth_probe(force=True)
    assert first == {"ok": False, "needLogin": False, "pending": True,
                     "retryable": True, "mode": "checking"}
    assert client._auth_probe_result is None and client._auth_probe_at == 0
    assert client._auth_probe_completed_result is None
    assert client._auth_maintenance["state"] == "checking"
    assert client._auth_maintenance["failure"] is None
    assert client._auth_maintenance["lastCheckedAt"] is None
    assert not client.upstream_down()
    assert not getattr(client, "_session_dead", False)
    second = client.proactive_auth_probe()
    assert second == {"ok": True, "needLogin": False, "mode": "ok"}
    assert not outcomes
    assert len(calls) == (3 if busy_stage == "confirmation" else 2)
    assert len(renewals) == (0 if busy_stage == "initial_probe" else 1)


def test_busy_silent_renewal_is_not_memoized_as_failed_authentication(client):
    calls = []

    def renew(*_args, **_kwargs):
        calls.append(1)
        if len(calls) == 1:
            raise UpstreamBusy("renewal queue full")
        return True

    client.s.auth_targets = [("Jira", "https://jira.example", ["/rest/api/2/myself"])]
    client._sso_store = SimpleNamespace(revision=lambda _: 1)
    client._provider = SimpleNamespace(renew_silent=renew)
    client._provider_built = True
    with pytest.raises(UpstreamBusy):
        client._renew_service("Jira")
    assert "jira" not in client._renew_at and "jira" not in client._renew_results
    assert not client.upstream_down()
    assert not getattr(client, "_session_dead", False)
    assert client._renew_service("Jira") is True
    assert len(calls) == 2
