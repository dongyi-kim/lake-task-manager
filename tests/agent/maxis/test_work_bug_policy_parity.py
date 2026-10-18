"""Bug defaults must reach both real prompt boundaries from one shared policy owner.

These are prompt-transport regressions, not a claim that a live model obeys them.
The existing synthetic Work fixture deliberately has related work but no Jira type
or parent capability; role-context prose cannot supply those missing facts.
"""
import copy

import pytest

from app.agent.workflow.agents import work_architect as work
from app.agent.workflow.state import Intent
from tools.maxis_role_eval_cases import build_fixture_payload
from test_core_work_prompt import build, contract, texts


BUG_DEFAULT = "For Bug reports, default to one Task-tier `Bug` without Sub-Tasks."
EXCEPTION = "user-requested split or placement, with any required Jira issue-type and parent capabilities verified"
CONTEXT_LIMIT = "related-work context alone is insufficient"
CAUSE_LIMIT = "A suspected-cause ticket key requires direct research support."


@pytest.fixture
def bug_state(monkeypatch):
    monkeypatch.setenv("JIRA_ENV", "mock")
    monkeypatch.setenv("LAKE_AGENT_PROVIDER", "fake")
    monkeypatch.setattr(work, "_placement_material", lambda state: "")
    monkeypatch.setattr(work, "_rules_material", lambda state: "")
    return build_fixture_payload("role-work")["state"]


def test_bug_default_reaches_actual_local_and_native_system_once(contract, texts, bug_state):
    local = work.WorkArchitect().system(bug_state)
    native = build(contract, texts)
    for policy in (BUG_DEFAULT, EXCEPTION, CONTEXT_LIMIT, CAUSE_LIMIT):
        assert policy in texts[0]
        assert local.count(policy) == native.count(policy) == 1
        assert policy not in texts[1]  # Transport is not a second semantic policy owner.


def test_dynamic_bug_task_selects_shared_policy_without_repeating_it(bug_state):
    dynamic = work.WorkArchitect().task(bug_state)
    for old_instruction in (
        "Draft one Task-tier `Bug`.", "Default to one Bug without Sub-Tasks",
        'Use `type="Bug"` and a Korean summary',
        "Add a verified suspected-cause ticket key",
        "If an open Bug already has the same symptom",
    ):
        assert old_instruction not in dynamic
    assert "shared Bug creation policy" in dynamic
    assert bug_state["request_text"] in dynamic


def test_related_work_context_does_not_become_typed_capability_or_selection(contract, texts):
    fixture = build_fixture_payload("role-work")
    before = copy.deepcopy(fixture)
    pack = fixture["payload"]["evidencePack"]
    assert "부모 업무" in next(row["content_json"] for row in pack["role_context"]
                              if row["name"] == "evidence")
    jira = next(row for row in pack["sources"] if row["kind"] == "jira")
    assert '"issuetype"' not in jira["content"]
    assert '"subtask"' not in jira["content"]
    assert "DL-9090" not in fixture["state"]["request_text"]
    prompt = build(contract, texts)
    assert EXCEPTION in prompt and CONTEXT_LIMIT in prompt
    assert "Use only types with Jira metadata `subtask=true`" in prompt
    assert fixture == before


@pytest.mark.parametrize("kind", ["comment", "update"])
def test_create_policy_stays_out_of_existing_ticket_only_system(bug_state, kind, monkeypatch):
    state = {**bug_state, "intent": Intent.MODIFY,
             "request_text": "DL-9090 우선순위만 변경", "mentioned_keys": ["DL-9090"]}
    monkeypatch.setattr(work, "_work_action", lambda state: kind)
    system = work.WorkArchitect().system(state)
    assert BUG_DEFAULT not in system and CAUSE_LIMIT not in system
