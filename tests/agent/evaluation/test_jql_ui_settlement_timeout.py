"""Bounded local settlement endpoint; no full world, provider or app server."""
from functools import partial
import threading

import pytest
from starlette.applications import Starlette
from starlette.routing import Route
from starlette.testclient import TestClient

from tools import jql_ui_benchmark_fixture as fixture


class RecordingLedger:
    def __init__(self):
        self.timeouts = []
        self.result = {"complete": False, "timedOut": True, "active": 2,
                       "admitted": 3, "waitSeconds": .025}

    def settle(self, timeout=10):
        self.timeouts.append(timeout)
        return self.result


def client_for(ledger, host="127.0.0.1"):
    app = Starlette(routes=[Route("/__jql_benchmark/settle",
        partial(fixture.settlement_response, ledger=ledger), methods=["POST"])])
    return TestClient(app, client=(host, 50000))


@pytest.mark.parametrize("value,expected", [(None, 10), ("0", 0), ("0.025", .025),
                                            ("120", 120), ("1e1", 10)])
def test_endpoint_passes_valid_budget_and_preserves_actual_ledger_result(value, expected):
    ledger = RecordingLedger()
    with client_for(ledger) as client:
        response = client.post("/__jql_benchmark/settle",
                               params={} if value is None else {"timeoutSeconds": value})
    assert response.status_code == 200
    assert ledger.timeouts == [expected]
    assert response.json() == ledger.result


@pytest.mark.parametrize("value", ["", "abc", "true", "NaN", "Infinity", "-Infinity",
                                    "-0.001", "120.001", "1e1000"])
def test_invalid_budget_is_rejected_before_any_ledger_work(value):
    ledger = RecordingLedger()
    with client_for(ledger) as client:
        response = client.post("/__jql_benchmark/settle", params={"timeoutSeconds": value})
    assert response.status_code == 400
    assert ledger.timeouts == []


@pytest.mark.parametrize("host", ["127.0.0.1", "::1"])
def test_local_clients_remain_allowed(host):
    ledger = RecordingLedger()
    with client_for(ledger, host) as client:
        assert client.post("/__jql_benchmark/settle").status_code == 200
    assert ledger.timeouts == [10]


def test_localhost_guard_precedes_even_invalid_budget():
    ledger = RecordingLedger()
    with client_for(ledger, "192.0.2.1") as client:
        response = client.post("/__jql_benchmark/settle?timeoutSeconds=NaN")
    assert response.status_code == 403
    assert ledger.timeouts == []


def test_zero_budget_immediately_reports_admitted_work_without_false_completion():
    ledger = fixture.WireLedger()
    ledger.admitted = 1
    with client_for(ledger) as client:
        result = client.post("/__jql_benchmark/settle?timeoutSeconds=0").json()
    assert result["complete"] is False
    assert result["timedOut"] is True
    assert result["active"] == 0
    assert result["admitted"] == 1
    assert 0 <= result["waitSeconds"] < .1


def test_zero_budget_can_report_already_quiet_idle_ledger():
    ledger = fixture.WireLedger()
    ledger.last_activity -= 1
    with client_for(ledger) as client:
        result = client.post("/__jql_benchmark/settle?timeoutSeconds=0").json()
    assert result["complete"] is True
    assert result["timedOut"] is False
    assert result["active"] == result["admitted"] == 0


def test_custom_budget_still_times_out_live_admission_and_later_can_settle():
    ledger = fixture.WireLedger()
    entered, release = threading.Event(), threading.Event()

    def waiting_work():
        entered.set()
        assert release.wait(2)

    worker = threading.Thread(target=ledger.observe_admission(waiting_work))
    worker.start()
    try:
        assert entered.wait(1)
        with client_for(ledger) as client:
            result = client.post("/__jql_benchmark/settle?timeoutSeconds=0.02").json()
        assert result["complete"] is False
        assert result["timedOut"] is True
        assert result["active"] == 0
        assert result["admitted"] == 1
        assert .015 <= result["waitSeconds"] < .5
        assert ledger.snapshot() == []  # Admission is not a second REST attempt.
    finally:
        release.set()
        worker.join(2)
    assert not worker.is_alive()
    with client_for(ledger) as client:
        result = client.post("/__jql_benchmark/settle?timeoutSeconds=0.5").json()
    assert result["complete"] is True
    assert result["timedOut"] is False
    assert result["active"] == result["admitted"] == 0
