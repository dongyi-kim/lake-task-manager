"""Opt-in, deterministic Tracking workload in the canonical ``world.py`` format.

Normal mock/local startup never loads these records. Tests and benchmarks use
``fixture = build_tracking_fixture(); TestClient(fixture.make_app())``. Every
factory/app call owns independent dictionaries. ``heavy=True`` adds long comment
threads/history to two issues without changing the 10/30/60 active scope.
"""
from collections import Counter
from copy import deepcopy
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from jira820 import make_app
from jira820.config import Config

from app.infra.settings import get_settings
from app.mock.fakebridge import _ISSUE_TYPES, _PRIORITIES, _STATUSES, _TRANSITIONS
from app.mock.permissions import PermissionStore
from app.mock.query_compat import install_query_compat

AS_OF = "2026-11-16T12:00:00+09:00"


@dataclass
class TrackingFixture:
    issues: dict
    users: dict
    plan: dict
    people: dict
    expected: dict
    epic_field: str
    as_of: str = AS_OF

    def make_app(self, **compat_options):
        settings = get_settings()
        config = Config(project_key="TRACK", project_name="Tracking workload", base_date=date(2026, 11, 16),
                        current_user=self.people["Tracking"][0], statuses=_STATUSES, transition_scheme=_TRANSITIONS,
                        issue_types=_ISSUE_TYPES, priorities=_PRIORITIES, modules=["Tracking"], latency_ms=0,
                        sp_field=settings.sp_field_id, epic_link_field=self.epic_field,
                        epic_name_field=settings.epic_name_field_id)
        store = PermissionStore(config, seed=False)
        store.users.update(deepcopy(self.users))
        store.issues.update(deepcopy(self.issues))
        store.reindex()
        app = make_app(store=store)
        install_query_compat(app, **compat_options)
        return app


