# -*- coding: utf-8 -*-
"""Typed per-outcome continuation decisions stay attached to their exact write result."""

import os
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JIRA_ENV", "mock")

pytest.importorskip("langgraph", reason="requirements-agent.txt 미설치")

from app.agent import approval  # noqa: E402
from app.agent.workflow import graph as G  # noqa: E402
from app.agent.workflow.anchors import requested_outcome_contract  # noqa: E402
from app.agent.workflow.agents.work_architect import WorkArchitect  # noqa: E402
from app.agent.workflow.continuation import (  # noqa: E402
    capture_continuation_decisions,
    merge_continuation_decisions,
)
from app.agent.workflow.state import Intent  # noqa: E402


@pytest.fixture(autouse=True)
def fake(monkeypatch, tmp_path):
    monkeypatch.setenv("LAKE_AGENT_PROVIDER", "fake")
    import app.infra.settings as settings

    monkeypatch.setattr(settings, "CACHE_DIR", tmp_path)
    approval.clear()
    G.reset()
    yield
    approval.clear()
    G.reset()


def _request_state() -> dict:
    request = "인증과 검색 후속 Task를 각각 만들어줘"
    state = {
        "request_text": request,
        "turn_continuation": True,
        "intent": Intent.PLAN_WORK,
        "request_plan": {"goal": request, "tasks": [
            {"id": "auth", "kind": "ticket", "write_intent": True,
             "instruction": "인증 후속 Task 생성"},
            {"id": "search", "kind": "ticket", "write_intent": True,
             "instruction": "검색 후속 Task 생성"},
        ]},
    }
    state["continuation_contract"] = {
        "version": "continuation.v1",
        "root_request": request,
        "intent": Intent.PLAN_WORK,
        "action": "create",
        "target_keys": ["DL-100", "DL-200"],
        "outcome_ids": ["auth", "search"],
        "decisions": [
            {"field": "target:auth", "value": "인증 시스템",
             "source": "interview_answer"},
            {"field": "target:search", "value": "검색 시스템",
             "source": "interview_answer"},
            {"field": "parent:auth", "value": "DL-100",
             "source": "interview_answer"},
            {"field": "parent:search", "value": "DL-200",
             "source": "interview_answer"},
            {"field": "assignee:auth", "value": "skcc.a1001",
             "source": "interview_answer"},
            {"field": "assignee:search", "value": "미할당",
             "source": "interview_answer"},
        ],
    }
    return state


def _outcome_refs(state: dict) -> tuple[str, str, str]:
    contract = requested_outcome_contract(state)
    by_task = {row["source_task_id"]: row["id"] for row in contract["outcomes"]}
    return contract["id"], by_task["auth"], by_task["search"]


def _item(summary: str, ref: str, *, epic: str, assignee: str = "") -> dict:
    item = {
        "summary": summary,
        "type": "Task",
        "outcome_refs": [ref],
        "epic": epic,
        "background": f"{summary} 요청됨",
        "scope_in": [summary],
        "scope_out": [],
        "dod": [f"{summary} 결과를 기록한다"],
    }
    if assignee:
        item["assignee"] = assignee
    return item


def test_scoped_target_decisions_accumulate_without_erasing_sibling_targets():
    contract = _request_state()["continuation_contract"]
    contract["target_keys"] = []
    contract["decisions"] = []

    first = merge_continuation_decisions(contract, [{
        "field": "target:auth", "value": "DL-100", "source": "interview_answer",
    }])
    second = merge_continuation_decisions(first, [{
        "field": "target:search", "value": "DL-200", "source": "interview_answer",
    }])

    assert second["target_keys"] == ["DL-100", "DL-200"]
    assert {row["field"] for row in second["decisions"]} == {
        "target:auth", "target:search",
    }


def test_question_capture_preserves_scoped_field_identity():
    decisions = capture_continuation_decisions(
        "부모는 DL-100이고 검색 작업은 미할당으로 둘게",
        [
            {"field": "parent:auth", "options": []},
            {"field": "assignee:search", "options": []},
        ],
    )

    assert {row["field"] for row in decisions} >= {
        "parent:auth", "assignee:search",
    }


