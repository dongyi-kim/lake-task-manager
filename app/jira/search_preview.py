"""Bounded discovery prefixes, with reuse of already complete query evidence."""
import hashlib
import json

from app.auth.base import QueryStateChanged
from app.infra.cache_policy import note_read, read_scope
from app.jira.jql import JqlUnsupported, fields_with_order, sort_issues


def _covered_rows(client, compiled, fields, light, generation, projection):
    """Read proven whole/leaf membership only; misses never hydrate or query Jira."""
    candidates = [(compiled.whole_query,)]
    if compiled.leaves != candidates[0]:
        candidates.append(compiled.leaves)
    required = [spec.field for spec in compiled.order if spec.field != "key"]
    for leaves in candidates:
        with read_scope() as sources:
            keys = []
            for leaf in leaves:
                entry = client.cache.get_with_metadata(client._jql_leaf_key(leaf)[2])
                if entry is None or not isinstance(entry[0], list):
                    break
                keys.extend(entry[0])
            else:
                keys = list(dict.fromkeys(keys))
                rows, missing, _ = client._resolve_cached_jql_rows(
                    keys, fields, light, generation, projection, required)
                if not missing:
                    ordered = sort_issues(rows.values(), compiled.order)
                    selected = ordered, list(sources)
                    break
        # Sources from incomplete coverage are not evidence for the returned preview.
    else:
        return None
    for source in selected[1]:
        note_read(source)
    return selected[0]


def search_preview(client, jql, *, limit, fields, light):
    """Return up to one server-capped page; never refill a cold discovery prefix."""
    return _search(client, jql, limit=limit, fields=fields, light=light, one_page=True)


def search_prefix(client, jql, *, limit=200, fields=None, light=True):
    """Collect a bounded ordered prefix, never a complete-query membership proof."""
    return _search(client, jql, limit=limit, fields=fields, light=light, one_page=False)


def _search(client, jql, *, limit, fields, light, one_page):
    size = max(1, min(int(limit or 100), 100 if one_page else 200))
    if isinstance(fields, (list, tuple, set)):
        fields = ",".join(str(value) for value in fields if str(value).strip())
    fields = (fields or client._issue_fields(light=light)).strip()
    fence, generation = client.cache.fence(), client._jql_generation()
    try:
        compiled = client._compile_jql(jql)
    except JqlUnsupported:
        if not one_page:
            raise
        compiled = None
    query = compiled.canonical if compiled else str(jql).strip()
    if compiled:
        fields = fields_with_order(fields, compiled.order)
    projection = client._projection_signature(fields, light)
    identity = [client._jql_user_context(), generation, fence, query, projection, size]
    if not one_page:
        identity.append("bounded-prefix")
    digest = hashlib.sha256(json.dumps(identity, ensure_ascii=False).encode()).hexdigest()
    cache_key = f"search:preview:v1:{client.env}:{digest}"

    def produce():
        rows = (_covered_rows(client, compiled, fields, light, generation, projection)
                if compiled else None)
        if rows is not None:
            selected = rows[:size]
            more = len(selected) < len(rows)
            return {"startAt": 0, "maxResults": size, "issues": selected,
                "total": len(rows), "returned": len(selected), "hasMore": more,
                "nextStartAt": len(selected) if more else None}
        if not one_page:
            return _prefix_pages(client, query, fields, light, size, compiled.order, fence, generation)
        return client._legacy_search_issues_page(
            query, max_results=size, fields=fields, light=light)

    page = client.cache.get_or_set_strict(cache_key, client.s.cache_ttl_seconds, produce)[0]
    if fence != client.cache.fence() or generation != client._jql_generation():
        raise QueryStateChanged("티켓이 변경되어 검색 결과를 다시 확인해야 합니다.")
    return page


def _prefix_pages(client, query, fields, light, size, order, fence, generation):
    issues, seen, total = [], set(), None
    while len(issues) < size:
        page = client._legacy_search_issues_page(
            query, start_at=len(issues), max_results=min(100, size - len(issues)),
            fields=fields, light=light)
        if fence != client.cache.fence() or generation != client._jql_generation():
            raise QueryStateChanged("티켓이 변경되어 검색 결과를 다시 확인해야 합니다.")
        observed = page.get("total")
        if type(observed) is not int or total is not None and total != observed:
            raise ValueError("Jira 검색 prefix의 전체 건수가 없거나 변경되었습니다.")
        total = observed
        batch = page["issues"]
        if any(row["key"] in seen for row in batch):
            raise ValueError("Jira 검색 prefix에 중복된 티켓이 있습니다.")
        if any(spec.field != "key" and spec.field not in row["fields"] for spec in order for row in batch):
            raise ValueError("Jira 검색 prefix의 정렬 필드가 없습니다.")
        seen.update(row["key"] for row in batch)
        issues.extend(batch)
        if not page["hasMore"]:
            if len(issues) != total:
                raise ValueError("Jira 검색 prefix의 pagination이 완전하지 않습니다.")
            break
        if not batch or page["nextStartAt"] != len(issues):
            raise ValueError("Jira 검색 prefix의 다음 페이지가 진행되지 않습니다.")
    if [row["key"] for row in sort_issues(issues, order)] != [row["key"] for row in issues]:
        raise ValueError("Jira 검색 prefix의 정렬 순서가 일관되지 않습니다.")
    more = len(issues) < total
    return {"startAt": 0, "maxResults": size, "issues": issues, "total": total,
            "returned": len(issues), "hasMore": more, "nextStartAt": len(issues) if more else None}
