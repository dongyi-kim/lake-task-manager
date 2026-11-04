"""Identity evidence/fixture checks, independent of live Codex or semantic scoring."""
import copy

import anyio
import httpx2
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

from tools.codex_mcp_eval import identity_cases as battery
from tools.codex_mcp_eval.identity_fixture import IdentityFixture, build_identity_data
from tools.codex_mcp_eval.runner import _battery, parse_args


def test_identity_cases_preregister_ambiguity_compound_surname_and_id_distinction():
    data = build_identity_data("2026-11-04")
    assert len(battery.CASES) == len({case["id"] for case in battery.CASES}) == 9
    assert battery.SUITE == "codex-mcp-identity" and battery.BATTERY_VERSION == "1.0.1"
    assert data["oracle"]["identities"]["kang"]["id"] == "강수아 가람데이터"
    assert data["oracle"]["identities"]["kim"]["id"] != "김수아 나래솔루션"
    assert data["oracle"]["identities"]["namgung"]["openTaskKeys"] == ["DL-93061", "DL-93062"]
    suite, specs = battery.review_contract()
    assert suite and set(specs) == {case["id"] for case in battery.CASES}
    assert specs["ID-GIVEN-AMBIGUOUS"]["elements"][0]["expected"]["mustAskClarification"]
    assert specs["ID-NOT-FOUND"]["elements"][0]["expected"]["notFoundIsNotZeroTasks"]
    assert _battery("identities", "2026-11-04").fixture is IdentityFixture
    assert parse_args(["--battery", "identities", "--cases", "ID-HONORIFIC"]).battery == "identities"


def test_real_user_search_serialization_and_spaced_username_jql_are_preserved(tmp_path):
    with IdentityFixture(tmp_path / "identities", port=0, today="2026-11-04") as fixture:
        conformance = fixture.assert_conformance()
        assert not conformance["cacheWarmed"] and not conformance["aliasSearchOverride"]
        assert fixture.upstream_calls == []
        assert {row["name"] for row in conformance["userSearch"]["수아"]} == {
            "강수아 가람데이터", "identity.sua.kim"}
        # This source server does not pretend Jira understands colloquial honorifics.
        literal = fixture.provider._client.get("/rest/api/2/user/search", params={"username": "한울선임님"}).json()
        assert literal == []

        async def scenario():
            async with httpx2.AsyncClient(headers={"Authorization": "Bearer " + fixture.token}) as http:
                async with Client(streamable_http_client(fixture.url, http_client=http)) as mcp:
                    users = await mcp.call_tool("search_users", {"query": "수아"})
                    assert not users.is_error, users.structured_content
                    rows = users.structured_content["data"]["users"]
                    assert {row["id"] for row in rows} == {"강수아 가람데이터", "identity.sua.kim"}
                    query = 'project = DL AND assignee = "강수아 가람데이터" AND issuetype = Task AND statusCategory != Done ORDER BY key ASC'
                    first = await mcp.call_tool("search_issues", {"jql": query, "max_results": 1})
                    assert not first.is_error, first.structured_content
                    page = first.structured_content["data"]
                    assert page["total"] == 2 and [row["key"] for row in page["issues"]] == ["DL-93001"]
                    args = {"jql": query, "start_at": 1, "max_results": 1, "snapshot_id": page["snapshotId"]}
                    last = await mcp.call_tool("search_issues", args)
                    assert not last.is_error, last.structured_content
                    assert [row["key"] for row in last.structured_content["data"]["issues"]] == ["DL-93002"]
                    before = len(fixture.upstream_calls)
                    repeat = await mcp.call_tool("search_issues", {"jql": query, "max_results": 1})
                    assert not repeat.is_error and len(fixture.upstream_calls) == before
        anyio.run(scenario)
        assert fixture.tool_calls and all(row.get("result") for row in fixture.tool_calls)
        assert any("강수아 가람데이터" in row["params"].get("jql", "") for row in fixture.upstream_calls)
        summary = fixture.summary()
        assert summary["worldUnchanged"] and summary["providerStoreUnchanged"]
        original_client = fixture.client
    with IdentityFixture(tmp_path / "new-identities", port=0, today="2026-11-04") as other:
        assert other.client is not original_client and not other.tool_calls and not other.upstream_calls
        assert other.summary()["worldUnchanged"] and other.summary()["providerStoreUnchanged"]


def test_identity_checker_uses_observed_ids_not_query_arguments_or_display_names():
    truth = build_identity_data("2026-11-04")
    oracle = {**truth["oracle"], "users": truth["users"], "issues": truth["issues"]}
    case = next(row for row in battery.CASES if row["id"] == "ID-FULL-COMPANY")
    person = oracle["identities"]["kang"]
    answer = {"resultKeys": person["openTaskKeys"], "reportedTotal": 2, "progress": None,
              "sources": [{"kind": "person", "id": person["id"], "url": ""}]}
    output = {"ok": True, "reply": "합성 자료로 확인한 업무 목록", "structuredAnswer": answer}
    calls = [{"name": "search_issues", "arguments": {"jql": 'assignee = "강수아 가람데이터"'},
              "result": {"ok": True, "data": {"issues": [{"key": key} for key in person["openTaskKeys"]]}}}]
    assert "target_identity_not_observed_or_cited" in battery.check(case, output, calls, oracle)["failures"]
    calls.append({"name": "search_users", "result": {"ok": True, "data": {"users": [
        {"id": person["id"], "display": person["displayName"]}]}}})
    result = battery.check(case, output, calls, oracle)
    assert result["pass"] and result["humanReviewRequired"] and result["notAQualitativeScore"]
    changed = copy.deepcopy(output)
    changed["structuredAnswer"]["sources"][0]["id"] = "made.up.username"
    assert "unobserved_person_source" in battery.check(case, changed, calls, oracle)["failures"]
    calls[-1]["result"]["data"]["users"] = [{"displayName": person["displayName"]}]
    assert "target_identity_not_observed_or_cited" in battery.check(case, output, calls, oracle)["failures"]


