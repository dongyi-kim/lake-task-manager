"""Authored reporting truth and real readonly transport; no real LLM or delays."""
import copy
import json
from datetime import datetime

import anyio
import httpx2
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

from tools.codex_mcp_eval import reporting_cases as battery
from tools.codex_mcp_eval.reporting_fixture import (
    DENIED_CHILD, EPIC, KST, TASK, ReportingFixture, build_reporting_data,
)


def test_authored_oracle_distinguishes_period_scope_and_semantic_units():
    data = build_reporting_data("2026-11-04")
    truth, issues = data["oracle"], data["issues"]
    assert truth["periods"] == {
        "day": {"start": "2026-11-03", "end": "2026-11-04"},
        "week": {"start": "2026-10-26", "end": "2026-11-02"},
        "month": {"start": "2026-10-01", "end": "2026-11-01"},
    }
    assert truth["completed"] == {
        "day": {"person": ["DL-92003", "DL-92021"], "module": ["DL-92003", "DL-92021"],
                "epic": ["DL-92003", "DL-92021"], "task": ["DL-92021"]},
        "week": {"person": ["DL-92002", "DL-92005", "DL-92014", "DL-92023", "DL-92024"],
                 "module": ["DL-92002", "DL-92004", "DL-92007", "DL-92014", "DL-92023", "DL-92024"],
                 "epic": ["DL-92002", "DL-92004", "DL-92007", "DL-92014", "DL-92023", "DL-92024"],
                 "task": ["DL-92014", "DL-92023", "DL-92024"]},
        "month": {"person": ["DL-92001", "DL-92002", "DL-92005", "DL-92006", "DL-92013", "DL-92014", "DL-92022", "DL-92023"],
                  "module": ["DL-92001", "DL-92002", "DL-92004", "DL-92006", "DL-92013", "DL-92014", "DL-92022", "DL-92023"],
                  "epic": ["DL-92001", "DL-92002", "DL-92004", "DL-92006", "DL-92013", "DL-92014", "DL-92022", "DL-92023"],
                  "task": ["DL-92014", "DL-92022", "DL-92023"]},
    }
    assert set(truth["semanticTables"]) == {"CRM.PUBLIC.CUSTOMERS", "CRM.PUBLIC.ORDERS",
        "CRM.PUBLIC.ORDER_LINES", "CRM.PUBLIC.SHIPMENTS", "SAP.PUBLIC.MATERIALS"}
    assert issues["DL-92009"]["resolved"] is None
    assert [entry["items"][0]["toString"] for entry in issues["DL-92009"]["changelog"]] == ["Resolved", "Reopened"]
    assert str(issues["DL-92008"]["updated"]) == "2026-11-04"
    assert issues[TASK]["updated"] < issues[DENIED_CHILD]["resolved"]
    assert issues[TASK]["statusCategory"] == "inprogress"
    assert all(issues[key]["statusCategory"] == "done" and issues[key]["epicKey"] is None for key in truth["children"])
    assert len(issues["DL-92004"]["comments"]) == 211
    assert "취소" in issues["DL-92004"]["comments"][-1]["body"]
    assert len(issues[DENIED_CHILD]["changelog"]) == 107
    assert issues[DENIED_CHILD]["changelog"][-1]["items"][0]["field"] == "summary"


def test_reporting_cases_cover_matrix_and_keep_generic_tool_contract():
    cases = battery.materialize_cases()
    assert len(cases) == 15 and len({row["id"] for row in cases}) == 15
    assert {(row["period"], row["scope"]) for row in cases if "period" in row} == {
        (period, scope) for period in ("day", "week", "month") for scope in ("person", "module", "epic", "task")}
    assert "get_work_report" not in json.dumps(cases)
    assert "2026-10-01" in next(row for row in cases if row["id"] == "RPT-INGEST")["prompts"][0]
    suite, specs = battery.review_contract()
    assert suite and set(specs) == {case["id"] for case in cases}
    assert specs["RPT-HISTORY"]["elements"][0]["expected"]["requiredTail"]["id"] == "1106"
    # Relative-date support must change the gold, not retain the reference five.
    shifted = build_reporting_data("2026-11-25")["oracle"]
    assert set(shifted["semanticTables"]) == {"CRM.PUBLIC.CUSTOMERS", "CRM.PUBLIC.SHIPMENTS", "SAP.PUBLIC.MATERIALS"}


