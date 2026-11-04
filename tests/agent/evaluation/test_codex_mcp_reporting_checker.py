"""Reporting checker regressions; authored responses, never an LLM call."""
import copy
import hashlib
import json

import pytest

from tools.agent_eval_isolation import _canonical
from tools.codex_mcp_eval import reporting_cases as battery
from tools.codex_mcp_eval.reporting_fixture import TASK, build_reporting_data


DATES = ("2026-11-04", "2026-11-25", "2026-12-08")
BASE_TABLES = ["CRM.PUBLIC.CUSTOMERS", "CRM.PUBLIC.SHIPMENTS", "SAP.PUBLIC.MATERIALS"]
BASE_SOURCES = ["DL-92001", "DL-92006", "DL-92013", "DL-92022"]


def _ingestion(date):
    world = build_reporting_data(date)
    tables, keys = list(BASE_TABLES), list(BASE_SOURCES)
    if date == "2026-11-04":
        tables += ["CRM.PUBLIC.ORDERS", "CRM.PUBLIC.ORDER_LINES"]
        keys += ["DL-92002", "DL-92004", "DL-92014", "DL-92023"]
    calls = [{"arguments": {"key": key}, "result": {"ok": True,
              "data": {"key": key, "descriptionHtml": world["issues"][key]["description"]}}} for key in keys]
    if date == "2026-11-04":
        calls.append({"arguments": {"key": "DL-92004", "kind": "comments"},
                      "result": {"ok": True, "data": {"key": "DL-92004", "items": [
                          {"id": "50210", "body": world["issues"]["DL-92004"]["comments"][-1]["body"]}]}}})
    output = {"ok": True, "reply": " ".join([*tables, *keys]) + " 기존 테이블 변경 및 취소 제외",
              "structuredAnswer": {"resultKeys": keys, "reportedTotal": len(tables)}}
    case = next(row for row in battery.materialize_cases(date) if row["id"] == "RPT-INGEST")
    return case, output, calls, world["oracle"]


@pytest.mark.parametrize("date", DATES)
def test_ingestion_accepts_only_period_eligible_sources_and_required_cancellations(date):
    case, output, calls, truth = _ingestion(date)
    assert battery.check(case, output, calls, truth)["pass"]
    assert truth["semanticTableSources"]["CRM.PUBLIC.SHIPMENTS"] == ["DL-92022"]
    assert truth["semanticTableSources"]["SAP.PUBLIC.MATERIALS"] == ["DL-92006"]
    cancelled = truth["semanticCancellations"]
    assert [row["key"] for row in cancelled] == (["DL-92004"] if date == "2026-11-04" else [])
    _, specs = battery.review_contract(date)
    expected = specs[case["id"]]["elements"][0]["expected"]
    assert expected["tableSources"] == truth["semanticTableSources"]
    assert expected["cancellations"] == cancelled


@pytest.mark.parametrize("table,removed_keys", [
    ("CRM.PUBLIC.ORDER_LINES", {"DL-92014", "DL-92023"}),
    ("CRM.PUBLIC.SHIPMENTS", {"DL-92022"}),
])
def test_ingestion_rejects_table_without_a_contributing_ticket_even_with_correct_total(table, removed_keys):
    case, output, calls, truth = _ingestion(DATES[0])
    output["structuredAnswer"]["resultKeys"] = [key for key in output["structuredAnswer"]["resultKeys"] if key not in removed_keys]
    calls = [call for call in calls if call["arguments"]["key"] not in removed_keys]
    # A mention by another, observed ticket does not prove this deliverable.
    calls[0]["result"]["data"]["descriptionHtml"] += " related " + table
    failures = battery.check(case, output, calls, truth)["failures"]
    assert "semantic_source_missing" in failures
    assert "included_table_evidence_not_observed" in failures


@pytest.mark.parametrize("date", ["2026-11-04", "2026-11-25"])
def test_korean_particles_after_verified_table_and_ticket_identifiers_are_prose(date):
    case, output, calls, truth = _ingestion(date)
    output["reply"] = "와 ".join(truth["semanticTables"]) + "의 적재를 확인했습니다. "
    output["reply"] += " ".join(key + "를 검토했습니다." for key in output["structuredAnswer"]["resultKeys"]
                               if key != "DL-92004")
    if date == "2026-11-04":
        output["reply"] += " DL-92004는 취소했으므로 제외했습니다."
    output["reply"] += " 기존 변경은 제외했습니다."
    assert battery.check(case, output, calls, truth)["pass"]


def test_cancellation_citation_cannot_substitute_a_different_ticket_number():
    case, output, calls, truth = _ingestion("2026-11-04")
    output["reply"] = output["reply"].replace("DL-92004", "DL-920040")
    assert "semantic_source_missing" in battery.check(case, output, calls, truth)["failures"]


@pytest.mark.parametrize("mode", ["failed", "missing_text", "foreign_ticket", "argument_only", "prefix_only"])
def test_ingestion_requires_successful_response_text_at_the_eligible_source(mode):
    case, output, calls, truth = _ingestion("2026-11-25")
    target = next(call for call in calls if call["arguments"]["key"] == "DL-92022")
    if mode == "failed":
        target["result"]["ok"] = False
    else:
        target["result"]["data"]["descriptionHtml"] = ""
        if mode == "foreign_ticket":
            calls[0]["result"]["data"]["descriptionHtml"] += " CRM.PUBLIC.SHIPMENTS"
        elif mode == "argument_only":
            target["arguments"]["query"] = "CRM.PUBLIC.SHIPMENTS"
        elif mode == "prefix_only":
            target["result"]["data"]["descriptionHtml"] = "CRM.PUBLIC.SHIPMENTS_ARCHIVE"
    assert "included_table_evidence_not_observed" in battery.check(case, output, calls, truth)["failures"]


