"""Read-only activity projection; Jira history remains available for interpretation."""
from collections import defaultdict
from copy import deepcopy

from app.tracking.analysis import event_counts


_IGNORED_FIELDS = {
    "timespent", "timeestimate", "timeoriginalestimate", "originalestimate", "remainingestimate",
    "worklogid", "aggregatetimespent", "aggregatetimeestimate", "aggregatetimeoriginalestimate",
}


def _field_name(value):
    return "".join(char for char in str(value or "").casefold() if char.isalnum())


def _ignored(event):
    kind = event.get("kind") or event.get("type")
    if kind not in ("fieldChanged", "completed", "reopened"):
        return False
    field = _field_name(event.get("field"))
    # The exact transition is bookkeeping even with custom status categories.
    return (kind == "fieldChanged" and field in _IGNORED_FIELDS) or (
        field == "status" and str(event.get("from") or "").strip().casefold() == "resolved"
        and str(event.get("to") or "").strip().casefold() == "closed")


def project_activity(snapshot):
    """Hide bookkeeping in old and new runs without mutating durable evidence.

    Recount the stored hierarchy rather than rebuilding it from today's plan.
    A saved AI summary can contain prose or totals based on excluded evidence,
    so it is hidden until the user explicitly generates a new summary.
    """
    result = deepcopy(snapshot)
    summary_event_ids = result.pop("_activitySummaryEventIds", None)
    if result.get("events") is None:
        recovered = {event["id"]: event for issue in result.get("issues") or []
                     for event in issue.get("events") or []}
        result["events"] = sorted(recovered.values(), key=lambda event: (
            event.get("date") or event.get("at") or "", event["id"]), reverse=True)
    containers = [result, *(result.get("issues") or [])]
    events = [event for row in containers for event in row.get("events") or []]
    removed = {event["id"] for event in events if _ignored(event)}
    if not removed:
        return result

    for row in containers:
        if "events" in row:
            row["events"] = [event for event in row["events"] or [] if event["id"] not in removed]
        if "eventIds" in row:
            row["eventIds"] = [eid for eid in row["eventIds"] or [] if eid not in removed]

    # Include issue-local events as well for older snapshots with no root array.
    visible = {event["id"]: event for row in containers for event in row.get("events") or []}
    by_issue = defaultdict(list)
    for event in visible.values():
        by_issue[event["issueKey"]].append(event)

    def counts(events, previous):
        previous = previous or {}
        return {**previous, **event_counts(events, bool(previous.get("provisional")))}

    if "summary" in result:
        result["summary"] = counts(list(visible.values()), result["summary"])

    def recount(node):
        keys = set(node.get("issueKeys") or [])
        for child in node.get("children") or []:
            keys.update(recount(child))
        key = node.get("issueKey")
        if key:
            keys.add(key)
            node["contextOnly"] = not by_issue[key]
        if "counts" in node:
            node["counts"] = counts([event for key in keys for event in by_issue[key]], node["counts"])
        return keys

    for node in result.get("tree") or []:
        recount(node)
    if summary_event_ids != sorted(event["id"] for event in result.get("events") or []):
        result.pop("aiSummary", None)
    return result
