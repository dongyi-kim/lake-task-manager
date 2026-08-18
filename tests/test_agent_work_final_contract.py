"""Typed Work/Auditor/final-boundary contracts with domain-neutral fixtures."""

from __future__ import annotations

import copy

import pytest
from langchain_core.messages import HumanMessage


def _state(text: str, tasks: list[dict], *, action: str = "create") -> dict:
    return {
        "thread_id": "typed-contract",
        "request_text": text,
        "messages": [HumanMessage(content=text)],
        "intent": "plan_work" if action == "create" else "modify",
        "request_plan": {"goal": text, "tasks": tasks},
        "continuation_contract": {
            "version": "continuation.v1",
            "root_request": text,
            "intent": "plan_work" if action == "create" else "modify",
            "action": action,
            "target_keys": [],
            "outcome_ids": [str(task["id"]) for task in tasks],
            "decisions": [],
        },
    }


def test_question_contract_keeps_user_slots_and_downgrades_safe_parent_defaults():
    from app.agent.workflow.agents.work_architect import _normalize_question_contracts

    state = {
        "materialized_ticket_sources": {
            "parentCandidateKeys": ["ACME-100"],
            "ticketDetails": [{"key": "ACME-100", "type": "Epic"}],
        },
    }
    questions = [
        {"question": "어느 데이터셋을 바꿀까요?", "kind": "text", "field": "target",
         "options": [], "required_input": True, "why_required": "대상이 필요함"},
        {"question": "어느 상위에 둘까요?", "kind": "choice", "field": "parent",
         "options": ["ACME-100", "최상위"], "required_input": True,
         "why_required": "배치가 필요함"},
        {"question": "몇 개로 나눌까요?", "kind": "choice", "field": "structure",
         "options": ["하나", "여러 개"], "required_input": True,
         "why_required": "구조가 필요함"},
    ]

    contracts = _normalize_question_contracts(
        state, questions, mode="task", items=[{"type": "Task"}],
    )

    assert [row["field"] for row in contracts] == ["target"]
    assert contracts[0]["ownership"] == "user_required"
    assert contracts[0]["required_input"] is True
    assert contracts[0]["contract"] == "question.v1"
    assert [row["field"] for row in _normalize_question_contracts(
        {}, questions, mode="task", items=[{"type": "Task"}],
    )] == ["target"], "top-level is a safe Task placement fallback"


def test_question_contract_requires_a_parent_only_for_an_unresolved_subtask():
    from app.agent.workflow.agents.work_architect import _normalize_question_contracts

    question = {
        "question": "어느 Task 아래에 둘까요?", "kind": "choice", "field": "parent",
        "options": [], "required_input": True, "why_required": "Sub-Task 부모가 필요함",
    }
    contracts = _normalize_question_contracts(
        {}, [question], mode="subtask", items=[{"type": "Sub-Task"}],
    )

    assert len(contracts) == 1
    assert contracts[0]["ownership"] == "user_required"
    assert contracts[0]["required_input"] is True


def test_question_contract_downgrades_only_a_verified_subtask_parent(monkeypatch):
    import app.agent.workflow.agents.work_architect as work

    question = {
        "question": "어느 Task 아래에 둘까요?", "kind": "choice", "field": "parent",
        "options": [], "required_input": True, "why_required": "Sub-Task 부모가 필요함",
    }
    monkeypatch.setattr(work, "_can_parent_subtask", lambda key: key == "ACME-9")

    assert work._normalize_question_contracts(
        {}, [question], mode="subtask",
        items=[{"type": "Sub-Task", "parent": "ACME-9"}],
    ) == []
    contracts = work._normalize_question_contracts(
        {}, [question], mode="subtask",
        items=[{"type": "Sub-Task", "parent": "ACME-8"}],
    )
    assert len(contracts) == 1 and contracts[0]["ownership"] == "user_required"