@pytest.mark.parametrize("omitted_key", ["DL-92014", "DL-92001"])
def test_duplicate_table_accepts_either_observed_eligible_source_and_requires_in_period_cancellation(omitted_key):
    case, output, calls, truth = _ingestion(DATES[0])
    output["structuredAnswer"]["resultKeys"].remove(omitted_key)
    calls = [call for call in calls if call["arguments"]["key"] != omitted_key]
    assert battery.check(case, output, calls, truth)["pass"]
    without_cancellation = [call for call in calls if call["arguments"].get("kind") != "comments"]
    assert "late_exclusion_comment_not_observed" in battery.check(case, output, without_cancellation, truth)["failures"]


def test_batch_response_keeps_nested_ticket_evidence_separate_and_failed_calls_do_not_count():
    case, output, calls, truth = _ingestion("2026-11-25")
    records = [call["result"]["data"] for call in calls]
    batch = [{"result": {"ok": True, "data": {"issues": records}}}]
    assert battery.check(case, output, batch, truth)["pass"]
    shipment = next(row for row in records if row["key"] == "DL-92022")
    text = shipment.pop("descriptionHtml")
    shipment["related"] = {"key": "DL-92001", "descriptionHtml": text}
    failed = {"isError": True, "arguments": {"key": "DL-92022"},
              "result": {"ok": True, "data": {"descriptionHtml": text}}}
    assert "included_table_evidence_not_observed" in battery.check(case, output, [*batch, failed], truth)["failures"]


def test_missing_new_oracle_contract_fails_closed_instead_of_regrading_old_raw():
    case, output, calls, truth = _ingestion("2026-11-25")
    del truth["semanticCancellations"]
    assert "oracle_unavailable" in battery.check(case, output, calls, truth)["failures"]


@pytest.mark.parametrize("date", DATES)
def test_history_only_requires_tail_and_activity_when_within_requested_period(date):
    world = build_reporting_data(date)
    truth = world["oracle"]
    keys = sorted([TASK, *truth["children"]])
    start, end = truth["periods"]["month"].values()
    calls = [{"result": {"ok": True, "data": {"issues": [{"key": key} for key in keys]}}}]
    for key in keys:
        issue = world["issues"][key]
        relevant_history = [event for event in issue["changelog"] if start <= str(event["date"]) < end]
        relevant_comments = [event for event in issue["comments"] if start <= str(event["created"]) < end]
        if relevant_history or relevant_comments:
            calls.append({"arguments": {"key": key}, "result": {"ok": True, "data": {
                "history": _canonical(relevant_history), "comments": _canonical(relevant_comments)}}})
    output = {"ok": True, "reply": f"{TASK} 부모는 진행 중이고 하위만 100% 완료",
              "structuredAnswer": {"resultKeys": keys, "reportedTotal": 6,
                                   "progress": {"done": 5, "total": 5, "percent": 100}}}
    case = next(row for row in battery.materialize_cases(date) if row["id"] == "RPT-HISTORY")
    assert battery.check(case, output, calls, truth)["pass"]
    _, specs = battery.review_contract(date)
    expected = specs[case["id"]]["elements"][0]["expected"]
    assert bool(expected["requiredTail"]) == (date == "2026-11-04")
    assert bool(expected["requiredActivity"]) == (date == "2026-11-04")
    assert {row["key"] for row in expected["requiredPeriodHistory"]} == (
        {"DL-92014", "DL-92022", "DL-92023"} if date == "2026-11-04" else {"DL-92022"})
    # Shifted dates still contain SHIPMENTS' completion: an empty activity
    # report justified only by current ticket keys must never pass.
    assert "period_history_not_observed" in battery.check(case, output, calls[:1], truth)["failures"]
    timeline = [{"arguments": {"key": TASK}, "result": {"ok": True, "data": {"timeline": [
        {"srcKey": row["key"], "date": row["date"], "field": row["field"],
         "from": row["from"], "to": row["to"]} for row in truth["requiredPeriodHistory"]]}}}]
    assert "period_history_not_observed" not in battery.check(case, output, [calls[0], *timeline], truth)["failures"]
    if date == "2026-11-04":
        missing = copy.deepcopy(calls)
        for call in missing:
            if call.get("arguments", {}).get("key") == "DL-92023":
                call["result"]["data"]["comments"] = []
        assert "descendant_comment_activity_not_observed" in battery.check(case, output, missing, truth)["failures"]


@pytest.mark.parametrize("date,expected_hash", [
    ("2026-11-04", "bd14eb6be327856a6fcd8bdb8d1ad60348b4d7b5b589cf1252ebced089b5b20e"),
    ("2026-11-25", "af708fd41a87dfa7f8b9ba7a59d81379b44c8c33c28f47a6a1f83e30019fdcae"),
    ("2026-12-08", "0ae8b4dd3c4800f362027ac8cff2ee74e492e905671f60d25975bc5319fefa7a"),
])
def test_checker_revision_preserves_the_frozen_authored_world(date, expected_hash):
    source = _canonical(build_reporting_data(date)["issues"])
    actual = hashlib.sha256(json.dumps(source, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    assert actual == expected_hash
