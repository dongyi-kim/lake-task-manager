from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace

import pytest


def _task() -> dict:
    return {
        "id": "comment-draft-1",
        "kind": "comment",
        "instruction": "DL-9090 아래의 남은 하위 Task 하나에만 댓글 초안을 만든다",
        "depends_on": [],
        "write_intent": True,
        "completion_criteria": ["유일한 미완료 직계 자식을 확인한다"],
    }


def _raw_selector(anchor: str = "DL-9090") -> dict:
    return {
        "task_id": "comment-draft-1",
        "anchor_key": anchor,
        "relation": "direct_child",
        "state": "incomplete",
        "cardinality": "exactly_one",
    }


def _grounded_plan(boundary: str = "DL-9090 아래의 남은 하위 Task 하나에 댓글을 남겨줘") -> dict:
    from app.agent.workflow.target_resolution import ground_target_selectors

    tasks = [_task()]
    return {
        "goal": "유일한 미완료 하위 Task에 댓글 초안을 만든다",
        "tasks": tasks,
        "request_questions": [],
        "blocking_questions": [],
        "assumptions": [],
        "target_selectors": ground_target_selectors([_raw_selector()], tasks, boundary),
    }


def _selector_query() -> dict:
    return {
        "id": "target-resolution-comment-draft-1",
        "source": "jira",
        "query": "",
        "where": "parent = DL-9090",
        "order_by": "",
        "fields": ["key", "summary", "status", "issuetype", "assignee", "updated"],
        "completeness": "all",
        "page_size": 100,
        "depends_on": [],
        "target_selector_id": "comment-draft-1",
    }


def _base_state() -> dict:
    boundary = "DL-9090 아래의 남은 하위 Task 하나에 댓글을 남겨줘"
    return {
        "thread_id": "target-resolution-thread",
        "turn_attempt_id": "attempt-1",
        "request_text": boundary,
        "messages": [],
        "intent": "modify",
        "request_plan": _grounded_plan(boundary),
        "query_plan": {"queries": [_selector_query()], "joins": [], "uncertainty": []},
        "continuation_contract": {
            "version": "continuation.v1",
            "root_request": boundary,
            "intent": "modify",
            "action": "comment",
            "target_keys": ["DL-9090"],
            "outcome_ids": ["comment-draft-1"],
            "decisions": [],
        },
    }


def _snapshot(*, incomplete=("DL-9095",), complete: bool = True) -> dict:
    rows = [
        {"key": "DL-9093", "summary": "렌더링", "status": "Done",
         "statusCategory": "done", "type": "Sub-Task", "parentKey": "DL-9090"},
        {"key": "DL-9094", "summary": "업스트림", "status": "Done",
         "statusCategory": "done", "type": "Sub-Task", "parentKey": "DL-9090"},
    ]
    rows.extend({
        "key": key, "summary": f"미완료 {key}", "status": "In Progress",
        "statusCategory": "inprogress", "type": "Sub-Task", "parentKey": "DL-9090",
    } for key in incomplete)
    return {
        "contract": "jira-direct-children-snapshot.v1",
        "parentKey": "DL-9090",
        "parentType": "Task",
        "children": rows,
        "expectedKeys": [row["key"] for row in rows],
        "returned": len(rows),
        "total": len(rows),
        "complete": complete,
        "remaining": 0 if complete else None,
    }


def _resolved_state(snapshot=None) -> dict:
    from app.agent.workflow.target_resolution import (
        TARGET_RESOLUTION_ARTIFACT,
        build_target_resolution_result,
    )

    state = _base_state()
    result, artifact = build_target_resolution_result(
        state, _selector_query(), snapshot or _snapshot(),
    )
    state["query_results"] = [{
        "id": _selector_query()["id"], "source": "jira", "result": result,
    }]
    state["query_artifacts"] = {TARGET_RESOLUTION_ARTIFACT: {
        "comment-draft-1": artifact,
    }}
    return state


