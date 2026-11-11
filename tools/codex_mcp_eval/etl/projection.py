"""Render authored events into ordinary public Jira/Confluence source records."""
from __future__ import annotations

import hashlib
from copy import deepcopy
from datetime import datetime, timezone
from types import SimpleNamespace

from .events import stamp


def comment_id(identity):
    return str(int(hashlib.sha256(identity.encode()).hexdigest()[:14], 16))


def comment_source(key, identity):
    return f"jira:{key}:comment:{comment_id(identity)}"


def project(events, spec):
    issues, sources, documents, mappings = {}, {}, {}, {}
    users = {f"etl.user{i:02d}": {"name": f"etl.user{i:02d}", "displayName": name + " 가상데이터팀",
                                "timeZone": "Asia/Seoul"}
             for i, name in enumerate(("김지우", "박서준", "이하은", "최민재", "정수아", "윤도윤", "한채원", "송지호"))}
    for e in sorted(events, key=lambda row: (row.recorded_at, row.identity)):
        if not e.sources:
            continue
        p = e.payload
        if e.kind == "issue":
            date = e.occurred_at.date()
            issues[e.issue_key] = {"key": e.issue_key, "project": "DL", "summary": p["summary"],
                "description": p["description"], "type": p["type"], "component": p["module"],
                "assignee": p["assignee"], "reporter": p["assignee"],
                "statusCategory": "todo" if p.get("initial_status") == "Open" else "inprogress",
                "statusName": p.get("initial_status", "In Progress"), "priority": "P2-Major", "labels": p.get("labels", []),
                "sp": p.get("sp"), "epicKey": p.get("epic"), "epicName": p["summary"] if p["type"] == "Epic" else None,
                "parentKey": p.get("parent"), "created": date, "updated": date, "resolved": None,
                "due": None, "tcreated": e.occurred_at.strftime("%H:%M:%S"), "tupdated": e.recorded_at.strftime("%H:%M:%S"),
                "tresolved": None, "comments": [], "changelog": [], "worklog": [], "subtasks": [],
                "links": deepcopy(p.get("links", [])), "remotelinks": [], "_timestamps": {
                    "created": stamp(e.occurred_at.astimezone(timezone.utc)),
                    "updated": stamp(e.recorded_at.astimezone(timezone.utc)), "resolutiondate": None}}
            sources[f"jira:{e.issue_key}:description"] = {"kind": "jira_description", "key": e.issue_key,
                "text": p["description"], "recordedAt": stamp(e.recorded_at)}
        elif e.kind in {"publication", "mapping", "inventory", "development_report"}:
            if e.kind == "mapping":
                mappings[p["wbs"]] = p["epics"]
            document = p["document"]
            page = documents.setdefault(document, {"id": p["page_id"], "title": p["title"], "space": "DL",
                "ancestors": p.get("ancestors", []), "revisions": []})
            revision = {"version": p["version"], "body": p["text"], "published_at": stamp(e.recorded_at),
                        "source_id": e.sources[0]}
            page["revisions"].append(revision)
            sources[e.sources[0]] = {"kind": "confluence_revision", "id": p["page_id"], "title": p["title"],
                "version": p["version"], "text": p["text"], "recordedAt": stamp(e.recorded_at),
                "ancestors": p.get("ancestors", [])}
            continue
        if e.issue_key not in issues:
            continue
        row = issues[e.issue_key]
        if e.kind == "field":
            field, value = p["field"], p["value"]
            row["changelog"].append({"author": p.get("author", row["assignee"]),
                "date": stamp(e.recorded_at), "time": e.recorded_at.strftime("%H:%M:%S"),
                "items": [{"field": {"epic": "Epic Link", "parent": "parent"}.get(field, field),
                           "fieldtype": "jira", "from": p.get("old"), "fromString": p.get("old"),
                           "to": value, "toString": value}]})
            if field == "status":
                row["statusName"] = value
                row["statusCategory"] = "done" if value in {"Resolved", "Closed"} else "inprogress"
                row["resolved"] = e.occurred_at.date() if row["statusCategory"] == "done" else None
                row["_timestamps"]["resolutiondate"] = stamp(e.occurred_at.astimezone(timezone.utc)) if row["resolved"] else None
            elif field in {"epic", "parent", "assignee", "priority", "summary"}:
                row[{"epic": "epicKey", "parent": "parentKey"}.get(field, field)] = value
        if e.kind != "issue":
            row["updated"] = max(row["updated"], e.recorded_at.date())
            row["_timestamps"]["updated"] = stamp(e.recorded_at.astimezone(timezone.utc))
        # Event occurrence is stated separately; the Jira comment timestamp is the recording time.
        if p.get("text"):
            text = p["text"] + f"\n업무 발생 시각: {stamp(e.occurred_at)}; 기록 시각: {stamp(e.recorded_at)}."
            identifier = comment_id(e.identity)
            row["comments"].append({"id": identifier, "author": p.get("author", row["assignee"]),
                "body": text, "text": text, "kind": "note", "created": stamp(e.recorded_at),
                "tcreated": e.recorded_at.strftime("%H:%M:%S")})
            sources[comment_source(e.issue_key, e.identity)] = {"kind": "jira_comment", "key": e.issue_key,
                "id": identifier, "text": text, "recordedAt": stamp(e.recorded_at)}
    for key, row in issues.items():
        if row["parentKey"] in issues:
            issues[row["parentKey"]]["subtasks"].append(key)
        for index, history in enumerate(row["changelog"]):
            sources[f"jira:{key}:history:{1000 + index}"] = {
                "kind": "jira_history", "key": key, "id": str(1000 + index),
                "items": deepcopy(history["items"]), "recordedAt": history["date"]}
    confluence = {"etl.user00": [{"id": p["id"], "title": p["title"], "space": "DL",
        "date": datetime.fromisoformat(p["revisions"][-1]["published_at"]).date(),
        "ancestors": p.get("ancestors", []), "body": p["revisions"][-1]["body"]} for p in documents.values()]}
    plan = {"project_key": "DL", "modules": ["ETL", "Catalog", "Query", "Security", "Storage", "Platform"],
            "epics": {}, "wbs": [{"id": "ETL-1", "module": "ETL", "moduleId": "ETL", "name": "데이터 파이프라인 안정화",
                "start": spec.start.date().isoformat(), "end": spec.end.date().isoformat(),
                "epics": [{"key": key, "weight": weight}
                          for key, weight in mappings.get("ETL-1", {}).items()]}]}
    world = SimpleNamespace(today=spec.observed_until.date(), ME="etl.user00", issues=issues, users=users, confluence=confluence,
        attachments={}, activity={}, sprints={}, plan=plan, project="DL", modules=plan["modules"],
        people={m: list(users) for m in plan["modules"]}, by_label={}, by_assignee={}, epic_children={})
    for key, row in issues.items():
        for label in row["labels"]:
            world.by_label.setdefault(label, []).append(key)
        world.by_assignee.setdefault(row["assignee"], []).append(key)
        if row["epicKey"]:
            world.epic_children.setdefault(row["epicKey"], []).append(key)
    return world, sources, documents
