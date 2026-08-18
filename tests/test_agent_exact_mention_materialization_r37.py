from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace

import pytest
from langchain_core.messages import HumanMessage


def _contract(root: str, keys: list[str], *, action: str = "comment") -> dict:
    return {
        "version": "continuation.v1",
        "root_request": root,
        "intent": "modify",
        "action": action,
        "target_keys": keys,
        "outcome_ids": ["research", "write"],
        "decisions": [],
    }


def _query(query_id: str, source: str, *, query: str = "", where: str = "",
           completeness: str = "page") -> dict:
    return {
        "id": query_id,
        "source": source,
        "query": query,
        "where": where,
        "order_by": "updated DESC",
        "fields": [],
        "completeness": completeness,
        "page_size": 25,
        "depends_on": [],
    }


def _detail(key: str) -> dict:
    return {
        "key": key,
        "summary": f"[{key.split('-', 1)[0]}] 검증 작업",
        "status": "In Progress",
        "description": "현재 범위와 검증 조건을 기록한다.",
        "comments": [],
    }


def _jira_client_with_pages(pages: dict[int, dict]):
    from app.jira.jira_client import JiraClient

    calls = []

    def get_json(_path, params=None, **_kwargs):
        params = dict(params or {})
        calls.append(params)
        return deepcopy(pages[int(params.get("startAt") or 0)])

    client = object.__new__(JiraClient)
    client._provider = SimpleNamespace(get_json=get_json)
    client._provider_built = True
    return client, calls


def test_paginated_comment_snapshot_preserves_total_and_proves_last_page():
    client, calls = _jira_client_with_pages({
        0: {"startAt": 0, "maxResults": 2, "total": 3, "comments": [
            {"id": "1", "created": "2031-01-01", "body": "first"},
            {"id": "2", "created": "2031-01-02", "body": "second"},
        ]},
        2: {"startAt": 2, "maxResults": 2, "total": 3, "comments": [
            {"id": "3", "created": "2031-01-03", "body": "third"},
        ]},
    })

    got = client.comment_snapshot("WK-101", cap=8, page_size=2)

    assert [row["id"] for row in got["comments"]] == ["1", "2", "3"]
    assert got == {
        **got,
        "key": "WK-101", "total": 3, "returned": 3, "pages": 2,
        "complete": True, "hasMore": False, "remaining": 0,
    }
    assert [row["startAt"] for row in calls] == [0, 2]


@pytest.mark.parametrize("pages, reason", [
    ({0: {"startAt": 0, "maxResults": 2, "total": 3, "comments": [
        {"id": "1", "body": "first"}, {"id": "2", "body": "second"},
    ]}}, "cap_exceeded"),
    ({0: {"startAt": 0, "maxResults": 2, "comments": [
        {"id": "1", "body": "first"},
    ]}}, "total_missing"),
    ({
        0: {"startAt": 0, "maxResults": 1, "total": 2,
            "comments": [{"id": "1", "body": "first"}]},
        1: {"startAt": 0, "maxResults": 1, "total": 2,
            "comments": [{"id": "1", "body": "first"}]},
    }, "non_advancing"),
])
def test_comment_snapshot_fails_closed_on_cap_missing_total_or_nonadvancing(pages, reason):
    client, _calls = _jira_client_with_pages(pages)

    got = client.comment_snapshot("WK-101", cap=2, page_size=2 if reason != "non_advancing" else 1)

    assert got["complete"] is False
    assert got["incompleteReason"] == reason
    assert got["hasMore"] is True


def test_comment_snapshot_rejects_duplicate_ids_changed_total_and_invalid_bounds():
    duplicate, _ = _jira_client_with_pages({
        0: {"startAt": 0, "total": 2, "comments": [
            {"id": "same", "body": "one"}, {"id": "same", "body": "two"},
        ]},
    })
    assert duplicate.comment_snapshot("WK-101")["incompleteReason"] == "duplicate_page"

    changed, _ = _jira_client_with_pages({
        0: {"startAt": 0, "total": 2, "comments": [{"id": "1", "body": "one"}]},
        1: {"startAt": 1, "total": 3, "comments": [{"id": "2", "body": "two"}]},
    })
    assert changed.comment_snapshot("WK-101", page_size=1)["incompleteReason"] == "total_changed"
    assert changed.comment_snapshot("WK-101", cap=True)["incompleteReason"] == "invalid_bounds"


def test_search_comments_exposes_ticket_and_comment_coverage_separately(monkeypatch):
    import app.agent.tools.query_tools as query_tools

    class Client:
        def comment_snapshot(self, key, **_kwargs):
            return {
                "key": key, "total": 1, "returned": 1, "pages": 1,
                "complete": True, "hasMore": False, "remaining": 0,
                "comments": [{"id": "c1", "authorId": "user-1",
                              "date": "2031-01-01", "html": "verified"}],
            }

    monkeypatch.setattr(query_tools, "client", lambda: Client())
    monkeypatch.setattr(query_tools, "_jql_page", lambda *_args, **_kwargs: {
        "canonicalJql": 'project in ("WK") AND key=WK-101',
        "scopeProjects": ["WK"],
        "tickets": [{"key": "WK-101", "summary": "[WK] 검증"}],
        "returned": 1, "total": 1, "hasMore": False, "nextCursor": None,
    })

    got = query_tools.search_comments_complete_page({
        "query": "", "jql_where": "key=WK-101", "page_size": 25,
    })

    assert got["candidateCoverage"] == {
        "returned": 1, "total": 1, "hasMore": False, "complete": True,
        "keys": ["WK-101"],
    }
    assert got["commentCoverage"] == {
        "tickets": 1, "comments": 1, "complete": True,
        "incompleteTickets": [], "remaining": 0,
        "resultTruncated": False, "resultRemaining": 0,
    }
    assert got["complete"] is True


