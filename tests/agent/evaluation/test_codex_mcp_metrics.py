"""Shared normal/recovery accounting; no model or external service is invoked."""
import anyio
import httpx2
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

from tools.codex_mcp_eval import cli, runner


def test_metrics_count_failed_recovery_attempts_without_double_counting_subsets():
    phases = [
        {"output": {"ok": False, "usage": {"promptTokens": 10, "completionTokens": 5,
             "totalTokens": 15, "cachedTokens": 4, "reasoningTokens": 2}}},
        {"output": {"ok": True, "usage": {"promptTokens": 20, "completionTokens": 8,
             "totalTokens": 28, "cachedTokens": 7, "reasoningTokens": 3}}},
    ]
    record = {"output": {"usage": {"promptTokens": 30, "completionTokens": 13, "totalTokens": 43,
              "cachedTokens": 11, "reasoningTokens": 5}}, "phases": phases,
              "durationSeconds": 3, "automaticPass": True, "upstreamCalls": [
                  {"service": "jira", "status": 503, "durationSeconds": 0.1, "responseBytes": 5},
                  {"service": "jira", "status": 200, "durationSeconds": 0.2, "responseBytes": 20}],
              "warmReplay": {"reads": [{"upstreamRequests": 1000}]}}
    result = runner._payload({"battery": {"name": "test"}}, [record], [{}], {})
    assert result["metrics"]["attempts"] == 2
    assert result["metrics"]["totalTokens"] == 43
    assert result["metrics"]["cachedTokens"] == 11
    assert result["metrics"]["reasoningTokens"] == 5
    assert result["summary"]["jiraApi"]["requests"] == 2


def test_partial_usage_stays_unknown_and_preserves_observed_lower_bound():
    record = {"output": {"usage": {"promptTokens": None}}, "phases": [
        {"output": {"ok": False, "usage": {"promptTokens": 9, "totalTokens": 12}}},
        {"output": {"ok": True, "usage": {}}}],
        "durationSeconds": 2, "automaticPass": False, "upstreamCalls": []}
    result = runner._payload({"battery": {"name": "test"}}, [record], [{}], {})
    assert result["metrics"]["promptTokens"] is None
    assert result["metrics"]["calls"] is None
    assert result["metrics"]["costUsd"] is None
    assert result["usageCoverage"]["promptTokens"] == {
        "observedAttempts": 1, "totalAttempts": 2, "observedTotal": 9}


def test_file_capability_is_opt_in_and_equal_for_baseline_candidate(tmp_path):
    from tools.codex_mcp_eval.work_fixture import WorkFixture
    catalogs = []
    for condition in ("baseline", "candidate"):
        root = tmp_path / condition
        root.mkdir()
        with WorkFixture(tmp_path / (condition + "-fixture"), port=0, today="2026-11-09",
                         skill_condition=condition, files_root=root) as fixture:
            async def scenario():
                async with httpx2.AsyncClient(headers={"Authorization": "Bearer " + fixture.token}) as http:
                    async with Client(streamable_http_client(fixture.files_url, http_client=http)) as mcp:
                        catalogs.append([tool.model_dump(mode="json") for tool in (await mcp.list_tools()).tools])
                        saved = await mcp.call_tool("write_file", {"path": "evidence/a.json", "content": "근거"})
                        assert not saved.is_error
                        read = await mcp.call_tool("read_file", {"path": "evidence/a.json"})
                        assert read.structured_content["content"] == "근거"
            anyio.run(scenario)
            assert len(fixture.files.calls) == 2
            assert fixture.upstream_calls == []
    assert catalogs[0] == catalogs[1]
    assert not runner.parse_args(["--battery", "work"]).research_files
    assert runner.parse_args(["--battery", "work", "--research-files"]).research_files


def test_cli_reasoning_usage_is_optional_subset_of_total():
    import json
    result = cli.parse_events(json.dumps({"type": "turn.completed", "usage": {
        "input_tokens": 20, "output_tokens": 12, "cached_input_tokens": 10,
        "reasoning_output_tokens": 8}}))
    assert result["usage"]["totalTokens"] == 32
    assert result["usage"]["reasoningTokens"] == 8
    assert cli.parse_events("")["usage"]["reasoningTokens"] is None