def test_scoped_question_capture_binds_each_value_to_its_named_clause():
    decisions = capture_continuation_decisions(
        "인증은 DL-100, 검색은 DL-200",
        [
            {"field": "parent:auth", "question": "인증 작업의 상위 Epic은?"},
            {"field": "parent:search", "question": "검색 작업의 상위 Epic은?"},
        ],
    )
    values = {row["field"]: row["value"] for row in decisions}

    assert values == {"parent:auth": "DL-100", "parent:search": "DL-200"}

    assignments = capture_continuation_decisions(
        "인증은 skcc.a1001, 검색은 미할당",
        [
            {"field": "assignee:auth", "question": "인증 작업 담당자는?"},
            {"field": "assignee:search", "question": "검색 작업 담당자는?"},
        ],
    )
    assigned = {row["field"]: row["value"] for row in assignments}
    assert assigned == {"assignee:auth": "skcc.a1001", "assignee:search": "미할당"}


def test_latest_scoped_target_replaces_only_its_prior_sibling_value():
    contract = _request_state()["continuation_contract"]
    contract["decisions"] = [
        {"field": "target:auth", "value": "DL-100", "source": "interview_answer"},
        {"field": "target:search", "value": "DL-200", "source": "interview_answer"},
    ]
    contract["target_keys"] = ["DL-100", "DL-200"]

    changed = merge_continuation_decisions(contract, [{
        "field": "target:auth", "value": "DL-300", "source": "interview_answer",
    }])

    assert changed["target_keys"] == ["DL-200", "DL-300"]
    values = {row["field"]: row["value"] for row in changed["decisions"]}
    assert values == {"target:search": "DL-200", "target:auth": "DL-300"}


def test_work_applies_scoped_parent_and_assignment_by_opaque_outcome_ref(monkeypatch):
    from app.agent.workflow import anchors
    from app.agent.workflow.agents import work_architect as work

    state = _request_state()
    contract_id, auth_ref, search_ref = _outcome_refs(state)
    monkeypatch.setattr(work, "_is_epic", lambda key: key in {"DL-100", "DL-200"})
    monkeypatch.setattr(work, "_ticket_exists", lambda key: key in {"DL-100", "DL-200"})
    monkeypatch.setattr(work, "_known_labels", lambda: set())
    monkeypatch.setattr(work, "_known_components", lambda: set())

    scoped = anchors.scoped_continuation_decisions(state)
    assert scoped[auth_ref]["target"]["value"] == "인증 시스템"
    assert scoped[search_ref]["target"]["value"] == "검색 시스템"

    output = {
        "questions": [], "mode": "task", "structure": "multiple_tasks",
        "structure_why": "독립 산출물", "rationale": "",
        "outcome_contract_id": contract_id,
        "items": [
            _item("인증 후속", auth_ref, epic="DL-200", assignee="skcc.wrong1"),
            _item("검색 후속", search_ref, epic="DL-100", assignee="skcc.wrong2"),
        ],
    }

    draft = WorkArchitect().apply(state, output)["draft"]
    by_ref = {row["outcome_refs"][0]: row for row in draft["items"]}

    assert by_ref[auth_ref]["epic"] == "DL-100"
    assert by_ref[auth_ref]["parent_source"] == "user"
    assert by_ref[auth_ref]["assignee"] == "skcc.a1001"
    assert by_ref[auth_ref]["assignee_source"] == "user"
    assert by_ref[search_ref]["epic"] == "DL-200"
    assert by_ref[search_ref]["parent_source"] == "user"
    assert by_ref[search_ref].get("assignee") in (None, "")
    assert by_ref[search_ref]["assignee_source"] == "user_unassigned"


