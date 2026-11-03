"""External evaluation contract tests; subprocesses are stubbed, no real LLM calls."""
import json
import subprocess
from types import SimpleNamespace

import anyio
import httpx2
import pytest
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

from tools import agent_eval_launcher, agent_eval_protocol as protocol
from tools.codex_mcp_eval import cases, cli, runner
from tools.codex_mcp_eval.fixture import EvaluationFixture


def _output(keys=(), total=None, reply=None, progress=None):
    answer = {"reply": reply if reply is not None else "\n".join(keys), "resultKeys": list(keys),
              "reportedTotal": total, "progress": progress, "sources": []}
    return {"ok": True, "reply": answer["reply"], "structuredAnswer": answer, "forbiddenToolEvents": [],
            "events": [], "usage": {"calls": None, "promptTokens": 10, "completionTokens": 5,
                                       "totalTokens": 15, "cachedTokens": 2, "costUsd": None}, "durationSeconds": 1}


def _case(identifier):
    return next(case for case in cases.CASES if case["id"] == identifier)


def _call(name, arguments=None, data=None, *, ok=True):
    return {"name": name, "arguments": arguments or {}, "result": {"ok": ok, "data": data or {}}}


def test_cli_security_final_output_and_private_trace_redaction(tmp_path):
    answer = _output(["DL-1"], 1)["structuredAnswer"]
    events = [{"type": "item.completed", "item": {"type": "reasoning", "text": "private reasoning"}},
              {"type": "item.completed", "item": {"type": "agent_message", "text": "intermediate commentary"}},
              {"type": "item.completed", "item": {"type": "agent_message", "text": json.dumps(answer)}},
              {"type": "turn.completed", "usage": {"input_tokens": 10, "output_tokens": 5, "cached_input_tokens": 2}}]
    def process(args, **kwargs):
        assert kwargs["shell"] is False and kwargs["env"][cli.TOKEN_ENV] == "private-bearer"
        assert "private-bearer" not in json.dumps(args)
        assert {"--ignore-user-config", "--ephemeral", "--sandbox", "read-only", "--output-schema"} <= set(args)
        assert 'features.shell_tool=false' in args and 'project_doc_max_bytes=0' in args
        assert 'web_search="disabled"' in args and 'features.skip_host_skill_discovery=true' in args
        return SimpleNamespace(returncode=0, stdout="\n".join(map(json.dumps, events)), stderr="")
    result = cli.run("codex.exe", "request", tmp_path / "workspace", mcp_url="http://127.0.0.1:4457/mcp",
                     token="private-bearer", schema=cases.OUTPUT_SCHEMA, process_run=process)
    assert result["ok"] and result["reply"] == "DL-1" and result["structuredAnswer"] == answer
    assert "private reasoning" not in json.dumps(result)
    assert result["usage"]["calls"] is None and result["usage"]["costUsd"] is None
    assert list((tmp_path / "workspace").iterdir()) == []


def test_timeout_preserves_safe_partial_events_and_reports_unknown_metrics(tmp_path):
    partial = json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": "partial answer"}})
    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], 10, output=partial.encode())
    result = cli.run("codex.exe", "request", tmp_path / "workspace", process_run=timeout)
    assert not result["ok"] and result["error"] == "codex_timeout" and result["incomplete"]
    assert result["reply"] == "partial answer" and result["events"]
    assert result["usage"]["promptTokens"] is None


def test_forbidden_external_tools_and_malformed_json_are_not_success():
    raw = '\n'.join([json.dumps({"type": "item.completed", "item": {"type": "command_execution"}}), "not JSON"])
    parsed = cli.parse_events(raw)
    assert parsed["forbiddenToolEvents"] == ["command_execution"] and "invalid_json_event" in parsed["errors"]


def _jql_calls(query=cases.EXACT_JQL):
    return [_call("search_issues", {"jql": query, "max_results": 3, "start_at": start,
                                    **({"snapshot_id": "snapshot"} if start else {})},
                  {"issues": [{"key": key} for key in cases.HISTORY_KEYS[start:start + 3]],
                   "total": 8, "snapshotId": "snapshot", "hasMore": start + 3 < 8}) for start in (0, 3, 6)]


def test_exact_jql_checks_prescribed_input_complete_pagination_and_visible_output():
    good = _output(cases.HISTORY_KEYS, 8)
    assert cases.check(_case("MCP-J1"), good, _jql_calls())["pass"]
    assert not cases.check(_case("MCP-J1"), good, _jql_calls("key in (unrelated)"))["pass"]
    wrong_reply = _output(cases.HISTORY_KEYS, 8, "총 99건\n" + "\n".join(reversed(cases.HISTORY_KEYS)))
    defects = cases.check(_case("MCP-J1"), wrong_reply, _jql_calls())["failures"]
    assert "reply_total_contradicts_structured_total" in defects and "reply_jql_order_mismatch" in defects
    truncated = _jql_calls()[:-1]
    assert not cases.check(_case("MCP-J1"), good, truncated)["pass"]
    changed = _jql_calls()
    changed[1]["arguments"]["snapshot_id"] = "different"
    assert "snapshot_changed_or_omitted" in cases.check(_case("MCP-J1"), good, changed)["failures"]


