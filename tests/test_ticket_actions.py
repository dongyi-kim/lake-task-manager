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