def test_work_resolves_named_scoped_assignee_and_final_gate_rejects_unresolved_name(
        monkeypatch):
    from app.agent import tools as agent_tools
    from app.agent.workflow.agents import auditor
    from app.agent.workflow.agents import work_architect as work

    state = _request_state()
    _contract_id, auth_ref, search_ref = _outcome_refs(state)
    for decision in state["continuation_contract"]["decisions"]:
        if decision["field"] == "assignee:auth":
            decision["value"] = "담당자는 김철수"
    monkeypatch.setitem(agent_tools.BY_NAME, "find_person", SimpleNamespace(
        invoke=lambda _args: {"resolved": "skcc.k1001", "ambiguous": False},
    ))
    monkeypatch.setattr(work, "_is_epic", lambda key: key in {"DL-100", "DL-200"})
    monkeypatch.setattr(work, "_ticket_exists", lambda key: key in {"DL-100", "DL-200"})
    monkeypatch.setattr(work, "_known_labels", lambda: set())
    monkeypatch.setattr(work, "_known_components", lambda: set())
    output = {
        "questions": [], "mode": "task", "structure": "multiple_tasks",
        "structure_why": "독립 산출물", "rationale": "",
        "outcome_contract_id": requested_outcome_contract(state)["id"],
        "items": [
            _item("인증 후속", auth_ref, epic="DL-100", assignee="skcc.wrong1"),
            _item("검색 후속", search_ref, epic="DL-200", assignee="skcc.wrong2"),
        ],
    }

    draft = WorkArchitect().apply(state, output)["draft"]
    auth = next(row for row in draft["items"] if row["outcome_refs"] == [auth_ref])
    assert auth["assignee"] == "skcc.k1001"
    assert auth["assignee_source"] == "user"

    bad = {**state, "draft": {"mode": "task", "items": [
        {**_item("인증 후속", auth_ref, epic="DL-100", assignee="skcc.x9999")},
        _item("검색 후속", search_ref, epic="DL-200"),
    ]}}
    errors = auditor._scoped_decision_errors(bad)
    assert any(row.get("field") == "assignee" for row in errors)


def test_global_assignee_and_top_level_parent_are_applied_and_final_gated(monkeypatch):
    from app.agent.workflow.agents import auditor
    from app.agent.workflow.agents import work_architect as work

    state = _request_state()
    state["continuation_contract"]["decisions"] = [
        {"field": "parent", "value": "최상위 Task", "source": "interview_answer"},
        {"field": "assignee", "value": "skcc.x1103", "source": "interview_answer"},
    ]
    monkeypatch.setattr(work, "_known_labels", lambda: set())
    monkeypatch.setattr(work, "_known_components", lambda: set())
    _contract_id, auth_ref, search_ref = _outcome_refs(state)
    output = {
        "questions": [], "mode": "task", "structure": "multiple_tasks",
        "structure_why": "독립 산출물", "rationale": "",
        "outcome_contract_id": requested_outcome_contract(state)["id"],
        "items": [
            _item("인증 후속", auth_ref, epic="DL-100", assignee="skcc.wrong1"),
            _item("검색 후속", search_ref, epic="DL-200", assignee="skcc.wrong2"),
        ],
    }

    draft = WorkArchitect().apply(state, output)["draft"]
    assert all(not (row.get("epic") or row.get("parent")) for row in draft["items"])
    assert all(row.get("parent_source") == "user_top_level" for row in draft["items"])
    assert all(row.get("assignee") == "skcc.x1103" for row in draft["items"])
    assert auditor._typed_parent_errors({**state, "draft": draft}) == []
    assert auditor._typed_assignee_errors({**state, "draft": draft}) == []

    wrong = {**state, "draft": {"mode": "task", "items": [
        {**draft["items"][0], "epic": "DL-100", "assignee": "skcc.wrong1"},
        draft["items"][1],
    ]}}
    assert auditor._typed_parent_errors(wrong)
    assert auditor._typed_assignee_errors(wrong)


@pytest.mark.parametrize(
    ("refs", "parents"),
    [
        (("auth", "search"), ("DL-200", "DL-100")),
        (("auth", ""), ("DL-100", "DL-200")),
        (("auth", "auth"), ("DL-100", "DL-100")),
    ],
    ids=["swapped-fields", "missing-ref", "duplicate-ref"],
)
def test_final_gate_rejects_invalid_scoped_outcome_binding_and_stages_nothing(
        monkeypatch, refs, parents):
    from app.agent.workflow.agents import auditor

    state = _request_state()
    contract_id, auth_ref, search_ref = _outcome_refs(state)
    aliases = {"auth": auth_ref, "search": search_ref, "": ""}
    monkeypatch.setattr(auditor, "_machine_check", lambda _state: {
        "ok": True, "errors": [], "warnings": [], "text": "ok",
    })
    state.update({
        "thread_id": "scoped-final-gate",
        "review": {"ok": True, "errors": [], "problems": []},
        "draft": {
            "mode": "task", "outcome_contract_id": contract_id,
            "items": [
                {"summary": "인증 후속", "type": "Task", "epic": parents[0],
                 "outcome_refs": [aliases[refs[0]]] if aliases[refs[0]] else []},
                {"summary": "검색 후속", "type": "Task", "epic": parents[1],
                 "outcome_refs": [aliases[refs[1]]] if aliases[refs[1]] else []},
            ],
        },
    })

    staged = G._propose(state)

    assert staged["review"]["ok"] is False
    assert staged["approval_token"] == ""
    assert any(row.get("field") in {"parent", "outcome_refs"}
               for row in staged["review"]["errors"])


