from __future__ import annotations

from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from app.agent.tools import _ctx
from app.agent.tools.query_tools import (execute_jql_all, run_jql_v2,
                                         query_people, search_documents, set_thread)
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


def test_runner_filters_generic_web_navigation_before_evidence_but_keeps_raw_artifact(
        monkeypatch):
    from app.agent import tools as T

    generic = [
        {"title": "Search the documentation - StarRocks",
         "url": "https://docs.starrocks.io/search/", "snippet": "Search the documentation"},
        {"title": "StarRocks", "url": "https://www.starrocks.io/",
         "snippet": "High-performance analytical database"},
        {"title": "StarRocks intro",
         "url": "https://docs.starrocks.io/docs/introduction/StarRocks_intro/",
         "snippet": "Product introduction"},
        {"title": "README", "url":
         "https://github.com/StarRocks/starrocks/blob/main/docs/README.md",
         "snippet": "Contributor License Agreement and Markdown checks"},
    ]
    component_readme = {
        "title": "Puffin component specification",
        "url": "https://github.com/apache/iceberg/blob/main/puffin/README.md",
        "snippet": "Puffin NDV binary layout and validation rules", "official": True,
    }
    direct = {"title": "Apache Iceberg Puffin specification",
              "url": "https://iceberg.apache.org/puffin-spec/",
              "snippet": "Puffin files store NDV statistics", "official": True}
    monkeypatch.setitem(T.BY_NAME, "search_web", SimpleNamespace(invoke=lambda _args: {
        "query": "StarRocks Puffin NDV official documentation",
        "attempted": True, "results": [*generic, component_readme, direct],
    }))

    got = QueryRunner()._run({"query_plan": {"queries": [{
        "id": "external-official", "source": "web",
        "query": "StarRocks Puffin NDV official documentation", "page_size": 5,
    }]}})

    assert got["query_results"][0]["result"]["results"] == [component_readme, direct]
    assert got["query_results"][0]["result"]["genericResultsFiltered"] == 4
    assert got["query_artifacts"]["external-official"]["results"] == [
        *generic, component_readme, direct,
    ]


def test_runner_keeps_direct_official_intro_for_definition_request(monkeypatch):
    from app.agent import tools as T

    intro = {
        "title": "StarRocks introduction",
        "url": "https://docs.starrocks.io/docs/introduction/StarRocks_intro/",
        "snippet": "StarRocks is an analytical database", "official": True,
    }
    monkeypatch.setitem(T.BY_NAME, "search_web", SimpleNamespace(invoke=lambda _args: {
        "query": "StarRocks official documentation", "attempted": True,
        "results": [intro],
    }))

    got = QueryRunner()._run({
        "intent": "ask", "request_text": "StarRocks가 뭐야",
        "messages": [HumanMessage(content="StarRocks가 뭐야")],
        "query_plan": {"queries": [{
            "id": "overview", "source": "web",
            "query": "StarRocks official documentation", "page_size": 5,
        }]},
    })

    result = got["query_results"][0]["result"]
    assert result["results"] == [intro]
    assert "genericResultsFiltered" not in result


def test_runner_drops_unrelated_product_intro_for_other_subject_explanation(monkeypatch):
    from app.agent import tools as T

    intro = {
        "title": "StarRocks introduction",
        "url": "https://docs.starrocks.io/docs/introduction/StarRocks_intro/",
        "snippet": "StarRocks is an analytical database", "official": True,
    }
    monkeypatch.setitem(T.BY_NAME, "search_web", SimpleNamespace(invoke=lambda _args: {
        "query": "Puffin NDV official documentation", "attempted": True,
        "results": [intro],
    }))

    got = QueryRunner()._run({
        "intent": "ask", "request_text": "Puffin NDV 설명해줘",
        "messages": [HumanMessage(content="Puffin NDV 설명해줘")],
        "query_plan": {"queries": [{
            "id": "definition", "source": "web",
            "query": "Puffin NDV official documentation", "page_size": 5,
        }]},
    })

    result = got["query_results"][0]["result"]
    assert result["results"] == []
    assert result["genericResultsFiltered"] == 1


@pytest.mark.parametrize(("source", "prompt_text", "hit"), [
    (
        "web", "StarRocks 공식 홈페이지 알려줘",
        {"title": "StarRocks", "url": "https://www.starrocks.io/",
         "snippet": "Official StarRocks homepage"},
    ),
    (
        "web", "StarRocks 공식 문서 링크 알려줘",
        {"title": "StarRocks Documentation", "url": "https://docs.starrocks.io/docs/",
         "snippet": "Official StarRocks documentation"},
    ),
    (
        "github", "Qwen 공식 GitHub 저장소 찾아줘",
        {"title": "QwenLM/Qwen3 - GitHub", "url": "https://github.com/QwenLM/Qwen3",
         "snippet": "Official Qwen model repository"},
    ),
])
def test_runner_keeps_explicitly_requested_official_navigation_target(
        monkeypatch, source, prompt_text, hit):
    from app.agent import tools as T

    monkeypatch.setitem(T.BY_NAME, "search_" + source, SimpleNamespace(invoke=lambda _args: {
        "query": prompt_text, "attempted": True, "results": [hit],
    }))
    got = QueryRunner()._run({
        "intent": "ask", "request_text": prompt_text,
        "messages": [HumanMessage(content=prompt_text)],
        "query_plan": {"queries": [{
            "id": "official-navigation", "source": source,
            "query": prompt_text, "page_size": 5,
        }]},
    })

    result = got["query_results"][0]["result"]
    assert result["results"] == [hit]
    assert "genericResultsFiltered" not in result


@pytest.mark.parametrize(("source", "prompt_text", "hit"), [
    (
        "web", "StarRocks Puffin NDV writer pipeline을 분석해줘",
        {"title": "StarRocks", "url": "https://www.starrocks.io/",
         "snippet": "Official StarRocks homepage"},
    ),
    (
        "github", "Qwen structured output 동작을 분석해줘",
        {"title": "QwenLM/Qwen3 - GitHub", "url": "https://github.com/QwenLM/Qwen3",
         "snippet": "Official Qwen model repository"},
    ),
])
def test_runner_filters_navigation_target_for_feature_specific_research(
        monkeypatch, source, prompt_text, hit):
    from app.agent import tools as T

    monkeypatch.setitem(T.BY_NAME, "search_" + source, SimpleNamespace(invoke=lambda _args: {
        "query": prompt_text, "attempted": True, "results": [hit],
    }))
    got = QueryRunner()._run({
        "intent": "ask", "request_text": prompt_text,
        "messages": [HumanMessage(content=prompt_text)],
        "query_plan": {"queries": [{
            "id": "feature-research", "source": source,
            "query": prompt_text, "page_size": 5,
        }]},
    })

    result = got["query_results"][0]["result"]
    assert result["results"] == []
    assert result["genericResultsFiltered"] == 1


def test_materialized_ticket_ledger_merges_only_for_true_continuation():
    from app.agent.workflow.agents.query_runner import _merge_materialized_ticket_sources

    prior = {
        "ticketDetails": [{"key": "DL-9200", "type": "Epic"},
                          {"key": "DL-9201", "type": "Task"}],
        "parentCandidateKeys": [],
        "parentCandidateSearchAttempted": True,
    }
    current = {
        "ticketDetails": [{"key": "DL-9202", "type": "Task"}],
        "parentCandidateKeys": [],
    }

    continued = _merge_materialized_ticket_sources(
        {"turn_continuation": True, "materialized_ticket_sources": prior}, current,
    )
    fresh = _merge_materialized_ticket_sources(
        {"turn_continuation": False, "materialized_ticket_sources": prior}, current,
    )

    assert [row["key"] for row in continued["ticketDetails"]] == [
        "DL-9202", "DL-9200", "DL-9201",
    ]
    assert [row["key"] for row in fresh["ticketDetails"]] == ["DL-9202"]
    assert continued["parentCandidateSearchAttempted"] is True
    assert "parentCandidateSearchAttempted" not in fresh


