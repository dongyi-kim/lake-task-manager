"""Current scoped Jira hierarchy with shared issue/membership caches."""

from app.auth.base import SessionExpired
from app.infra.cache_policy import note_read, read_scope
from app.jira.evidence_service import validate_page, _check_auth, _json_size, EVIDENCE_PAGE_MAX_BYTES

def get_descendants(client, root_key, *, projects, include_root=True, start_at=0, max_results=100,
                    missing_start_at=0):
    """Traverse complete shared child membership; paginate presentation, never discovery.

    A parent timestamp is never a filter for descendant discovery. Failed branches remain
    in the missing ledger and are retried on the next call; no aggregate is cached here.
    """
    validate_page(start_at, max_results)
    validate_page(missing_start_at, max_results)
    root_key = root_key.strip().upper()
    allowed = {str(project).upper() for project in projects}
    if root_key.rsplit("-", 1)[0] not in allowed:
        raise ValueError("설정된 Jira 조회 범위 밖의 티켓입니다.")
    missing, rows, seen = [], [], {root_key}
    pending = [(root_key, None, 0, None, (root_key,))]
    light_fields = set(client._issue_fields(light=True).split(",")) - {"subtasks"}
    # Prevent stale/offline display fallback from being asserted as a complete hierarchy.
    # Propagate observed source ages back into the enclosing MCP read metadata context.
    with read_scope(strict=True) as sources:
        try:
            while pending:
                next_pending = []
                # Batch cold siblings through the existing issue/search caches. A batch failure
                # is only an optimisation failure: individual strict reads retain exact errors.
                try:
                    client.prefetch_issues([item[0] for item in pending], light=True)
                except Exception:
                    pass
                for key, parent, depth, inherited_epic, path in pending:
                    try:
                        raw = client.get_issue_light(key, strict=True)
                        if not isinstance(raw, dict) or raw.get("key") != key or not isinstance(raw.get("fields"), dict):
                            raise ValueError("Jira issue 응답이 요청한 티켓을 포함하지 않습니다.")
                        fields = raw["fields"]
                        is_epic = ((fields.get("issuetype") or {}).get("name") or "").casefold() == "epic"
                        epic = key if is_epic else fields.get(client.s.epic_link_field_id) or inherited_epic
                        # A full issue cache can satisfy a light read. Keep the transport light
                        # even on that hit: do not repeat long descriptions/attachments in a tree.
                        projected = {name: value for name, value in raw.items() if name in {"key", "id", "self"}}
                        projected["fields"] = {name: value for name, value in fields.items() if name in light_fields}
                        rows.append({"key": key, "parentKey": parent, "depth": depth,
                                     "epicKey": epic, "issue": projected})
                        children, refreshed = _descendant_children(client, key, raw, allowed)
                        if refreshed is not raw:
                            projected["fields"] = {name: value for name, value in refreshed["fields"].items()
                                                   if name in light_fields}
                            epic = refreshed["fields"].get(client.s.epic_link_field_id) or inherited_epic
                            rows[-1]["epicKey"] = epic
                    except Exception as exc:
                        kind = client.issue_read_error_kind(exc)
                        if kind == "auth":
                            raise
                        missing.append({"key": key, "parentKey": parent, "kind": kind,
                                        "stage": "children" if any(row["key"] == key for row in rows) else "issue"})
                        continue
                    for child in sorted(set(children)):
                        if child in path:
                            missing.append({"key": child, "parentKey": key,
                                            "kind": "cycle", "stage": "hierarchy"})
                            continue
                        if child in seen:
                            continue  # The same SubTask can also have a direct Epic Link.
                        seen.add(child)
                        if child.rsplit("-", 1)[0] not in allowed:
                            missing.append({"key": child, "parentKey": key,
                                            "kind": "outside_scope", "stage": "issue"})
                            continue
                        next_pending.append((child, key, depth + 1, epic, (*path, child)))
                pending = next_pending
            by_key = {row["key"]: row for row in rows}
            for row in rows:
                actual_parent = (row["issue"]["fields"].get("parent") or {}).get("key")
                if row["key"] != root_key and actual_parent in by_key:
                    row["parentKey"] = actual_parent
            for row in rows:
                ancestors, parent = {row["key"]}, row["parentKey"]
                while parent in by_key and parent not in ancestors:
                    ancestors.add(parent)
                    parent = by_key[parent]["parentKey"]
                if parent in ancestors:
                    missing.append({"key": row["key"], "kind": "cycle", "stage": "hierarchy"})
                else:
                    row["depth"] = len(ancestors) - 1
            # Starting at a SubTask still inherits its parent's Epic through the same batch
            # resolver used by workload. Normal traversals already carry this information.
            unresolved = []
            for row in rows:
                fields = row["issue"]["fields"]
                parent = (fields.get("parent") or {}).get("key")
                if row["epicKey"] or not (fields.get("issuetype") or {}).get("subtask") or not parent:
                    continue
                if parent.rsplit("-", 1)[0] not in allowed:
                    missing.append({"key": row["key"], "parentKey": parent,
                                    "kind": "outside_scope", "stage": "epic"})
                else:
                    unresolved.append(row["issue"])
            if unresolved:
                epics, unavailable = client._wl_effective_epics(unresolved)
                for key, epic in epics.items():
                    by_key[key]["epicKey"] = epic
                for key, failure in unavailable.items():
                    if failure.get("kind") == "auth":
                        raise SessionExpired("SubTask의 parent Epic 확인에 인증이 필요합니다.")
                    missing.append({"key": key, "parentKey": failure.get("parentKey"),
                                    "kind": failure.get("kind", "other"), "stage": "epic"})
        finally:
            observed = list(sources)
    for source in observed:
        note_read(source)
    selected = rows if include_root else [row for row in rows if row["key"] != root_key]
    page = selected[start_at:start_at + max_results]
    more = start_at + len(page) < len(selected)
    missing_page = missing[missing_start_at:missing_start_at + max_results]
    missing_more = missing_start_at + len(missing_page) < len(missing)
    result = {"rootKey": root_key, "includeRoot": include_root, "issues": page,
            "startAt": start_at, "maxResults": max_results, "returned": len(page),
            "total": len(selected), "hasMore": more,
            "nextStartAt": start_at + len(page) if more else None,
            "complete": not missing, "partial": bool(missing), "missing": missing_page,
            "missingStartAt": missing_start_at, "missingTotal": len(missing),
            "missingHasMore": missing_more,
            "nextMissingStartAt": missing_start_at + len(missing_page) if missing_more else None,
            "consistency": "live_hierarchy; no frozen snapshot; deduplicate keys across pages",
            "visibility": "configured projects and Jira-visible membership; hidden children may be undiscoverable"}
    return _bounded_hierarchy_page(result)


