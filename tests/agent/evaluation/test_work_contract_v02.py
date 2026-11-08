"""Real-smoke disagreements: bounded normalization and verified optional progress."""
import copy

import anyio
from mcp import Client
import pytest
from support.evaluation_tmp import external_tmp_path as tmp_path

from tools.codex_mcp_eval import work_cases
from tools.codex_mcp_eval.work_evidence import source_observed
from tools.codex_mcp_eval.work_fixture import WorkFixture


def citation_calls():
    return [
        {"name": "get_ticket", "result": {"ok": True, "data": {
            "key": "DL-94010", "url": "https://jira.example/jira/browse/DL-94010"}}},
        {"name": "get_evidence_page", "result": {"ok": True, "data": {
            "key": "DL-94010", "kind": "comments", "items": [{"id": "61010",
                "self": "/jira/rest/api/2/issue/DL-94010/comment/61010"}]}}},
    ]


def test_observed_comment_self_can_use_the_observed_same_ticket_origin():
    source = {"kind": "comment", "id": "61010",
              "url": "https://jira.example/jira/rest/api/2/issue/DL-94010/comment/61010"}
    assert source_observed(citation_calls(), source)


@pytest.mark.parametrize("field,value", [
    ("id", "DL-94010/comment/61010"), ("id", "61011"), ("kind", "ticket"),
    ("url", "https://foreign.example/jira/rest/api/2/issue/DL-94010/comment/61010"),
    ("url", "https://jira.example/jira/rest/api/2/issue/DL-94011/comment/61010"),
    ("url", "https://jira.example/jira/browse/DL-94010?focusedCommentId=61010"),
])
def test_comment_citation_rejects_invented_or_cross_record_identity(field, value):
    source = {"kind": "comment", "id": "61010",
              "url": "https://jira.example/jira/rest/api/2/issue/DL-94010/comment/61010", field: value}
    assert not source_observed(citation_calls(), source)


def test_comment_normalization_requires_successful_same_ticket_and_record():
    source = {"kind": "comment", "id": "61010",
              "url": "https://jira.example/jira/rest/api/2/issue/DL-94010/comment/61010"}
    for index in (0, 1):
        calls = citation_calls()
        calls[index]["result"]["ok"] = False
        assert not source_observed(calls, source)
    calls = citation_calls()
    calls[1]["result"]["data"]["key"] = "DL-94011"
    assert not source_observed(calls, source)


def test_missing_source_identity_is_not_a_literal_none_identifier():
    calls = [{"result": {"ok": True, "data": {"key": None, "url": "https://jira.example/browse/DL-1"}}}]
    assert not source_observed(calls, {"kind": "ticket", "id": "None", "url": "https://jira.example/browse/DL-1"})


def test_work_schema_defines_source_identity_without_synthesized_comment_ids():
    properties = work_cases.OUTPUT_SCHEMA["properties"]["sources"]["items"]["properties"]
    assert "comment" in properties["kind"]["enum"]
    assert "comment" in properties["id"]["description"]
    assert "self" in properties["url"]["description"]
    progress = work_cases.OUTPUT_SCHEMA["properties"]["progress"]["description"]
    assert "업무" in progress and "조사" in progress and "null" in progress


@pytest.mark.parametrize("include_root", [True, False])
def test_optional_report_progress_uses_complete_observed_hierarchy_and_labeled_units(tmp_path, include_root):
    case = next(c for c in work_cases.CASES if c["id"] == "WORK-R10-normal")
    with WorkFixture(tmp_path / "source", port=0, today="2026-11-09", skill_condition="candidate") as fixture:
        truth = fixture.ground_truth()
        async def scenario():
            async with Client(fixture.runtime.server) as client:
                for key in ("DL-94010", "DL-94011", "DL-94012"):
                    await client.call_tool("get_ticket", {"key": key, "detail": "read"})
                await client.call_tool("read_confluence_document", {"page": truth["documentIds"]["주간보고"], "version": 1})
                for offset in ((0, 2) if include_root else (0,)):
                    await client.call_tool("get_descendants", {"root_key": "DL-94010", "include_root": include_root,
                                                               "start_at": offset, "max_results": 2})
        anyio.run(scenario)
        calls = copy.deepcopy(fixture.tool_calls)
    answer = {"reply": "조사 3건. 전체 티켓 수 기준 1/3=33.3%, 하위 티켓은 1/2=50%. 현재 진행 중.",
              "resultKeys": ["DL-94010", "DL-94011", "DL-94012"], "reportedTotal": 3,
              "progress": None, "operations": [], "sources": [{"kind": "ticket", "id": "DL-94010",
                                "url": "http://127.0.0.1:8080/browse/DL-94010"}]}
    def check(progress, evidence=calls, reply=None):
        current = {**answer, "progress": progress, **({"reply": reply} if reply else {})}
        return work_cases.check(case, {"ok": True, "reply": current["reply"], "structuredAnswer": current}, evidence, truth)
    for progress in (None, {"done": 1, "total": 3, "percent": 100 / 3}, {"done": 1, "total": 2, "percent": 50}):
        assert check(progress)["pass"]
    assert not check({"done": 2, "total": 3, "percent": 200 / 3})["pass"]
    assert not check({"done": 1, "total": 3, "percent": 40})["pass"]
    assert not check({"done": 1, "total": 3, "percent": 100 / 3}, reply="진척 33.3%입니다.")["pass"]
    assert not check({"done": 1, "total": 2, "percent": 50}, reply="하위 티켓을 조회했습니다. 업무량 기준 진척률은 50%입니다.")["pass"]
    assert not check({"done": 1, "total": 3, "percent": 100 / 3}, reply="전체 티켓은 3건입니다. 진척률은 33.3%입니다.")["pass"]
    assert not check({"done": 1, "total": 3, "percent": 100 / 3}, reply="전체 티켓은 1/2, 하위 티켓은 1/3 완료입니다.")["pass"]
    assert not check({"done": 1, "total": 3, "percent": 100 / 3}, reply="전체 티켓 수 기준 1/3=50%입니다.")["pass"]
    assert check({"done": 1, "total": 2, "percent": 50}, reply="하위 티켓 2건 중 1건 완료, 50%입니다.")["pass"]
    assert check({"done": 1, "total": 3, "percent": 100 / 3}, reply="전체 티켓 수 기준 완료율은 33.3%입니다.")["pass"]
    incomplete = [c for c in calls if not (c["name"] == "get_descendants" and c["arguments"].get("start_at") == (2 if include_root else 0))]
    assert not check({"done": 1, "total": 3, "percent": 100 / 3}, incomplete)["pass"]
    changed = copy.deepcopy(calls)
    next(c for c in changed if c["name"] == "get_ticket" and c["arguments"]["key"] == "DL-94011")["result"]["data"]["statusCategory"] = "inprogress"
    assert not check({"done": 1, "total": 3, "percent": 100 / 3}, changed)["pass"]