def test_materialized_ticket_ledger_reserves_current_non_parent_inside_full_cap():
    from app.agent.workflow.agents.query_runner import _merge_materialized_ticket_sources

    prior = {
        "ticketDetails": [
            {"key": f"DL-{index}", "type": "Task", "summary": "prior history"}
            for index in range(1, 9)
        ],
        "parentCandidateKeys": [],
    }
    current = {
        "ticketDetails": [{
            "key": "DL-100", "type": "Task", "summary": "current exact target",
        }],
        "parentCandidateKeys": [],
    }

    got = _merge_materialized_ticket_sources(
        {"turn_continuation": True, "materialized_ticket_sources": prior}, current,
    )

    assert len(got["ticketDetails"]) == 8
    assert got["ticketDetails"][0] == {
        "key": "DL-100", "type": "Task", "summary": "current exact target",
    }
    assert "DL-8" not in {row["key"] for row in got["ticketDetails"]}


def test_materialized_ticket_ledger_reserves_new_exact_parent_inside_full_cap():
    from app.agent.workflow.agents.query_runner import _merge_materialized_ticket_sources

    prior = {
        "ticketDetails": [
            {"key": f"DL-{index}", "type": "Task", "summary": "old"}
            for index in range(1, 8)
        ] + [{"key": "DL-99", "type": "Epic", "summary": "stale"}],
        "parentCandidateKeys": [],
    }
    current = {
        "ticketDetails": [
            {"key": "DL-100", "type": "Epic", "summary": "new exact parent"},
            {"key": "DL-99", "type": "Epic", "summary": "fresh exact detail"},
        ],
        "parentCandidateKeys": ["DL-100", "DL-99"],
    }

    got = _merge_materialized_ticket_sources(
        {"turn_continuation": True, "materialized_ticket_sources": prior}, current,
    )

    assert len(got["ticketDetails"]) == 8
    assert got["parentCandidateKeys"] == ["DL-100", "DL-99"]
    assert got["ticketDetails"][0] == {
        "key": "DL-100", "type": "Epic", "summary": "new exact parent",
    }
    assert got["ticketDetails"][1] == {
        "key": "DL-99", "type": "Epic", "summary": "fresh exact detail",
    }


def test_runner_marks_successful_zero_hit_parent_candidate_search_as_attempted():
    fake = _Client(0)
    _ctx.bind(fake, _settings(["AAA", "BBB"]))

    got = QueryRunner()._run({"query_plan": {"queries": [{
        "id": "parent-candidate-check", "source": "jira",
        "query": "NoSuchPublicTopic", "where": "issueType = Epic",
        "page_size": 50, "completeness": "all",
    }]}})

    result = got["query_results"][0]["result"]
    assert result["tickets"] == [] and not result.get("error")
    assert got["materialized_ticket_sources"] == {
        "parentCandidateSearchAttempted": True,
    }


def test_runner_does_not_mark_failed_parent_candidate_search_as_attempted():
    fake = _Client(0)
    _ctx.bind(fake, _settings([]))

    got = QueryRunner()._run({"query_plan": {"queries": [{
        "id": "parent-candidate-check", "source": "jira",
        "query": "NoSuchPublicTopic", "where": "issueType = Epic",
        "page_size": 50, "completeness": "all",
    }]}})

    assert got["query_results"][0]["result"].get("error")
    assert "parentCandidateSearchAttempted" not in got["materialized_ticket_sources"]


def test_research_query_runner_materializes_ticket_and_document_bodies(monkeypatch):
    """One-pass synthesis receives the body/comment evidence that ReAct used to open later."""
    from app.agent import tools as T

    fake = _Client(3)
    _ctx.bind(fake, _settings(["AAA", "BBB"], ["SPACE1", "SPACE2"]))
    opened_tickets, opened_docs = [], []

    def ticket(args):
        opened_tickets.append(args["key"])
        return {"key": args["key"], "summary": "ticket detail",
                "description": "verified body", "comments": [{"body": "decision"}]}

    def document(args):
        opened_docs.append(args["url_or_id"])
        return {"title": "allowed", "url": args["url_or_id"], "text": "full design body"}

    monkeypatch.setitem(T.BY_NAME, "get_ticket", SimpleNamespace(invoke=ticket))
    monkeypatch.setitem(T.BY_NAME, "read_document", SimpleNamespace(invoke=document))
    got = QueryRunner()._run({
        "intent": "ask",
        "request_plan": {"tasks": [{"id": "r1", "kind": "research"}]},
        "query_plan": {"queries": [
            {"id": "jira", "source": "jira", "query": "ticket", "page_size": 10},
            {"id": "docs", "source": "confluence", "query": "design", "page_size": 10},
        ]},
    })
    jira = next(row["result"] for row in got["query_results"] if row["id"] == "jira")
    docs = next(row["result"] for row in got["query_results"] if row["id"] == "docs")
    assert opened_tickets == ["AAA-1", "BBB-2", "AAA-3"]
    assert jira["ticketDetails"][0]["description"] == "verified body"
    assert opened_docs == ["http://confluence.example/pages/1"]
    assert docs["documentBodies"][0]["text"] == "full design body"
    assert got["query_artifacts"]["evidence-materialization"]["tickets"] == 3
    assert got["query_artifacts"]["evidence-materialization"]["documents"] == 1


def test_runner_keeps_full_materialized_raw_but_bounds_llm_and_continuation_projection(
        monkeypatch):
    """Row caps do not protect prompts when every opened comment/body is multi-kilobyte."""
    import json

    from app.agent import tools as T

    fake = _Client(1)
    _ctx.bind(fake, _settings(["AAA"]))
    # Match the real get_ticket ceiling: 1,200 description chars plus eight 500-char comments.
    long_description = (("오래된 일반 기록 " * 150)
                        + " StarRocks Puffin NDV 상위 Epic의 reader 검증 범위 확정")[-1200:]
    long_comments = [
        {"author": f"user-{index}", "created": f"2026-08-{index + 1:02d}",
         "body": (("일반 코멘트 " * 80) + f" comment-{index}")[-500:]}
        for index in range(7)
    ]
    long_comments.append({
        "author": "decision-owner", "created": "2026-08-18",
        "body": (("부가 설명 " * 80)
                 + " StarRocks Puffin NDV reader 결정 근거")[-500:],
    })

    def ticket(args):
        return {
            "key": args["key"], "type": "Epic", "status": "Open",
            "summary": "StarRocks Puffin NDV 도입", "parentKey": "AAA-ROOT",
            "description": long_description, "comments": long_comments,
        }

    monkeypatch.setitem(T.BY_NAME, "get_ticket", SimpleNamespace(invoke=ticket))
    request = "StarRocks Puffin NDV 상위 Epic과 reader 결정 근거를 조사해줘"
    got = QueryRunner()._run({
        "intent": "ask", "request_text": request,
        "messages": [HumanMessage(content=request)],
        "request_plan": {"tasks": [{"id": "r1", "kind": "research"}]},
        "query_plan": {"queries": [{
            "id": "jira", "source": "jira", "query": "StarRocks Puffin NDV",
            "page_size": 10,
        }]},
    })

    raw = got["query_artifacts"]["evidence-materialization"]["ticketDetails"][0]
    compact = got["query_results"][0]["result"]["ticketDetails"][0]
    ledger = got["materialized_ticket_sources"]["ticketDetails"][0]
    assert raw["description"] == long_description
    assert raw["comments"] == long_comments
    assert {"key", "type", "status", "summary", "parentKey"} <= set(compact)
    assert compact["key"] == ledger["key"] == "AAA-1"
    assert len(compact["description"]) <= 360
    assert "StarRocks Puffin NDV" in compact["description"]
    assert len(compact["comments"]) == 2
    assert any("StarRocks Puffin NDV" in row["body"] for row in compact["comments"])
    assert len(json.dumps(compact, ensure_ascii=False)) < 1800
    assert len(json.dumps(ledger, ensure_ascii=False)) < 1800
    rendered = json.dumps({"query_results": got["query_results"],
                           "ledger": got["materialized_ticket_sources"]},
                          ensure_ascii=False)
    assert len(rendered) < 4000
    assert len(json.dumps(raw, ensure_ascii=False)) > len(rendered) * 2


