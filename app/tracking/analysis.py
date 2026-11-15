"""Pure activity interpretation. Current hierarchy and historical activity are separate."""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone

KST = timezone(timedelta(hours=9))
COUNT_KINDS = {"created": "created", "completed": "completed", "reopened": "reopened",
               "comment": "comments", "commentEdited": "commentEdits", "fieldChanged": "fieldChanges"}


def instant(value):
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed.replace(tzinfo=KST) if parsed.tzinfo is None else parsed
    except (ValueError, TypeError):
        return None


def person(raw):
    raw = raw or {}
    if isinstance(raw, str):
        return {"id": raw, "name": raw}
    uid = str(raw.get("name") or raw.get("key") or raw.get("accountId") or raw.get("id") or "")
    return {"id": uid, "name": raw.get("displayName") or raw.get("display") or uid}


def identity_aliases(raw, comments=None):
    """Only immutable Jira identity pairs establish aliases; display names never do."""
    f = raw.get("fields") or {}
    users = [f.get(field) for field in ("assignee", "creator", "reporter")]
    users += [h.get("author") for h in (raw.get("changelog") or {}).get("histories", [])]
    rows = comments if comments is not None else (f.get("comment") or {}).get("comments", [])
    users += [c.get(field) for c in rows for field in ("author", "updateAuthor")]
    aliases = {}
    for user in users:
        if isinstance(user, dict) and user.get("name"):
            canonical = str(user["name"]).casefold()
            for field in ("name", "key", "accountId"):
                if user.get(field):
                    aliases[str(user[field]).casefold()] = canonical
    return aliases


def period_bounds(start_date, end_date, now=None):
    start = datetime.combine(date.fromisoformat(start_date), time.min, KST)
    end = datetime.combine(date.fromisoformat(end_date) + timedelta(days=1), time.min, KST)
    now = now or datetime.now(KST)
    if start >= end:
        raise ValueError("시작일은 종료일보다 늦을 수 없습니다.")
    if start.date() > now.astimezone(KST).date() or date.fromisoformat(end_date) > now.astimezone(KST).date():
        raise ValueError("미래 날짜는 조회할 수 없습니다.")
    return start, min(end, now.astimezone(KST))


