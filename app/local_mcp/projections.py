"""Response-only projections; never shrink shared Jira caches or snapshots."""

TICKET_METADATA = ("key", "summary", "type", "status", "statusCategory", "assignee", "assigneeId",
                   "reporter", "reporterId", "priority", "created", "updated", "resolved", "due",
                   "labels", "components", "epicKey", "url", "timeSpentSeconds")
SEARCH_FIELDS = ("summary", "status", "issuetype", "assignee", "reporter", "priority", "created",
                 "updated", "resolutiondate", "duedate", "labels", "components", "parent", "timespent")


def ticket_projection(ticket, detail):
    if detail == "full":
        return ticket
    result = {key: ticket[key] for key in TICKET_METADATA if key in ticket}
    result["detail"] = detail
    if detail in ("read", "edit"):
        key = "descriptionHtml" if detail == "read" else "descriptionEditHtml"
        result[key] = ticket[key]
    return result


def issue_projection(issue):
    fields = {}
    for key, value in (issue.get("fields") or {}).items():
        if key not in SEARCH_FIELDS:
            continue
        if isinstance(value, dict):
            value = {name: value[name] for name in ("id", "key", "name", "displayName", "statusCategory", "subtask") if name in value}
        fields[key] = value
    return {"key": issue["key"], "fields": fields, **({"id": issue["id"]} if "id" in issue else {})}