def build_tracking_fixture(*, heavy=False, archived_count=0):
    """15 members, ten current Epics, three Tasks/Epic, two Sub-Tasks/Task.

    The event oracle is recorded at construction, independently of collector
    analysis. Compare by (issueKey, kind, actorId, at), counting duplicates. It
    includes lifetime events; filter by the requested half-open date interval.
    All timestamps precede static as_of and all 100 current statuses are active.
    """
    if isinstance(archived_count, bool) or not isinstance(archived_count, int) or archived_count < 0:
        raise ValueError("archived_count must be a nonnegative integer")
    members = [f"tracking.x{i:02d}" for i in range(1, 16)]
    users = {uid: {"name": uid, "displayName": f"Tracking member {i + 1:02d}"} for i, uid in enumerate(members)}
    plan = {"project_key": "TRACK", "modules": ["Tracking"], "wbs": [
        {"id": f"tracking-wbs-{i + 1}", "module": "Tracking", "name": f"Delivery stream {i + 1}",
         "start": "2026-01-01", "end": "2026-12-31",
         "epics": [{"key": f"TRACK-{2 * i + 1}", "weight": 1}, {"key": f"TRACK-{2 * i + 2}", "weight": 1}]}
        for i in range(5)]}
    issues, expected = {}, []

    def observed(issue, kind, when, actor):
        expected.append({"issueKey": issue["key"], "kind": kind, "actorId": actor, "at": when.isoformat()})

    def at(day, hour=1, minute=0):
        # Early UTC work hours are 10:00 onwards in Korea. No asOf-day event
        # after 03:00 UTC (12:00 KST), even when the last edit is on November 16.
        return datetime.combine(day, datetime.min.time(), timezone.utc).replace(hour=hour, minute=minute)

    def change(issue, day, field, before, after, actor, hour=1, minute=0):
        names = {"1": "Open", "3": "In Progress", "4": "Reopened", "5": "Resolved", "6": "Closed"}
        item = {"field": field, "fieldtype": "jira", "from": before, "to": after,
                "fromString": names.get(before, before) if field == "status" else before,
                "toString": names.get(after, after) if field == "status" else after}
        issue["changelog"].append({"author": actor, "date": day, "time": f"{hour:02d}:{minute:02d}", "items": [item]})
        if field == "status":
            issue["statusName"] = names[after]
            issue["statusCategory"] = "inprogress" if after == "3" else "todo" if after in {"1", "4"} else "done"
            if before not in {"5", "6"} and after in {"5", "6"}:
                observed(issue, "completed", at(day, hour, minute), actor)
            elif before in {"5", "6"} and after not in {"5", "6"}:
                observed(issue, "reopened", at(day, hour, minute), actor)
            else:
                observed(issue, "fieldChanged", at(day, hour, minute), actor)
        else:
            observed(issue, "fieldChanged", at(day, hour, minute), actor)
        if field == "assignee":
            issue["assignee"] = after

    def comment(issue, day, text, actor, edited=False, minute=0):
        row = {"id": f"{issue['key']}-c{len(issue['comments']) + 1}", "author": actor, "kind": "note",
               "body": text, "text": text, "created": day, "tcreated": f"01:{minute:02d}"}
        issue["comments"].append(row)
        observed(issue, "comment", at(day, 1, minute), actor)
        if edited:
            editor = members[(members.index(actor) + 1) % len(members)]
            row.update(updated=date(2026, 11, 16), tupdated="02:00", updateAuthor=editor)
            observed(issue, "commentEdited", at(date(2026, 11, 16), 2), editor)

    for number in range(1, 101):
        index = number - 1
        kind = "Epic" if number <= 10 else "Task" if number <= 40 else "Sub-Task"
        epic = None if kind == "Epic" else f"TRACK-{1 + (number - 11) // 3}" if kind == "Task" else None
        parent = f"TRACK-{11 + (number - 41) // 2}" if kind == "Sub-Task" else None
        if parent:
            epic = issues[parent]["epicKey"]
        scenario = index % 6
        created = date(2026, 2, 2) if scenario == 3 else date(2026, 11, 10) if scenario == 2 else date(2026, 10, 1)
        actor = members[index % 15]
        creator, reporter = members[(index + 3) % 15], members[(index + 7) % 15]
        issue = {"key": f"TRACK-{number}", "project": "TRACK", "type": kind,
                 "summary": f"{kind} {number}: {['delayed start', 'partial delivery', 'sparse discussion', 'long running', 'completion and reopen', 'handover'][scenario]}",
                 "description": "Existing runtime scope; Tracking does not request this body. " * 8,
                 "module": "Tracking", "component": "Tracking", "assignee": actor, "creator": creator, "reporter": reporter,
                 "statusName": "Open", "statusCategory": "todo", "labels": [], "sp": 3,
                 "epicKey": epic, "parentKey": parent, "epicName": f"Stream {number}" if kind == "Epic" else None,
                 "created": created, "tcreated": "00:30", "updated": date(2026, 11, 16), "tupdated": "02:00",
                 "resolved": None, "due": date(2026, 11, 20), "comments": [], "changelog": [], "worklog": [],
                 "subtasks": [], "links": [], "remotelinks": []}
        issues[issue["key"]] = issue
        if parent:
            issues[parent]["subtasks"].append(issue["key"])
        observed(issue, "created", at(created, 0, 30), creator)
        if scenario == 0:
            change(issue, date(2026, 11, 14), "status", "1", "3", actor)
            comment(issue, date(2026, 11, 14), "Access was delayed. Investigation started after credentials arrived.", actor)
        elif scenario == 1:
            change(issue, date(2026, 11, 2), "status", "1", "3", actor)
            comment(issue, date(2026, 11, 12), "Parser implementation is 50% complete. Retry handling and integration checks remain.", actor)
        elif scenario == 2:
            change(issue, created, "status", "1", "3", actor)
        elif scenario == 3:
            change(issue, date(2026, 3, 2), "status", "1", "3", actor)
            change(issue, date(2026, 11, 11), "duedate", "2026-11-13", "2026-11-20", actor)
        elif scenario == 4:
            change(issue, date(2026, 10, 20), "status", "1", "3", actor)
            for day, before, after in ((5, "3", "5"), (6, "5", "6"), (7, "6", "4"), (8, "4", "3"), (12, "3", "5"), (13, "5", "4")):
                change(issue, date(2026, 11, day), "status", before, after, actor)
            comment(issue, date(2026, 11, 13), "Reopened after integration found duplicate delivery. Prior completion remains in the audit trail.", actor)
        else:
            change(issue, date(2026, 10, 5), "status", "1", "3", actor)
            change(issue, date(2026, 11, 9), "assignee", actor, members[(index + 1) % 15], creator)
            comment(issue, date(2026, 11, 10), "Handover notes updated after review; deployment checklist still pending.", actor, edited=True)
        # Real conversations vary from silent tasks to multi-author threads.
        for offset in range(index % 5):
            comment(issue, date(2026, 11, 10) + timedelta(days=offset),
                    ["Validation sample attached; waiting for the producer response.",
                     "Schema compatibility checked. One nullable field still needs confirmation.",
                     "Retry backoff is ready for review; metrics dashboard is next.",
                     "Integration environment is available for the next verification pass."][offset],
                    members[(index + offset + 2) % 15], minute=10 + offset)
        if heavy and number in {1, 41}:
            for offset in range(110):
                day = created + timedelta(days=offset % 30)
                comment(issue, day, f"Investigation note {offset + 1}: verified partition {offset % 13}; follow-up recorded.",
                        members[(index + offset) % 15], minute=offset % 60)
                change(issue, day, "labels", "review", f"review-{offset}", actor, minute=offset % 60)
        issue["changelog"].sort(key=lambda row: (row["date"], row["time"]))
        issue["comments"].sort(key=lambda row: (row["created"], row["tcreated"], row["id"]))
    for index in range(archived_count):
        # Spread archive records across the year and all fifteen members. Their
        # last source update is before November, while the current 100 stay
        # unchanged. This makes the yearly 10k scan and recent candidate slice
        # materially different without polluting the default development world.
        actor = members[index % 15]
        created = date(2026, 1, 1) + timedelta(days=index % 250)
        issue = {"key": f"TRACK-{101 + index}", "project": "TRACK", "type": "Task",
                 "summary": f"Archived delivery {index + 1}", "description": "Archived delivery notes. " * 10,
                 "module": "Tracking", "component": "Tracking", "creator": actor,
                 "reporter": members[(index + 7) % 15], "assignee": actor,
                 "statusName": "Open", "statusCategory": "todo", "labels": ["archived"], "sp": 2,
                 "epicKey": None, "parentKey": None, "epicName": None, "created": created,
                 "tcreated": "00:30", "updated": created + timedelta(days=5), "tupdated": "02:00",
                 "resolved": created + timedelta(days=5), "tresolved": "02:00", "due": created + timedelta(days=7),
                 "comments": [], "changelog": [], "worklog": [], "subtasks": [], "links": [], "remotelinks": []}
        issues[issue["key"]] = issue
        observed(issue, "created", at(created, 0, 30), actor)
        change(issue, created + timedelta(days=1), "status", "1", "3", actor)
        change(issue, created + timedelta(days=5), "status", "3", "5", actor, hour=2)
        if index % 4 == 0:
            comment(issue, created + timedelta(days=3), "Archive verification passed; release notes recorded.", actor)
    counts = Counter(event["kind"] for event in expected)
    return TrackingFixture(issues, users, plan, {"Tracking": members},
                           {"events": expected, "lifetimeCounts": dict(counts), "issueCount": len(issues),
                            "currentActive": {"Epic": 10, "Task": 30, "Sub-Task": 60}, "memberCount": 15},
                           get_settings().epic_link_field_id)