def test_complete_comment_projection_preserves_distinct_ids_and_rejects_clipped_body(
        monkeypatch):
    import app.agent.tools.query_tools as query_tools

    comments = [
        {"id": "c1", "authorId": "user-1", "date": "2031-01-01", "body": "same"},
        {"id": "c2", "authorId": "user-1", "date": "2031-01-01", "body": "same"},
    ]

    class Client:
        def comment_snapshot(self, key, **_kwargs):
            return {"key": key, "total": len(comments), "returned": len(comments),
                    "pages": 1, "complete": True, "hasMore": False, "remaining": 0,
                    "comments": deepcopy(comments)}

    monkeypatch.setattr(query_tools, "client", lambda: Client())
    monkeypatch.setattr(query_tools, "_jql_page", lambda *_args, **_kwargs: {
        "tickets": [{"key": "WK-101", "summary": "[WK] 검증"}],
        "returned": 1, "total": 1, "hasMore": False, "nextCursor": None,
    })

    complete = query_tools.search_comments_complete_page({
        "query": "", "jql_where": "key=WK-101", "page_size": 25,
    })
    assert [row["id"] for row in complete["comments"]] == ["c1", "c2"]
    assert complete["commentCoverage"]["comments"] == 2
    assert complete["complete"] is True

    comments[:] = [{"id": "long", "body": "x" * 900}]
    clipped = query_tools.search_comments_complete_page({
        "query": "", "jql_where": "key=WK-101", "page_size": 25,
    })
    assert clipped["comments"][0]["bodyTruncated"] is True
    assert clipped["commentCoverage"]["complete"] is False
    assert clipped["complete"] is False


def test_public_comment_search_keeps_cached_page_path(monkeypatch):
    import app.agent.tools.query_tools as query_tools

    calls = {"cached": 0, "snapshot": 0}

    class Client:
        def issue_comments(self, _key, _limit):
            calls["cached"] += 1
            return [{"id": "c1", "authorId": "u1", "date": "2031-01-01",
                     "html": "same"}]

        def comment_snapshot(self, _key, **_kwargs):
            calls["snapshot"] += 1
            raise AssertionError("public/ReAct page search must not scan full history")

    monkeypatch.setattr(query_tools, "client", lambda: Client())
    monkeypatch.setattr(query_tools, "_jql_page", lambda *_args, **_kwargs: {
        "tickets": [{"key": "WK-101", "summary": "work"}],
        "returned": 1, "total": 1, "hasMore": False, "nextCursor": None,
    })

    got = query_tools.search_comments.invoke({"jql_where": "key=WK-101"})

    assert calls == {"cached": 1, "snapshot": 0}
    assert got["comments"][0]["id"] == "c1"
    assert "commentCoverage" not in got


def test_model_broad_all_comment_plan_cannot_trigger_private_snapshot(monkeypatch):
    from app.agent import tools as T
    import app.agent.tools.query_tools as query_tools
    from app.agent.workflow.agents.query_runner import QueryRunner
    from app.agent.workflow.exact_mention_materialization import EXACT_MENTION_ARTIFACT

    state = _runner_state()
    state["query_plan"]["queries"][1].update(
        query="incident", where="status != Done", completeness="all",
    )
    calls = {"public": 0, "private": 0}

    monkeypatch.setitem(T.BY_NAME, "run_jql_v2", SimpleNamespace(invoke=lambda _args: {
        "tickets": [{"key": "WK-101", "summary": "work"}],
        "returned": 1, "total": 1, "hasMore": False, "nextCursor": None,
    }))
    monkeypatch.setitem(T.BY_NAME, "get_ticket", SimpleNamespace(
        invoke=lambda args: _detail(args["key"]),
    ))

    def public(_args):
        calls["public"] += 1
        return {"comments": [], "returned": 0, "hasMore": False,
                "nextCursor": None}

    def private(_args):
        calls["private"] += 1
        raise AssertionError("a model-authored broad all query must stay bounded")

    monkeypatch.setitem(T.BY_NAME, "search_comments", SimpleNamespace(invoke=public))
    monkeypatch.setattr(query_tools, "search_comments_complete_page", private)

    got = QueryRunner().node()(state)
    comment = next(row for row in got["query_results"] if row["source"] == "comments")
    assert calls == {"public": 1, "private": 0}
    assert comment["result"]["complete"] is False
    assert comment["result"]["incompleteReason"] == "unproven_comment_coverage"
    assert EXACT_MENTION_ARTIFACT not in got["query_artifacts"]


def test_comment_all_pagination_keeps_distinct_ids_and_global_cap():
    from app.agent.workflow.agents.query_runner import QueryRunner

    def page(args):
        offset = 0 if not args.get("cursor") else 150
        rows = [{"id": f"c{index}", "ticketKey": f"WK-{1 + offset // 150}",
                 "author": "same", "date": "2031-01-01", "snippet": "same"}
                for index in range(offset, offset + 150)]
        last = bool(offset)
        return {
            "comments": rows, "hasMore": not last,
            "nextCursor": None if last else "page-2",
            "candidateCoverage": {"returned": 1, "total": 2,
                                  "hasMore": not last, "complete": last,
                                  "keys": [f"WK-{1 + offset // 150}"]},
            "commentCoverage": {"tickets": 1, "comments": 150,
                                "complete": True, "incompleteTickets": [],
                                "remaining": 0, "resultTruncated": False,
                                "resultRemaining": 0},
        }

    rows, meta = QueryRunner._all_pages(page, {})

    assert len(rows) == 200 and len({row["id"] for row in rows}) == 200
    assert meta["complete"] is False
    assert meta["incompleteReason"] == "result_cap"
    assert meta["commentCoverage"]["resultTruncated"] is True
    assert meta["commentCoverage"]["resultRemaining"] == 100


@pytest.mark.parametrize("defect", [
    "wrong_candidate", "wrong_comment", "zero_candidate", "count_mismatch",
    "body_truncated",
])
def test_exact_comment_coverage_requires_identity_cardinality_and_complete_body(defect):
    from app.agent.workflow.agents.query_runner import _exact_comment_coverage

    plan = {"queries": [
        _query("comments", "comments", where="issueKey=WK-101", completeness="all"),
    ], "joins": [], "uncertainty": []}
    result = {
        "comments": [{"id": "c1", "ticketKey": "WK-101", "snippet": "complete"}],
        "returned": 1,
        "candidateCoverage": {"returned": 1, "total": 1, "hasMore": False,
                              "complete": True, "keys": ["WK-101"]},
        "commentCoverage": {"tickets": 1, "comments": 1, "complete": True,
                            "incompleteTickets": [], "remaining": 0,
                            "resultTruncated": False, "resultRemaining": 0},
        "complete": True,
    }
    if defect == "wrong_candidate":
        result["candidateCoverage"]["keys"] = ["OTHER-9"]
    elif defect == "wrong_comment":
        result["comments"][0]["ticketKey"] = "OTHER-9"
    elif defect == "zero_candidate":
        result["candidateCoverage"].update(returned=0, total=0, keys=[])
        result["commentCoverage"].update(tickets=0, comments=0)
        result.update(comments=[], returned=0)
    elif defect == "count_mismatch":
        result["commentCoverage"]["comments"] = 10
    elif defect == "body_truncated":
        result["comments"][0]["bodyTruncated"] = True

    rows = [{"id": "comments", "source": "comments", "result": result}]
    assert _exact_comment_coverage(plan, rows) == {"WK-101": False}