def test_continuation_reprojects_legacy_oversized_ticket_ledger_around_current_subject():
    import json

    from app.agent.workflow.agents.query_runner import _merge_materialized_ticket_sources

    request = "StarRocks Puffin NDV parent 결정 근거를 이어서 정리해줘"
    prior = {
        "ticketDetails": [{
            "key": "DL-9200", "type": "Epic", "status": "Open",
            "summary": "StarRocks Puffin NDV 도입", "epicKey": "DL-9200",
            "description": ("이전 일반 본문 " * 1200)
            + " StarRocks Puffin NDV parent 결정 근거 LEGACY-RAW-TAIL",
            "comments": [{"author": "owner", "body": "댓글 " * 3000}],
        }],
        "parentCandidateKeys": ["DL-9200"],
    }

    got = _merge_materialized_ticket_sources({
        "turn_continuation": True, "request_text": request,
        "messages": [HumanMessage(content=request)],
        "materialized_ticket_sources": prior,
    }, {})

    detail = got["ticketDetails"][0]
    assert got["parentCandidateKeys"] == ["DL-9200"]
    assert detail["type"] == "Epic" and detail["epicKey"] == "DL-9200"
    assert "StarRocks Puffin NDV" in detail["description"]
    assert len(json.dumps(got, ensure_ascii=False)) < 1800


def test_runner_reserves_parent_materialization_after_more_than_eight_duplicate_hits(monkeypatch):
    """A broad bounded history read cannot starve the later structural Epic candidates."""
    from app.agent import tools as T

    class PurposeAwareClient(_Client):
        def __init__(self):
            super().__init__(12)
            self.rows = [_issue(index, "AAA") for index in range(12)]
            self.parents = [_issue(900 + index, "AAA") for index in range(2)]
            for row in self.parents:
                row["fields"]["issuetype"] = {"name": "Epic", "subtask": False}

        def search_issues_page(self, jql, start_at=0, max_results=100,
                               fields=None, light=True):
            self.calls.append({"jql": jql, "startAt": start_at,
                               "maxResults": max_results, "fields": fields, "light": light})
            source = self.parents if "issuetype = epic" in jql.casefold() else self.rows
            rows = source[start_at:start_at + max_results]
            nxt = start_at + len(rows)
            more = nxt < len(source)
            return {"startAt": start_at, "maxResults": max_results, "total": len(source),
                    "issues": rows, "returned": len(rows), "hasMore": more,
                    "nextStartAt": nxt if more else None}

    fake = PurposeAwareClient()
    _ctx.bind(fake, _settings(["AAA"]))

    def ticket(args):
        key = args["key"]
        return {"key": key, "summary": f"detail {key}",
                "type": "Epic" if key in {"AAA-901", "AAA-902"} else "Task",
                "status": "Open", "description": f"verified {key}", "comments": []}

    monkeypatch.setitem(T.BY_NAME, "get_ticket", SimpleNamespace(invoke=ticket))
    got = QueryRunner()._run({
        "intent": "plan_work",
        "request_plan": {"tasks": [{"kind": "plan", "write_intent": True}]},
        "query_plan": {"queries": [
            {"id": "internal-duplicate-check", "source": "jira",
             "query": "StarRocks Puffin NDV", "where": "",
             "order_by": "updated DESC", "page_size": 50, "completeness": "all"},
            {"id": "parent-candidate-check", "source": "jira",
             "query": "StarRocks Puffin NDV", "where": "issueType = Epic",
             "order_by": "updated DESC", "page_size": 50, "completeness": "all"},
        ]},
    })

    artifact = got["query_artifacts"]["evidence-materialization"]
    assert len(artifact["ticketKeys"]) == 8
    assert artifact["parentCandidateKeys"] == ["AAA-901", "AAA-902"]
    assert len([key for key in artifact["ticketKeys"] if key not in artifact["parentCandidateKeys"]]) == 6
    details = {row["key"] for row in artifact["ticketDetails"]}
    assert {"AAA-901", "AAA-902"}.issubset(details)
    parent = next(row["result"] for row in got["query_results"]
                  if row["id"] == "parent-candidate-check")
    assert parent["materializedCandidateKeys"] == ["AAA-901", "AAA-902"]


def test_plain_listing_query_does_not_materialize_every_ticket(monkeypatch):
    """A list/count query stays cheap; evidence bodies are reserved for research synthesis."""
    from app.agent import tools as T

    fake = _Client(3)
    _ctx.bind(fake, _settings(["AAA", "BBB"]))
    monkeypatch.setitem(T.BY_NAME, "get_ticket", SimpleNamespace(invoke=lambda _args: (_ for _ in ()).throw(
        AssertionError("plain listing must not open ticket bodies"))))
    got = QueryRunner()._run({
        "intent": "ask",
        "request_plan": {"tasks": [{"id": "q1", "kind": "query"}]},
        "query_plan": {"queries": [
            {"id": "jira", "source": "jira", "where": "status = Open", "page_size": 10},
        ]},
    })
    assert "ticketDetails" not in got["query_results"][0]["result"]
    assert "evidence-materialization" not in got["query_artifacts"]


def test_runner_combines_jira_query_terms_with_where_instead_of_silently_dropping_them():
    fake = _Client(3)
    _ctx.bind(fake, _settings(["AAA", "BBB"]))
    QueryRunner()._run({"query_plan": {"queries": [{
        "id": "topic", "source": "jira", "query": "Iceberg Puffin NDV",
        "where": 'statusCategory != Done', "page_size": 20,
    }]}})
    jql = fake.calls[0]["jql"]
    assert 'statusCategory != Done' in jql
    assert 'text ~ "Iceberg"' in jql and 'text ~ "Puffin"' in jql and 'text ~ "NDV"' in jql
    assert jql.startswith('project in ("AAA", "BBB") AND (')


def test_three_public_technology_terms_use_two_of_three_recall_without_becoming_single_term_or():
    from app.agent.workflow.agents.query_runner import _jira_where

    jql = _jira_where("", "Iceberg Puffin NDV")

    assert all(f'text ~ "{term}"' in jql for term in ("Iceberg", "Puffin", "NDV"))
    assert jql.count(" AND ") == 3
    assert jql.count(" OR ") == 2
    assert '(text ~ "Puffin" AND text ~ "NDV")' in jql


def test_runner_rejects_jql_misplaced_in_lexical_query_instead_of_changing_its_meaning():
    fake = _Client(3)
    _ctx.bind(fake, _settings(["AAA", "BBB"]))
    result = QueryRunner()._run({"query_plan": {"queries": [{
        "id": "misplaced", "source": "jira",
        "query": "project = YOUR_PROJECT_KEY AND summary ~ '적재 지연' AND status != 'Done'",
        "where": "", "page_size": 20,
    }]}})
    assert fake.calls == []
    assert "query에는 JQL" in result["query_results"][0]["result"]["error"]


def test_runner_rejects_misplaced_full_text_jql_instead_of_lexical_fallback():
    fake = _Client(3)
    _ctx.bind(fake, _settings(["AAA", "BBB"]))
    QueryRunner()._run({"query_plan": {"queries": [{
        "id": "topic", "source": "jira",
        "query": "project = ETL AND text ~ 'iceberg puffin ndv' AND text ~ '통계정보'",
        "where": "",
    }]}})
    assert fake.calls == []


def test_runner_strips_korean_particles_and_planner_filler_for_duplicate_recall():
    from app.agent.workflow.agents.query_runner import _jira_where

    jql = _jira_where("", "프로듀서를 Avro로 전환하는 작업을 위한 티켓을 생성한다.")

    assert jql == ('text ~ "프로듀서" AND text ~ "Avro" AND text ~ "전환"')
    assert "project = ETL" not in jql


def test_runner_rejects_subtask_parent_jql_misplaced_in_query():
    fake = _Client(3)
    _ctx.bind(fake, _settings(["AAA", "BBB"]))
    QueryRunner()._run({"query_plan": {"queries": [{
        "id": "children", "source": "jira",
        "query": 'issueType=SubTask AND "Epic Link"=DL-9090', "where": "",
    }]}})
    assert fake.calls == []


def test_query_specialist_turns_human_title_in_project_clause_into_summary_search():
    from app.agent.workflow.agents.query_specialist import _normalize_model_jira_query

    query = {"source": "jira", "query": 'issueType = Epic AND project = "최소 기능 1차 구현"',
             "where": ""}
    assert _normalize_model_jira_query(query)
    assert "project" not in query["query"].lower()
    assert 'summary ~ "최소 기능 1차 구현"' in query["query"]


