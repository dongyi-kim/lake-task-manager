"""Architecture guards for the incrementally decomposed Work Architect facade."""

import inspect

from support.paths import REPO_ROOT

from app.agent.workflow.agents import work_architect as facade
from app.agent.workflow.work_architect import (
    apply_pipeline,
    body_text,
    change_plan,
    context,
    contracts,
    due_dates,
    finalize,
)


def test_facade_preserves_existing_contract_and_policy_imports():
    assert facade.SCHEMA is contracts.SCHEMA
    assert facade.CREATE_SCHEMA is contracts.CREATE_SCHEMA
    assert facade.UPDATE_SCHEMA is contracts.UPDATE_SCHEMA
    assert facade.COMMENT_SCHEMA is contracts.COMMENT_SCHEMA
    assert facade._relative_due is due_dates.relative_due
    assert facade._current_request_boundary_text is context.current_request_boundary_text
    assert facade._visible_body_text is body_text.visible_body_text


def test_facade_delegates_apply_and_change_plan_with_live_policy_bindings(monkeypatch):
    apply_seen = {}
    change_seen = {}

    def fake_apply(agent, state, out, policies):
        apply_seen.update(agent=agent, state=state, out=out, policies=policies)
        return {"delegated": "apply"}

    def fake_change(state, out, items, questions, policies):
        change_seen.update(
            state=state, out=out, items=items, questions=questions, policies=policies,
        )
        return {"delegated": "change"}, questions

    monkeypatch.setattr(facade, "apply_work_architect", fake_apply)
    monkeypatch.setattr(facade, "build_change_plan", fake_change)
    agent = facade.WorkArchitect()

    assert agent.apply({"request": 1}, {"projection": 2}) == {"delegated": "apply"}
    assert apply_seen["policies"] is facade.__dict__
    assert facade._change_plan({"request": 3}, {"change": {}}, [], ["q"])[0] == {
        "delegated": "change",
    }
    assert change_seen["policies"].ticket_exists is facade._ticket_exists


def test_facade_does_not_reabsorb_extracted_responsibilities():
    path = REPO_ROOT / "app" / "agent" / "workflow" / "agents" / "work_architect.py"
    source = path.read_text(encoding="utf-8")
    forbidden_definitions = (
        "def _relative_due(",
        "def _current_request_boundary_text(",
        "def _map_visible_body_text(",
        "SCHEMA = {",
    )
    assert all(definition not in source for definition in forbidden_definitions)
    assert "return apply_work_architect(self, state, out, globals())" in source
    assert "return build_change_plan(state, out, items, qs, policies)" in source
    assert path.stat().st_size < 375_000

    package = path.parent.parent / "work_architect"
    assert (package / "apply_pipeline.py").stat().st_size < 112_000
    assert (package / "change_plan.py").stat().st_size < 30_000
    assert (package / "finalize.py").stat().st_size < 21_000
    assert callable(apply_pipeline.apply_work_architect)
    assert callable(change_plan.build_change_plan)
    assert callable(finalize.finalize_work_architect)