def test_stable_outcome_identity_survives_assignment_merge_without_title_matching(monkeypatch):
    from app.agent.workflow import graph
    from app.agent.workflow.anchors import (
        requested_outcome_contract,
        seal_work_item_identities,
    )
    from app.agent.workflow.agents import auditor

    tasks = [
        {"id": "producer", "kind": "ticket", "write_intent": True,
         "instruction": "AcmeStream format producer 호환성 확인"},
        {"id": "consumer", "kind": "ticket", "write_intent": True,
         "instruction": "AcmeStream format consumer 호환성 확인"},
    ]
    state = _state("두 호환성 작업을 생성", tasks)
    contract = requested_outcome_contract(state)
    left, right = [row["id"] for row in contract["outcomes"]]
    draft = {
        "mode": "task", "outcome_contract_id": contract["id"],
        "items": [
            {"summary": "AcmeStream format 호환성 확인", "type": "Task",
             "epic": None, "outcome_refs": [left]},
            {"summary": "AcmeStream format 호환성 확인", "type": "Task",
             "epic": None, "outcome_refs": [right]},
        ],
    }
    seal_work_item_identities(state, draft)
    left_id, right_id = [row["item_id"] for row in draft["items"]]
    state.update({
        "draft": draft,
        "review": {"ok": True, "errors": [], "problems": []},
        # Deliberately stale indexes: stable ids, not title or list position, own the merge.
        "assignments": [
            {"index": 0, "item_id": right_id, "user": "acct.b200",
             "reasons": ["verified"]},
            {"index": 1, "item_id": left_id, "user": "acct.a100",
             "reasons": ["verified"]},
        ],
    })
    monkeypatch.setattr(auditor, "_machine_check", lambda _state: {
        "ok": True, "errors": [], "warnings": [], "text": "ok",
    })
    monkeypatch.setattr(graph, "_apply_named_assignees", lambda *_args, **_kwargs: None,
                        raising=False)
    monkeypatch.setattr("app.agent.workflow.agents.work_architect._apply_named_assignees",
                        lambda *_args, **_kwargs: None)
    monkeypatch.setattr("app.agent.tools._ctx.client",
                        lambda: (_ for _ in ()).throw(RuntimeError("offline")))

    merged = graph._merge_assignments(state)
    by_id = {row["item_id"]: row for row in merged["draft"]["items"]}

    assert by_id[left_id]["assignee"] == "acct.a100"
    assert by_id[right_id]["assignee"] == "acct.b200"
    assert {row["item_id"] for row in merged["assignments"]} == {left_id, right_id}


def test_auditor_finding_has_stable_identity_path_evidence_and_payload_digest(monkeypatch):
    from app.agent.workflow.anchors import requested_outcome_contract, seal_work_item_identities
    from app.agent.workflow.agents import auditor

    tasks = [{"id": "deliver", "kind": "ticket", "write_intent": True,
              "instruction": "AcmeIndex 생성"}]
    state = _state("AcmeIndex 생성", tasks)
    contract = requested_outcome_contract(state)
    draft = {"mode": "task", "outcome_contract_id": contract["id"], "items": [{
        "summary": "AcmeIndex 생성", "type": "Task", "epic": None,
        "outcome_refs": [contract["outcomes"][0]["id"]],
    }]}
    seal_work_item_identities(state, draft)
    state["draft"] = draft
    monkeypatch.setattr(auditor, "_machine_check", lambda _state: {
        "ok": True, "errors": [], "warnings": [], "text": "ok",
    })

    reviewed = auditor.Auditor().apply(state, {
        "grounded": True, "rule_compliant": True, "answers_request": False,
        "problems": [{"index": 0, "check": "request", "message": "산출물이 다름",
                      "fix": "요청 산출물로 복원"}],
    })["review"]
    finding = reviewed["findings"][0]

    assert finding["item_id"] == draft["items"][0]["item_id"]
    assert finding["field_path"]
    assert finding["expected"] and finding["actual"]
    assert finding["evidence"]
    assert len(finding["payload_digest"]) == 64
    assert reviewed["defect_signature"]

    draft["repair_attempt"] = {
        "defect_signature": reviewed["defect_signature"],
        "payload_digest": reviewed["payload_digest"],
    }
    repeated = auditor.Auditor().apply(state, {
        "grounded": True, "rule_compliant": True, "answers_request": False,
        "problems": [{"index": 0, "check": "request", "message": "산출물이 다름",
                      "fix": "요청 산출물로 복원"}],
    })["review"]
    assert repeated["defect_signature"] == reviewed["defect_signature"]
    assert repeated["repeated_defect"] is True