def test_exact_mention_authority_uses_current_human_or_true_frozen_continuation_only():
    from app.agent.workflow.exact_mention_materialization import exact_mention_request

    stale = "OLD-900의 이전 요청"
    fresh = {
        "messages": [HumanMessage(content="NEW-101과 NEW-102를 확인해줘")],
        "request_text": stale,
        "turn_continuation": False,
        "mentioned_keys": ["OLD-900"],
        "request_plan": {"tasks": [{"instruction": "OLD-900 조회"}]},
        "continuation_contract": _contract(stale, ["OLD-900"]),
    }
    assert exact_mention_request(fresh).keys == ("NEW-101", "NEW-102")

    root = "WK-101과 WK-102의 결정 근거를 확인하고 REF-900은 배경으로 읽어줘"
    continued = {
        "messages": [HumanMessage(content="계속해줘")],
        "request_text": root,
        "turn_continuation": True,
        # MODEL-999 is schema-valid but absent from the frozen human root.
        "continuation_contract": _contract(root, ["WK-101", "MODEL-999", "WK-102"]),
    }
    assert exact_mention_request(continued).keys == ("WK-101", "WK-102", "REF-900")

    correction = deepcopy(continued)
    correction["messages"] = [HumanMessage(content="별도 요청으로 NEW-202만 확인해줘")]
    # A current explicit key wins over stale frozen roots even if a malformed caller falsely
    # marks the turn as a continuation.
    assert exact_mention_request(correction).keys == ("NEW-202",)

    too_many = {
        "messages": [HumanMessage(content=" ".join(f"WK-{i}" for i in range(1, 10)))],
        "turn_continuation": False,
    }
    assert exact_mention_request(too_many) is None

    embedded = {"messages": [HumanMessage(content="ABC-WK-101 / WK-102-example")],
                "turn_continuation": False}
    assert exact_mention_request(embedded) is None


def test_keyless_write_task_cannot_promote_background_or_stale_bulk_targets(monkeypatch):
    import app.agent.workflow.session as session
    from app.agent.workflow.continuation import build_continuation_contract

    root = "REF-900은 배경으로 읽되 새 댓글 초안은 정한 대상에만 작성해줘"
    state = {
        "messages": [HumanMessage(content=root)], "request_text": root,
        "turn_continuation": True, "intent": "modify",
        "mentioned_keys": ["REF-900"],
        "bulk_targets": ["REF-900"],
        "request_plan": {"tasks": [
            {"id": "read", "kind": "query", "instruction": "REF-900 배경 확인",
             "write_intent": False},
            {"id": "write", "kind": "comment", "instruction": "확정 대상에 댓글 작성",
             "write_intent": True},
        ]},
        "continuation_contract": _contract(root, ["REF-900"]),
    }
    contract = build_continuation_contract(
        state, existing=state["continuation_contract"],
    )
    assert contract["target_keys"] == []

    monkeypatch.setattr(session, "_is_interview_continuation", lambda *_args: True)
    patch = session._turn_start_patch("계속", state)
    assert patch["continuation_contract"]["target_keys"] == []
    assert patch["bulk_targets"] == []


def test_query_specialist_normalizes_only_exact_human_key_echoes_and_keeps_sources():
    from app.agent.workflow.agents.query_specialist import QuerySpecialist

    request = "WK-101의 현재 값과 전체 댓글을 확인하고 Apache Puffin 외부 공식 문서도 찾아줘"
    state = {
        "messages": [HumanMessage(content=request)],
        "request_text": request,
        "intent": "modify",
        "mentioned_keys": ["MODEL-999"],
    }
    plan = {
        "queries": [
            _query("ticket", "jira", query="WK-101", where="key=WK-101"),
            _query("comments", "comments", query="WK-101", where="issueKey=WK-101",
                   completeness="all"),
            _query("web", "web", query="public protocol official documentation"),
        ],
        "joins": [],
        "uncertainty": [],
    }

    got = QuerySpecialist().apply(state, plan)["query_plan"]
    by_id = {row["id"]: row for row in got["queries"]}
    assert by_id["ticket"]["query"] == ""
    assert by_id["comments"]["query"] == ""
    assert by_id["comments"]["completeness"] == "all"
    assert {row["source"] for row in got["queries"]} == {"jira", "comments", "web"}


@pytest.mark.parametrize("source,where", [
    ("jira", "key=WK-102"),
    ("jira", "status != Done"),
    ("jira", "key=WK-101 AND status=Open"),
    ("jira", "key != WK-101"),
    ("comments", "issueKey=WK-102"),
    ("comments", "issueKey=WK-101 OR issueKey=WK-102"),
    ("comments", "issueKey != WK-101"),
])
def test_exact_key_echo_normalizer_rejects_wrong_broad_compound_or_negative(source, where):
    from app.agent.workflow.exact_mention_materialization import normalize_exact_key_echo

    text = "WK-101을 확인해줘"
    state = {"messages": [HumanMessage(content=text)], "request_text": text,
             "turn_continuation": False}
    row = _query("q", source, query="WK-101", where=where)

    assert normalize_exact_key_echo(state, row) is False
    assert row["query"] == "WK-101"


def _runner_state(*, comments: bool = True) -> dict:
    request = "WK-101의 현재 값" + ("과 전체 댓글" if comments else "") + "을 확인해줘"
    queries = [_query("ticket", "jira", where="key=WK-101")]
    if comments:
        queries.append(_query(
            "comments", "comments", where="issueKey=WK-101", completeness="all",
        ))
    return {
        "thread_id": "thread-r37",
        "turn_attempt_id": "attempt-r37-current",
        "messages": [HumanMessage(content=request)],
        "request_text": request,
        "turn_continuation": False,
        "intent": "modify",
        "mentioned_keys": ["WK-101"],
        "request_plan": {"tasks": [
            {"id": "research", "kind": "research", "instruction": "현재 근거 확인",
             "write_intent": False, "depends_on": [], "completion_criteria": ["근거 확인"]},
            {"id": "write", "kind": "comment", "instruction": "WK-101 댓글 초안",
             "write_intent": True, "depends_on": ["research"],
             "completion_criteria": ["댓글 초안"]},
        ]},
        "continuation_contract": _contract(request, ["WK-101"]),
        "query_plan": {"queries": queries, "joins": [], "uncertainty": []},
        "trace": [],
    }


