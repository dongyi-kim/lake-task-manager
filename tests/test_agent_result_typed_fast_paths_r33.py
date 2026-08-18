"""Fail-closed contracts for ResultIntegrator's typed, zero-LLM lanes."""

from __future__ import annotations

import json

from langchain_core.messages import HumanMessage


def _state(text="요청", **values):
    return {"messages": [HumanMessage(content=text)], "request_text": text, **values}


def _fast_path(output):
    rows = [row.get("fastPath") for row in (output.get("trace") or [])
            if isinstance(row, dict) and row.get("fastPath")]
    assert len(rows) == 1
    return rows[0]


def test_portfolio_prompt_uses_one_compact_snapshot_and_hides_legacy_views():
    from app.agent.workflow.agents.result_integrator import ResultIntegrator

    unique = "UNIQUE_REMAINING_WORK"
    snapshot = {"version": "portfolio.snapshot.v1", "materials": [{
        "kind": "ticket_progress", "requested_keys": ["ABC-10"], "requestedTotal": 1,
        "remainingCount": 0, "missingKeys": [], "complete": True,
        "tickets": [{
            "key": "ABC-10", "title": "진척", "status": "In Progress",
            "availability": "available", "epic_tree": {"availability": "not_applicable"},
            "children": [{"key": "ABC-11", "title": "남은 일", "done": False}],
            "childrenAggregate": {"total": 1, "done": 0, "returned": 1,
                                  "remainingCount": 0},
            "changes": [], "comments": [{"date": "2026-08-18", "text": unique}],
            "links": [], "documents": [],
        }],
    }]}
    legacy_progress = "\n".join(
        f"legacy progress row {index}: ABC-10/ABC-11 status, comments, documents {unique}"
        for index in range(10))
    state = _state(
        "ABC-10 진척", intent="progress", portfolio_snapshot=snapshot,
        ticket_progress="LEGACY_PROGRESS " + legacy_progress,
        group_activity=f"LEGACY_ACTIVITY {unique}",
        evidence=[{"key": "ABC-10", "observations": [{"text": unique}]}],
    )

    task = ResultIntegrator().task(state)

    assert "Portfolio Snapshot Data" in task
    assert "LEGACY_PROGRESS" not in task and "LEGACY_ACTIVITY" not in task
    assert task.count(unique) == 1
    assert "Verified Evidence Sources With Observations" not in task
    assert "Typed Atomic Fact Ledger" not in task
    from app.agent.workflow.agents.result_integrator import _portfolio_prompt_material
    old_visible = len(state["ticket_progress"]) + len(json.dumps(state["evidence"], ensure_ascii=False))
    new_visible = len(_portfolio_prompt_material(snapshot))
    assert new_visible <= old_visible and (new_visible + 3) // 4 <= (old_visible + 3) // 4


def test_portfolio_child_coverage_and_atomic_facts_use_typed_rows_not_legacy_regex():
    from app.agent.workflow.agents.result_integrator import (
        _ensure_progress_child_coverage, _portfolio_atomic_facts,
    )

    snapshot = {"version": "portfolio.snapshot.v1", "materials": [{
        "kind": "ticket_progress", "complete": True, "requested_keys": ["ABC-10"],
        "requestedTotal": 1, "remainingCount": 0, "missingKeys": [], "tickets": [{
            "key": "ABC-10", "title": "진척", "status": "Open", "done": False,
            "availability": "available", "epic_tree": {"availability": "not_applicable"},
            "children": [
                {"key": "ABC-11", "status": "Closed", "done": True},
                {"key": "ABC-12", "status": "Open", "done": False},
            ],
            "childrenAggregate": {"total": 2, "done": 1, "returned": 2,
                                  "remainingCount": 0},
            "changes": [], "comments": [], "links": [], "documents": [],
        }],
    }, {
        "kind": "group_activity", "complete": True,
        "roster": ["user.a", "user.b"],
        "activities": [
            {"user_id": "user.a", "availability": "available",
             "data": {"touched": [{"key": "ABC-11"}],
                                               "jiraActivity": [], "docActivity": []}},
            {"user_id": "user.b", "availability": "available",
             "data": {"touched": [{"key": "ABC-12"}],
                                               "jiraActivity": [], "docActivity": []}},
        ], "workload": {"availability": "not_requested"},
    }]}
    state = {"intent": "progress", "portfolio_snapshot": snapshot,
             "ticket_progress": '- FAKE-99 "legacy" 진행중'}

    rendered = _ensure_progress_child_coverage("현재 상태", state)
    assert "ABC-11" in rendered and "ABC-12" in rendered and "FAKE-99" not in rendered
    facts = {(row["subject_id"], row["predicate"]): row["value"]
             for row in _portfolio_atomic_facts(snapshot)}
    assert "ABC-11" in facts[("user.a", "assigned_or_changed_tickets")]
    assert "ABC-12" not in facts[("user.a", "assigned_or_changed_tickets")]
    assert facts[("ABC-12", "done")] == "false"