def test_paid_preflight_is_in_cumulative_cost_but_separate_from_case_metrics():
    record = {"output": {"usage": {"promptTokens": 10, "completionTokens": 5, "totalTokens": 15}},
              "durationSeconds": 2, "automaticPass": True, "upstreamCalls": []}
    preflight = {"durationSeconds": 1, "usage": {"promptTokens": 3, "completionTokens": 2, "totalTokens": 5}}
    result = runner._payload({"battery": {"name": "test"}}, [record], [{}], preflight)
    assert result["metrics"]["totalTokens"] == 15
    assert result["cumulativeModelMetrics"]["totalTokens"] == 20
    assert result["cumulativeModelMetrics"]["attempts"] == 2
    assert result["cumulativeModelMetrics"]["durationSeconds"] == 3


def test_wire_metrics_capture_discovery_and_rejected_calls_without_arguments(tmp_path):
    import json
    import httpx
    from tools.codex_mcp_eval.fixture import EvaluationFixture
    with EvaluationFixture(tmp_path / "fixture", port=0, today="2026-11-09") as fixture:
        async def scenario():
            async with httpx2.AsyncClient(headers={"Authorization": "Bearer " + fixture.token}) as http:
                async with Client(streamable_http_client(fixture.url, http_client=http)) as mcp:
                    await mcp.list_tools()
                    # Validation failures happen before/inside tool dispatch; neither
                    # a successful handler trace nor an answer can erase this cost.
                    rejected = await mcp.call_tool("get_ticket", {"key": "DL-9090", "fresh": "private-canary"})
                    assert rejected.is_error
        anyio.run(scenario)
        with httpx.Client() as http:
            assert http.post(fixture.url, json={"jsonrpc": "2.0", "id": 999,
                                               "method": "tools/list"}).status_code == 401
            permits = fixture.runtime.transport_host._permits
            acquired = 0
            try:
                while permits.acquire(blocking=False):
                    acquired += 1
                capacity = http.post(fixture.url,
                    headers={"Authorization": "Bearer " + fixture.token, "Accept": "application/json, text/event-stream"},
                    json={"jsonrpc": "2.0", "id": "capacity", "method": "tools/call",
                          "params": {"name": "get_ticket", "arguments": {"key": "DL-9090"}}})
                assert capacity.status_code == 429
            finally:
                for _ in range(acquired):
                    permits.release()
        calls = fixture.protocol_calls
        assert sum(call["method"] == "tools/call" for call in calls) == 2
        assert sum(call["method"] == "tools/list" for call in calls) == 1
        assert any(call["method"] in {"initialize", "server/discover"} for call in calls)
        rejected = next(call for call in calls if call["method"] == "tools/call")
        assert rejected["isError"] is True and rejected["httpStatus"] == 200
        assert any(call["id"] == "capacity" and call["isError"] and call["httpStatus"] == 429 for call in calls)
        assert "private-canary" not in json.dumps(calls)
        assert fixture.token not in json.dumps(calls)
        assert all("arguments" not in call and "params" not in call for call in calls)
        summary = runner._payload({"battery": {"name": "test"}}, [{"output": {},
            "durationSeconds": 1, "automaticPass": False, "upstreamCalls": [],
            "protocolCalls": calls}], [{}], {})["summary"]
        assert summary["mcpWire"]["ltmToolAttempts"] == 2
        assert summary["mcpWire"]["fileToolAttempts"] == 0
        assert summary["mcpWire"]["discoveryRequests"] >= 2