def _install_runner_tools(monkeypatch, *, detail=None, comment_error: bool = False,
                          jira_tickets=None):
    from app.agent import tools as T
    import app.agent.tools.query_tools as query_tools

    calls = {"get_ticket": 0}

    def run_jql(_args):
        rows = ([{"key": "WK-101", "summary": "[WK] 검증 작업"}]
                if jira_tickets is None else jira_tickets)
        return {
            "canonicalJql": 'project in ("WK") AND key=WK-101',
            "scopeProjects": ["WK"],
            "tickets": rows,
            "returned": len(rows),
            "total": len(rows),
            "hasMore": False,
            "nextCursor": None,
        }

    def comments(_args):
        if comment_error:
            return {"error": "permission denied", "comments": [], "hasMore": False,
                    "candidateCoverage": {"returned": 0, "total": 1,
                                          "hasMore": False, "complete": False,
                                          "keys": []},
                    "commentCoverage": {"tickets": 1, "comments": 0,
                                        "complete": False,
                                        "incompleteTickets": ["WK-101"],
                                        "remaining": None}}
        return {
            "canonicalJql": 'project in ("WK") AND issueKey=WK-101',
            "scopeProjects": ["WK"],
            "comments": [],
            "returned": 0,
            "hasMore": False,
            "nextCursor": None,
            "candidateCoverage": {"returned": 1, "total": 1,
                                  "hasMore": False, "complete": True,
                                  "keys": ["WK-101"]},
            "commentCoverage": {"tickets": 1, "comments": 0,
                                "complete": True, "incompleteTickets": [],
                                "remaining": 0, "resultTruncated": False,
                                "resultRemaining": 0},
            "complete": True,
        }

    def get_ticket(args):
        calls["get_ticket"] += 1
        value = detail(args["key"]) if callable(detail) else detail
        return deepcopy(value if value is not None else _detail(args["key"]))

    monkeypatch.setitem(T.BY_NAME, "run_jql_v2", SimpleNamespace(invoke=run_jql))
    monkeypatch.setitem(T.BY_NAME, "search_comments", SimpleNamespace(invoke=comments))
    monkeypatch.setattr(query_tools, "search_comments_complete_page", comments)
    monkeypatch.setitem(T.BY_NAME, "get_ticket", SimpleNamespace(invoke=get_ticket))
    return calls


def test_query_runner_issues_current_receipt_reuses_current_detail_and_keeps_all_comments(
        monkeypatch):
    from app.agent.workflow.agents.query_runner import QueryRunner
    from app.agent.workflow.exact_mention_materialization import (
        EXACT_MENTION_ARTIFACT,
        parse_exact_mention_receipt,
        verified_exact_mention_keys,
    )

    calls = _install_runner_tools(monkeypatch)
    state = _runner_state()
    got = QueryRunner().node()(state)
    combined = {**state, **got}

    # The generic materializer already opened the exact search hit; the new producer must
    # bind that current read instead of issuing a duplicate GET.
    assert calls["get_ticket"] == 1
    receipt = parse_exact_mention_receipt(
        got["query_artifacts"][EXACT_MENTION_ARTIFACT]["receipt"], combined,
    )
    assert receipt is not None and receipt.complete is True
    assert receipt.requested == ("WK-101",)
    assert verified_exact_mention_keys(combined) == {"WK-101"}
    comments = next(row for row in got["query_results"] if row["source"] == "comments")
    assert comments["result"]["complete"] is True


def test_comments_only_receipt_survives_detail_attachment_but_rejects_evidence_tamper(
        monkeypatch):
    from app.agent.workflow.agents.query_runner import QueryRunner
    from app.agent.workflow.exact_mention_materialization import (
        EXACT_MENTION_ARTIFACT,
        parse_exact_mention_receipt,
    )

    _install_runner_tools(monkeypatch)
    state = _runner_state()
    state["query_plan"]["queries"] = [state["query_plan"]["queries"][1]]
    got = QueryRunner().node()(state)
    combined = {**state, **got}
    receipt = got["query_artifacts"][EXACT_MENTION_ARTIFACT]["receipt"]
    comment = next(row for row in got["query_results"] if row["source"] == "comments")

    # QueryRunner attaches independently digested ticketDetails after minting the comment
    # receipt.  That unrelated projection must not self-stale the evidence receipt.
    assert comment["result"]["ticketDetails"][0]["key"] == "WK-101"
    assert parse_exact_mention_receipt(receipt, combined) is not None

    changed = deepcopy(combined)
    row = next(item for item in changed["query_results"] if item["source"] == "comments")
    row["result"]["candidateCoverage"]["keys"] = ["OTHER-9"]
    assert parse_exact_mention_receipt(receipt, changed) is None

    from app.agent.workflow.agents.research_analyst import _completed_typed_write_action
    hidden = deepcopy(combined)
    hidden_row = next(item for item in hidden["query_results"]
                      if item["source"] == "comments")
    hidden_row["result"].pop("ticketDetails")
    assert parse_exact_mention_receipt(receipt, hidden) is not None
    assert _completed_typed_write_action(hidden) == ""


def test_comment_action_without_all_comments_plan_keeps_semantic_acquisition(monkeypatch):
    from app.agent.workflow.agents.query_runner import QueryRunner
    from app.agent.workflow.agents.research_analyst import _completed_typed_write_action
    from app.agent.workflow.exact_mention_materialization import (
        EXACT_MENTION_ARTIFACT,
        parse_exact_mention_receipt,
    )

    _install_runner_tools(monkeypatch)
    state = _runner_state(comments=False)
    got = QueryRunner().node()(state)
    combined = {**state, **got}
    receipt = parse_exact_mention_receipt(
        got["query_artifacts"][EXACT_MENTION_ARTIFACT]["receipt"], combined,
    )

    assert receipt is not None and receipt.complete is True
    assert _completed_typed_write_action(combined) == ""