@pytest.mark.parametrize(
    ("items", "valid_parent", "ok"),
    [
        ([{"summary": "wrapper", "type": "Task", "children": [
            {"summary": "child", "type": "Sub-Task"},
        ]}], True, False),
        ([{"summary": "wrong type", "type": "Task", "parent": "DL-9090"}], True, False),
        ([{"summary": "missing parent", "type": "Sub-Task"}], True, False),
        ([{"summary": "invalid parent", "type": "Sub-Task", "parent": "DL-9090"}], False, False),
        ([{"summary": "one child", "type": "Sub-Task", "parent": "DL-9090"}], True, True),
    ],
    ids=["wrapper-plus-child", "wrong-type", "missing-parent", "invalid-parent", "valid"],
)
def test_exact_one_subtask_final_gate_enforces_total_type_and_parent(
        monkeypatch, items, valid_parent, ok):
    from app.agent.workflow.agents import auditor

    monkeypatch.setattr(auditor, "_machine_check", lambda _state: {
        "ok": True, "errors": [], "warnings": [], "text": "ok",
    })
    monkeypatch.setattr(
        auditor, "_can_parent_subtask",
        lambda key: bool(valid_parent and key == "DL-9090"),
    )
    state = {
        "continuation_contract": {
            "version": "continuation.v1",
            "root_request": "DL-9090 아래에 회귀 검증 Sub-Task 하나 만들어줘",
            "intent": "plan_work", "action": "create",
            "target_keys": ["DL-9090"], "outcome_ids": [], "decisions": [],
        },
        "review": {"ok": True, "errors": [], "problems": []},
        "draft": {"mode": "subtask", "items": items},
    }

    review = auditor.final_authority_review(state, require_effect=True)

    assert review["ok"] is ok
    if not ok:
        assert any(row.get("field") == "cardinality" for row in review["errors"])


@pytest.mark.parametrize("phrase", [
    "DL-9090 아래에 하위 태스크 1건 만들어줘",
    "DL-9090 아래에 서브테스크 하나 만들어줘",
    "DL-9090 아래에 Sub Task 하나 만들어줘",
])
def test_exact_one_subtask_aliases_reject_wrapper_with_children(monkeypatch, phrase):
    from app.agent.workflow.agents import auditor

    monkeypatch.setattr(auditor, "_can_parent_subtask", lambda key: key == "DL-9090")
    state = {
        "continuation_contract": {
            "version": "continuation.v1", "root_request": phrase,
            "intent": "plan_work", "action": "create", "target_keys": ["DL-9090"],
            "outcome_ids": [], "decisions": [],
        },
        "draft": {"mode": "task", "items": [{
            "summary": "wrapper", "type": "Task", "children": [
                {"summary": "one", "type": "Sub-Task", "parent": "DL-9090"},
            ],
        }]},
    }
    assert auditor._cardinality_errors(state)


def test_distributive_one_subtask_per_parent_is_not_one_total_issue():
    from app.agent.workflow.agents import auditor

    state = {
        "continuation_contract": {
            "version": "continuation.v1",
            "root_request": "DL-100과 DL-200에 각각 Sub-Task 한 개씩 만들어줘",
            "intent": "plan_work", "action": "create",
            "target_keys": ["DL-100", "DL-200"], "outcome_ids": [], "decisions": [],
        },
        "draft": {"mode": "subtask", "items": [
            {"summary": "A", "type": "Sub-Task", "parent": "DL-100"},
            {"summary": "B", "type": "Sub-Task", "parent": "DL-200"},
        ]},
    }
    assert auditor._cardinality_errors(state) == []