def test_ambiguity_and_missing_person_require_unknown_total_but_prose_is_direct_review():
    data = build_identity_data("2026-11-04")
    truth = {**data["oracle"], "users": data["users"], "issues": data["issues"]}
    case = next(row for row in battery.CASES if row["id"] == "ID-GIVEN-AMBIGUOUS")
    candidates = [truth["identities"][alias] for alias in case["candidates"]]
    calls = [{"name": "search_users", "result": {"ok": True, "data": {"users": [
        {"id": person["id"], "display": person["displayName"]} for person in candidates]}}}]
    output = {"ok": True, "reply": "이 문장의 적절성은 자동 점수가 아닌 직접 리뷰 대상이다.",
              "structuredAnswer": {"resultKeys": [], "reportedTotal": None, "progress": None, "sources": []}}
    assert battery.check(case, output, calls, truth)["pass"]
    output["structuredAnswer"]["reportedTotal"] = 0
    assert "unresolved_identity_has_asserted_workload" in battery.check(case, output, calls, truth)["failures"]
    output["structuredAnswer"]["reportedTotal"] = None
    output["forbiddenToolEvents"] = [{"name": "shell"}]
    assert "unapproved_tool_used" in battery.check(case, output, calls, truth)["failures"]
    output.pop("forbiddenToolEvents")
    truth["readOnlyToolNames"] = ["search_users"]
    calls.append({"name": "create_task", "result": {"ok": False}})
    assert "tool_outside_read_only_catalog_requested" in battery.check(case, output, calls, truth)["failures"]


def test_missing_identity_requires_successful_relevant_empty_search():
    data = build_identity_data("2026-11-04")
    truth = {**data["oracle"], "users": data["users"], "issues": data["issues"]}
    case = next(row for row in battery.CASES if row["id"] == "ID-NOT-FOUND")
    output = {"ok": True, "reply": "이름을 확인해 주세요.", "structuredAnswer": {
        "resultKeys": [], "reportedTotal": None, "progress": None, "sources": []}}
    search = {"name": "search_users", "arguments": {"query": "유나"}, "result": {"ok": True, "data": {"users": []}}}
    for query in case["emptySearchQueries"]:
        search["arguments"]["query"] = query
        assert battery.check(case, output, [search], truth)["pass"]
    for bad in (
        {"name": "get_auth_status", "result": {"ok": True, "data": {"authenticated": True}}},
        {**search, "arguments": {"query": "관련 없는 사람"}},
        {**search, "isError": True}, {**search, "result": {"ok": False, "data": {"users": []}}},
        {**search, "result": {"ok": True, "data": {"users": [{"id": "someone"}]}}},
    ):
        assert "relevant_empty_identity_search_not_observed" in battery.check(case, output, [bad], truth)["failures"]


def test_context_identity_requires_key_and_assignee_in_same_successful_record():
    data = build_identity_data("2026-11-04")
    truth = {**data["oracle"], "users": data["users"], "issues": data["issues"]}
    case = next(row for row in battery.CASES if row["id"] == "ID-TICKET-CONTEXT")
    person = truth["identities"]["kang"]
    output = {"ok": True, "reply": "확인한 담당자의 미완료 업무", "structuredAnswer": {
        "resultKeys": person["openTaskKeys"], "reportedTotal": 2, "progress": None,
        "sources": [{"kind": "person", "id": person["id"], "url": ""}]}}
    baseline = [{"name": "search_users", "result": {"ok": True, "data": {"users": [{"id": person["id"]}]}}},
                {"name": "search_issues", "result": {"ok": True, "data": {
                    "issues": [{"key": key} for key in person["openTaskKeys"]]}}}]
    failure = "ticket_context_assignee_not_observed"
    assert failure in battery.check(case, output, baseline, truth)["failures"]
    for tool, record in (
        ("get_ticket", {"key": case["context"], "assigneeId": person["id"]}),
        ("search_issues", {"issues": [{"key": case["context"], "fields": {"assignee": {"name": person["id"]}}}]}),
        ("search_issues", {"issues": [{"key": case["context"], "fields": {"assignee": {"accountId": person["id"]}}}]}),
    ):
        linked = {"name": tool, "result": {"ok": True, "data": record}}
        assert battery.check(case, output, [*baseline, linked], truth)["pass"]
        assert failure in battery.check(case, output, [*baseline, {**linked, "isError": True}], truth)["failures"]
        assert failure in battery.check(case, output, [*baseline, {**linked, "result": {"ok": False, "data": record}}], truth)["failures"]
    split = {"name": "search_issues", "result": {"ok": True, "data": {"issues": [
        {"key": case["context"]}, {"key": "DL-93002", "assigneeId": person["id"]}]}}}
    assert failure in battery.check(case, output, [*baseline, split], truth)["failures"]
