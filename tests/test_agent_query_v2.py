from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.agent.tools import _ctx
from app.agent.tools.query_tools import (execute_jql_all, run_jql_v2,
                                         search_documents, set_thread)
from app.agent.tools.search_tools import find_parent_epic
from app.agent.tools.people_tools import scoped_person_workload
from app.agent.workflow.agents.query_runner import QueryRunner
from app.agent.workflow.assignment_completion import (
    asks_incomplete_assignees, completion_topic,
)


def _issue(index: int, project: str) -> dict:
    return {
        "key": f"{project}-{index + 1}",
        "fields": {
            "project": {"key": project},
            "summary": f"ticket {index + 1}",
            "issuetype": {"name": "Bug" if index % 2 else "Task", "subtask": False},
            "status": {"name": "Open", "statusCategory": {"key": "new"}},
            "updated": "2026-08-11T00:00:00.000+0000",
        },
    }


class _Provider:
    def get_json(self, *_args, **_kwargs):
        return []


class _Client:
    def __init__(self, count=137):
        self.rows = [_issue(i, "AAA" if i % 2 == 0 else "BBB") for i in range(count)]
        self.calls = []
        self.provider = _Provider()

    def search_issues_page(self, jql, start_at=0, max_results=100, fields=None, light=True):
        self.calls.append({"jql": jql, "startAt": start_at, "maxResults": max_results,
                           "fields": fields, "light": light})
        rows = self.rows[start_at:start_at + max_results]
        nxt = start_at + len(rows)
        more = nxt < len(self.rows)
        return {"startAt": start_at, "maxResults": max_results, "total": len(self.rows),
                "issues": rows, "returned": len(rows), "hasMore": more,
                "nextStartAt": nxt if more else None}

    def _conf_get_json(self, _url, params=None):
        assert 'space in ("SPACE1", "SPACE2")' in params["cql"]
        return {
            "size": 2,
            "totalSize": 2,
            "results": [
                {"title": "allowed", "url": "/pages/1", "excerpt": "ok",
                 "content": {"id": "1", "space": {"key": "SPACE2"}}},
                {"title": "forbidden", "url": "/pages/2", "excerpt": "no",
                 "content": {"id": "2", "space": {"key": "OTHER"}}},
            ],
        }


def _settings(projects=None, spaces=None):
    return SimpleNamespace(
        search_jira_projects=list(projects or []),
        search_confluence_spaces=list(spaces or []),
        project_key="PRIMARY",
        jira_env="mock",
        confluence_base="http://confluence.example",
        epic_name_field_id="customfield_10011",
    )


@pytest.fixture(autouse=True)
def _reset_binding():
    _ctx.bind()
    set_thread("query-test")
    yield
    _ctx.bind()
    set_thread("")


def test_jql_scope_uses_every_configured_project_and_never_primary():
    fake = _Client(3)
    _ctx.bind(fake, _settings(["AAA", "BBB"]))
    result = run_jql_v2.invoke({
        "where": "status = Open OR project = ESCAPE",
        "order_by": "updated DESC",
        "page_size": 10,
    })
    assert "error" not in result
    assert result["scopeProjects"] == ["AAA", "BBB"]
    assert result["canonicalJql"].startswith(
        'project in ("AAA", "BBB") AND (status = Open OR project = ESCAPE)')
    assert "PRIMARY" not in result["canonicalJql"]


def test_empty_jira_search_config_does_not_fallback_to_primary_or_all():
    fake = _Client(3)
    _ctx.bind(fake, _settings([]))
    result = run_jql_v2.invoke({"where": "status = Open"})
    assert "search.jira.projects" in result["error"]
    assert result["tickets"] == []
    assert fake.calls == []


def test_parent_epic_candidates_also_use_all_search_projects_not_write_destination():
    fake = _Client(4)
    for row in fake.rows:
        row["fields"]["issuetype"] = {"name": "Epic", "subtask": False}
        row["fields"]["components"] = [{"name": "ETL"}]
        row["fields"]["customfield_10011"] = "후보"
    _ctx.bind(fake, _settings(["AAA", "BBB"]))
    rows = find_parent_epic.invoke({"query": "", "limit": 4})
    assert {x["key"].split("-", 1)[0] for x in rows} == {"AAA", "BBB"}
    assert fake.calls[0]["jql"].startswith(
        'project in ("AAA", "BBB") AND (issuetype = Epic)')
    assert "PRIMARY" not in fake.calls[0]["jql"]


def test_all_pagination_exhausts_more_than_fifty_results_without_skips():
    fake = _Client(137)
    _ctx.bind(fake, _settings(["AAA", "BBB"]))
    result = execute_jql_all("status is not EMPTY", page_size=50)
    assert result["returned"] == 137
    assert result["pages"] == 3
    assert [x["startAt"] for x in fake.calls] == [0, 50, 100]
    assert len({x["key"] for x in result["tickets"]}) == 137


