"""Read catalog uses the running JiraClient and its existing projections/snapshots."""

import json
from typing import Literal

from app.jira.jql import _split_order, tokenize


def register_reads(register, runtime):
    def search_issues(jql: str, start_at: int = 0, max_results: int = 50,
                      snapshot_id: str | None = None) -> dict:
        """Search exact JQL within configured Jira projects. Follow hasMore/nextStartAt with the
        returned snapshotId to retain ordering without duplicates. At most 100 issues per page.
        No implicit total cap; OR leaf and issue caches are shared with the LTM UI.
        """
        if not 1 <= max_results <= 100 or start_at < 0:
            raise ValueError("max_results는 1~100, start_at은 0 이상이어야 합니다.")
        projects = runtime.projects()
        where, order = _split_order(tokenize(jql.strip().rstrip(";").strip()))
        query = "project in (" + ", ".join(json.dumps(p) for p in projects) + ")"
        if where:
            query += " AND (" + " ".join(t.value for t in where) + ")"
        if order:
            query += " ORDER BY " + " ".join(t.value for t in order)
        return runtime.client.search_issues_page(query, start_at=start_at, max_results=max_results,
                                                  snapshot_id=snapshot_id)

    def get_ticket(key: str, fresh: bool = False) -> dict:
        """Read description and issue metadata from LTM's cached ticket view. fresh forces refresh."""
        runtime.check_key(key)
        result = runtime.client.ticket_view(key, fresh=fresh)
        if result is None:
            raise ValueError("티켓을 찾을 수 없거나 조회 권한이 없습니다.")
        return result

    def get_comments(key: str, limit: int = 100) -> dict:
        """Read cached comments, newest first; returnedCount/limit make truncation explicit."""
        runtime.check_key(key)
        if not 1 <= limit <= 1000:
            raise ValueError("limit은 1~1000이어야 합니다.")
        rows = runtime.client.issue_comments(key, limit=limit)
        return {"comments": rows, "returnedCount": len(rows), "limit": limit,
                "mayHaveMore": len(rows) >= limit}

    def get_history(key: str, include_children: bool = False, limit: int = 100) -> dict:
        """Read timeline; the default includes only this ticket. Child history is opt-in."""
        runtime.check_key(key)
        if not 1 <= limit <= 1000:
            raise ValueError("limit은 1~1000이어야 합니다.")
        return {"timeline": runtime.client.ticket_timeline(key, limit=limit, include_children=include_children),
                "limit": limit}

    def get_children(key: str) -> dict:
        """Read direct child tasks using the same parent leaf cache as dialogs, Task and WBS."""
        runtime.check_key(key)
        return {"children": runtime.client.ticket_children(key)}

    def get_epic(key: str) -> dict:
        """Read cached Epic metadata and Epic/Task/SubTask tree."""
        runtime.check_key(key)
        return {"epic": runtime.client.epic_metadata(key), "tree": runtime.client.epic_tree(key)}

    def search_users(query: str = "") -> dict:
        """Find Jira user IDs/display names through LTM's shared user lookup cache."""
        from app.domain.search import search_users as search
        return {"users": search(runtime.client, runtime.settings, query)}

    def get_user(user_id: str = "") -> dict:
        """Read a cached user badge; empty user_id returns the current Jira identity."""
        return runtime.client.user_badge(user_id) if user_id else runtime.client.current_user()

    def list_options(kind: Literal["task_types", "child_types", "edit_fields", "transitions",
                                    "priorities", "components", "epics", "link_types"],
                     key: str = "", query: str = "") -> dict:
        """Discover actual Jira types, edit fields, transition IDs and allowed values before writing.
        child_types/edit_fields/transitions require a ticket key; no guessed field or status IDs.
        """
        client = runtime.client
        if kind in ("child_types", "edit_fields", "transitions"):
            runtime.check_key(key)
            method = {"child_types": client.child_types, "edit_fields": client.editmeta,
                      "transitions": client.transitions}[kind]
            return {"options": method(key)}
        methods = {"task_types": client.task_types, "priorities": client.priorities,
                   "components": client.components, "link_types": client.link_types,
                   "epics": lambda: client.epic_options(query)}
        return {"options": methods[kind]()}

    def get_workload(user_id: str, done_days: int = 7, assigned_window: str = "all") -> dict:
        """Read a person's assigned/recently completed tickets and statistics from shared workload caches."""
        runtime.require_person_access(user_id)
        if done_days not in runtime.client.WL_DONE_DAYS or assigned_window not in runtime.client.WL_ASSIGNED_WINDOWS:
            raise ValueError("done_days는 7/14/28, assigned_window는 1w/1m/all이어야 합니다.")
        return {"statistics": runtime.client.workload_person(user_id, done_days, assigned_window),
                "tickets": runtime.client.workload_tickets(user_id, done_days, assigned_window)}

    def get_attachments(key: str) -> dict:
        """List attachment IDs and names; no arbitrary local filesystem access."""
        runtime.check_key(key)
        return {"attachments": runtime.client.ticket_attachments(key)}

    def get_documents(key: str) -> dict:
        """List linked remote documents, including link IDs needed for deletion."""
        runtime.check_key(key)
        return {"documents": runtime.client.ticket_documents(key)}

    for fn in (search_issues, get_ticket, get_comments, get_history, get_children, get_epic,
               search_users, get_user, list_options, get_workload, get_attachments, get_documents):
        register(fn, read_only=True)