def test_query_specialist_drops_mutation_phrase_from_read_plan():
    from app.agent.workflow.agents.query_specialist import _normalize_model_jira_query

    query = {"source": "jira", "query": "create issue", "where": ""}
    assert not _normalize_model_jira_query(query)


def test_create_plan_adds_scoped_internal_duplicate_search_when_model_only_used_web():
    from app.agent.workflow.agents.query_specialist import _ensure_creation_duplicate_query

    state = {
        "intent": "plan_work",
        "request_text": "프로듀서를 Avro로 전환하는 작업을 새로 만들자",
        "keywords": ["ETL"],
    }
    plan = {"queries": [{"id": "external", "source": "web", "query": "Avro docs"}]}
    _ensure_creation_duplicate_query(state, plan)

    jira = plan["queries"][0]
    assert jira["source"] == "jira" and jira["completeness"] == "all"
    assert all(term in jira["query"] for term in ("프로듀서", "Avro", "전환"))


def test_create_plan_collapses_speculative_status_people_and_comment_fanout():
    from app.agent.workflow.agents.query_specialist import _ensure_creation_duplicate_query

    state = {
        "intent": "plan_work",
        "request_text": "Iceberg Puffin NDV 배치 Job 구현 Task 만들어줘",
        "keywords": ["Iceberg Puffin NDV", "배치 Job"],
        "messages": [],
    }
    plan = {"queries": [
        {"id": "all", "source": "jira", "query": "status = all"},
        {"id": "todo", "source": "jira", "query": "status = todo"},
        {"id": "comments", "source": "comments", "query": "NDV"},
        {"id": "people", "source": "people", "query": "NDV"},
        {"id": "web", "source": "web", "query": "Iceberg NDV docs"},
    ]}
    _ensure_creation_duplicate_query(state, plan)

    assert [row["source"] for row in plan["queries"]] == ["jira", "web"]
    assert plan["queries"][0]["query"] == "Iceberg Puffin NDV"
    assert plan["queries"][0]["completeness"] == "all"


def test_concrete_delegated_cross_module_plan_uses_deterministic_duplicate_query(monkeypatch):
    from app.agent.workflow.agents.query_specialist import QuerySpecialist
    from app.agent.workflow.state import Intent

    model_calls = []

    def fail_model(*_args, **_kwargs):
        model_calls.append(True)
        raise AssertionError("one deterministic duplicate query must not call the model")

    monkeypatch.setattr(QuerySpecialist, "invoke_structured", fail_model)
    state = {
        "intent": Intent.PLAN_WORK,
        "request_text": ("리니지 뷰어 성능 측정하고, 결과에 따라 쿼리 엔진 인덱스도 "
                         "손봐야 해. 그리고 사용 가이드도 써야 하고. 초안 잡아줘. 알아서"),
        "messages": [HumanMessage(content=(
            "리니지 뷰어 성능 측정하고, 결과에 따라 쿼리 엔진 인덱스도 손봐야 해. "
            "그리고 사용 가이드도 써야 하고. 초안 잡아줘. 알아서"))],
        "keywords": ["리니지 뷰어", "쿼리 엔진"],
    }

    result = QuerySpecialist().node()(state)

    assert model_calls == []
    assert [row["source"] for row in result["query_plan"]["queries"]] == ["jira"]
    assert result["query_plan"]["queries"][0]["completeness"] == "all"


def test_public_technology_create_plan_is_compiled_without_query_model(monkeypatch):
    """Public docs do not require the model to restate a deterministic create lookup."""
    from app.agent.workflow.agents.query_specialist import QuerySpecialist
    from app.agent.workflow.agents import work_architect
    from app.agent.workflow.state import Intent

    monkeypatch.setattr(work_architect, "_recover_delegated_creation",
                        lambda _state: [{"summary": "grounded work"}])

    def fail_model(*_args, **_kwargs):
        raise AssertionError("concrete creation retrieval must be compiled, not generated")

    monkeypatch.setattr(QuerySpecialist, "invoke_structured", fail_model)
    original = "Apache Iceberg Puffin 통계 파이프라인을 개발해야해"
    state = {
        "intent": Intent.PLAN_WORK,
        "request_text": original,
        "turn_continuation": True,
        "messages": [
            HumanMessage(content=original),
            HumanMessage(content="Epic은 골라줘. 최소 기능 범위까지 알아서 진행해"),
        ],
        # Simulate an upstream semantic mutation. User-authored text must win.
        "keywords": ["Apache Iceberg Puffin 통계 파이프라인 구축 Epic 생성"],
    }

    queries = QuerySpecialist().node()(state)["query_plan"]["queries"]

    # Public disclosure is authorized by the current utterance only. The frozen public
    # subject still drives internal Jira retrieval, but a control-only follow-up does not
    # silently repeat a web search.
    assert [query["source"] for query in queries] == ["jira", "jira"]
    jira = queries[0]["query"]
    assert jira == "Apache Iceberg Puffin"
    assert not any(term.casefold() in jira.casefold()
                   for term in ("Epic", "생성", "선택", "골라"))
    assert queries[1]["id"] == "parent-candidate-check"
    assert queries[1]["query"] == "Apache Iceberg Puffin"
    assert queries[1]["where"] == "issueType = Epic"


def test_delegated_existing_epic_selection_uses_real_deterministic_recovery(monkeypatch):
    """Regression: choosing an existing Epic must not become an Epic-create search."""
    from app.agent.workflow.agents.query_specialist import QuerySpecialist
    from app.agent.workflow.state import Intent

    def fail_model(*_args, **_kwargs):
        raise AssertionError("a concrete delegated create plan must not call QuerySpecialist LLM")

    monkeypatch.setattr(QuerySpecialist, "invoke_structured", fail_model)
    original = "StarRocks Puffin NDV 통계정보를 생성하는 파이프라인을 개발해야해"
    state = {
        "intent": Intent.PLAN_WORK,
        "request_text": original,
        "turn_continuation": True,
        "messages": [
            HumanMessage(content=original),
            HumanMessage(content=(
                "Epic은 네가 골라줘. 범위는 최소 기능 1차 구현까지, "
                "마감은 2026-09-30. 알아서 진행해")),
        ],
        "keywords": ["StarRocks Puffin NDV 통계 파이프라인 구축 Epic 생성"],
        "module": "ETL",
        "request_plan": {"tasks": [{
            "kind": "plan", "instruction": "기존 Epic을 선택해 1차 구현 범위를 계획",
            "write_intent": True,
        }]},
    }

    result = QuerySpecialist().node()(state)

    assert "error" not in result
    jira = next(row for row in result["query_plan"]["queries"] if row["source"] == "jira")
    assert jira["query"] == "StarRocks Puffin NDV"
    parent = next(row for row in result["query_plan"]["queries"]
                  if row["id"] == "parent-candidate-check")
    assert parent["query"] == "StarRocks Puffin NDV"
    assert parent["where"] == "issueType = Epic" and parent["completeness"] == "all"
    assert all(row["source"] != "web" for row in result["query_plan"]["queries"])