def test_wrong_identity_extra_assignments_and_false_progress_fail():
    truth = {"personId": "person", "personOpenKeys": ["DL-1"]}
    output = _output(["DL-1", "DL-9201"], 99, "wrong person 총 99건 DL-1 DL-9201")
    assert not cases.check(_case("S4-사람"), output, [_call("search_users", ok=False)], truth)["pass"]
    progress = _output(["DL-9093", "DL-9094", "DL-9095"], 3, "DL-9090 모두 완료 100%",
                       {"done": 3, "total": 3, "percent": 100})
    assert "progress_state_count_mismatch" in cases.check(_case("S6-진척"), progress, [_call("get_children")])["failures"]


def test_failed_or_tail_only_document_reads_are_not_complete_evidence():
    output = _output(["DL-7001"], 1, "DL-7001 모두 확인 완료")
    names = ("search_workspace", "get_ticket", "get_comments", "read_confluence_document")
    assert not cases.check(_case("MCP-U1"), output, [_call(name, ok=False) for name in names])["pass"]
    calls = [_call("search_workspace", data={"jira": {"items": [{}]}, "confluence": {"items": [{}]}}),
             _call("get_ticket", {"key": "DL-7001"}), _call("get_comments", {"key": "DL-7001"}),
             _call("read_confluence_document", data={"id": "1", "title": "[Lake] Iceberg Puffin NDV 적용 검토 노트",
                 "content": "tail", "offset": 96, "totalCharacters": 100, "hasMore": False, "nextOffset": None})]
    assert "document_body_not_complete" in cases.check(_case("MCP-U1"), output, calls)["failures"]


def test_protocol_external_identity_and_unavailable_metrics_are_explicit(monkeypatch):
    suite, specs = cases.review_contract()
    metadata = protocol.build_run_metadata(suite=cases.SUITE, battery_version=cases.BATTERY_VERSION,
        cases=cases.CASES, selected_case_ids=[case["id"] for case in cases.CASES],
        model=cli.MODEL, simple_model="not-applicable", prompt_version=cases.PROMPT_VERSION,
        suite_review_elements=suite, case_review_specs=specs, checker_dependencies=cases.CHECKER_DEPENDENCIES,
        external_runtime={"provider": "codex-chatgpt", "runtime": "codex-cli-jsonl", "version": "test"},
        benchmark_context={"fixtureDate": "2026-11-04"})
    assert metadata["run"]["provider"] == "codex-chatgpt"
    assert metadata["run"]["structuredOutputBackend"] == "codex-cli-jsonl"
    assert metadata["run"]["externalContext"]["fixtureDate"] == "2026-11-04"
    metrics = protocol.quantitative_metrics(attempts=1, duration_seconds=1, calls=None,
        prompt_tokens=10, completion_tokens=5, total_tokens=15, cached_tokens=2, cost_usd=None)
    assert metrics["calls"] is None and metrics["costUsd"] is None


def test_launcher_codex_lane_does_not_route_through_internal_llm_profiles(monkeypatch):
    import tools.agent_codex_mcp_eval as entry
    received = []
    monkeypatch.setattr(entry, "main", lambda args: received.append(args) or 7)
    monkeypatch.setattr(agent_eval_launcher, "_authorize_network", lambda *args: pytest.fail("wrong provider preflight"))
    assert agent_eval_launcher.main(["codex-mcp", "--run-group", "test"]) == 7
    assert received == [["--run-group", "test"]]


def test_runner_retains_real_sdk_page_trace_and_isolation_without_calling_llm(tmp_path, monkeypatch):
    raw = tmp_path / "raw"
    monkeypatch.setattr(protocol, "RAW_RESULT_ROOT", raw)
    monkeypatch.setattr(runner, "RAW_RESULT_ROOT", raw)
    def fixture(path, **kwargs):
        return EvaluationFixture(path, **{**kwargs, "port": 0})
    def fake_cli(binary, prompt, workspace, **kwargs):
        if not kwargs.get("mcp_url"):
            return {**_output(), "reply": "LTM_PREFLIGHT_OK"}
        async def scenario():
            async with httpx2.AsyncClient(headers={"Authorization": "Bearer " + kwargs["token"]}) as http:
                async with Client(streamable_http_client(kwargs["mcp_url"], http_client=http)) as mcp:
                    snapshot = None
                    for start in (0, 3, 6):
                        result = await mcp.call_tool("search_issues", {"jql": cases.EXACT_JQL, "start_at": start,
                            "max_results": 3, "snapshot_id": snapshot})
                        assert not result.is_error
                        snapshot = result.structured_content["data"].get("snapshotId")
        anyio.run(scenario)
        return _output(cases.HISTORY_KEYS, 8)
    def identity(*args, **kwargs):
        return {"runtime": "codex-cli-jsonl", "version": "test", "model": cli.MODEL, "authKind": "existing-chatgpt-login"}
    args = ["--run-group", "sdk-fixture", "--date", "2026-11-04", "--cases", "MCP-J1", "--codex", "stub"]
    assert runner.launch(args, cli_run=fake_cli, fixture_factory=fixture, identity_fn=identity) == 0
    result = json.loads(next((raw / "sdk-fixture").glob("codex-mcp-*.json")).read_text(encoding="utf-8"))
    assert result["executionComplete"] and result["summary"]["automaticPass"] == 1
    record = result["cases"][0]
    assert record["isolation"]["worldUnchanged"] and len(record["output"]["mcpToolCalls"]) == 3
    assert result["metrics"]["calls"] is None and result["humanReview"]["status"] == "pending"
    with pytest.raises(FileExistsError):
        runner.launch(args, cli_run=lambda *a, **k: pytest.fail("duplicate paid attempt"), fixture_factory=fixture, identity_fn=identity)
