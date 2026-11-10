"""Bind request planning to proven membership, projection and source freshness."""
from dataclasses import replace
from app.jira.jql_budget import current_budget

from app.jira.jql_execution import (
    Member, build_execution_plan, group_query, partition_fields, plan_groups, selector_fields,
)


def execution_plan(client, compiled, fields, light, strategy, max_parts, *, groups=None):
    generation = client._jql_generation()
    whole = ((Member(0, compiled.whole_query,
                     tuple(sorted({field for leaf in compiled.leaf_fields for field in leaf})), whole=True),),)
    if groups is None:
        groups = plan_groups(compiled, client.s.epic_link_field_id, strategy=strategy)
    selected = partition_fields(fields, groups)
    # Coverage is read-only: it must never hydrate or probe upstream to estimate cost.
    def covered(groups):
        projection = client._projection_signature(selected, light)
        return {member.leaf for group in groups for member in group
                if client._jql_leaf_cache_ready(member.leaf, selected, light, generation,
                                                projection, selector_fields(group))}

    def pending(groups, covered):
        return pending_requests(client, groups, selected, light, generation,
                                client._projection_signature(selected, light), covered_leaves=covered)[1]

    plan = build_execution_plan(compiled, client.s.epic_link_field_id, strategy=strategy,
                                max_parts=max_parts, covered_leaves=covered, pending_queries=pending,
                                groups=groups)
    projection = client._projection_signature(selected, light)
    if strategy != "auto" and client._jql_leaf_cache_ready(compiled.whole_query, selected, light, generation, projection):
        return replace(plan, groups=whole, physical_group_count=0), selected
    return plan, selected


def pending_requests(client, groups, fields, light, generation, projection, *, covered_leaves=()):
    """Union missing entity rows before batching across logical membership boundaries."""
    queries, absent_keys = [], {}
    for group in groups:
        group = tuple(member for member in group if member.leaf not in covered_leaves)
        if not group:
            continue
        missing = []
        memberships = [(member, client.cache.get_with_metadata(client._jql_leaf_key(member.leaf)[2]))
                       for member in group]
        if (len(group) > 1 and any(not member.family for member in group)
                and any(entry is None or not isinstance(entry[0], list) for _member, entry in memberships)):
            queries.append(group_query(group))
            continue
        for member, entry in memberships:
            if entry is None or not isinstance(entry[0], list):
                missing.append(member)
                continue
            keys, source = entry
            _epoch, digest, _key = client._jql_leaf_key(member.leaf)
            opaque_key = client._jql_opaque_key(generation, projection, digest, source)
            if opaque_key and isinstance(client.cache.get(opaque_key), list):
                continue
            _rows, absent, _sources = client._resolve_cached_jql_rows(
                keys, fields, light, generation, projection, selector_fields(group))
            absent_keys.update(dict.fromkeys(absent))
        if missing:
            queries.append(group_query(tuple(missing)))
    absent = list(absent_keys)
    queries.extend("key in (%s) ORDER BY key ASC" % ",".join(absent[start:start + client.ISSUE_BATCH])
                   for start in range(0, len(absent), client.ISSUE_BATCH))
    return absent, queries


def hydrate_pending(client, groups, fields, light, generation, projection):
    absent, queries = pending_requests(client, groups, fields, light, generation, projection)
    budget = current_budget()
    if absent and (budget is None or budget.can_fit(queries)):
        client._hydrate_jql_leaf_rows(absent, fields, light, generation, projection)