@pytest.mark.parametrize("provider", [
    lambda _key: {**_detail("OTHER-909")},
    lambda _key: {"error": "not found"},
    lambda key: {"key": key, "summary": "partial", "status": "In Progress",
                 "comments_error": "forbidden"},
    lambda key: {"key": key, "summary": True, "status": 1, "comments": []},
    lambda key: {**_detail(key), "sp": float("nan")},
])
def test_current_wrong_error_or_partial_read_cannot_be_filled_by_stale_sidecar(
        monkeypatch, provider):
    from app.agent.workflow.agents.query_runner import QueryRunner
    from app.agent.workflow.agents.research_analyst import _completed_typed_write_action
    from app.agent.workflow.exact_mention_materialization import (
        EXACT_MENTION_ARTIFACT,
        parse_exact_mention_receipt,
        verified_exact_mention_keys,
    )

    calls = _install_runner_tools(
        monkeypatch, detail=provider, jira_tickets=[],
    )
    state = _runner_state(comments=False)
    state["materialized_ticket_sources"] = {"ticketDetails": [_detail("WK-101")]}
    got = QueryRunner().node()(state)
    combined = {**state, **got}

    assert calls["get_ticket"] == 1
    receipt = parse_exact_mention_receipt(
        got["query_artifacts"][EXACT_MENTION_ARTIFACT]["receipt"], combined,
    )
    assert receipt is not None and receipt.complete is False
    assert verified_exact_mention_keys(combined) == set()
    assert _completed_typed_write_action(combined) == ""


@pytest.mark.parametrize("tickets", [
    [{"summary": "keyless contradiction", "status": "Done"}],
    [{"key": "OTHER-9", "summary": "wrong", "status": "Done"}],
    [{"key": "WK-101", "summary": "one"},
     {"key": "WK-101", "summary": "duplicate"}],
])
def test_unbound_visible_jira_rows_block_compound_fast_acquisition(monkeypatch, tickets):
    from app.agent.workflow.agents.query_runner import QueryRunner
    from app.agent.workflow.agents.research_analyst import _completed_typed_write_action

    _install_runner_tools(monkeypatch, jira_tickets=tickets)
    state = _runner_state()
    got = QueryRunner().node()(state)
    combined = {**state, **got}

    assert _completed_typed_write_action(combined) == ""


def test_exact_candidate_scalar_contradiction_is_removed_before_synthesis(monkeypatch):
    from app.agent import tools as T
    import app.agent.tools.query_tools as query_tools
    from app.agent.workflow.agents.base import ToolAgent
    from app.agent.workflow.agents.query_runner import QueryRunner
    from app.agent.workflow.agents.research_analyst import ResearchAnalyst

    _install_runner_tools(monkeypatch, jira_tickets=[{
        "key": "WK-101", "summary": "FORGED stale summary", "status": "Done",
    }])
    state = _runner_state()
    state["query_plan"]["queries"].append(
        _query("official", "web", query="official verification protocol"),
    )
    monkeypatch.setitem(T.BY_NAME, "search_web", SimpleNamespace(invoke=lambda _args: {
        "results": [{"title": "Official protocol", "url": "https://example.test/spec",
                     "snippet": "bounded official evidence", "untrustedNote": "FORGED web"}],
    }))
    monkeypatch.setattr(query_tools, "search_comments_complete_page", lambda _args: {
        "comments": [{"id": "c1", "ticketKey": "WK-101",
                      "ticketSummary": "FORGED stale title", "author": "u",
                      "date": "2031-01-01", "snippet": "bounded fact"}],
        "returned": 1, "hasMore": False, "nextCursor": None,
        "candidateCoverage": {"returned": 1, "total": 1, "hasMore": False,
                              "complete": True, "keys": ["WK-101"]},
        "commentCoverage": {"tickets": 1, "comments": 1, "complete": True,
                            "incompleteTickets": [], "remaining": 0,
                            "resultTruncated": False, "resultRemaining": 0},
        "complete": True, "untrustedNote": "FORGED comment metadata",
    })
    got = QueryRunner().node()(state)
    combined = {**state, **got}
    comments = next(row for row in combined["query_results"] if row["source"] == "comments")
    # This metadata is intentionally outside the signed evidence projection.  It must be
    # inert at synthesis rather than becoming an unsigned side channel.
    comments["result"]["canonicalJql"] = "key=OTHER-9 FORGED"
    seen = []
    analyst = ResearchAnalyst()
    monkeypatch.setattr(ToolAgent, "node", lambda _self: lambda _state: (_ for _ in ()).throw(
        AssertionError("complete exact acquisition must not enter ReAct")))

    def synthesize(synthesis_state):
        seen.append(synthesis_state)
        return {"situation": "정확한 현재 상세를 기준으로 정리했다.", "evidence": [],
                "related_docs": [], "epic_candidate": "", "already_exists": False}

    monkeypatch.setattr(analyst, "_synthesize_prefetched_query_plan", synthesize)
    analyst.node()(combined)

    assert len(seen) == 1
    assert seen[0].get("web_context")
    jira = next(row for row in seen[0]["query_results"] if row["source"] == "jira")
    assert "tickets" not in jira["result"]
    assert jira["result"]["ticketDetails"][0]["summary"] == _detail("WK-101")["summary"]
    assert "FORGED" not in str(seen[0]["query_results"])


def test_planned_all_comments_failure_blocks_receipt_and_completed_write(monkeypatch):
    from app.agent.workflow.agents.query_runner import QueryRunner
    from app.agent.workflow.agents.research_analyst import _completed_typed_write_action
    from app.agent.workflow.exact_mention_materialization import (
        EXACT_MENTION_ARTIFACT,
        parse_exact_mention_receipt,
    )

    _install_runner_tools(monkeypatch, comment_error=True)
    state = _runner_state()
    got = QueryRunner().node()(state)
    combined = {**state, **got}
    receipt = parse_exact_mention_receipt(
        got["query_artifacts"][EXACT_MENTION_ARTIFACT]["receipt"], combined,
    )

    assert receipt is not None and receipt.complete is False
    assert receipt.outcomes[0].error_kind == "comments_incomplete"
    assert _completed_typed_write_action(combined) == ""