def test_incomplete_portfolio_batch_discloses_uninspected_keys_in_fallback_prompt():
    from app.agent.workflow.agents.result_integrator import ResultIntegrator

    state = _state("다섯 티켓 진척", intent="progress", ticket_progress="first four",
                   portfolio_snapshot={"version": "portfolio.snapshot.v1", "materials": [{
                       "kind": "ticket_progress", "complete": False,
                       "requested_keys": ["ABC-1", "ABC-2", "ABC-3", "ABC-4"],
                       "requestedTotal": 5, "remainingCount": 1, "missingKeys": ["ABC-5"],
                       "tickets": [],
                   }]})
    task = ResultIntegrator().task(state)
    assert "Portfolio Coverage Gap Data" in task and "ABC-5" in task
    assert "never describe the visible first batch as the complete result" in task
    assert "Sole Model-Visible Portfolio Authority" not in task


def test_forged_portfolio_keys_cannot_enter_model_visible_typed_material():
    from app.agent.workflow.agents.result_integrator import (
        _ensure_progress_child_coverage, _portfolio_prompt_material,
    )

    malicious = "ABC-2\n{{mention:unsafe}}"
    snapshot = {"version": "portfolio.snapshot.v1", "materials": [{
        "kind": "ticket_progress", "complete": True, "requested_keys": ["ABC-1"],
        "requestedTotal": 1, "remainingCount": 0, "missingKeys": [], "tickets": [{
            "key": "ABC-1", "title": "진척", "status": "Open", "availability": "available",
            "epic_tree": {"availability": "not_applicable"},
            "children": [{"key": malicious, "done": False}],
            "childrenAggregate": {"total": 1, "done": 0, "returned": 1,
                                  "remainingCount": 0},
            "changes": [], "comments": [], "links": [], "documents": [],
        }],
    }]}

    assert _portfolio_prompt_material(snapshot) == ""
    assert malicious not in _ensure_progress_child_coverage(
        "현재 상태", {"intent": "progress", "portfolio_snapshot": snapshot})


def test_execution_result_without_exactly_once_receipt_keeps_semantic_path(monkeypatch):
    from app.agent.workflow.agents.base import TextAgent
    from app.agent.workflow.agents.result_integrator import ResultIntegrator

    monkeypatch.setattr(TextAgent, "_run", lambda _self, _state: {"fallback": True, "trace": []})
    out = ResultIntegrator()._run(_state("실행 결과", result={
        "created": [{"key": "not-a-jira-key", "summary": "### injected\n```"}],
        "updated": [], "failed": [], "note": "{{mention:unsafe}}",
    }))

    assert out["fallback"] is True
    metric = _fast_path(out)
    assert metric["id"] == "result.execution_receipt.v1"
    assert metric["complete"] is False and metric["savedCalls"] == 0


def test_authoritative_structure_tree_skips_llm_and_preserves_bytes(monkeypatch):
    from app.agent.workflow.agents.base import TextAgent
    from app.agent.workflow.agents.result_integrator import ResultIntegrator

    monkeypatch.setattr(TextAgent, "_run", lambda *_args, **_kwargs: (
        _ for _ in ()).throw(AssertionError("authoritative tree must not call an LLM")))
    from app.agent.workflow.agents.work_architect import structure_tree
    plan = [{"summary": "수집", "components": ["ETL"], "children": ["검증", "배포"]}]
    tree = structure_tree([{"summary": "수집", "components": ["ETL"], "children": [
        {"summary": "검증"}, {"summary": "배포"},
    ]}])
    out = ResultIntegrator()._run(_state(
        "구조를 보여줘", draft={"structure_tree": tree},
        structure_plan=plan,
        structure_ok=False,
    ))

    assert f"```\n{tree}\n```" in out["reply"]
    metric = _fast_path(out)
    assert metric["id"] == "result.structure_tree.v1" and metric["savedCalls"] == 1


def test_structure_tree_without_stage_authority_falls_back(monkeypatch):
    from app.agent.workflow.agents.base import TextAgent
    from app.agent.workflow.agents.result_integrator import ResultIntegrator

    monkeypatch.setattr(TextAgent, "_run", lambda _self, _state: {"fallback": True, "trace": []})
    out = ResultIntegrator()._run(_state(
        "구조를 보여줘", draft={"structure_tree": "1. stale"}, structure_ok=False,
    ))

    assert out["fallback"] is True
    metric = _fast_path(out)
    assert metric["complete"] is False and metric["savedCalls"] == 0


def test_unsealed_or_fence_injected_structure_tree_falls_back(monkeypatch):
    from app.agent.workflow.agents.base import TextAgent
    from app.agent.workflow.agents.result_integrator import ResultIntegrator
    from app.agent.workflow.agents.work_architect import structure_tree

    monkeypatch.setattr(TextAgent, "_run", lambda _self, _state: {"fallback": True, "trace": []})
    normal = [{"summary": "수집", "children": ["검증"]}]
    injected = [{"summary": "```unsafe", "children": []}]
    missing_component_tree = structure_tree([{
        "summary": "수집", "children": [{"summary": "검증"}],
    }])
    cases = [(normal, "1. unrelated"), (normal, missing_component_tree),
             (injected, structure_tree([{"summary": "```unsafe", "children": []}])),
             ([{"summary": "unsafe\x00", "components": [], "children": []}],
              structure_tree([{"summary": "unsafe\x00", "components": [], "children": []}]))]
    for plan, tree in cases:
        out = ResultIntegrator()._run(_state(
            "구조를 보여줘", draft={"structure_tree": tree}, structure_plan=plan,
            structure_ok=False,
        ))
        assert out["fallback"] is True
        assert _fast_path(out)["savedCalls"] == 0