def test_r23_continuation_retains_human_subject_merges_opened_ledger_and_revalidates_epic(
        monkeypatch):
    """STARR1-shaped regression across Query Specialist → Runner continuation state.

    The first turn opens the topic records. A later control-only answer may select an
    existing Epic, but it must neither search for ``은 차 구현`` nor discard those opened
    records. The previously opened relevant Epic is exact-read and promoted only after its
    type is revalidated.
    """
    from app.agent import tools as T
    from app.agent.workflow.agents.query_specialist import QuerySpecialist
    from app.infra.cache import Cache
    from app.infra.settings import get_settings
    from app.jira.jira_client import JiraClient

    settings = get_settings()
    _ctx.bind(JiraClient(settings, Cache(":memory:")), settings)
    monkeypatch.setitem(T.BY_NAME, "search_web", SimpleNamespace(invoke=lambda _args: {
        "query": "StarRocks Puffin NDV official documentation",
        "attempted": True,
        "results": [],
    }))

    original = "starrocks puffin ndv 통계정보를 생성하는 파이프라인을 개발해야해"
    first_state = {
        "intent": "plan_work",
        "request_text": original,
        "messages": [HumanMessage(content=original)],
        "keywords": ["StarRocks", "Puffin", "NDV"],
        "request_plan": {"tasks": [{"kind": "plan", "instruction": original,
                                     "write_intent": True}]},
    }
    first_plan = QuerySpecialist().apply(
        first_state, {"queries": [], "joins": [], "uncertainty": []},
    )["query_plan"]
    first = QueryRunner()._run({**first_state, "query_plan": first_plan})
    opened_first = {row["key"] for row in
                    first["materialized_ticket_sources"]["ticketDetails"]}
    assert {"DL-9200", "DL-9201", "DL-9202"}.issubset(opened_first)

    follow_up = (
        "Epic 은 네가 골라줘. 범위는 최소 기능 1차 구현까지, "
        "마감은 2026-09-30. 알아서 진행해"
    )
    continued_state = {
        "intent": "plan_work",
        # Reproduce a checkpoint whose nominal frozen field contains only the refinement.
        "request_text": follow_up,
        "turn_continuation": True,
        "messages": [
            HumanMessage(content=original),
            AIMessage(content="상위 Epic과 범위를 정해 주세요"),
            HumanMessage(content=follow_up),
        ],
        "keywords": ["기존 Epic", "1차 구현"],
        "request_plan": {"goal": "기존 Epic 선택 및 1차 구현", "tasks": [{
            "kind": "plan", "instruction": follow_up, "write_intent": True,
        }]},
        "materialized_ticket_sources": first["materialized_ticket_sources"],
    }
    second_plan = QuerySpecialist().apply(
        continued_state, {"queries": [], "joins": [], "uncertainty": []},
    )["query_plan"]

    duplicate = next(row for row in second_plan["queries"]
                     if row["id"] == "internal-duplicate-check")
    parent = next(row for row in second_plan["queries"]
                  if row["id"] == "parent-candidate-check")
    assert duplicate["query"] == "starrocks puffin ndv"
    assert parent["query"] == "starrocks puffin ndv"
    assert parent["parent_reference_keys"] == ["DL-9200"]
    assert all(row["source"] != "web" for row in second_plan["queries"])

    second = QueryRunner()._run({**continued_state, "query_plan": second_plan})
    parent_result = next(row["result"] for row in second["query_results"]
                         if row["id"] == "parent-candidate-check")
    assert parent_result["parentResolution"] == "referenced-ticket-hierarchy"
    assert [row["key"] for row in parent_result["tickets"]] == ["DL-9200"]
    merged = second["materialized_ticket_sources"]
    assert merged["parentCandidateKeys"] == ["DL-9200"]
    assert merged["parentCandidateSearchAttempted"] is True
    assert {"DL-9200", "DL-9201", "DL-9202"}.issubset(
        {row["key"] for row in merged["ticketDetails"]})


def test_related_ticket_key_does_not_suppress_delegated_parent_candidate_query():
    from app.agent.workflow.agents.query_specialist import (
        _ensure_creation_duplicate_query,
        _explicit_creation_parent_keys,
    )

    request = (
        "DL-9201 참고해서 StarRocks Puffin NDV 파이프라인 Task를 만들고 "
        "기존 Epic은 네가 골라줘"
    )
    state = {
        "intent": "plan_work", "request_text": request,
        "messages": [HumanMessage(content=request)],
        "mentioned_keys": ["DL-9201"],
        "keywords": ["StarRocks", "Puffin", "NDV"],
    }
    plan = {"queries": []}

    _ensure_creation_duplicate_query(state, plan)

    assert _explicit_creation_parent_keys(state) == set()
    parent = next(row for row in plan["queries"] if row["id"] == "parent-candidate-check")
    assert parent["query"] == "StarRocks Puffin NDV"
    assert parent["where"] == "issueType = Epic"
    assert parent["parent_reference_keys"] == ["DL-9201"]
    duplicate = next(row for row in plan["queries"] if row["id"] == "internal-duplicate-check")
    assert duplicate["where"] == "key in (DL-9201)"


def test_bare_related_ticket_reference_keeps_a_narrow_parent_candidate_read():
    from app.agent.workflow.agents.query_specialist import _ensure_creation_duplicate_query
    from app.infra.cache import Cache
    from app.infra.settings import get_settings
    from app.jira.jira_client import JiraClient

    request = "DL-9201 참고해서 기존 Epic 골라줘"
    state = {
        "intent": "plan_work", "request_text": request,
        "messages": [HumanMessage(content=request)], "mentioned_keys": ["DL-9201"],
    }
    plan = {"queries": []}

    _ensure_creation_duplicate_query(state, plan)

    parent = next(row for row in plan["queries"] if row["id"] == "parent-candidate-check")
    assert parent["query"] == ""
    assert parent["where"] == "issueType = Epic"
    assert parent["parent_reference_keys"] == ["DL-9201"]

    settings = get_settings()
    _ctx.bind(JiraClient(settings, Cache(":memory:")), settings)
    got = QueryRunner()._run({
        **state,
        "request_plan": {"tasks": [{"kind": "plan", "write_intent": True}]},
        "query_plan": plan,
    })

    resolved = next(row["result"] for row in got["query_results"]
                    if row["id"] == "parent-candidate-check")
    assert resolved["parentResolution"] == "referenced-ticket-hierarchy"
    assert [row["key"] for row in resolved["tickets"]] == ["DL-9200"]
    epic = next(row for row in resolved["ticketDetails"] if row["key"] == "DL-9200")
    assert "DL-9201" not in f'{epic.get("summary", "")} {epic.get("description", "")}'
    assert "canonicalJql" not in resolved
    sources = got["materialized_ticket_sources"]
    assert sources["parentCandidateKeys"] == ["DL-9200"]
    assert {row["key"] for row in sources["ticketDetails"]} >= {"DL-9200", "DL-9201"}


def test_parent_reference_resolution_follows_modern_subtask_task_epic_parent_chain(monkeypatch):
    from app.agent import tools as T
    from app.agent.workflow.agents.query_runner import _resolve_parent_reference_candidates

    details = {
        "DL-9302": {"key": "DL-9302", "type": "Sub-Task", "parentKey": "DL-9301"},
        "DL-9301": {"key": "DL-9301", "type": "Task", "parentKey": "DL-9300"},
        "DL-9300": {"key": "DL-9300", "type": "Epic", "summary": "bounded parent"},
    }
    monkeypatch.setitem(
        T.BY_NAME, "get_ticket",
        SimpleNamespace(invoke=lambda args: details.get(args["key"], {"error": "missing"})),
    )

    got = _resolve_parent_reference_candidates(["DL-9302"])

    assert [row["key"] for row in got["candidates"]] == ["DL-9300"]
    assert got["openedKeys"] == ["DL-9302", "DL-9301", "DL-9300"]


def test_only_an_unambiguous_parent_relation_suppresses_parent_candidate_search():
    from app.agent.workflow.agents.query_specialist import (
        _ensure_creation_duplicate_query,
        _explicit_creation_parent_keys,
    )

    request = "StarRocks Puffin NDV Task는 Epic DL-9200 아래에 만들고 알아서 진행해"
    state = {
        "intent": "plan_work", "request_text": request,
        "messages": [HumanMessage(content=request)], "mentioned_keys": ["DL-9200"],
    }
    plan = {"queries": []}

    _ensure_creation_duplicate_query(state, plan)

    assert _explicit_creation_parent_keys(state) == {"DL-9200"}
    assert not any(row["id"] == "parent-candidate-check" for row in plan["queries"])


def test_missing_reference_hierarchy_uses_bounded_subject_fallback(monkeypatch):
    from app.agent import tools as T

    fake = _Client(2)
    for row in fake.rows:
        row["fields"]["issuetype"] = {"name": "Epic", "subtask": False}
    _ctx.bind(fake, _settings(["AAA", "BBB"]))

    def ticket(args):
        key = args["key"]
        if key == "DL-9999":
            return {"key": key, "type": "Task", "summary": "관계 없는 참조 Task",
                    "status": "Open", "description": "상위 Epic 없음", "comments": []}
        return {"key": key, "type": "Epic", "summary": f"candidate {key}",
                "status": "Open", "description": "subject candidate", "comments": []}

    monkeypatch.setitem(T.BY_NAME, "get_ticket", SimpleNamespace(invoke=ticket))
    got = QueryRunner()._run({
        "intent": "plan_work",
        "request_plan": {"tasks": [{"kind": "plan", "write_intent": True}]},
        "query_plan": {"queries": [{
            "id": "parent-candidate-check", "source": "jira",
            "query": "StarRocks Puffin NDV", "where": "issueType = Epic",
            "order_by": "updated DESC", "page_size": 50, "completeness": "all",
            "parent_reference_keys": ["DL-9999"],
        }]},
    })

    result = got["query_results"][0]["result"]
    assert all(f'text ~ "{term}"' in result["canonicalJql"]
               for term in ("StarRocks", "Puffin", "NDV"))
    assert "parentResolution" not in result
    assert fake.calls, "subject fallback must run only after the hierarchy lookup yielded no Epic"