def test_fixture_settlement_wait_is_bounded_and_reports_pending_provider(tmp_path, monkeypatch):
    import threading
    import time
    from app.auth import inprocess
    from tools.codex_mcp_eval.fixture import EvaluationFixture
    with EvaluationFixture(tmp_path / "settlement", port=0, today="2026-11-09") as fixture:
        entered, release = threading.Event(), threading.Event()
        def delay(*args):
            entered.set()
            assert release.wait(timeout=2)
        monkeypatch.setattr(inprocess, "_sleep_for", delay)
        worker = threading.Thread(target=lambda: fixture.provider.get_json("/rest/api/2/myself"))
        worker.start()
        try:
            assert entered.wait(timeout=1)
            started = time.monotonic()
            pending = fixture.settle_observers(timeout_seconds=0.03)
            assert time.monotonic() - started < 0.3
            assert pending["complete"] is False and pending["timedOut"] is True
            assert pending["active"]["provider"] == 1
            from tools.codex_mcp_eval.attempts import settle_attempt
            record = {"modelAttempted": True, "output": {"usage": {"totalTokens": 12}}}
            settle_attempt(record, fixture, timeout_seconds=0.01, after_teardown=True)
            assert record["settlement"]["timedOut"] is True
            assert record["infrastructureError"] == "observer_settlement_timeout"
            assert not record["automaticPass"]
        finally:
            release.set()
            worker.join(timeout=2)
        assert fixture.settle_observers(timeout_seconds=0.2)["complete"] is True
        assert len(fixture.upstream_calls) == 1


def test_real_rest_dispatch_is_retained_before_response_completion(tmp_path):
    import threading
    from tools.codex_mcp_eval.fixture import EvaluationFixture
    with EvaluationFixture(tmp_path / "dispatch", port=0, today="2026-11-09") as fixture:
        entered, release = threading.Event(), threading.Event()
        def slow_request():
            entered.set()
            assert release.wait(timeout=2)
            return {"ok": True}
        fixture.provider._client.app.add_api_route("/rest/api/2/eval-delay", slow_request)
        worker = threading.Thread(target=lambda: fixture.provider.get_json("/rest/api/2/eval-delay"))
        worker.start()
        try:
            assert entered.wait(timeout=1)
            assert len(fixture.upstream_calls) == 1
            request = fixture.upstream_calls[0]
            assert request["dispatchAttempt"] is True and request["complete"] is False
            assert request["serverReceived"] is None
        finally:
            release.set()
            worker.join(timeout=2)
        request = fixture.upstream_calls[0]
        assert request["complete"] is True and request["serverReceived"] is True
        assert request["status"] == 200 and request["responseBytes"] > 0


def test_web_form_identity_probe_is_counted_at_real_dispatch_boundary(tmp_path):
    import pytest
    from starlette.responses import PlainTextResponse
    from app.auth.base import PermissionDenied
    from tools.codex_mcp_eval.fixture import EvaluationFixture
    with EvaluationFixture(tmp_path / "identity-probe", port=0, today="2026-11-09") as fixture:
        fixture.provider._client.app.add_api_route("/secure/ConvertIssue.jspa",
            lambda: PlainTextResponse("denied", status_code=403))
        # This provider path calls TestClient.get(myself) directly, bypassing
        # provider._get. The denied web request and its REST auth probe differ.
        with pytest.raises(PermissionDenied):
            fixture.provider.web_form("ConvertIssue.jspa")
        assert [call["path"] for call in fixture.upstream_calls] == ["/rest/api/2/myself"]
        assert [call["status"] for call in fixture.transport_calls] == [403, 200]
        assert all(call["dispatchAttempt"] and call["complete"] and call["serverReceived"]
                   for call in fixture.transport_calls)


def test_file_handler_is_included_in_bounded_settlement(tmp_path):
    import threading
    from tools.codex_mcp_eval.fixture import EvaluationFixture
    entered, release = threading.Event(), threading.Event()
    files_root = tmp_path / "files"
    files_root.mkdir()
    def after_write(*args):
        entered.set()
        assert release.wait(timeout=2)
    with EvaluationFixture(tmp_path / "files-fixture", port=0, today="2026-11-09",
                           files_root=files_root, files_after_write=after_write) as fixture:
        results = []
        def save():
            async def call():
                results.append(await fixture.files.server.call_tool("write_file", {"path": "a.json", "content": "saved"}))
            anyio.run(call)
        worker = threading.Thread(target=save)
        worker.start()
        try:
            assert entered.wait(timeout=1)
            pending = fixture.settle_observers(timeout_seconds=0.01)
            assert pending["complete"] is False and pending["active"]["file"] == 1
        finally:
            release.set()
            worker.join(timeout=2)
        assert results and not results[0].is_error
        assert fixture.settle_observers(timeout_seconds=0.2)["complete"] is True
