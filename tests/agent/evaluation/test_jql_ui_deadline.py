"""UI observation drains real background work within the original action budget."""
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest

from tools import jql_ui_benchmark as harness


def runtime(monkeypatch, *, completion=85, drain=25, transport_error=False, metrics_error=False):
    clock = [0.0]
    state = {"ready": True, "complete": True, "cards": 1, "key": "task", "activeKey": "task"}
    calls, rows, requests = [], [], []
    page = SimpleNamespace(
        _jql_evidence={"pageId": "p1"},
        evaluate=lambda expression: dict(state) if expression == harness.STATE_JS else [],
        locator=lambda _: SimpleNamespace(count=lambda: 0),
    )

    def api(path, method="GET", **kwargs):
        if "/settle" not in path:
            if calls and metrics_error:
                raise OSError("metrics transport failed")
            return {"requests": list(requests)}
        budget = float(parse_qs(urlsplit(path).query)["timeoutSeconds"][0])
        calls.append((budget, kwargs["timeout"]))
        if transport_error:
            raise OSError("drain transport failed")
        clock[0] += min(drain, budget)
        requests.append({"path": "/rest/api/2/search", "done": drain <= budget})
        return {"complete": drain <= budget, "timedOut": drain > budget,
                "active": 0, "admitted": int(drain > budget), "waitSeconds": min(drain, budget)}

    def wait(_page, timeout=None):
        assert timeout == 100  # Action already spent 20 of its 120 seconds.
        clock[0] = completion
        return dict(state), 1, completion - 20

    monkeypatch.setattr(harness, "UI_TIMEOUT", 120)
    monkeypatch.setattr(harness.time, "perf_counter", lambda: clock[0])
    monkeypatch.setattr(harness, "request", api)
    monkeypatch.setattr(harness, "wait_state", wait)
    monkeypatch.setattr(harness, "capture_business", lambda *_: {"verified": True})
    monkeypatch.setattr(harness, "record_row", lambda _page, row, result: result.append(row))
    return page, clock, calls, rows


def test_background_pages_drain_after_ui_completion_without_renewing_deadline(monkeypatch):
    page, clock, calls, rows = runtime(monkeypatch)
    harness.measure(page, "background-prefix", lambda: clock.__setitem__(0, 20), rows)
    row = rows[0]
    assert calls == [(35, 40)]
    assert row["completeMs"] == 85000
    assert row["settledMs"] == 110000
    assert row["settlement"]["complete"] is True
    assert row["settlementBudgetSeconds"] == 35
    assert row["allRequests"] == row["searchRequests"] == 1
    assert row["deadlineAt"] == 120


def test_unfinished_admission_is_recorded_then_stops_next_action(monkeypatch):
    page, clock, calls, rows = runtime(monkeypatch, drain=40)
    with pytest.raises(TimeoutError, match="background work"):
        harness.measure(page, "unfinished", lambda: clock.__setitem__(0, 20), rows)
    assert calls == [(35, 40)]
    assert rows[0]["completeMs"] == 85000
    assert rows[0]["settledMs"] == 120000
    assert rows[0]["settlement"]["complete"] is False
    assert rows[0]["settlement"]["admitted"] == 1
    assert rows[0]["allRequests"] == 1


def test_failed_settlement_transport_retains_failed_action(monkeypatch):
    page, clock, _calls, rows = runtime(monkeypatch, transport_error=True)
    with pytest.raises(TimeoutError, match="background work"):
        harness.measure(page, "transport", lambda: clock.__setitem__(0, 20), rows)
    assert len(rows) == 1
    assert rows[0]["settlement"]["complete"] is False
    assert "drain transport failed" in rows[0]["settlement"]["error"]


def test_unavailable_server_preserves_row_with_unknown_costs(monkeypatch):
    page, clock, _calls, rows = runtime(monkeypatch, transport_error=True, metrics_error=True)
    with pytest.raises(TimeoutError, match="background work"):
        harness.measure(page, "server-unavailable", lambda: clock.__setitem__(0, 20), rows)
    assert len(rows) == 1
    row = rows[0]
    assert row["requests"] is row["allRequests"] is row["searchRequests"] is None
    assert row["requestAccountingComplete"] is False
    assert "metrics transport failed" in row["requestAccountingError"]
    assert "drain transport failed" in row["settlement"]["error"]


def test_zero_wait_budget_does_not_restart_default_timeout(monkeypatch):
    monkeypatch.setattr(harness.time, "perf_counter", lambda: 1.0)
    page = SimpleNamespace(evaluate=lambda _: pytest.fail("expired budget must not poll"))
    with pytest.raises(TimeoutError):
        harness.wait_state(page, timeout=0)


def test_slow_browser_evaluation_cannot_complete_after_deadline(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(harness.time, "perf_counter", lambda: clock[0])

    def evaluate(_expression):
        clock[0] = 11.0
        return {"complete": True, "cards": 1, "key": "task", "activeKey": "task"}

    with pytest.raises(TimeoutError):
        harness.wait_state(SimpleNamespace(evaluate=evaluate), timeout=10)
