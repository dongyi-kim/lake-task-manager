"""Already observed batched status fields must not require redundant body reads."""
import copy
import pytest

from tools.codex_mcp_eval import work_cases
from tools.codex_mcp_eval.work_data import build_work_data


@pytest.mark.parametrize("tool", ["search_issues", "get_tickets"])
def test_progress_counts_accept_typed_bulk_statuses_without_body_strings(tool):
    case = next(row for row in work_cases.CASES if row["id"] == "WORK-R04-normal")
    issues = [{"key": key, "url": "http://127.0.0.1:8080/browse/" + key,
               "fields": {"status": {"name": name, "statusCategory": {"key": category}}}}
              for key, name, category in [("DL-94010", "In Progress", "indeterminate"),
                                          ("DL-94011", "Resolved", "done"),
                                          ("DL-94012", "In Progress", "indeterminate")]]
    calls = [{"name": tool, "result": {"ok": True, "data": {
        "issues": issues, "total": 3, "returned": 3, "hasMore": False, "nextStartAt": None}}}]
    answer = {"reply": "부모는 진행 중, 하위 티켓 수 기준 완료 1/2=50%입니다. 조사 티켓 3건입니다.",
              "resultKeys": [row["key"] for row in issues], "reportedTotal": 3,
              "progress": {"done": 1, "total": 2, "percent": 50}, "operations": [],
              "sources": [{"kind": "ticket", "id": issues[0]["key"], "url": issues[0]["url"]}]}
    output = {"ok": True, "reply": answer["reply"], "structuredAnswer": answer}
    truth = build_work_data("2026-11-09")["oracle"]
    assert work_cases.check(case, output, calls, truth)["pass"]
    wrong, missing = copy.deepcopy(calls), copy.deepcopy(calls)
    wrong[0]["result"]["data"]["issues"][1]["fields"]["status"]["statusCategory"]["key"] = "indeterminate"
    missing[0]["result"]["data"]["issues"][1]["fields"].pop("status")
    missing[0]["result"]["data"]["issues"][1]["fields"]["description"] = {
        "key": "DL-94011", "statusCategory": "done"}  # Untrusted body is not a status field.
    for changed in (wrong, missing):
        assert not work_cases.check(case, output, changed, truth)["pass"]
    assert not work_cases.check(case, output, [{**calls[0], "result": {"ok": False, "data": calls[0]["result"]["data"]}}], truth)["pass"]


def test_upstream_metrics_keep_actual_requests_separate_and_unknown_totals_null():
    from tools.codex_mcp_eval.runner import _upstream_summary
    records = [{"upstreamCalls": [{"responseBytes": 120, "durationSeconds": 0.2},
                                  {"responseBytes": 80, "durationSeconds": 0.1}]}]
    assert _upstream_summary(records) == {"observed": True, "requests": 2, "responseBytes": 200,
                                          "sumRequestSeconds": 0.3}
    assert _upstream_summary([{"upstreamCalls": []}])["requests"] == 0
    assert _upstream_summary([{}])["requests"] is None
    records[0]["upstreamCalls"][1]["responseBytes"] = None
    assert _upstream_summary(records)["responseBytes"] is None
