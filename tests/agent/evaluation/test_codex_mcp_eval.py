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


@pytest.mark.parametrize("keys", [["DL-9028"], ["DL-9028", "DL-9002", "DL-9018"]])
def test_current_user_recommendation_accepts_grounded_ranked_subsets(keys):
    truth = {"currentUserOpenKeys": ["DL-9002", "DL-9018", "DL-9028", "DL-9050"]}
    result = cases.check(_case("S5-내일"), _output(keys, len(keys)), [_call("get_user")], truth)
    assert result["pass"]
    assert result["humanReviewRequired"], "priority and the primary recommendation still need direct review"


@pytest.mark.parametrize("keys,reply", [
    ([], "추천 없음"),
    (["DL-9028", "DL-9990"], "DL-9028 DL-9990"),  # another person's assignment
    (["DL-9028", "DL-9201"], "DL-9028 DL-9201"),  # completed, absent from open oracle
    (["DL-9028", "DL-9002"], "먼저 DL-9028"),
    (["DL-9028", "DL-9028"], "DL-9028"),
    (["DL-9028"], "DL-90280"),  # substring is not a citation of the recommended key
])
def test_current_user_recommendation_rejects_empty_foreign_done_missing_and_duplicate_results(keys, reply):
    truth = {"currentUserOpenKeys": ["DL-9002", "DL-9018", "DL-9028"]}
    assert not cases.check(_case("S5-내일"), _output(keys, len(keys), reply), [_call("get_user")], truth)["pass"]


def test_failed_or_tail_only_document_reads_are_not_complete_evidence():
    output = _output(["DL-7001"], 1, "DL-7001 모두 확인 완료")
    names = ("search_workspace", "get_ticket", "get_comments", "read_confluence_document")
    assert not cases.check(_case("MCP-U1"), output, [_call(name, ok=False) for name in names])["pass"]
    calls = [_call("search_workspace", data={"jira": {"items": [{}]}, "confluence": {"items": [{}]}}),
             _call("get_ticket", {"key": "DL-7001"}), _call("get_comments", {"key": "DL-7001"}),
             _call("read_confluence_document", data={"id": "1", "title": "[Lake] Iceberg Puffin NDV 적용 검토 노트",
                 "content": "tail", "offset": 96, "totalCharacters": 100, "hasMore": False, "nextOffset": None})]
    assert "document_body_not_complete" in cases.check(_case("MCP-U1"), output, calls)["failures"]


@pytest.mark.parametrize('tool', ['get_ticket', 'get_tickets', 'search_issues'])
def test_unified_search_accepts_actual_complete_body_from_single_or_batch(tool):
    from copy import deepcopy
    body = {'key': 'DL-7001', 'descriptionHtml': '<p>source</p>'} if tool == 'get_ticket' else {
        'issues': [{'key': 'DL-7001', 'fields': {'description': 'source'}}]}
    calls = [_call('search_workspace', data={'jira': {'items': [{}]}, 'confluence': {'items': [{}]}}),
             _call(tool, {'key': 'DL-7001'} if tool == 'get_ticket' else {'keys': ['DL-7001']}, body),
             _call('get_comments', {'key': 'DL-7001'}, {'comments': []}),
             _call('read_confluence_document', data={'id': '1', 'title': 'Puffin NDV 적용 검토 노트',
                 'content': 'source', 'offset': 0, 'totalCharacters': 6, 'hasMore': False})]
    output = _output(['DL-7001'], 1)
    assert cases.check(_case('MCP-U1'), output, calls)['pass']
    for failed in (True, False):
        bad = deepcopy(calls)
        if failed:
            bad[1]['result']['ok'] = False
        else:
            bad[1]['result']['data'] = {'key': 'DL-7001'} if tool == 'get_ticket' else {
                'issues': [{'key': 'DL-7001', 'fields': {'summary': 'source'}}]}
        assert 'required_ticket_body_not_observed' in cases.check(_case('MCP-U1'), output, bad)['failures']


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


def test_reporting_runner_date_applies_to_prompts_and_review_oracle():
    options = runner.parse_args(["--battery", "reporting", "--date", "2026-12-08",
                                 "--cases", "RPT-DAY-PERSON", "RPT-MONTH-MODULE"])
    selected = runner._battery(options.battery, options.date)
    suite, specs = selected.review_contract()
    assert suite and selected.SUITE == "codex-mcp-reporting"
    day = next(case for case in selected.CASES if case["id"] == "RPT-DAY-PERSON")
    month = next(case for case in selected.CASES if case["id"] == "RPT-MONTH-MODULE")
    assert "2026-12-07 00:00" in day["prompts"][0] and "2026-12-08 00:00" in day["prompts"][0]
    assert "2026-11-01 00:00" in month["prompts"][0] and "2026-12-01 00:00" in month["prompts"][0]
    assert specs[day["id"]]["elements"][0]["expected"]["period"] == {"start": "2026-12-07", "end": "2026-12-08"}
    assert specs[month["id"]]["elements"][0]["expected"]["period"] == {"start": "2026-11-01", "end": "2026-12-01"}


