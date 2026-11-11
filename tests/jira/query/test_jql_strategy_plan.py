"""Explicit strategies preserve proof boundaries and bound distinct upstream queries."""

from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from datetime import datetime, timezone

import pytest

from app.jira import jql
from app.jira.jql import JqlUnsupported, compile_jql
from app.jira import jql_execution as execution


def test_off_normalizes_context_without_expanding_exponential_dnf():
    query = " AND ".join(f"(F{i} = 1 OR F{i} = 2)" for i in range(7))
    query += " AND assignee = currentUser() AND updated >= startOfDay()"
    with pytest.raises(JqlUnsupported, match="64"):
        compile_jql(query, user_id="a")
    result = compile_jql(query, user_id="a", timezone_name="Asia/Seoul",
                         now=datetime(2026, 11, 9, 16, tzinfo=timezone.utc), decompose=False)
    assert len(result.leaves) == 1
    assert 'assignee = "a"' in result.leaves[0]
    assert 'updated >= "2026-11-10 00:00"' in result.leaves[0]
    assert set(result.leaf_fields[0]) == {"f0", "f1", "f2", "f3", "f4", "f5", "f6", "assignee", "updated"}


def test_dnf_limit_has_typed_fallback_distinct_from_unsupported_context_functions():
    query = " AND ".join(f"(F{i} = 1 OR F{i} = 2)" for i in range(7))
    with pytest.raises(jql.JqlExpansionLimit):
        compile_jql(query)
    with pytest.raises(JqlUnsupported) as unsupported:
        compile_jql("assignee in membersOf('group')")
    assert not isinstance(unsupported.value, jql.JqlExpansionLimit)


def test_full_queries_each_leaf_and_people_only_combines_proven_people():
    compiled = compile_jql("assignee = a OR reporter = b OR statusCategory = Done OR key = DL-1")
    full = execution.build_execution_plan(compiled, "customfield_10014", strategy="full")
    people = execution.build_execution_plan(compiled, "customfield_10014", strategy="people")
    assert [len(group) for group in full.groups] == [1, 1, 1, 1]
    assert sorted(len(group) for group in people.groups) == [2, 2]
    assert {tuple(member.family for member in group) for group in people.groups} == {
        ("people", "people"), ("", "")}
    assert full.logical_leaf_count == 4 and full.physical_group_count == 4
    assert people.requested_strategy == people.effective_strategy == "people"


def test_auto_preserves_existing_grouping_and_off_uses_one_opaque_query():
    compiled = compile_jql("assignee = a OR reporter = b OR key = DL-1")
    auto = execution.build_execution_plan(compiled, "customfield_10014")
    off = execution.build_execution_plan(compiled, "customfield_10014", strategy="off")
    assert auto.groups == execution.plan_groups(compiled, "customfield_10014")
    assert len(off.groups) == 1 and all(not member.family for member in off.groups[0])
    assert len(off.groups[0]) == 1
    assert off.groups[0][0].leaf == compiled.whole_query
    opaque = compile_jql("assignee = a OR reporter = b", decompose=False)
    assert execution.build_execution_plan(opaque, None, strategy="off").logical_leaf_count is None


def test_known_budget_shortfall_falls_back_before_dispatch_without_false_leaf_proofs():
    compiled = compile_jql("assignee = a OR reporter = b OR key = DL-1")
    plan = execution.build_execution_plan(compiled, None, strategy="full", max_parts=2)
    assert plan.requested_strategy == "full" and plan.effective_strategy == "off"
    assert plan.fallback_reason == "physical_query_budget"
    assert plan.physical_group_count == 1 and len(plan.groups) == 1
    assert len(plan.groups[0]) == 1
    assert plan.groups[0][0].leaf == compiled.whole_query
    assert all(not member.family for member in plan.groups[0])
    cached = execution.build_execution_plan(compiled, None, strategy="full", max_parts=1,
                                            covered_leaves=compiled.leaves[:2])
    assert cached.effective_strategy == "full" and cached.physical_group_count == 1


def test_coverage_is_checked_once_after_actual_strategy_selects_projection_requirements():
    compiled = compile_jql("assignee = a OR reporter = b OR key = DL-1")
    checked = []
    def coverage(groups):
        checked.append(execution.partition_fields("summary", groups))
        return ["key = DL-1"]
    plan = execution.build_execution_plan(compiled, None, strategy="people", max_parts=1,
                                          covered_leaves=coverage)
    assert checked == ["summary,assignee,reporter"]
    assert plan.effective_strategy == "people" and plan.physical_group_count == 1


def test_unexpanded_fallback_never_reports_effective_full_decomposition():
    compiled = compile_jql("assignee = a OR reporter = b", decompose=False)
    plan = execution.build_execution_plan(compiled, None, strategy="full")
    assert plan.effective_strategy == "off" and plan.logical_leaf_count is None
    assert plan.fallback_reason == "dnf_not_expanded"


@pytest.mark.parametrize("options", [{"strategy": "unknown"}, {"max_parts": 0},
                                   {"max_parts": 65}, {"max_parts": True}, {"max_parts": 1.5}])
def test_invalid_options_fail_before_any_execution(options):
    with pytest.raises(ValueError):
        execution.build_execution_plan(compile_jql("project = DL"), None, **options)


def test_physical_budget_deduplicates_pages_retries_but_includes_hydration_and_fallback():
    with execution.physical_query_scope(2) as budget:
        assert execution.claim_physical_query('summary ~ "two  words"')
        assert not execution.claim_physical_query(' summary  ~  "two  words" ')
        assert execution.claim_physical_query("key in (DL-1, DL-2)")
        with pytest.raises(execution.PhysicalQueryBudgetExceeded):
            execution.claim_physical_query("project = DL")
        assert budget.diagnostics() == {"maxParts": 2, "physicalQueryCount": 2}
    assert execution.claim_physical_query("project = DL") is False


def test_budget_shares_atomic_claims_across_copied_worker_contexts():
    def claim(index):
        try:
            return execution.claim_physical_query(f"key = DL-{index}")
        except execution.PhysicalQueryBudgetExceeded:
            return False
    with execution.physical_query_scope(3) as budget, ThreadPoolExecutor(8) as pool:
        futures = [pool.submit(copy_context().run, claim, index) for index in range(20)]
        assert sum(future.result() for future in futures) == 3
        assert budget.diagnostics()["physicalQueryCount"] == 3


def test_budget_preserves_string_values_and_nested_scope_cannot_reset_allowance():
    with execution.physical_query_scope(2) as outer:
        execution.claim_physical_query('summary ~ "two  words"')
        with execution.physical_query_scope(64) as inner:
            assert inner is outer
            execution.claim_physical_query('summary ~ "two words"')
            with pytest.raises(execution.PhysicalQueryBudgetExceeded):
                execution.claim_physical_query('summary ~ "other"')