def test_repeated_defect_signature_fails_closed_without_another_semantic_regeneration():
    from app.agent.workflow import graph

    state = {
        "revisions": 1,
        "review": {
            "ok": False,
            "problems": [{"message": "같은 결함"}],
            "defect_signature": "defect:abc",
            "repeated_defect": True,
        },
    }

    assert graph.route_after_auditor(state) == "respond"


def test_work_repair_discards_stale_review_and_carries_the_defect_signature():
    from app.agent.workflow.agents.work_architect import WorkArchitect

    text = "ACME-42 우선순위를 P1로 변경"
    tasks = [{"id": "update", "kind": "write", "write_intent": True,
              "instruction": text}]
    state = _state(text, tasks, action="update")
    state["continuation_contract"]["target_keys"] = ["ACME-42"]
    state["review"] = {
        "ok": False,
        "problems": [{"message": "요청 우선순위 누락"}],
        "defect_signature": "defect:priority",
        "payload_digest": "0" * 64,
    }

    repaired = WorkArchitect().apply(state, {
        "questions": [], "mode": "task", "items": [], "rationale": "",
        "change": {"key": "ACME-42", "priority": "P1-Critical"},
    })

    assert repaired["review"] == {}
    assert repaired["change_plan"]["repair_attempt"] == {
        "defect_signature": "defect:priority",
        "payload_digest": "0" * 64,
    }
    assert repaired["change_plan"]["requested_effects"]["effects"]


@pytest.mark.parametrize(
    ("item_parent", "present", "absent"),
    [
        ({"epic": "ACME-100"}, "ACME-100", "최상위"),
        ({}, "최상위", "ACME-100"),
    ],
    ids=("selected-parent", "removed-parent"),
)
def test_pending_rationale_is_projected_from_the_current_parent_snapshot(
        item_parent, present, absent):
    from app.agent.workflow.effect_contract import project_pending_rationale

    draft = {
        "mode": "task",
        "rationale": "이전 수정에서는 ACME-100 연결을 제거하고 ACME-9에 배치한다고 설명",
        "structure": "task_with_subtasks",
        "structure_why": "이전 수정의 ACME-9 배치 설명",
        "items": [{
            "summary": "AcmeStream 호환성 구현", "type": "Task",
            "duedate": "2026-10-03", **item_parent,
            "children": [{"summary": "호환성 검증", "type": "Sub-Task"}],
        }],
    }

    rationale = project_pending_rationale(draft=draft)

    assert present in rationale
    assert absent not in rationale
    assert "ACME-9" not in rationale
    assert "2026-10-03" in rationale
    assert "Task 1건" in rationale and "Sub-Task 1건" in rationale


def test_pending_rationale_lists_every_current_scalar_update_and_drops_stale_comment():
    from app.agent.workflow.effect_contract import project_pending_rationale

    plan = {
        "key": "ACME-42",
        "changes": {"priority": "P1-Critical", "duedate": "2026-10-03"},
        "comment": "",
        "why": "이전 턴의 댓글도 함께 게시하고 제목을 바꾼다",
    }

    rationale = project_pending_rationale(change_plan=plan)

    assert "ACME-42" in rationale
    assert "우선순위: P1-Critical" in rationale
    assert "마감: 2026-10-03" in rationale
    assert "댓글" not in rationale and "제목" not in rationale