def _bounded_hierarchy_page(result):
    """Bound both independent ledgers without hiding their remaining rows or dropping keys."""
    issues, missing = result["issues"], result["missing"]
    while _json_size(result) > EVIDENCE_PAGE_MAX_BYTES:
        if len(missing) > 1 and (len(issues) <= 1 or _json_size(missing) >= _json_size(issues)):
            missing.pop()
        elif len(issues) > 1:
            issues.pop()
        elif issues and not issues[0].get("projectionOmissions"):
            # Hierarchy is a light projection. A huge label/user/custom-field object must not
            # consume the agent context; keep exact identity/relation and identify omitted detail.
            issues[0] = {name: value for name, value in issues[0].items() if name != "issue"}
            issues[0].update(issue={"key": issues[0]["key"], "fields": {}},
                             projectionOmissions=["issue.fields"],
                             details={"tool": "get_ticket", "arguments": {"key": issues[0]["key"]}})
        else:
            raise ValueError("계층 식별자/오류 메타데이터가 응답 크기 한도를 초과했습니다.")
        result.update(returned=len(issues), hasMore=result["startAt"] + len(issues) < result["total"],
                      missingHasMore=result["missingStartAt"] + len(missing) < result["missingTotal"])
        result["nextStartAt"] = result["startAt"] + len(issues) if result["hasMore"] else None
        result["nextMissingStartAt"] = result["missingStartAt"] + len(missing) if result["missingHasMore"] else None
    return result


def _descendant_children(client, key, raw, projects):
    """Reuse UI membership for Tasks, with membership's own source age and write fence."""
    import json
    import time

    fields = raw["fields"]
    if ((fields.get("issuetype") or {}).get("name") or "").casefold() == "epic":
        # Scope must constrain the upstream query, not just the returned rows: the unscoped UI
        # Epic query can otherwise fetch and write-through tickets from a different project.
        scope = ",".join(json.dumps(project) for project in sorted(projects))
        children = client._search(f'project in ({scope}) AND "Epic Link" = {key}',
                                  max_results=None, light=True)
        return list(dict.fromkeys(row["key"] for row in children)), raw

    policy = getattr(client.cache, "policy", None)
    ttl = policy.freshnessSeconds if policy else client.s.cache_ttl_seconds
    current = [raw]
    cache_key = client._direct_child_cache_key(key)

    def build():
        source = current[0]
        metadata = [client.cache.metadata(f"{kind}:{client.env}:{key}")
                    for kind in ("issue", "issueL")]
        fresh = [row for row in metadata if row and row["state"] == "fresh"]
        fetched = max((row["fetchedAt"] for row in fresh), default=0)
        # Done issue data may be fresh for 24h; its child membership still expires after 10m.
        # Fetch inside the shared membership producer so an intervening write fences both
        # issue write-through and membership publication, and failures never cache an empty list.
        if time.time() - fetched > ttl or "subtasks" not in source["fields"]:
            fence = client.cache.fence()
            source = client.provider.get_json(f"/rest/api/2/issue/{key}", params={
                "fields": client._issue_fields(light=True),
            })
            _check_auth(client, source)
            if client._note_bad_field(source):
                source = client.provider.get_json(f"/rest/api/2/issue/{key}", params={
                    "fields": client._issue_fields(light=True),
                })
                _check_auth(client, source)
            if not isinstance(source, dict) or source.get("key") != key or not isinstance(source.get("fields"), dict):
                raise ValueError("Jira parent 응답이 요청한 티켓을 포함하지 않습니다.")
            fetched = time.time()
            client.cache.set_if_fence(f"issueL:{client.env}:{key}", source,
                                      client.s.cache_ttl_seconds, fence, fetched_at=fetched)
            current[0] = source
            if ((source["fields"].get("issuetype") or {}).get("name") or "").casefold() == "epic":
                raise ValueError("Parent 유형이 Epic으로 변경되었습니다. 계층을 다시 조회하세요.")
        children = source["fields"].get("subtasks")
        if not isinstance(children, list) or any(not isinstance(row, dict) or not row.get("key") for row in children):
            raise ValueError(f"{key} direct-child membership is incomplete")
        note_read({"kind": "childkeys", "fetchedAt": fetched, "freshUntil": fetched + ttl,
                   "displayUntil": fetched + client.cache.display_ttl(cache_key),
                   "invalidated": False, "state": "fresh"})
        return list(dict.fromkeys(str(row["key"]).strip().upper() for row in children))

    children, _ = client.cache.get_or_set_strict(cache_key, ttl, build)
    return children, current[0]