@pytest.mark.parametrize("defect", ["wrong_candidate", "wrong_comment", "zero", "count"])
def test_comment_identity_or_cardinality_contradiction_blocks_receipt(
        monkeypatch, defect):
    import app.agent.tools.query_tools as query_tools
    from app.agent.workflow.agents.query_runner import QueryRunner
    from app.agent.workflow.agents.research_analyst import _completed_typed_write_action
    from app.agent.workflow.exact_mention_materialization import (
        EXACT_MENTION_ARTIFACT,
        parse_exact_mention_receipt,
    )

    _install_runner_tools(monkeypatch)
    result = {
        "comments": [{"id": "c1", "ticketKey": "WK-101", "snippet": "complete"}],
        "returned": 1, "hasMore": False, "nextCursor": None,
        "candidateCoverage": {"returned": 1, "total": 1, "hasMore": False,
                              "complete": True, "keys": ["WK-101"]},
        "commentCoverage": {"tickets": 1, "comments": 1, "complete": True,
                            "incompleteTickets": [], "remaining": 0,
                            "resultTruncated": False, "resultRemaining": 0},
        "complete": True,
    }
    if defect == "wrong_candidate":
        result["candidateCoverage"]["keys"] = ["OTHER-9"]
    elif defect == "wrong_comment":
        result["comments"][0]["ticketKey"] = "OTHER-9"
    elif defect == "zero":
        result.update(comments=[], returned=0)
        result["candidateCoverage"].update(returned=0, total=0, keys=[])
        result["commentCoverage"].update(tickets=0, comments=0)
    else:
        result["commentCoverage"]["comments"] = 2
    monkeypatch.setattr(query_tools, "search_comments_complete_page", lambda _args: result)

    state = _runner_state()
    got = QueryRunner().node()(state)
    combined = {**state, **got}
    receipt = parse_exact_mention_receipt(
        got["query_artifacts"][EXACT_MENTION_ARTIFACT]["receipt"], combined,
    )
    assert receipt is not None and receipt.complete is False
    assert receipt.outcomes[0].error_kind == "comments_incomplete"
    assert _completed_typed_write_action(combined) == ""


@pytest.mark.parametrize("extra", [
    {"description": "prefix " + "x" * 500},
    {"labels": [f"label-{index}" for index in range(9)]},
    {"components": ["x" * 81]},
])
def test_lossy_ticket_projection_keeps_compound_acquisition_semantic(monkeypatch, extra):
    from app.agent.workflow.agents.query_runner import QueryRunner
    from app.agent.workflow.agents.research_analyst import _completed_typed_write_action
    from app.agent.workflow.exact_mention_materialization import (
        EXACT_MENTION_ARTIFACT,
        parse_exact_mention_receipt,
    )

    _install_runner_tools(
        monkeypatch,
        detail=lambda key: {**_detail(key), **extra},
    )
    state = _runner_state()
    got = QueryRunner().node()(state)
    combined = {**state, **got}
    receipt = parse_exact_mention_receipt(
        got["query_artifacts"][EXACT_MENTION_ARTIFACT]["receipt"], combined,
    )
    assert receipt is not None and receipt.complete is False
    assert receipt.outcomes[0].error_kind == "partial"
    assert _completed_typed_write_action(combined) == ""


def test_all_comments_compact_omission_keeps_semantic_path(monkeypatch):
    import app.agent.tools.query_tools as query_tools
    from app.agent.workflow.agents.query_runner import QueryRunner
    from app.agent.workflow.agents.research_analyst import _completed_typed_write_action
    from app.agent.workflow.exact_mention_materialization import (
        EXACT_MENTION_ARTIFACT,
        parse_exact_mention_receipt,
    )

    _install_runner_tools(monkeypatch)

    def comments(_args):
        return {
            "comments": [{"id": f"c{i}", "ticketKey": "WK-101",
                          "author": "u", "date": "2031-01-01", "snippet": f"row {i}"}
                         for i in range(13)],
            "returned": 13, "hasMore": False, "nextCursor": None,
            "candidateCoverage": {"returned": 1, "total": 1,
                                  "hasMore": False, "complete": True,
                                  "keys": ["WK-101"]},
            "commentCoverage": {"tickets": 1, "comments": 13, "complete": True,
                                "incompleteTickets": [], "remaining": 0,
                                "resultTruncated": False, "resultRemaining": 0},
            "complete": True,
        }

    monkeypatch.setattr(query_tools, "search_comments_complete_page", comments)
    state = _runner_state()
    got = QueryRunner().node()(state)
    combined = {**state, **got}
    comments_row = next(row for row in got["query_results"] if row["source"] == "comments")
    receipt = parse_exact_mention_receipt(
        got["query_artifacts"][EXACT_MENTION_ARTIFACT]["receipt"], combined,
    )

    assert len(comments_row["result"]["comments"]) == 12
    assert comments_row["result"]["contextTruncated"] is True
    assert receipt is not None and receipt.complete is False
    assert _completed_typed_write_action(combined) == ""


def test_exact_receipt_rejects_duplicate_outcomes():
    from app.agent.workflow.exact_mention_materialization import (
        ExactMentionOutcomeV1,
        exact_mention_request,
        issue_exact_mention_receipt,
    )

    state = _runner_state(comments=False)
    request = exact_mention_request(state)
    outcome = ExactMentionOutcomeV1(
        key="WK-101", status="success", returned_key="WK-101",
        detail_digest="1" * 64, error_kind="",
    )
    assert issue_exact_mention_receipt(
        request, state["query_plan"], [outcome, outcome], thread_id="thread-r37",
        attempt_id=state["turn_attempt_id"],
    ) is None


@pytest.mark.parametrize("shape", ["missing", "extra", "reordered"])
def test_exact_receipt_rejects_nonexact_ordered_outcome_coverage(shape):
    from app.agent.workflow.exact_mention_materialization import (
        ExactMentionOutcomeV1,
        exact_mention_request,
        issue_exact_mention_receipt,
    )

    text = "WK-101과 WK-102를 확인해줘"
    state = {
        "messages": [HumanMessage(content=text)], "request_text": text,
        "thread_id": "thread-r37", "turn_attempt_id": "attempt-coverage",
        "turn_continuation": False,
        "query_plan": {"queries": [
            _query("a", "jira", where="key=WK-101"),
            _query("b", "jira", where="key=WK-102"),
        ], "joins": [], "uncertainty": []},
    }
    outcomes = [ExactMentionOutcomeV1(
        key=key, status="success", returned_key=key, detail_digest=char * 64,
    ) for key, char in (("WK-101", "1"), ("WK-102", "2"))]
    if shape == "missing":
        outcomes = outcomes[:1]
    elif shape == "extra":
        outcomes.append(ExactMentionOutcomeV1(
            key="OTHER-9", status="success", returned_key="OTHER-9",
            detail_digest="3" * 64,
        ))
    else:
        outcomes.reverse()

    assert issue_exact_mention_receipt(
        exact_mention_request(state), state["query_plan"], outcomes,
        thread_id=state["thread_id"], attempt_id=state["turn_attempt_id"],
    ) is None