def test_missing_reference_hierarchy_never_expands_to_all_epics_without_a_subject(monkeypatch):
    from app.agent import tools as T

    fake = _Client(2)
    _ctx.bind(fake, _settings(["AAA", "BBB"]))
    monkeypatch.setitem(T.BY_NAME, "get_ticket", SimpleNamespace(invoke=lambda args: {
        "key": args["key"], "type": "Task", "summary": "상위 Epic 없음",
        "status": "Open", "description": "관계 필드 없음", "comments": [],
    }))

    got = QueryRunner()._run({
        "intent": "plan_work",
        "request_plan": {"tasks": [{"kind": "plan", "write_intent": True}]},
        "query_plan": {"queries": [{
            "id": "parent-candidate-check", "source": "jira", "query": "",
            "where": "issueType = Epic", "page_size": 50, "completeness": "all",
            "parent_reference_keys": ["DL-9999"],
        }]},
    })

    result = got["query_results"][0]["result"]
    assert result["parentResolution"] == "unresolved-reference"
    assert result["tickets"] == [] and result.get("error")
    assert fake.calls == []


def test_public_technology_create_query_collects_related_mock_world_tasks_and_details():
    """The bounded 2-of-3 query retains writer/reader/criteria records with omitted terms."""
    from app.agent.workflow.agents.query_specialist import _ensure_creation_duplicate_query
    from app.infra.cache import Cache
    from app.infra.settings import get_settings
    from app.jira.jira_client import JiraClient

    settings = get_settings()
    client = JiraClient(settings, Cache(":memory:"))
    _ctx.bind(client, settings)
    original = "StarRocks Puffin NDV 통계정보를 생성하는 파이프라인을 개발해야해"
    follow_up = "Epic은 네가 골라줘. 최소 기능 1차 구현까지 알아서 진행해"
    state = {
        "intent": "plan_work",
        "request_text": original,
        "messages": [HumanMessage(content=original), HumanMessage(content=follow_up)],
        "keywords": ["StarRocks", "Puffin", "NDV"],
        "request_plan": {"tasks": [{"kind": "plan", "write_intent": True}]},
    }
    plan = {"queries": []}
    _ensure_creation_duplicate_query(state, plan)

    got = QueryRunner()._run({**state, "query_plan": plan})

    internal = next(row["result"] for row in got["query_results"]
                    if row["id"] == "internal-duplicate-check")
    found = {row["key"] for row in internal.get("tickets") or []}
    assert {"DL-9201", "DL-9202", "DL-9203"}.issubset(found)
    assert all(f'text ~ "{term}"' in internal["canonicalJql"]
               for term in ("StarRocks", "Puffin", "NDV"))
    assert internal["canonicalJql"].count(" OR ") >= 2
    details = {row["key"] for row in internal.get("ticketDetails") or []}
    assert {"DL-9200", "DL-9201", "DL-9202", "DL-9203"}.issubset(details)


def test_creation_subject_is_anchored_to_literal_request_not_polluted_keywords():
    from app.agent.workflow.agents.query_specialist import _creation_subject_terms

    state = {
        "request_text": "Puffin NDV 통계정보를 생성하는 파이프라인을 개발해야해",
        "keywords": ["Puffin NDV 통계 파이프라인 구축 Epic 생성"],
    }

    terms = _creation_subject_terms(state)

    assert terms == ["Puffin", "NDV", "통계정보", "파이프라인", "개발"]
    assert "Epic" not in terms and "생성" not in terms


def test_creation_subject_prioritizes_literal_technical_subject_over_generic_leadin():
    from app.agent.workflow.agents.query_specialist import _creation_subject_terms

    state = {
        "request_text": ("지금 우리 상황을 봤을 때 StarRocks Puffin NDV 통계정보를 "
                         "생성하는 파이프라인 Task를 만들어줘"),
        "keywords": ["StarRocks Puffin NDV"],
    }

    assert _creation_subject_terms(state) == [
        "StarRocks", "Puffin", "NDV", "통계정보", "파이프라인",
    ]


def test_creation_subject_recovers_frozen_literal_anchors_from_a_control_only_followup():
    """A short interview answer must not replace the technical creation subject."""
    from app.agent.workflow.agents.query_specialist import _creation_subject_terms

    original = "StarRocks Puffin NDV 통계정보를 생성하는 파이프라인을 개발해야해"
    follow_up = (
        "Epic은 네가 골라줘. 범위는 최소 기능 1차 구현까지, "
        "마감은 2026-09-30. 알아서 진행해"
    )
    state = {
        # Reproduce a legacy/checkpoint state whose nominal frozen field was overwritten.
        "request_text": follow_up,
        "turn_continuation": True,
        "messages": [
            HumanMessage(content=original),
            AIMessage(content="티켓 구조를 정해 주세요"),
            HumanMessage(content=follow_up),
        ],
        "request_plan": {
            "goal": "StarRocks Puffin NDV 통계정보 생성 파이프라인 1차 구현",
            "tasks": [{"kind": "plan", "instruction": follow_up, "write_intent": True}],
        },
        "keywords": ["StarRocks", "Puffin", "NDV", "구현"],
    }

    assert _creation_subject_terms(state) == [
        "StarRocks", "Puffin", "NDV", "통계정보", "파이프라인",
    ]


def test_compact_query_ast_compiles_runtime_defaults_and_is_materially_smaller():
    import json

    from app.agent.workflow.agents.query_specialist import (
        QuerySpecialist,
        _compile_compact_query_plan,
    )
    from app.agent.workflow.contracts import QueryPlan

    compact_schema = QuerySpecialist().schema()
    full_schema = QueryPlan.model_json_schema()
    assert len(json.dumps(compact_schema)) < len(json.dumps(full_schema)) * 0.7

    plan = _compile_compact_query_plan({
        "reads": [
            {"source": "jira", "subject": "Puffin NDV", "where": "status != Done",
             "exhaustive": True},
            {"source": "web", "subject": "Apache Iceberg Puffin", "exhaustive": False},
        ],
        "uncertainty": ["reader version is unknown"],
    })

    assert [row["id"] for row in plan["queries"]] == ["read-1-jira", "read-2-web"]
    assert plan["queries"][0]["completeness"] == "all"
    assert plan["queries"][0]["page_size"] == 50
    assert plan["queries"][1]["completeness"] == "page"
    assert plan["queries"][1]["page_size"] == 5
    assert plan["joins"] == [] and plan["uncertainty"] == ["reader version is unknown"]


def test_query_specialist_prompt_uses_compact_authoritative_context_only():
    from app.agent.workflow.agents.query_specialist import QuerySpecialist

    state = {
        "request_text": "Puffin NDV 적용 이력 조사",
        "messages": [
            HumanMessage(content="Puffin NDV 적용 이력 조사"),
            AIMessage(content="STALE_ASSISTANT_SPECULATION_SHOULD_NOT_BE_REUSED"),
            HumanMessage(content="댓글까지 전부 확인해줘"),
        ],
        "request_plan": {
            "goal": "MODEL_GOAL_IS_NOT_AN_AUTHORITY",
            "tasks": [{"kind": "research", "instruction": "내부 이력과 댓글 조회",
                       "completion_criteria": ["관련 댓글 전수 확인"]}],
            "assumptions": ["VERBOSE_MODEL_ASSUMPTION"],
        },
        "keywords": ["Puffin", "NDV"],
    }

    prompt = QuerySpecialist().task(state)

    assert "Puffin NDV 적용 이력 조사" in prompt
    assert "댓글까지 전부 확인해줘" in prompt
    assert "내부 이력과 댓글 조회" in prompt
    assert "STALE_ASSISTANT_SPECULATION" not in prompt
    assert "MODEL_GOAL_IS_NOT_AN_AUTHORITY" not in prompt
    assert "VERBOSE_MODEL_ASSUMPTION" not in prompt