def analyze_issue(raw, comments, start, end, selected_people, status_categories, epic_field,
                  history_complete, comments_complete, aliases=None):
    key = raw["key"]
    f = raw.get("fields") or {}
    status = f.get("status") or {}
    assignee, creator, reporter = (person(f.get(name)) for name in ("assignee", "creator", "reporter"))
    history = list({str(h.get("id") or repr(h)): h for h in
                    (raw.get("changelog") or {}).get("histories", [])}.values())
    history.sort(key=lambda h: instant(h.get("created")) or datetime.min.replace(tzinfo=KST))
    selected = {str(p).casefold() for p in selected_people}
    aliases = {**(aliases or {}), **identity_aliases(raw, comments)}
    aliases.update({p: p for p in selected})
    unresolved = set()

    def assignment(value):
        value = str(value or "").casefold()
        if not value:
            return ""
        if value in aliases:
            return aliases[value]
        if selected:
            unresolved.add(value)
        return value
    in_period = lambda value: value is not None and start <= value < end
    # Reverse the complete history from the current assignee. Changes after the requested
    # period matter: using today's assignee would silently lose reassigned historical work.
    at_start = assignee["id"]
    for h in reversed(history):
        when = instant(h.get("created"))
        if when and when >= start:
            for item in reversed(h.get("items") or []):
                if str(item.get("field")).casefold() == "assignee":
                    at_start = str(item.get("from") or "")
    at_start = assignment(at_start) if at_start else ""
    period_assignees = {at_start.casefold()}
    for h in history:
        if in_period(instant(h.get("created"))):
            for item in h.get("items") or []:
                if str(item.get("field")).casefold() == "assignee":
                    period_assignees.update(assignment(item.get(side)) for side in ("from", "to"))
    assigned = bool(selected & period_assignees) and history_complete
    assignment_changes = []
    for h in history:
        when = instant(h.get("created"))
        for item in h.get("items") or []:
            if str(item.get("field")).casefold() == "assignee" and in_period(when):
                assignment_changes.append((when, assignment(item.get("to"))))

    def responsible(when):
        current = at_start.casefold()
        for changed_at, after in assignment_changes:
            if start <= changed_at <= when:
                current = after
        return history_complete and current in selected
    events, missing = [], []
    if not history_complete:
        missing.append({"issueKey": key, "source": "history", "reason": "incomplete"})
    if not comments_complete:
        missing.append({"issueKey": key, "source": "comments", "reason": "incomplete"})
    if unresolved:
        missing.append({"issueKey": key, "source": "assignmentIdentity", "reason": "unmappedImmutableIdentity"})

    def add(eid, kind, value, who, **extra):
        when = instant(value)
        who = person(who)
        if not in_period(when):
            return
        actor_id = aliases.get(who["id"].casefold(), who["id"].casefold())
        if selected and not responsible(when) and actor_id not in selected:
            return
        text = str(extra.pop("body", "") or "")
        events.append({"id": f"{key}:{eid}", "issueKey": key, "kind": kind, "type": kind,
                       "date": when.isoformat(), "at": when.isoformat(), "actorId": who["id"],
                       "actorName": who["name"], "author": who, "body": text, "excerpt": text,
                       "source": "jira", **extra})

    add("created", "created", f.get("created"), f.get("creator"))
    for h in history:
        when = instant(h.get("created"))
        if not in_period(when):
            continue
        for index, item in enumerate(h.get("items") or []):
            field = str(item.get("field") or "")
            eid = f"history:{h.get('id', h.get('created'))}:{index}"
            details = {"field": field, "from": item.get("fromString") or item.get("from") or "",
                       "to": item.get("toString") or item.get("to") or ""}
            if field.casefold() == "status":
                def category(side):
                    return status_categories.get(str(item.get(side) or "").casefold()) or status_categories.get(
                        str(item.get(side + "String") or "").casefold())
                before, after = category("from"), category("to")
                if not before or not after:
                    missing.append({"issueKey": key, "source": "statusCategories", "reason": "unknownStatus"})
                elif before != "done" and after == "done":
                    add(eid, "completed", h.get("created"), h.get("author"), **details)
                elif before == "done" and after != "done":
                    add(eid, "reopened", h.get("created"), h.get("author"), **details)
                else:
                    add(eid, "fieldChanged", h.get("created"), h.get("author"), opportunistic=True, **details)
            else:
                add(eid, "fieldChanged", h.get("created"), h.get("author"), opportunistic=True, **details)
    for c in {str(c.get("id")): c for c in comments if c.get("id") is not None}.values():
        add(f"comment:{c['id']}:created", "comment", c.get("created"), c.get("author"),
            body=c.get("body"), commentId=str(c["id"]), observedUpdatedAt=c.get("updated"))
        if instant(c.get("updated")) and instant(c.get("created")) and instant(c["updated"]) > instant(c["created"]):
            add(f"comment:{c['id']}:edited", "commentEdited", c.get("updated"), c.get("updateAuthor") or c.get("author"),
                body=c.get("body"), commentId=str(c["id"]), latestObservedEdit=True)
    events = list({event["id"]: event for event in events}.values())
    actor = any(aliases.get(e["actorId"].casefold(), e["actorId"].casefold()) in selected for e in events) if selected else False
    reasons = (["periodAssignee"] if assigned else []) + (["actor"] if actor else [])
    issue = {"key": key, "issueKey": key, "summary": f.get("summary") or key,
             "type": (f.get("issuetype") or {}).get("name") or "Task",
             "isSubtask": bool((f.get("issuetype") or {}).get("subtask")),
             "status": status.get("name") or "", "statusCategory": (status.get("statusCategory") or {}).get("key"),
             "assignee": assignee, "assigneeId": assignee["id"], "assigneeName": assignee["name"],
             "creator": creator, "creatorId": creator["id"], "reporter": reporter,
             "created": f.get("created"), "updated": f.get("updated"),
             "fetchedAt": datetime.fromtimestamp(raw["_trackingFetchedAt"], KST).isoformat() if raw.get("_trackingFetchedAt") else None,
             "parentKey": (f.get("parent") or {}).get("key") or "",
             "epicKey": f.get(epic_field) or "", "modules": [c.get("name") for c in f.get("components", []) if c.get("name")],
             "events": events, "eventIds": [e["id"] for e in events], "scopeReasons": reasons,
             "inScope": not selected or assigned or actor,
             "scopeUncertain": bool(selected and (not history_complete or unresolved) and not (assigned or actor)),
             "coverage": {"historyComplete": history_complete, "commentsComplete": comments_complete,
                          "complete": not missing}}
    return issue, events, missing