def test_exact_receipt_is_bound_to_thread_turn_plan_and_detail(monkeypatch):
    from app.agent.workflow.agents.query_runner import QueryRunner
    from app.agent.workflow.exact_mention_materialization import (
        EXACT_MENTION_ARTIFACT,
        parse_exact_mention_receipt,
        verified_exact_mention_keys,
    )

    _install_runner_tools(monkeypatch)
    state = _runner_state(comments=False)
    got = QueryRunner().node()(state)
    combined = {**state, **got}
    artifact = got["query_artifacts"][EXACT_MENTION_ARTIFACT]
    assert parse_exact_mention_receipt(artifact["receipt"], combined) is not None

    assert parse_exact_mention_receipt(
        artifact["receipt"], {**combined, "thread_id": "other-thread"},
    ) is None
    assert parse_exact_mention_receipt(
        artifact["receipt"], {**combined, "turn_attempt_id": "next-turn"},
    ) is None
    changed_plan = deepcopy(combined)
    changed_plan["query_plan"]["queries"][0]["page_size"] = 26
    assert parse_exact_mention_receipt(artifact["receipt"], changed_plan) is None
    changed_detail = deepcopy(combined)
    changed_detail["query_artifacts"][EXACT_MENTION_ARTIFACT]["details"][0][
        "summary"
    ] = "stale replacement"
    assert verified_exact_mention_keys(changed_detail) == set()


def test_turn_attempt_resets_once_per_user_turn_and_is_not_public():
    from app.agent.workflow.session import _shape, _turn_start_patch

    first = _turn_start_patch("WK-101 확인", {})
    second = _turn_start_patch("WK-101 확인", first)

    assert first["turn_attempt_id"] and second["turn_attempt_id"]
    assert first["turn_attempt_id"] != second["turn_attempt_id"]
    assert first["query_artifacts"] == second["query_artifacts"] == {}
    assert "turn_attempt_id" not in _shape(
        "thread-r37", {"turn_attempt_id": first["turn_attempt_id"]}, snap=False,
    )


def test_resume_and_new_turn_share_one_checkpoint_lock(monkeypatch):
    import threading
    import time
    from app.agent import tools as T
    import app.agent.workflow.session as session

    resume_entered = threading.Event()
    release_resume = threading.Event()
    ask_entered = threading.Event()

    class Snapshot:
        values = {}
        config = {}

    class Graph:
        def get_state(self, _config):
            return Snapshot()

        def invoke(self, initial, _config):
            if initial is None:
                resume_entered.set()
                assert release_resume.wait(2)
                return {"result": {"updated": []}}
            ask_entered.set()
            return {"turn_attempt_id": initial["turn_attempt_id"]}

    graph = Graph()
    monkeypatch.setattr(session, "get_graph", lambda: graph)
    monkeypatch.setattr(session, "_apply_overrides", lambda *_args: "")
    monkeypatch.setattr(session.approval, "approve", lambda *_args: True)
    monkeypatch.setattr(session, "_shape", lambda tid, state, snap=None: {
        "thread_id": tid, "ok": True, "state": state,
    })
    monkeypatch.setattr(session, "_prepare_turn", lambda *_args, **_kwargs:
                        session._PreparedTurn(
                            initial={"turn_attempt_id": "new-attempt"}, claim=None,
                            checkpoint_revision="",
                        ))
    monkeypatch.setattr(T, "set_thread", lambda _tid: None)

    outputs = {}
    resume_thread = threading.Thread(
        target=lambda: outputs.setdefault(
            "resume", session.resume("same-thread", "approval-token"),
        ),
    )
    ask_thread = threading.Thread(
        target=lambda: outputs.setdefault(
            "ask", session.ask("new request", thread_id="same-thread"),
        ),
    )
    resume_thread.start()
    assert resume_entered.wait(1)
    ask_thread.start()
    time.sleep(0.05)
    assert ask_entered.is_set() is False
    release_resume.set()
    resume_thread.join(2)
    ask_thread.join(2)

    assert not resume_thread.is_alive() and not ask_thread.is_alive()
    assert ask_entered.is_set() is True
    assert outputs["resume"]["ok"] is True and outputs["ask"]["ok"] is True


