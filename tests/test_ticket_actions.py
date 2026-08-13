# -*- coding: utf-8 -*-
"""Ticket tier/field/action 계약은 prompt가 아니라 domain code가 최종 집행한다."""

from app.domain.ticket_actions import (ACTIONS, CREATE_FIELDS, EDITABLE_FIELDS, TIER_EPIC,
                                       TIER_SUBTASK, TIER_TASK, action_error, can_parent,
                                       field_update_error, issue_tier, reopen_transition)


def test_three_tiers_and_parent_matrix():
    assert issue_tier("Epic", False) == TIER_EPIC
    assert issue_tier("Bug", False) == TIER_TASK
    assert issue_tier("Story", False) == TIER_TASK
    assert issue_tier("이름이 달라도", True) == TIER_SUBTASK
    assert can_parent(TIER_EPIC, TIER_TASK)
    assert can_parent(TIER_TASK, TIER_SUBTASK)
    assert not can_parent(TIER_EPIC, TIER_SUBTASK)
    assert not can_parent(TIER_SUBTASK, TIER_SUBTASK)


def test_fields_match_agent_write_contract():
    assert "epic_name" in CREATE_FIELDS[TIER_EPIC]
    assert "epic" in CREATE_FIELDS[TIER_TASK] and "parent" not in CREATE_FIELDS[TIER_TASK]
    assert "parent" in CREATE_FIELDS[TIER_SUBTASK] and "epic" not in CREATE_FIELDS[TIER_SUBTASK]
    assert EDITABLE_FIELDS == {"assignee", "duedate", "priority", "summary", "labels",
                               "components", "description"}


def test_done_blocks_fields_but_not_comment_or_transition():
    done = {"statusCategory": "done"}
    assert "속성은 바꿀 수 없습니다" in field_update_error(done, ["summary"])
    assert not action_error(TIER_TASK, "comment", done)
    assert not action_error(TIER_TASK, "transition", done)
    assert action_error(TIER_SUBTASK, "create_child")
    assert "comment" in ACTIONS[TIER_EPIC] and "comment" in ACTIONS[TIER_SUBTASK]


def test_reopened_must_come_from_actual_transition_list():
    rows = [{"id": "6", "name": "Close", "to": "Closed", "toCategory": "done"},
            {"id": "4", "name": "Reopen Issue", "to": "Reopened", "toCategory": "todo"}]
    assert reopen_transition(rows)["id"] == "4"
    assert reopen_transition(rows[:1]) is None


def test_field_update_route_imports_done_guard_and_updates_description(monkeypatch):
    """Done 보호 로직의 import 누락으로 /fields 전체가 500이 된 회귀 방지."""
    import json
    import app.main as main

    class Client:
        def ticket_badge(self, key):
            return {"key": key, "statusCategory": "inprogress"}

        def editmeta(self, key):
            return {"description": {}}

        def desc_field_value(self, html):
            return "converted:" + html

        def update_fields(self, key, fields):
            return {"ok": True, "key": key, "fields": fields}

    monkeypatch.setattr(main, "_client", Client())
    response = main.api_update_fields(
        "DL-1", main._FieldsBody(descriptionHtml="<p>본문</p>"))
    assert response.status_code == 200
    payload = json.loads(response.body)
    assert payload["fields"]["description"] == "converted:<p>본문</p>"