def test_selector_grounding_binds_one_write_task_and_current_anchor():
    from app.agent.workflow.target_resolution import ground_target_selectors

    boundary = "DL-9090 아래의 남은 하위 Task 하나에 댓글을 남겨줘"
    got = ground_target_selectors([_raw_selector()], [_task()], boundary)

    assert len(got) == 1
    assert got[0] == {**got[0], **_raw_selector(), "contract": "target-selector.v1"}
    assert len(got[0]["source_digest"]) == 64


def test_request_architect_persists_only_runtime_grounded_selector():
    from langchain_core.messages import HumanMessage
    from app.agent.workflow.agents.request_architect import RequestArchitect

    boundary = "DL-9090 아래의 남은 하위 Task 하나에 댓글을 남겨줘"
    patch = RequestArchitect().apply(
        {"request_text": boundary, "messages": [HumanMessage(content=boundary)]},
        {
            "intent": "modify", "keywords": ["DL-9090"], "mentioned_keys": ["DL-9090"],
            "sufficient": True, "request_questions": [], "requested_effects": [],
            "target_selectors": [_raw_selector()],
            "goal": "유일한 미완료 하위 Task에 댓글을 남긴다",
            # Reproduce the local model contradiction from CTX4: the typed kind is a
            # comment draft, but the boolean was false.  RequestPlan canonicalization owns
            # this invariant so downstream deterministic retrieval does not depend on the
            # model repeating the same fact twice.
            "tasks": [{**_task(), "write_intent": False}],
            "blocking_questions": [], "assumptions": [],
        },
    )

    selectors = patch["request_plan"]["target_selectors"]
    assert selectors[0]["anchor_key"] == "DL-9090"
    assert selectors[0]["task_id"] == "comment-draft-1"
    assert len(selectors[0]["source_digest"]) == 64
    assert patch["request_plan"]["tasks"][0]["write_intent"] is True


@pytest.mark.parametrize("raw,tasks,boundary", [
    (_raw_selector("DL-9999"), [_task()], "DL-9090 아래의 남은 하위 Task 하나"),
    (_raw_selector(), [_task(), {**_task(), "id": "second"}],
     "DL-9090 아래의 남은 하위 Task 하나"),
    ({**_raw_selector(), "cardinality": "all"}, [_task()],
     "DL-9090 아래의 남은 하위 Task 하나"),
])
def test_selector_grounding_fails_closed_on_wrong_anchor_multi_write_or_broad_shape(
        raw, tasks, boundary):
    from app.agent.workflow.target_resolution import ground_target_selectors

    assert ground_target_selectors([raw], tasks, boundary) == []


def test_query_specialist_adds_only_compiler_owned_resolution_query():
    from app.agent.workflow.agents.query_specialist import QuerySpecialist

    state = _base_state()
    out = QuerySpecialist().apply(state, {"reads": [], "uncertainty": []})

    assert out["query_plan"]["queries"] == [_selector_query()]


def test_query_specialist_discards_model_authored_selector_query():
    from app.agent.workflow.agents.query_specialist import QuerySpecialist

    state = _base_state()
    forged = deepcopy(_selector_query())
    forged.update(id="forged", target_selector_id="other", where="parent = DL-9999")
    out = QuerySpecialist().apply(
        state, {"queries": [forged], "joins": [], "uncertainty": []},
    )

    assert out["query_plan"]["queries"] == [_selector_query()]


def test_selector_compiler_replaces_anchor_search_comments_and_uncertainty():
    from app.agent.workflow.agents.query_specialist import (
        QuerySpecialist,
        _deterministic_plan_retrieval,
    )

    state = _base_state()
    # Persisted/model-authored duplicate boolean must not override the grounded selector.
    state["request_plan"]["tasks"][0]["write_intent"] = False
    out = QuerySpecialist().apply(state, {
        "queries": [
            {"id": "anchor", "source": "jira", "query": "", "where": "key=DL-9090",
             "order_by": "", "fields": [], "completeness": "all", "page_size": 25,
             "depends_on": []},
            {"id": "anchor-comments", "source": "comments", "query": "",
             "where": "issueKey=DL-9090", "order_by": "", "fields": [],
             "completeness": "all", "page_size": 25, "depends_on": []},
        ],
        "joins": [],
        "uncertainty": ["남은 하위 Task를 식별해야 한다"],
    })

    assert _deterministic_plan_retrieval(state) is True
    assert out["query_plan"] == {
        "queries": [_selector_query()], "joins": [], "uncertainty": [],
    }


