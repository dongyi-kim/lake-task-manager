"""Response-only projections; never shrink shared Jira caches or snapshots."""

TICKET_METADATA = ("key", "summary", "type", "status", "statusCategory", "assignee", "assigneeId",
                   "reporter", "reporterId", "priority", "created", "updated", "resolved", "due",
                   "labels", "components", "epicKey", "url", "timeSpentSeconds")
SEARCH_FIELDS = ("summary", "status", "issuetype", "assignee", "reporter", "priority", "created",
                 "updated", "resolutiondate", "duedate", "labels", "components", "parent", "timespent")


def ticket_projection(ticket, detail, *, include_description=None, include_comments=False):
    result = dict(ticket) if detail == "full" else {
        key: ticket[key] for key in TICKET_METADATA if key in ticket}
    if detail != "full":
        result["detail"] = detail
    if include_description is False:
        for key in ("description", "descriptionHtml", "descriptionEditHtml", "descriptionSections", "descriptionFormat"):
            result.pop(key, None)
    elif detail in ("read", "edit") or (detail == "summary" and include_description is True):
        key = "descriptionEditHtml" if detail == "edit" else "descriptionHtml"
        result[key] = ticket[key]
    for key in ("comments", "commentsCoverage", "commentsRequested", "commentsMissingKeys"):
        if include_comments and key in ticket:
            result[key] = ticket[key]
        else:
            result.pop(key, None)
    for key in ("descriptionRequested", "descriptionMissingKeys"):
        if include_description is not False and key in ticket:
            result[key] = ticket[key]
        else:
            result.pop(key, None)
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