def test_typed_title_replacement_rebuilds_why_from_the_current_effect_only():
    from app.agent.workflow.agents.work_architect import WorkArchitect

    text = "ACME-42 제목만 'AcmeStream 호환성 검증'으로 변경"
    tasks = [{"id": "rename", "kind": "write", "write_intent": True,
              "instruction": text}]
    state = _state(text, tasks, action="update")
    state["continuation_contract"]["target_keys"] = ["ACME-42"]
    state["change_plan"] = {
        "key": "ACME-42", "changes": {"priority": "P2-Major"},
        "comment": "이전 턴 댓글", "why": "이전 우선순위와 댓글 변경",
    }

    result = WorkArchitect().apply(state, {
        "questions": [], "mode": "task", "items": [],
        "rationale": "이전 우선순위와 댓글 변경",
        "change": {"key": "ACME-42", "summary": "AcmeStream 호환성 검증"},
    })

    assert result["change_plan"]["changes"] == {
        "summary": "AcmeStream 호환성 검증",
    }
    assert not result["change_plan"].get("comment")
    assert result["change_plan"]["why"] == (
        "ACME-42 변경 초안 — 제목: AcmeStream 호환성 검증"
    )


def test_structured_scope_and_dod_drop_only_exact_adjacent_token_spans():
    from app.agent.workflow.agents.work_architect import _materialize_creation_parts

    out = {"items": [{
        "summary": "AcmeStream 호환성", "type": "Task", "background": "요청됨",
        "scope_in": ["schema schema 호환성 확인", "read path read path 검증"],
        "scope_out": [],
        "dod": ["result result 기록", "cache cash 비교"],
    }]}

    _materialize_creation_parts(out, {})
    body = out["items"][0]["description"]

    assert "schema 호환성 확인" in body and "schema schema" not in body
    assert "read path 검증" in body and "read path read path" not in body
    assert "result 기록" in body and "result result" not in body
    assert "cache cash 비교" in body, "non-identical words are not typo-corrected"


def test_final_pending_keeps_every_requested_effect_and_exact_update_payload():
    from app.agent import approval
    from app.agent.workflow import graph
    from app.agent.workflow.effect_contract import seal_requested_effect_contract

    text = "ACME-42 우선순위를 P1로 바꾸고 마감은 2026-10-03으로 변경"
    tasks = [{"id": "update", "kind": "write", "write_intent": True,
              "instruction": text}]
    state = _state(text, tasks, action="update")
    state["continuation_contract"]["target_keys"] = ["ACME-42"]
    state["change_plan"] = {
        "key": "ACME-42",
        "changes": {"priority": "P1-Critical", "duedate": "2026-10-03"},
        "comment": "",
        "why": "이전 턴에는 제목과 댓글도 바꾼다고 설명",
    }
    seal_requested_effect_contract(state)

    proposed = graph._propose(state)
    token = proposed["approval_token"]
    pending = approval.peek(token)

    try:
        assert token
        assert len(proposed["change_plan"]["requested_effects"]["effects"]) == 2
        assert proposed["change_plan"]["why"] == (
            "ACME-42 변경 초안 — 우선순위: P1-Critical, 마감: 2026-10-03"
        )
        assert pending and pending["payload"]["changes"] == {
            "priority": "P1-Critical", "duedate": "2026-10-03",
        }
    finally:
        approval.reject(token)


def test_final_authority_denies_token_when_the_effect_seal_itself_is_removed():
    from app.agent.workflow import graph
    from app.agent.workflow.effect_contract import seal_requested_effect_contract

    text = "ACME-42 마감을 2026-10-03으로 변경"
    tasks = [{"id": "update", "kind": "write", "write_intent": True,
              "instruction": text}]
    state = _state(text, tasks, action="update")
    state["continuation_contract"]["target_keys"] = ["ACME-42"]
    state["change_plan"] = {
        "key": "ACME-42", "changes": {"duedate": "2026-10-03"}, "why": "마감 변경",
    }
    seal_requested_effect_contract(state)
    state["change_plan"].pop("effect_contract")
    state["change_plan"].pop("requested_effects")

    blocked = graph._propose(state)

    assert blocked["approval_token"] == ""
    assert any(row.get("actual") == "missing" for row in blocked["review"]["errors"])