def test_query_context_bounds_long_minutes_and_preserves_full_text_identifiers():
    from app.agent.workflow.agents.query_specialist import _compact_request_context

    request = "회의 시작 " + ("일반 논의 " * 4000) + " DL-9911 fdc_summary_trace_ic " + \
        ("후속 논의 " * 4000) + "회의 종료 및 Puffin 검증 요청"
    context = _compact_request_context({
        "request_text": request,
        "messages": [HumanMessage(content=request)],
        "request_plan": {"tasks": [{
            "kind": "research", "instruction": "Puffin 관련 내부 이력 조사",
            "completion_criteria": ["DL-9911 연관성 확인"],
        }]},
    })

    assert len(context["request_excerpt"]) < 1900
    assert "회의 시작" in context["request_excerpt"]
    assert "회의 종료" in context["request_excerpt"]
    assert {"DL-9911", "fdc_summary_trace_ic"} <= set(context["literal_anchors"])
    assert context["tasks"][0]["instruction"] == "Puffin 관련 내부 이력 조사"


def test_query_specialist_and_runner_fail_loud_on_unsupported_dependencies():
    from app.agent.workflow.agents.query_runner import QueryRunner
    from app.agent.workflow.agents.query_specialist import QuerySpecialist

    legacy = {"queries": [{
        "id": "child", "source": "comments", "query": "${parent.keys}",
        "where": "", "order_by": "updated DESC", "fields": [],
        "completeness": "page", "page_size": 25, "depends_on": ["parent"],
    }], "joins": [], "uncertainty": []}

    with pytest.raises(ValueError, match="dependencies/joins are unsupported"):
        QuerySpecialist().apply({"intent": "ask", "messages": []}, legacy)
    with pytest.raises(ValueError, match="dependencies/joins are unsupported"):
        QueryRunner()._run({"query_plan": legacy, "trace": []})


def test_explicit_parent_create_does_not_publicly_search_ambiguous_technical_acronym():
    """PAR2: internal CDC means Change Data Capture here, not the public-health agency."""
    from app.agent.workflow.agents.query_specialist import _external_research_allowed
    from app.agent.workflow.state import Intent

    state = {
        "intent": Intent.PLAN_WORK,
        "request_text": "DL-101 에픽 아래에 CDC 재처리 배치 개선 Task 하나 만들어줘. 알아서",
        "messages": [HumanMessage(content=(
            "DL-101 에픽 아래에 CDC 재처리 배치 개선 Task 하나 만들어줘. 알아서"))],
        "mentioned_keys": ["DL-101"],
    }

    assert _external_research_allowed(state) is False
    state["request_text"] += " 외부 공식 문서도 조사해줘"
    state["messages"] = [HumanMessage(content=state["request_text"])]
    assert _external_research_allowed(state) is True


def test_model_generated_terms_never_authorize_external_research():
    """Only human messages may cross the public-search provenance boundary."""
    from app.agent.workflow.agents.query_specialist import _external_research_allowed
    from app.agent.workflow.state import Intent

    request = "DL-101 아래 재처리 배치 개선 Task를 만들어줘"
    state = {
        "intent": Intent.PLAN_WORK,
        "request_text": request,
        "mentioned_keys": ["DL-101"],
        "messages": [
            HumanMessage(content=request),
            AIMessage(content="DoD official documentation을 참고해 초안을 작성하겠습니다."),
        ],
    }

    assert _external_research_allowed(state) is False
    state["messages"].append(HumanMessage(content="외부 공식 문서도 조사해줘"))
    state["turn_continuation"] = True
    assert _external_research_allowed(state) is True


def test_past_web_permission_never_authorizes_a_new_topic_or_leaks_its_identifier():
    """A public-search grant is scoped to one session request boundary, not all human history."""
    from app.agent.workflow.agents.query_specialist import (
        QuerySpecialist, _external_research_allowed, _user_authored_text,
    )
    from app.agent.workflow.state import Intent

    current = "secret_client_code 성능 Task 생성해"
    state = {
        "intent": Intent.PLAN_WORK,
        "request_text": current,
        "turn_continuation": False,
        "messages": [
            HumanMessage(content="Qwen 공식 문서를 웹 검색해"),
            AIMessage(content="조사했습니다."),
            HumanMessage(content=current),
        ],
        "mentioned_keys": [],
    }

    assert _user_authored_text(state) == current
    assert _external_research_allowed(state) is False
    out = QuerySpecialist().apply(state, {
        "queries": [{
            "id": "model-web", "source": "web",
            "query": "secret_client_code Qwen official documentation",
            "where": "", "order_by": "updated DESC", "fields": [],
            "completeness": "page", "page_size": 5, "depends_on": [],
        }],
        "joins": [], "uncertainty": [],
    })
    assert all(row.get("source") not in {"web", "github"}
               for row in out["query_plan"]["queries"])
    assert "Qwen" not in str(out["query_plan"])


def test_explicit_external_followup_reuses_only_the_frozen_public_subject():
    from app.agent.workflow.agents.query_specialist import (
        QuerySpecialist, _external_research_allowed, _public_query_subject_text,
    )
    from app.agent.workflow.state import Intent

    original = "Qwen structured output 기능을 검토해줘"
    follow_up = "공식 문서도 찾아줘"
    state = {
        "intent": Intent.ASK,
        "request_text": original,
        "turn_continuation": True,
        "messages": [HumanMessage(content=original), HumanMessage(content=follow_up)],
    }

    assert _external_research_allowed(state) is True
    assert "Qwen" in _public_query_subject_text(state)
    out = QuerySpecialist().apply(
        state, {"queries": [], "joins": [], "uncertainty": []},
    )
    web = [row for row in out["query_plan"]["queries"] if row["source"] == "web"]
    assert len(web) == 1
    assert web[0]["query"].startswith("Qwen")
    assert web[0]["query"].endswith("official documentation")


def test_github_followup_uses_current_authorization_and_frozen_public_subject():
    from app.agent.workflow.agents.query_specialist import QuerySpecialist
    from app.agent.workflow.state import Intent

    original = "Qwen structured output 기능을 검토해줘"
    follow_up = "GitHub에서도 공식 자료를 찾아줘"
    state = {
        "intent": Intent.ASK,
        "request_text": original,
        "turn_continuation": True,
        "messages": [HumanMessage(content=original), HumanMessage(content=follow_up)],
    }

    out = QuerySpecialist().apply(
        state, {"queries": [], "joins": [], "uncertainty": []},
    )

    github = [row for row in out["query_plan"]["queries"] if row["source"] == "github"]
    assert len(github) == 1
    assert github[0]["query"].startswith("Qwen")
    assert "official" not in github[0]["query"].casefold()
    assert "documentation" not in github[0]["query"].casefold()


def test_github_model_query_cannot_leak_private_frozen_subject_on_continuation():
    from app.agent.workflow.agents.query_specialist import QuerySpecialist
    from app.agent.workflow.state import Intent

    private = "secret_client_code"
    follow_up = "GitHub에서도 찾아줘"
    state = {
        "intent": Intent.ASK,
        "request_text": f"{private} 개선",
        "turn_continuation": True,
        "messages": [
            HumanMessage(content=f"{private} 개선"),
            HumanMessage(content=follow_up),
        ],
    }
    out = QuerySpecialist().apply(state, {
        "queries": [{
            "id": "unsafe-github", "source": "github",
            "query": f"{private} Qwen", "where": "", "order_by": "",
            "fields": [], "completeness": "page", "page_size": 5,
            "depends_on": [],
        }],
        "joins": [], "uncertainty": [],
    })

    assert all(row["source"] != "github" for row in out["query_plan"]["queries"])
    assert private not in str(out["query_plan"])