def test_warm_replay_failure_retains_paid_answer_measured_trace_and_raw_attempt(tmp_path, monkeypatch):
    from tools.codex_mcp_eval import warm_replay

    raw = tmp_path / "raw"
    monkeypatch.setattr(protocol, "RAW_RESULT_ROOT", raw)
    monkeypatch.setattr(runner, "RAW_RESULT_ROOT", raw)
    class Fixture:
        catalog_sha256 = "a" * 64
        settings = SimpleNamespace(search_jira_projects=["DL"], search_confluence_spaces=["DL"])
        url, token = "http://127.0.0.1:4457/mcp", "never-transmitted-test-token"

        def __init__(self, *args, **kwargs):
            self.tool_calls = _jql_calls()
            self.upstream_calls = [{"path": "/rest/api/2/search", "durationSeconds": 0.01}]

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def assert_conformance(self):
            return {"cacheWarmed": False}

        def ground_truth(self):
            return {}

        def summary(self):
            return {"worldUnchanged": True, "providerStoreUnchanged": True}

    paid = _output(cases.HISTORY_KEYS, 8)
    def fake_cli(binary, prompt, workspace, **kwargs):
        return paid if kwargs.get("mcp_url") else {**_output(), "reply": "LTM_PREFLIGHT_OK"}

    def fail_after_diagnostic_read(fixture, calls):
        fixture.tool_calls.append(_call("get_ticket", {"key": "DL-999999"}, ok=False))
        fixture.upstream_calls.append({"path": "/diagnostic-only", "durationSeconds": 0.5})
        raise TimeoutError("diagnostic transport timed out")

    monkeypatch.setattr(warm_replay, "replay", fail_after_diagnostic_read)
    assert runner.launch(["--run-group", "warm-failure", "--date", "2026-11-04", "--cases", "MCP-J1",
                          "--codex", "stub", "--warm-replay"], cli_run=fake_cli, fixture_factory=Fixture,
                         identity_fn=lambda *a, **k: {"runtime": "codex-cli-jsonl", "version": "test"}) == 0
    result = json.loads(next((raw / "warm-failure").glob("codex-mcp-*.json")).read_text(encoding="utf-8"))
    record = result["cases"][0]
    assert result["executionComplete"] and record["automaticPass"]
    assert record["output"]["reply"] == paid["reply"]
    assert record["output"]["usage"] == paid["usage"] and record["durationSeconds"] == 1
    assert len(record["output"]["mcpToolCalls"]) == 3
    assert record["upstreamCalls"] == [{"path": "/rest/api/2/search", "durationSeconds": 0.01}]
    assert record["warmReplay"] == {"measuredAfterAnswer": True, "error": "warm_replay_failed", "errorType": "TimeoutError"}
    assert (raw / record["sourceSnapshot"]).is_file()
    assert (raw / "warm-failure" / "attempt-01" / "MCP-J1" / "events.json").is_file()


@pytest.mark.parametrize("warm", [False, True])
def test_runner_retains_real_sdk_page_trace_and_isolation_without_calling_llm(tmp_path, monkeypatch, warm):
    raw = tmp_path / "raw"
    monkeypatch.setattr(protocol, "RAW_RESULT_ROOT", raw)
    monkeypatch.setattr(runner, "RAW_RESULT_ROOT", raw)
    def fixture(path, **kwargs):
        return EvaluationFixture(path, **{**kwargs, "port": 0})
    def fake_cli(binary, prompt, workspace, **kwargs):
        if not kwargs.get("mcp_url"):
            return {**_output(), "reply": "LTM_PREFLIGHT_OK."}
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
    if warm:
        args.append("--warm-replay")
    assert runner.launch(args, cli_run=fake_cli, fixture_factory=fixture, identity_fn=identity) == 0
    result = json.loads(next((raw / "sdk-fixture").glob("codex-mcp-*.json")).read_text(encoding="utf-8"))
    assert result["executionComplete"] and result["summary"]["automaticPass"] == 1
    conformance = result["evaluation"]["run"]["externalContext"]["fixtureConformance"]
    assert conformance["parentJql"]["normal"]["total"] == 3
    assert conformance["parentJql"]["denied"]["total"] == 2
    assert conformance["directDenial"] == 403 and not conformance["cacheWarmed"]
    record = result["cases"][0]
    assert record["isolation"]["worldUnchanged"] and len(record["output"]["mcpToolCalls"]) == 3
    if warm:
        replay = record["warmReplay"]
        assert replay["uniqueCalls"] == 3 and len(replay["reads"]) == 9
        assert all(row["ok"] and row["dataByteIdentical"] for row in replay["reads"])
        assert sum(row["upstreamRequests"] for row in replay["reads"]) == 0
    else:
        assert record["warmReplay"] is None
    assert result["metrics"]["calls"] is None and result["humanReview"]["status"] == "pending"
    with pytest.raises(FileExistsError):
        runner.launch(args, cli_run=lambda *a, **k: pytest.fail("duplicate paid attempt"), fixture_factory=fixture, identity_fn=identity)
