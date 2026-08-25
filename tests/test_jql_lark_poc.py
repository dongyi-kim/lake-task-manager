"""Differential contracts for the optional Lark JQL parser POC."""

from __future__ import annotations

import itertools
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from app.jira.jql import JqlUnsupported, compile_jql, preprocess_context_jql
from app.jira.jql_lark import compile_jql_lark


NOW = datetime(2026, 8, 24, 10, 2, tzinfo=ZoneInfo("Asia/Seoul"))
CONTEXT = {
    "user_id": "test.ui01",
    "timezone_name": "Asia/Seoul",
    "now": NOW,
    "ttl_seconds": 900,
}


APP_QUERY_CORPUS = (
    "",
    "ORDER BY created DESC",
    "project = DL ORDER BY updated DESC",
    'project = DL AND labels = "PMO_VIT" ORDER BY updated DESC',
    "project in (ZZ, AA, ZZ) AND statusCategory != done",
    'project = DL AND issuetype = "Sub-Task" AND assignee IS EMPTY',
    "sprint in openSprints() AND resolution IS NOT EMPTY",
    'text ~ "literal AND OR ORDER BY currentUser() -14d" ORDER BY updated DESC',
    '"Epic Link" = DL-9000 OR parent in (DL-9020, DL-9025)',
    "(A = 1 OR A = 2) AND (B = 3 OR C = 4)",
    "NOT (statusCategory = done OR priority = Minor)",
    "assignee = currentUser() AND updated >= -14d "
    "AND created >= startOfWeek('-1w') ORDER BY updated DESC",
    "duedate < now() ORDER BY duedate ASC",
    '(assignee = "test.ui01" OR reporter = "test.ui01") AND '
    '((statusCategory = "In Progress") OR '
    '(statusCategory = "To Do" AND updated >= -14d) OR '
    '(statusCategory = Done AND resolved >= -7d)) ORDER BY duedate ASC',
    'assignee = "test.ui01" AND statusCategory = Done AND '
    '(resolved >= -28d OR (resolved IS EMPTY AND updated >= -28d)) '
    'ORDER BY updated DESC, key ASC',
)


@pytest.mark.parametrize("jql", APP_QUERY_CORPUS)
def test_lark_matches_production_compiler_for_app_corpus(jql):
    assert compile_jql_lark(jql, **CONTEXT) == compile_jql(jql, **CONTEXT)


def test_context_is_resolved_before_lark_and_quoted_text_is_untouched():
    prepared = preprocess_context_jql(
        'summary ~ "currentUser() AND -14d" AND assignee = currentUser() '
        "AND updated >= -1d",
        **CONTEXT,
    )
    assert 'summary ~ "currentUser() AND -14d"' in prepared
    assert 'assignee = "test.ui01"' in prepared
    assert 'updated >= "2026-08-23 10:00"' in prepared
    assert "currentUser()" not in prepared.replace('"currentUser() AND -14d"', "")


def test_context_preprocessor_rejects_unknown_functions_before_either_parser():
    query = "assignee in membersOf('jira-users')"
    with pytest.raises(JqlUnsupported, match="membersOf"):
        compile_jql(query, **CONTEXT)
    with pytest.raises(JqlUnsupported, match="membersOf"):
        compile_jql_lark(query, **CONTEXT)


@pytest.mark.parametrize(
    "query",
    (
        "project =",
        "project = DL AND",
        "project = DL ORDER key ASC",
        "status CHANGED AFTER -1d",
        "project in ()",
    ),
)
def test_lark_rejects_malformed_or_out_of_scope_syntax_for_safe_fallback(query):
    with pytest.raises(JqlUnsupported):
        compile_jql_lark(query, **CONTEXT)


def test_lark_reuses_existing_dnf_leaf_and_atom_limits():
    groups = [f"(F{index} = 1 OR F{index} = 2)" for index in range(7)]
    with pytest.raises(JqlUnsupported, match="64"):
        compile_jql_lark(" AND ".join(groups), **CONTEXT)

    atoms = [f"F{index} = {index}" for index in range(65)]
    with pytest.raises(JqlUnsupported, match="64"):
        compile_jql_lark(" AND ".join(atoms), **CONTEXT)


def test_lark_canonicalization_is_invariant_to_and_order():
    atoms = ("project = DL", "statusCategory != done", "assignee = test.ui01")
    expected = compile_jql_lark(" AND ".join(atoms), **CONTEXT)
    for permutation in itertools.permutations(atoms):
        assert compile_jql_lark(" AND ".join(permutation), **CONTEXT) == expected


def test_lark_input_limit_is_checked_by_the_shared_preprocessor():
    with pytest.raises(JqlUnsupported, match="16KiB"):
        compile_jql_lark("project = " + ("X" * (16 * 1024)), **CONTEXT)