@pytest.mark.parametrize("missing_field", ["priority", "duedate"])
def test_final_authority_denies_token_when_one_requested_update_effect_is_lost(
        monkeypatch, missing_field):
    from app.agent import approval
    from app.agent.workflow import graph
    from app.agent.workflow.effect_contract import seal_requested_effect_contract

    text = "ACME-42 우선순위를 P1로 바꾸고 마감은 2026-10-03으로 변경"
    tasks = [{"id": "update", "kind": "write", "write_intent": True,
              "instruction": text}]
    state = _state(text, tasks, action="update")
    state["continuation_contract"]["target_keys"] = ["ACME-42"]
    state["change_plan"] = {
        "key": "ACME-42",
        "changes": {"priority": "P1-Critical", "duedate": "2026-10-03"},
        "why": "두 필드 변경",
    }
    seal_requested_effect_contract(state)
    assert len(state["change_plan"]["requested_effects"]["effects"]) == 2
    state["change_plan"]["changes"].pop(missing_field)

    blocked = graph._propose(state)

    assert blocked["approval_token"] == ""
    assert approval.peek("") is None
    assert any(row.get("field") == "requested_effects"
               for row in blocked["review"]["errors"])


def test_three_overlapping_outcomes_keep_exact_parent_due_and_owner_by_id(monkeypatch):
    from app.agent.workflow.anchors import requested_outcome_contract
    from app.agent.workflow.agents.work_architect import WorkArchitect
    import app.agent.workflow.agents.work_architect as work

    specs = [
        ("alpha", "ACME-101", "2026-10-01", "skcc.a100"),
        ("beta", "ACME-102", "2026-10-02", "skcc.b200"),
        ("gamma", "ACME-103", "2026-10-03", "skcc.c300"),
    ]
    tasks = [{
        "id": name, "kind": "ticket", "write_intent": True,
        "instruction": (f"AcmeStream format 호환성 {name} 작업을 {parent} 아래에 "
                        f"마감 {due}, 담당 {owner}로 생성"),
    } for name, parent, due, owner in specs]
    text = "; ".join(task["instruction"] for task in tasks)
    state = _state(text, tasks)
    contract = requested_outcome_contract(state)
    refs = {row["source_task_id"]: row["id"] for row in contract["outcomes"]}
    state["continuation_contract"]["decisions"] = [
        {"field": f"parent:{name}", "value": parent, "source": "interview_answer"}
        for name, parent, _due, _owner in specs
    ] + [
        {"field": f"assignee:{name}", "value": owner, "source": "interview_answer"}
        for name, _parent, _due, owner in specs
    ]
    monkeypatch.setattr(work, "_is_epic", lambda key: key in {row[1] for row in specs})
    monkeypatch.setattr(work, "_ticket_exists", lambda _key: True)
    output = {
        "questions": [], "mode": "task", "structure": "multiple_tasks",
        "structure_why": "세 독립 산출물", "rationale": "",
        "outcome_contract_id": contract["id"],
        "items": [{
            "summary": "AcmeStream format 호환성 확인", "type": "Task",
            "outcome_refs": [refs[name]], "background": "요청됨",
            "scope_in": ["호환성 확인"], "scope_out": [], "dod": ["결과 기록"],
        } for name, _parent, _due, _owner in specs],
    }

    draft = WorkArchitect().apply(state, copy.deepcopy(output))["draft"]
    by_ref = {row["outcome_refs"][0]: row for row in draft["items"]}
    assert set(by_ref) == set(refs.values()), draft

    for name, parent, due, owner in specs:
        row = by_ref[refs[name]]
        assert row["item_id"]
        assert row["epic"] == parent
        assert row["duedate"] == due
        assert row["assignee"] == owner