def test_signed_resolution_rebinds_only_unique_incomplete_direct_child():
    from app.agent.workflow.target_resolution import authoritative_mutation_targets

    state = _resolved_state()

    assert authoritative_mutation_targets(state) == ("DL-9095",)


@pytest.mark.parametrize("snapshot", [
    _snapshot(incomplete=("DL-9095", "DL-9096")),
    _snapshot(incomplete=()),
    _snapshot(complete=False),
    {**_snapshot(), "parentKey": "DL-9999"},
])
def test_resolution_is_not_authority_when_multiple_empty_incomplete_or_wrong_parent(snapshot):
    from app.agent.workflow.target_resolution import authoritative_mutation_targets

    state = _resolved_state(snapshot)

    assert authoritative_mutation_targets(state) == ()


@pytest.mark.parametrize("mutation", [
    lambda state: state.update(turn_attempt_id="attempt-2"),
    lambda state: state["request_plan"].update(goal="stale plan"),
    lambda state: state["query_plan"]["queries"][0].update(where="parent = DL-9999"),
    lambda state: state["query_results"][0]["result"]["targetResolution"].update(
        resolvedKeys=["DL-9999"]),
])
def test_resolution_receipt_rejects_stale_or_tampered_state(mutation):
    from app.agent.workflow.target_resolution import authoritative_mutation_targets

    state = _resolved_state()
    mutation(state)

    assert authoritative_mutation_targets(state) == ()


@pytest.mark.parametrize("missing", ["thread_id", "turn_attempt_id"])
def test_resolution_receipt_is_not_issued_without_turn_identity(missing):
    from app.agent.workflow.target_resolution import build_target_resolution_result

    state = _base_state()
    state[missing] = ""
    result, artifact = build_target_resolution_result(state, _selector_query(), _snapshot())

    assert result["complete"] is False
    assert result["incompleteReason"] == "turn_identity_missing"
    assert artifact is None


def test_work_projection_uses_resolved_child_not_anchor():
    from app.agent.workflow.agents.work_architect import WorkArchitect

    state = _resolved_state()
    projected = WorkArchitect().pre_validate_structured_output(
        state,
        {"questions": [], "change": {"key": "DL-9090", "comment": "측정 결과를 첨부해 주세요"}},
        output_contract="work.comment", execution_stage="projection",
    )

    assert projected["change"]["key"] == "DL-9095"


def test_auditor_accepts_resolved_child_and_rejects_sibling():
    from app.agent.workflow.agents.auditor import _typed_change_target_errors

    state = _resolved_state()
    state["change_plan"] = {
        "key": "DL-9095", "changes": {}, "comment": "측정 결과를 첨부해 주세요",
    }
    assert _typed_change_target_errors(state) == []

    state["change_plan"]["key"] = "DL-9094"
    errors = _typed_change_target_errors(state)
    assert any(row.get("field") == "target" for row in errors)


def test_query_runner_uses_server_child_snapshot_and_materializes_resolved_target(monkeypatch):
    from app.agent import tools as T
    from app.agent.tools import _ctx
    from app.agent.workflow.agents.query_runner import QueryRunner
    from app.agent.workflow.target_resolution import authoritative_mutation_targets

    calls = {"snapshot": 0, "get": []}

    class Client:
        def ticket_children_snapshot(self, key, **_kwargs):
            calls["snapshot"] += 1
            assert key == "DL-9090"
            return _snapshot()

    monkeypatch.setattr(_ctx, "client", lambda: Client())
    monkeypatch.setitem(T.BY_NAME, "get_ticket", SimpleNamespace(invoke=lambda args: (
        calls["get"].append(args["key"])
        or {"key": args["key"], "summary": args["key"], "status": "In Progress",
            "comments": [], "parentKey": "DL-9090"}
    )))

    state = _base_state()
    patch = QueryRunner()._run(state)
    resolved = {**state, **patch}

    assert calls["snapshot"] == 1
    assert "DL-9095" in calls["get"]
    assert authoritative_mutation_targets(resolved) == ("DL-9095",)