def event_counts(events, provisional=False):
    result = {key: 0 for key in COUNT_KINDS.values()}
    unique = {e["id"]: e for e in events}
    unique_kinds = {kind: set() for kind in ("created", "completed", "reopened")}
    for event in unique.values():
        if event["kind"] in COUNT_KINDS:
            if event["kind"] in unique_kinds:
                unique_kinds[event["kind"]].add(event["issueKey"])
            else:
                result[COUNT_KINDS[event["kind"]]] += 1
    for kind, keys in unique_kinds.items():
        result[COUNT_KINDS[kind]] = len(keys)
        result[kind + "Events"] = sum(e["kind"] == kind for e in unique.values())
    result.update(uniqueIssues=len({e["issueKey"] for e in unique.values()}), totalEvents=len(unique), provisional=provisional)
    return result


def build_result(issues, events, plan, selected_modules, complete):
    by_key = {i["key"]: i for i in issues}
    events = list({e["id"]: e for e in events}.values())
    by_events = defaultdict(list)
    for event in events:
        by_events[event["issueKey"]].append(event)
    children = defaultdict(list)
    for issue in issues:
        parent = issue.get("parentKey") or issue.get("epicKey")
        if parent and parent != issue["key"]:
            children[parent].append(issue["key"])
    selected_modules = set(selected_modules)
    covered, tree = set(), []

    def issue_node(key, prefix, module, seen):
        if key in seen:
            return None
        issue = by_key.get(key, {"key": key, "summary": key, "type": "Epic", "contextOnly": True})
        covered.add(key)
        nested = [issue_node(k, prefix + "/" + key, module, seen | {key}) for k in sorted(children[key])]
        nested = [n for n in nested if n]
        own_events = by_events[key]
        ids = {key} | {k for child in nested for k in child["issueKeys"]}
        descendant_events = [e for k in ids for e in by_events[k]]
        return {"id": prefix + "/" + key, "key": key, "issueKey": key, "name": issue["summary"],
                "summary": issue["summary"], "kind": issue.get("type", "Task"), "type": issue.get("type", "Task"),
                "moduleId": module, "children": nested, "issueKeys": sorted(ids),
                "contextOnly": not own_events, "counts": event_counts(descendant_events, not complete)}

    module_nodes = {}
    for task in plan.get("wbs", []):
        module = task["module"]
        if selected_modules and module not in selected_modules:
            continue
        mod = module_nodes.setdefault(module, {"id": "module:" + module, "key": module, "moduleId": module,
            "name": module, "summary": module, "kind": "module", "type": "module", "children": []})
        task_id = "wbs:" + str(task.get("id") or task["name"])
        nodes = [issue_node(e.get("key") or e.get("ticket"), task_id, module, set()) for e in task.get("epics", [])]
        nodes = [n for n in nodes if n and any(k in by_key for k in n["issueKeys"])]
        if nodes:
            keys = {k for node in nodes for k in node["issueKeys"]}
            mod["children"].append({"id": task_id, "key": task_id, "name": task["name"], "summary": task["name"],
                "kind": "wbs", "type": "wbs", "moduleId": module, "children": nodes,
                "issueKeys": sorted(keys), "counts": event_counts([e for k in keys for e in by_events[k]], not complete)})
    for mod in module_nodes.values():
        if mod["children"]:
            keys = {k for node in mod["children"] for k in node["issueKeys"]}
            mod["issueKeys"] = sorted(keys)
            mod["counts"] = event_counts([e for k in keys for e in by_events[k]], not complete)
            tree.append(mod)
    orphans = [key for key in by_key if key not in covered and not (
        by_key[key].get("parentKey") in by_key or by_key[key].get("epicKey") in by_key)]
    orphan_nodes = [issue_node(key, "unmapped", "", set()) for key in sorted(orphans)]
    if orphan_nodes:
        tree.append({"id": "unmapped", "key": "unmapped", "name": "WBS 미연결", "summary": "WBS 미연결",
                     "kind": "module", "type": "module", "moduleId": "", "children": orphan_nodes,
                     "counts": event_counts([e for n in orphan_nodes for k in n["issueKeys"] for e in by_events[k]], not complete)})
    return {"issues": issues, "events": sorted(events, key=lambda e: (e["date"], e["id"]), reverse=True),
            "summary": event_counts(events, not complete), "tree": tree}
