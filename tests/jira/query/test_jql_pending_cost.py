"""Physical cost estimation reuses the planner's existing complete coverage proof."""
from collections import Counter
import time

import pytest

from app.infra.cache import Cache
from app.infra.cache_policy import read_scope, reduce_read_metadata
from app.infra.settings import get_settings
from app.jira.jira_client import JiraClient
from app.jira import jql_strategy
from app.jira.jql_execution import group_query, plan_groups


@pytest.fixture
def planning(monkeypatch):
    client = JiraClient(get_settings(), Cache(":memory:"))
    collected = time.time() - 10
    fields = "summary,status"
    projection = client._projection_signature(fields, True)
    resolutions, estimates = [], []
    resolve, pending = client._resolve_cached_jql_rows, jql_strategy.pending_requests

    def resolver(keys, *args, **kwargs):
        resolutions.append(tuple(keys))
        return resolve(keys, *args, **kwargs)

    def estimate(*args, **kwargs):
        result = pending(*args, **kwargs)
        estimates.append(result)
        return result

    def seed(key, *, rows=True):
        leaf = client._compile_jql(f"key = {key}").leaves[0]
        client.cache.set_if_fence(client._jql_leaf_key(leaf)[2], [key], 600,
                                  client.cache.fence(), fetched_at=collected)
        if rows:
            client._store_jql_rows([{"key": key, "fields": {"summary": key, "status": {"name": "Open"}}}],
                client._jql_generation(), projection, fields, fetched_at=collected)

    monkeypatch.setattr(client, "_resolve_cached_jql_rows", resolver)
    monkeypatch.setattr(jql_strategy, "pending_requests", estimate)
    monkeypatch.setattr(client.provider, "get_json", lambda *_a, **_kw: pytest.fail("Planning must not query Jira"))
    try:
        yield client, seed, resolutions, estimates, collected
    finally:
        client.close()
        client.cache.close()


def test_complete_cached_leaves_are_not_resolved_again_for_pending_cost(planning):
    client, seed, resolutions, estimates, collected = planning
    seed("DL-1")
    seed("DL-2")
    compiled = client._compile_jql("key = DL-1 OR key = DL-2")
    with read_scope(cache_mode="hot") as sources:
        plan, fields = jql_strategy.execution_plan(client, compiled, "summary,status", True, "auto", 1)
    assert Counter(resolutions) == Counter({("DL-1",): 1, ("DL-2",): 1})
    assert estimates == [([], [])]
    assert plan.physical_group_count == 0 and plan.fallback_reason is None
    assert fields == "summary,status"
    assert reduce_read_metadata(sources)["fetchedAt"] == collected


def test_uncovered_membership_hydration_still_counts_toward_budget_fallback(planning):
    client, seed, resolutions, estimates, _ = planning
    seed("DL-1")
    seed("DL-2", rows=False)
    compiled = client._compile_jql("key = DL-1 OR key = DL-2 OR key = DL-3")
    plan, _ = jql_strategy.execution_plan(client, compiled, "summary,status", True, "full", 1)
    assert Counter(resolutions) == Counter({("DL-1",): 1, ("DL-2",): 2})
    assert estimates == [(["DL-2"], ["key = DL-3", "key in (DL-2) ORDER BY key ASC"])]
    assert plan.effective_strategy == "off" and plan.physical_group_count == 1
    assert plan.fallback_reason == "physical_query_budget"


def test_partial_opaque_group_cost_is_only_its_uncovered_execution_group(planning):
    client, seed, resolutions, estimates, _ = planning
    seed("DL-1")
    compiled = client._compile_jql("key = DL-1 OR key = DL-2 OR key = DL-3")
    plan, _ = jql_strategy.execution_plan(client, compiled, "summary,status", True, "auto", 1)
    group = plan_groups(compiled, client.s.epic_link_field_id)[0]
    assert estimates == [([], [group_query(group[1:])])]
    assert resolutions == [("DL-1",)]
    assert plan.physical_group_count == 1 and plan.fallback_reason is None