def test_faithful_multi_source_replay_skips_acquisition_react_but_synthesizes_once(
        monkeypatch):
    from app.agent import tools as T
    import app.agent.tools.query_tools as query_tools
    from app.agent.workflow.agents.base import ToolAgent
    from app.agent.workflow.agents.query_runner import QueryRunner
    from app.agent.workflow.agents.query_specialist import QuerySpecialist
    from app.agent.workflow.agents.research_analyst import ResearchAnalyst
    from app.agent.workflow.continuation import build_continuation_contract

    request = (
        "회의 결정을 WK-101, WK-102 댓글로 알려줘. REF-900은 배경 근거지만 "
        "그 티켓은 변경하지 마. Apache Puffin 외부 공식 문서도 찾아줘."
    )
    state = {
        "thread_id": "thread-replay",
        "turn_attempt_id": "attempt-r37-replay",
        "messages": [HumanMessage(content=request)],
        "request_text": request,
        "turn_continuation": False,
        "intent": "modify",
        "mentioned_keys": ["WK-101", "WK-102", "REF-900"],
        "keywords": ["회의", "결정"],
        "request_plan": {"tasks": [
            {"id": "research", "kind": "query",
             "instruction": "WK-101, WK-102, REF-900 근거 확인",
             "write_intent": False, "depends_on": [], "completion_criteria": ["근거 확인"]},
            {"id": "write", "kind": "comment", "instruction": "WK-101, WK-102 댓글 초안",
             "write_intent": True, "depends_on": ["research"],
             "completion_criteria": ["두 댓글 초안"]},
        ]},
        "trace": [],
    }
    state["continuation_contract"] = build_continuation_contract(state)
    assert state["continuation_contract"]["target_keys"] == ["WK-101", "WK-102"]
    polluted = build_continuation_contract(
        {**state, "turn_continuation": True,
         "bulk_targets": ["WK-101", "WK-102", "REF-900"]},
        existing=_contract(request, ["WK-101", "WK-102", "REF-900"]),
    )
    assert polluted["target_keys"] == ["WK-101", "WK-102"]
    import app.agent.workflow.session as session
    monkeypatch.setattr(session, "_is_interview_continuation", lambda *_args: True)
    resumed = session._turn_start_patch("계속 진행", {
        **state,
        "bulk_targets": ["WK-101", "WK-102", "REF-900"],
        "continuation_contract": _contract(
            request, ["WK-101", "WK-102", "REF-900"],
        ),
    })
    assert resumed["continuation_contract"]["target_keys"] == ["WK-101", "WK-102"]
    assert resumed["bulk_targets"] == ["WK-101", "WK-102"]
    raw_plan = {
        "queries": [
            _query("wk-1", "jira", query="WK-101", where="key=WK-101"),
            _query("wk-2", "jira", query="WK-102", where="key=WK-102"),
            _query("wk-1-comments", "comments", query="WK-101",
                   where="issueKey=WK-101", completeness="all"),
            _query("wk-2-comments", "comments", query="WK-102",
                   where="issueKey=WK-102", completeness="all"),
            _query("background-comments", "comments", query="REF-900",
                   where="issueKey=REF-900", completeness="all"),
            _query("public", "web", query="public protocol official documentation"),
        ],
        "joins": [], "uncertainty": [],
    }
    state.update(QuerySpecialist().apply(state, raw_plan))
    get_calls = []

    def run_jql(args):
        key = "WK-101" if "WK-101" in args["where"] else "WK-102"
        return {"tickets": [{"key": key, "summary": f"[{key}] 검증"}],
                "returned": 1, "total": 1, "hasMore": False, "nextCursor": None}

    def search_comments(args):
        key = next(value for value in ("WK-101", "WK-102", "REF-900")
                   if value in args["jql_where"])
        return {"comments": [], "returned": 0,
                "hasMore": False, "nextCursor": None,
                "candidateCoverage": {"returned": 1, "total": 1,
                                      "hasMore": False, "complete": True,
                                      "keys": [key]},
                "commentCoverage": {"tickets": 1, "comments": 0,
                                    "complete": True, "incompleteTickets": [],
                                    "remaining": 0, "resultTruncated": False,
                                    "resultRemaining": 0},
                "complete": True, "key": key}

    def get_ticket(args):
        get_calls.append(args["key"])
        return _detail(args["key"])

    monkeypatch.setitem(T.BY_NAME, "run_jql_v2", SimpleNamespace(invoke=run_jql))
    monkeypatch.setitem(T.BY_NAME, "search_comments", SimpleNamespace(invoke=search_comments))
    monkeypatch.setattr(query_tools, "search_comments_complete_page", search_comments)
    monkeypatch.setitem(T.BY_NAME, "search_web", SimpleNamespace(invoke=lambda _args: {
        "results": [{"title": "Public protocol", "url": "https://example.test/spec",
                     "snippet": "Public verification protocol."}],
    }))
    monkeypatch.setitem(T.BY_NAME, "get_ticket", SimpleNamespace(invoke=get_ticket))
    query_out = QueryRunner().node()(state)
    combined = {**state, **query_out}

    monkeypatch.setattr(ToolAgent, "node", lambda _self: lambda _state: (_ for _ in ()).throw(
        AssertionError("the seven-step acquisition ReAct path must be unreachable")))
    analyst = ResearchAnalyst()
    synth_calls = []

    def synthesize(synthesis_state):
        synth_calls.append(synthesis_state)
        return {
            "situation": "현재 티켓 근거와 배경 기록을 구분해 정리했다.",
            "evidence": [{
                "key": "WK-101", "title": "[WK] 검증 작업", "why": "현재 대상",
                "url": "", "confidence": "high", "fitness": "direct",
                "limitations": "", "observations": [{
                    "source": "description", "text": "현재 범위와 검증 조건을 기록한다.",
                }],
            }],
            "related_docs": [], "epic_candidate": "", "already_exists": False,
        }

    monkeypatch.setattr(analyst, "_synthesize_prefetched_query_plan", synthesize)
    research_out = analyst.node()(combined)

    assert len(synth_calls) == 1
    assert len(get_calls) <= 3 and set(get_calls) == {"WK-101", "WK-102", "REF-900"}
    assert {row["source"] for row in query_out["query_results"]} >= {
        "jira", "comments", "web",
    }
    assert "draft" not in query_out and "bulk_targets" not in query_out
    assert combined["continuation_contract"]["target_keys"] == ["WK-101", "WK-102"]
    receipt = query_out["query_artifacts"][
        "exact-mention-materialization.v1"
    ]["receipt"]
    assert tuple(receipt["requested"]) == ("WK-101", "WK-102", "REF-900")
    import app.agent.workflow.agents.work_architect as work_architect
    from app.agent.workflow.agents.work_architect import WorkArchitect, _typed_target_keys
    continued_work_state = {**combined, **resumed}
    assert _typed_target_keys(continued_work_state) == ["WK-101", "WK-102"]
    monkeypatch.setattr(work_architect, "_ticket_exists", lambda key: key in {
        "WK-101", "WK-102", "REF-900",
    })
    work = WorkArchitect()
    projected = work.pre_validate_structured_output(
        continued_work_state,
        {"change": {"comment": "회의 결정 기록"}, "rationale": ""},
        output_contract="native_json_schema", execution_stage="synthesis",
    )
    assert projected["change"]["keys"] == ["WK-101", "WK-102"]
    work_out = work.apply(continued_work_state, projected)
    change_plan = work_out["change_plan"]
    assert change_plan.get("keys") == ["WK-101", "WK-102"]
    assert "REF-900" not in str(work_out.get("draft"))
    assert "REF-900" not in str(change_plan)
    from app.agent import approval
    from app.agent.workflow.graph import _propose
    proposed = _propose({**continued_work_state, **research_out, **work_out})
    token = proposed.get("approval_token")
    staged = approval.peek(token) if token else None
    assert staged is not None
    assert [row["key"] for row in staged["payload"]["items"]] == ["WK-101", "WK-102"]
    assert "REF-900" not in str(staged["payload"])
    approval.reject(token)
    assert state["request_plan"]["tasks"][1]["instruction"] == "WK-101, WK-102 댓글 초안"
    assert research_out["situation"].startswith("현재 티켓 근거")