def test_query_runner_never_reads_out_of_scope_selector_anchor(monkeypatch):
    from app.agent.tools import _ctx
    from app.agent.workflow.agents.query_runner import QueryRunner
    from app.agent.workflow.target_resolution import authoritative_mutation_targets

    class Client:
        def ticket_children_snapshot(self, *_args, **_kwargs):
            raise AssertionError("out-of-scope Jira key must not reach the provider")

    monkeypatch.setattr(_ctx, "client", lambda: Client())
    monkeypatch.setattr(_ctx, "jira_key_allowed", lambda _key: False)
    state = _base_state()
    patch = QueryRunner()._run(state)

    assert patch["query_results"][0]["result"]["complete"] is False
    assert authoritative_mutation_targets({**state, **patch}) == ()


def _provider_child(key: str, category: str) -> dict:
    status_name = "Done" if category == "done" else "In Progress"
    return {
        "key": key,
        "fields": {
            "summary": key,
            "status": {"name": status_name,
                       "statusCategory": {"key": category}},
            "issuetype": {"name": "Sub-Task"},
            "parent": {"key": "DL-9090"},
            "assignee": None,
            "updated": "2031-01-01T00:00:00.000+0000",
        },
    }


def test_jira_child_snapshot_reads_parent_relation_and_exact_current_statuses():
    from app.jira.jira_client import JiraClient

    calls = []

    def get_json(path, params=None, **_kwargs):
        calls.append((path, deepcopy(params or {})))
        if path.endswith("/issue/DL-9090"):
            return {"key": "DL-9090", "fields": {
                "issuetype": {"name": "Task"},
                "subtasks": [{"key": "DL-9093"}, {"key": "DL-9095"}],
            }}
        assert path.endswith("/search")
        return {"total": 2, "issues": [
            _provider_child("DL-9093", "done"),
            _provider_child("DL-9095", "indeterminate"),
        ]}

    client = object.__new__(JiraClient)
    client._provider = SimpleNamespace(get_json=get_json)
    client._provider_built = True

    got = client.ticket_children_snapshot("DL-9090")

    assert got["complete"] is True
    assert got["expectedKeys"] == ["DL-9093", "DL-9095"]
    assert [row["statusCategory"] for row in got["children"]] == ["done", "inprogress"]
    assert len(calls) == 2


def test_jira_child_snapshot_fails_closed_on_partial_or_wrong_batch_identity():
    from app.jira.jira_client import JiraClient

    def get_json(path, params=None, **_kwargs):
        if path.endswith("/issue/DL-9090"):
            return {"key": "DL-9090", "fields": {
                "issuetype": {"name": "Task"},
                "subtasks": [{"key": "DL-9093"}, {"key": "DL-9095"}],
            }}
        return {"total": 2, "issues": [
            _provider_child("DL-9093", "done"),
            _provider_child("DL-9999", "indeterminate"),
        ]}

    client = object.__new__(JiraClient)
    client._provider = SimpleNamespace(get_json=get_json)
    client._provider_built = True

    got = client.ticket_children_snapshot("DL-9090")

    assert got["complete"] is False
    assert got["incompleteReason"] == "wrong_or_duplicate_child"


def test_jira_child_snapshot_requires_exact_parent_identity():
    from app.jira.jira_client import JiraClient

    client = object.__new__(JiraClient)
    client._provider = SimpleNamespace(get_json=lambda *_args, **_kwargs: {
        "fields": {"issuetype": {"name": "Task"}, "subtasks": []},
    })
    client._provider_built = True

    got = client.ticket_children_snapshot("DL-9090")

    assert got["complete"] is False
    assert got["incompleteReason"] == "wrong_parent"