def test_non_continuation_never_reuses_prior_public_subject_or_permission():
    from app.agent.workflow.agents.query_specialist import (
        QuerySpecialist, _external_research_allowed, _public_query_subject_text,
    )
    from app.agent.workflow.state import Intent

    current = "secret_client_code 성능 Task 생성해"
    state = {
        "intent": Intent.PLAN_WORK,
        "request_text": current,
        "turn_continuation": False,
        "messages": [
            HumanMessage(content="Qwen 공식 문서를 찾아줘"),
            AIMessage(content="확인 완료"),
            HumanMessage(content=current),
        ],
    }

    assert _external_research_allowed(state) is False
    assert _public_query_subject_text(state) == current
    out = QuerySpecialist().apply(
        state, {"queries": [], "joins": [], "uncertainty": []},
    )
    assert all(row["source"] not in {"web", "github"}
               for row in out["query_plan"]["queries"])
    assert "Qwen" not in str(out["query_plan"])


def test_meeting_query_plan_preserves_explicit_ticket_and_replaces_generic_note_search():
    from app.agent.workflow.agents.query_specialist import _normalize_meeting_research_queries
    from app.agent.workflow.state import Intent

    state = {
        "intent": Intent.ASK,
        "request_text": ("회의 메모를 Jira·Confluence·comment와 외부 공식 자료로 보강해줘. "
                         "DL-7001 Puffin StarRocks reader"),
        "mentioned_keys": ["DL-7001"],
    }
    plan = {"queries": [
        {"id": "jira-note", "source": "jira", "query": "회의 메모", "where": "",
         "fields": [], "order_by": "", "completeness": "all", "page_size": 50,
         "depends_on": []},
        {"id": "conf-note", "source": "confluence", "query": "회의 메모", "where": "",
         "fields": [], "order_by": "", "completeness": "all", "page_size": 50,
         "depends_on": []},
        {"id": "comments", "source": "comments", "query": "회의 메모", "where": "",
         "fields": [], "order_by": "", "completeness": "all", "page_size": 50,
         "depends_on": []},
    ], "joins": [], "uncertainty": []}
    _normalize_meeting_research_queries(state, plan)
    assert plan["queries"][0]["where"] == "key in (DL-7001)"
    assert any(q["source"] == "jira" and "Puffin StarRocks" in q["query"]
               for q in plan["queries"])
    assert any(q["source"] == "confluence" and q["query"] == "Puffin"
               for q in plan["queries"])
    comments = next(q for q in plan["queries"] if q["source"] == "comments")
    assert comments["query"] == "Puffin" and not comments["where"]


@pytest.mark.parametrize(("private_subject", "split_model_query"), [
    ("secret_client_code", "secret client code official documentation"),
    ("skcc.x1402", "x1402 official documentation"),
])
def test_meeting_external_plan_never_transmits_split_private_identifier(
        private_subject, split_model_query):
    from app.agent.workflow.agents.query_specialist import QuerySpecialist
    from app.agent.workflow.state import Intent

    request = (
        f"회의록\n{private_subject} 개선 논의\n"
        "외부 공식 자료와 GitHub 저장소도 조사해줘"
    )
    state = {
        "intent": Intent.ASK, "request_text": request,
        "messages": [HumanMessage(content=request)], "mentioned_keys": [],
    }
    plan = QuerySpecialist().apply(state, {
        "queries": [
            {"id": "meeting-web", "source": "web", "query": split_model_query,
             "where": "", "order_by": "", "fields": [], "completeness": "page",
             "page_size": 5, "depends_on": []},
            {"id": "meeting-github", "source": "github",
             "query": split_model_query.replace(" official documentation", ""),
             "where": "", "order_by": "", "fields": [], "completeness": "page",
             "page_size": 5, "depends_on": []},
        ],
        "joins": [], "uncertainty": [],
    })["query_plan"]

    # Private terms remain valid for scoped Jira/Confluence retrieval. The disclosure
    # boundary applies only to public sources; no split spelling may survive there.
    external = [row for row in plan["queries"] if row["source"] in {"web", "github"}]
    assert external == []


def test_meeting_external_plan_preserves_safe_public_technology_subject():
    from app.agent.workflow.agents.query_specialist import QuerySpecialist
    from app.agent.workflow.state import Intent

    request = (
        "회의록\nStarRocks Puffin NDV reader 검증 논의\n"
        "외부 공식 자료와 GitHub 저장소도 조사해줘"
    )
    state = {
        "intent": Intent.ASK, "request_text": request,
        "messages": [HumanMessage(content=request)], "mentioned_keys": [],
    }
    plan = QuerySpecialist().apply(
        state, {"queries": [], "joins": [], "uncertainty": []},
    )["query_plan"]

    external = [row for row in plan["queries"] if row["source"] in {"web", "github"}]
    assert {row["source"] for row in external} == {"web", "github"}
    for row in external:
        query = row["query"].casefold()
        assert all(term in query for term in ("starrocks", "puffin", "ndv"))
        assert "reader" not in query


def test_query_specialist_drops_unresolved_jql_placeholder():
    from app.agent.workflow.agents.query_specialist import _normalize_model_jira_query

    query = {"source": "jira", "query": '"Epic Link" = {Epic Key}', "where": ""}
    assert not _normalize_model_jira_query(query)


def test_query_specialist_removes_non_field_descriptions_from_jira_projection():
    from app.agent.workflow.agents.query_specialist import _normalize_query_fields

    query = {"source": "jira",
             "fields": ["summary", "성능 측정 방법론 정의", "Epic Link", "customfield_10018"]}
    _normalize_query_fields(query)
    assert query["fields"] == ["summary", "customfield_10018"]


def test_query_specialist_keeps_narrower_epic_search_for_same_summary_and_repairs_dependencies():
    from app.agent.workflow.agents.query_specialist import _dedupe_equivalent_queries

    plan = {"queries": [
        {"id": "broad", "source": "jira", "query": 'summary ~ "최소 기능"',
         "where": "", "fields": [], "page_size": 50, "depends_on": []},
        {"id": "epic", "source": "jira",
         "query": 'issueType = Epic AND summary ~ "최소 기능"',
         "where": "", "fields": [], "page_size": 50, "depends_on": ["broad"]},
    ]}
    _dedupe_equivalent_queries(plan)
    assert [query["id"] for query in plan["queries"]] == ["epic"]
    assert plan["queries"][0]["depends_on"] == []


def test_people_workload_uses_scoped_paginated_jql_not_primary_project_aggregate():
    fake = _Client(137)
    _ctx.bind(fake, _settings(["AAA", "BBB"]))
    result = scoped_person_workload("skcc.x1001", 28)
    assert result["scopeProjects"] == ["AAA", "BBB"]
    assert len(fake.calls) == 6  # open/inProgress/done 각각 100+37 두 page
    assert all(call["jql"].startswith('project in ("AAA", "BBB") AND (assignee = ')
               for call in fake.calls)
    assert all("PRIMARY" not in call["jql"] for call in fake.calls)


def test_people_name_query_filters_before_paginated_workload_enrichment(monkeypatch):
    """A two-person ambiguity must not enrich the whole roster again on every page."""
    import app.infra.settings as settings_module

    class PeopleProvider:
        def get_json(self, path, params=None):
            if path.endswith("/user/search"):
                assert params["username"] == "준서"
                return [
                    {"name": "skcc.x1103", "displayName": "이준서 SKCC"},
                    {"name": "skcc.x1327", "displayName": "임준서 SKCC"},
                ]
            return {"name": (params or {}).get("username", "")}

    fake = _Client(0)
    fake.provider = PeopleProvider()
    _ctx.bind(fake, _settings(["AAA", "BBB"]))
    monkeypatch.setattr(settings_module, "load_people", lambda: {
        "ETL": ["skcc.x1103"], "Runtime": ["skcc.x1327"],
        "Catalog": [f"skcc.x{n}" for n in range(2000, 2020)],
    })

    first = query_people.invoke({"name": "준서TL", "page_size": 1})
    assert first["total"] == 2 and first["returned"] == 1 and first["hasMore"]
    assert len(fake.calls) == 3  # one candidate × open/in-progress/done, one page each
    second = query_people.invoke({
        "name": "@준서", "page_size": 1, "cursor": first["nextCursor"]})
    assert second["total"] == 2 and second["returned"] == 1 and not second["hasMore"]
    assert len(fake.calls) == 6  # second candidate only; first one was not enriched again


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
