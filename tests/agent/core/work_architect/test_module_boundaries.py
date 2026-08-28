"""Architecture guards for the incrementally decomposed Work Architect facade."""

from support.paths import REPO_ROOT

from app.agent.workflow.agents import work_architect as facade
from app.agent.workflow.work_architect import body_text, context, contracts, due_dates


def test_facade_preserves_existing_contract_and_policy_imports():
    assert facade.SCHEMA is contracts.SCHEMA
    assert facade.CREATE_SCHEMA is contracts.CREATE_SCHEMA
    assert facade.UPDATE_SCHEMA is contracts.UPDATE_SCHEMA
    assert facade.COMMENT_SCHEMA is contracts.COMMENT_SCHEMA
    assert facade._relative_due is due_dates.relative_due
    assert facade._current_request_boundary_text is context.current_request_boundary_text
    assert facade._visible_body_text is body_text.visible_body_text


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
    assert path.stat().st_size < 530_000
