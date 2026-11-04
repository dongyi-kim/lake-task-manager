"""Pure query planning and proof-based membership partition contracts."""

import pytest

from app.jira.jql import compile_jql
from app.jira.jql_execution import (
    group_query, partition_rows, plan_groups, projection_scope, row_projection_complete,
)


def test_shared_status_guards_partition_overlapping_roles_without_local_date_evaluation():
    query = ('(assignee = a OR reporter = b) AND '
             '(statusCategory = "To Do" OR (statusCategory = Done AND resolved >= "2026-10-01"))')
    groups = plan_groups(compile_jql(query), "customfield_10014")
    assert len(groups) == 1 and len(groups[0]) == 4
    row = {"key": "DL-1", "fields": {"assignee": {"name": "a"}, "reporter": {"name": "b"},
           "status": {"statusCategory": {"key": "done"}}}}
    partitions = partition_rows(groups[0], [row])
    assert sum(bool(part) for part in partitions) == 2
    for member, rows in zip(groups[0], partitions):
        assert bool(rows) == (member.category == "done")


def test_incompatible_same_status_guards_and_sixth_person_use_separate_requests():
    query = " OR ".join(f"assignee = user{i} AND statusCategory = Done AND resolved >= -7d" for i in range(6))
    query += " OR (assignee = user0 AND statusCategory = Done AND resolved IS EMPTY AND updated >= -7d)"
    groups = plan_groups(compile_jql(query), "customfield_10014")
    assert len(groups) == 3
    assert all(len({member.value for member in group}) <= 5 for group in groups)
    assert all(not ("resolved IS EMPTY" in group_query(group) and "resolved >=" in group_query(group))
               for group in groups)


@pytest.mark.parametrize("value", [{"displayName": "a"}, {"key": "a"}, "a"])
def test_missing_verified_account_name_never_primes_empty_membership(value):
    group = plan_groups(compile_jql("assignee = a OR reporter = b"), "customfield_10014")[0]
    row = {"key": "DL-1", "fields": {"assignee": value, "reporter": {"name": "b"}}}
    assert partition_rows(group, [row]) is None
    row["fields"]["assignee"] = None
    partitions = partition_rows(group, [row])
    assert partitions is not None
    assert sum(len(part) for part in partitions if part is not None) == 1
    assert any(part is None for part in partitions)  # unverified assignee literal stays unknown
    del row["fields"]["assignee"]
    assert partition_rows(group, [row]) is None


def test_unrecognized_escape_and_disabled_epic_field_leave_query_opaque():
    query = r'assignee = "O\'Brien" OR reporter = bob'
    groups = plan_groups(compile_jql(query), None)
    assert len(groups) == 1
    assert any(not member.family for member in groups[0])


def test_single_query_narrow_projection_does_not_require_unrequested_selector():
    group = plan_groups(compile_jql("assignee = a"), "customfield_10014")[0]
    row = {"key": "DL-1", "fields": {"summary": "narrow query"}}
    with projection_scope(group):
        assert row_projection_complete(row, "summary")
        assert not row_projection_complete(row, "summary,assignee")
        assert not row_projection_complete(row, "*all")
    assert row_projection_complete(row, "summary")