def test_runner_preserves_full_page_as_artifact_but_caps_llm_context():
    fake = _Client(137)
    _ctx.bind(fake, _settings(["AAA", "BBB"]))
    got = QueryRunner()._run({"query_plan": {"queries": [{
        "id": "all-open", "source": "jira", "where": "status is not EMPTY",
        "order_by": "updated DESC", "page_size": 50, "completeness": "page"}]}})
    raw = got["query_artifacts"]["all-open"]
    compact = got["query_results"][0]["result"]
    assert len(raw["tickets"]) == 50
    assert len(compact["tickets"]) == 12
    assert compact["contextTruncated"] and compact["artifactId"] == "all-open"


def test_people_workload_uses_scoped_paginated_jql_not_primary_project_aggregate():
    fake = _Client(137)
    _ctx.bind(fake, _settings(["AAA", "BBB"]))
    result = scoped_person_workload("skcc.x1001", 28)
    assert result["scopeProjects"] == ["AAA", "BBB"]
    assert len(fake.calls) == 6  # open/inProgress/done 각각 100+37 두 page
    assert all(call["jql"].startswith('project in ("AAA", "BBB") AND (assignee = ')
               for call in fake.calls)
    assert all("PRIMARY" not in call["jql"] for call in fake.calls)


def test_cursor_is_bound_to_query_and_thread():
    fake = _Client(30)
    _ctx.bind(fake, _settings(["AAA", "BBB"]))
    first = run_jql_v2.invoke({"where": "status = Open", "page_size": 10})
    cursor = first["nextCursor"]
    assert cursor

    changed_query = run_jql_v2.invoke({
        "where": "status = Closed", "page_size": 10, "cursor": cursor})
    assert "cursor" in changed_query["error"]

    set_thread("another-thread")
    changed_thread = run_jql_v2.invoke({
        "where": "status = Open", "page_size": 10, "cursor": cursor})
    assert "cursor" in changed_thread["error"]


def test_confluence_uses_all_configured_spaces_and_filters_defensively():
    fake = _Client(0)
    _ctx.bind(fake, _settings(["AAA"], ["SPACE1", "SPACE2"]))
    result = search_documents.invoke({"query": "pipeline", "page_size": 10})
    assert "error" not in result
    assert result["scopeSpaces"] == ["SPACE1", "SPACE2"]
    assert [x["space"] for x in result["documents"]] == ["SPACE2"]


def test_empty_confluence_search_config_does_not_fallback():
    fake = _Client(0)
    _ctx.bind(fake, _settings(["AAA"], []))
    result = search_documents.invoke({"query": "anything"})
    assert "search.confluence.spaces" in result["error"]
    assert result["documents"] == []


def test_incomplete_assignee_query_joins_parent_children_and_hides_irrelevant(monkeypatch):
    """검색 한 건을 답으로 쓰지 않고 parent의 직계 Sub-Task 전체를 담당자별로 묶는다."""
    from langchain_core.messages import HumanMessage
    from app.agent.tools import search_tools

    class CompletionClient:
        def ticket_children(self, key):
            assert key == "AAA-100"
            rows = []
            for i in range(14):
                done = i < 10
                rows.append({
                    "key": f"AAA-{101 + i}", "summary": f"보안교육수강 - 인원 {i + 1}",
                    "status": "Closed" if done else "In Progress",
                    "statusCategory": "done" if done else "inprogress",
                    "assigneeId": f"skcc.x{1000 + i}", "assignee": f"작업자{i + 1}",
                })
            return rows

        def ticket_badge(self, key):
            return {"key": key, "summary": "보안 필수교육 수강 - IT서비스 자율보안체계 보안 교육"}

    fake_search = SimpleNamespace(invoke=lambda _args: {
        "jira": [
            {"key": "AAA-100", "title": "보안 필수교육 수강 - IT서비스 자율보안체계 보안 교육",
             "issuetype": "Task"},
            {"key": "AAA-999", "title": "레지스트리 보안 고도화", "issuetype": "Task"},
        ]})
    monkeypatch.setattr(search_tools, "search_work_history", fake_search)
    _ctx.bind(CompletionClient(), _settings(["AAA"]))

    asked = "보안 팔수 교육 수강 Task들 누가누가 미완료했나 궁금해"
    assert asks_incomplete_assignees(asked)
    assert "보안" in completion_topic(asked, ["보안 필수교육 수강"])
    got = QueryRunner()._run({
        "messages": [HumanMessage(content=asked)],
        "keywords": ["보안 필수교육 수강"], "query_plan": {"queries": []},
    })["assignment_completion"]
    assert [p["key"] for p in got["parents"]] == ["AAA-100"]
    assert got["totalSubtasks"] == 14 and got["doneSubtasks"] == 10
    assert got["incompleteSubtasks"] == 4 and len(got["people"]) == 4
    assert "AAA-999" not in repr(got)