def test_reporting_fixture_validates_rest_boundaries_and_sdk_cache_without_writes(tmp_path):
    with ReportingFixture(tmp_path / "reporting", port=0, today="2026-11-04") as fixture:
        conformance = fixture.assert_conformance()
        assert not conformance["cacheWarmed"] and fixture.upstream_calls == []
        assert datetime.fromisoformat(conformance["timezoneBoundary"]).astimezone(KST).isoformat() == "2026-10-01T00:00:00+09:00"
        assert set(fixture.store.issues) == set(build_reporting_data("2026-11-04")["issues"])
        assert fixture.ground_truth()["epicKey"] == EPIC

        async def scenario():
            async with httpx2.AsyncClient(headers={"Authorization": "Bearer " + fixture.token}) as http:
                async with Client(streamable_http_client(fixture.url, http_client=http)) as mcp:
                    query = 'parent = "DL-92020" ORDER BY key ASC'
                    first = await mcp.call_tool("search_issues", {"jql": query, "start_at": 0, "max_results": 2})
                    assert not first.is_error, first.structured_content
                    page = first.structured_content["data"]
                    assert page["total"] == 5 and len(page["issues"]) == 2
                    before = len(fixture.upstream_calls)
                    again = await mcp.call_tool("search_issues", {"jql": query, "start_at": 0, "max_results": 2})
                    assert not again.is_error and again.structured_content["data"] == page
                    assert len(fixture.upstream_calls) == before
                    child = await mcp.call_tool("get_ticket", {"key": DENIED_CHILD})
                    assert not child.is_error and child.structured_content["data"]["key"] == DENIED_CHILD
        anyio.run(scenario)
        summary = fixture.summary()
        assert summary["worldUnchanged"] and summary["providerStoreUnchanged"]


def test_permission_fault_preserves_parent_stub_but_denies_all_child_reads(tmp_path):
    with ReportingFixture(tmp_path / "partial", port=0, today="2026-11-04", fault="partial") as fixture:
        conformance = fixture.assert_conformance()
        assert DENIED_CHILD not in conformance["completedMonthModule"]
        assert fixture.upstream_calls == []
        raw = fixture.provider._client
        assert raw.get(f"/rest/api/2/issue/{DENIED_CHILD}").status_code == 403
        assert raw.get(f"/rest/api/2/issue/{DENIED_CHILD}", params={"fields": "summary,status"}).status_code == 403
        assert raw.get(f"/rest/api/2/issue/{DENIED_CHILD}/comment").status_code == 403
        parent = raw.get(f"/rest/api/2/issue/{TASK}").json()["fields"]
        assert DENIED_CHILD in {child["key"] for child in parent["subtasks"]}
        assert fixture.summary()["worldUnchanged"] and fixture.summary()["providerStoreUnchanged"]


def test_checker_rejects_lookalike_counts_unobserved_late_evidence_and_false_completeness():
    truth = build_reporting_data("2026-11-04")["oracle"]
    case = next(row for row in battery.CASES if row["id"] == "RPT-MONTH-MODULE")
    keys = truth["completed"]["month"]["module"]
    output = {"ok": True, "reply": "완료 목록 " + " ".join(keys), "structuredAnswer": {"resultKeys": keys, "reportedTotal": len(keys)}}
    calls = [{"result": {"ok": True, "data": {"issues": [{"key": key} for key in keys]}}}]
    assert battery.check(case, output, calls, truth)["pass"]
    wrong = copy.deepcopy(output)
    wrong["structuredAnswer"]["resultKeys"][-1] = "DL-92009"
    assert not battery.check(case, wrong, calls, truth)["pass"]

    ingest = next(row for row in battery.CASES if row["id"] == "RPT-INGEST")
    sources = ["DL-92001", "DL-92002", "DL-92004", "DL-92006"]
    output = {"ok": True, "reply": " ".join(truth["semanticTables"]) + " DL-92004 취소 제외",
              "structuredAnswer": {"resultKeys": sources, "reportedTotal": 5}}
    calls = [{"result": {"ok": True, "data": {"keys": sources}}}]
    assert "late_exclusion_comment_not_observed" in battery.check(ingest, output, calls, truth)["failures"]
    calls[0]["result"]["data"]["body"] = "CRM.PUBLIC.RETURNS 신규 적재를 취소"
    assert battery.check(ingest, output, calls, truth)["pass"]

    partial = next(row for row in battery.CASES if row["id"] == "RPT-PARTIAL")
    output = {"ok": True, "reply": f"{DENIED_CHILD} 권한 때문에 미확인", "structuredAnswer": {"resultKeys": [], "reportedTotal": None, "progress": None}}
    calls = [{"result": {"ok": True, "partial": True, "data": {"missing": [DENIED_CHILD]}}}]
    assert battery.check(partial, output, calls, truth)["pass"]
    output["structuredAnswer"].update(reportedTotal=5, progress={"done": 5, "total": 5, "percent": 100})
    assert "false_complete_progress_or_total" in battery.check(partial, output, calls, truth)["failures"]
