"""Read catalog uses the running JiraClient and its existing projections/snapshots."""

import json
from typing import Annotated, Literal

from pydantic import Field

from app.jira.jql import _split_order, tokenize
from app.services.ticket_commands import CommandError


def register_reads(register, runtime):
    def search_issues(jql: str, start_at: int = 0, max_results: int = 50,
                      snapshot_id: str | None = None) -> dict:
        """Search current Jira issues with exact JQL inside configured search projects.
        Returns issues, total, hasMore, nextStartAt and snapshotId; max_results is 1..100,
        start_at is zero-based. Follow every page with the same JQL and returned snapshotId
        (when present) before reporting a complete count. There is no implicit total cap.
        For calendar reports use explicit date boundaries and distinguish resolved from updated;
        a recent update does not prove completion, and current assignee is not historical ownership.
        Read get_ticket for the full description and get_evidence_page for paginated comments/history.
        Issue url is a user-facing Jira link; self is a REST URL. Caches are shared with the UI.
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
        page = runtime.client.search_issues_page(query, start_at=start_at, max_results=max_results,
                                                 snapshot_id=snapshot_id)
        base = runtime.settings.jira_base.rstrip("/")
        # Add navigation metadata without changing the shared snapshot/issue cache.
        return {**page, "issues": [
            {**issue, "url": base + "/browse/" + issue["key"]}
            for issue in page.get("issues") or []
        ]}

    def get_ticket(key: str, fresh: bool = False) -> dict:
        """Read one current ticket's metadata and complete description by Jira key.
        Returns the ticket view, including descriptionHtml for reading and descriptionEditHtml
        for safe editing. fresh=true waits for an upstream refresh; default reuses UI caches.
        Use get_evidence_page for full discussion/changes; get_comments is only a recent preview.
        This is the current ticket, not a past snapshot.
        A Done status alone does not establish when work completed or which claimed deliverables remain valid.
        """
        runtime.check_key(key)
        result = runtime.client.ticket_view(key, fresh=fresh)
        if result is None:
            raise ValueError("티켓을 찾을 수 없거나 조회 권한이 없습니다.")
        return result

    def get_comments(key: str, limit: int = 100) -> dict:
        """Read a recent comment preview for one ticket, newest first; limit=1..1000.
        The UI cache holds at most sourceLimit=20 comments even when a larger limit is requested.
        Returns comments, returnedCount, limit, sourceLimit and conservative mayHaveMore.
        Use get_evidence_page(kind=comments) to read the full discussion with real pagination.
        For editing, use get_comment_source with the verified comment ID.
        Use comment IDs/dates/authors and text as evidence for exclusions, cancellations and revised scope.
        Comment claims do not themselves change Jira status or prove completed deliverable counts.
        """
        runtime.check_key(key)
        if not 1 <= limit <= 1000:
            raise ValueError("limit은 1~1000이어야 합니다.")
        rows = runtime.client.issue_comments(key, limit=limit)
        return {"comments": rows, "returnedCount": len(rows), "limit": limit,
                "sourceLimit": runtime.client.CACHE_COMMENTS,
                "mayHaveMore": len(rows) >= min(limit, runtime.client.CACHE_COMMENTS)}

    def get_comment_source(key: str, comment_id: str) -> dict:
        """Read one comment's current editor HTML and ID before editing it.
        Obtain the comment ID from get_comments or get_evidence_page(kind=comments).
        Uses the UI's Jira comment-source lookup, which requests the newest 1000 comments;
        Jira may return fewer. A missing source is an error and may mean the ID was outside
        the returned window. If unavailable, do not replace the comment using its preview.
        Preserve unrelated content and mention IDs when sending the full HTML to edit_comment.
        """
        runtime.check_key(key)
        runtime.check_object_id(comment_id)
        source = runtime.client.comment_source(key, comment_id)
        if source is None:
            raise CommandError("이 티켓의 조회 범위에서 댓글 편집 원본을 찾지 못했습니다.", 404)
        return source

    def get_history(key: str, include_children: bool = False, limit: int = 100) -> dict:
        """Read a recent display timeline for one ticket, newest first, limited to 1..1000 events.
        Returns timeline and limit. Includes creation, selected field changes and only the latest
        10 comment events; include_children=true adds direct children's status changes, not all
        descendant discussion. This is a bounded view without pagination or a completeness guarantee.
        Use get_evidence_page for original comments or field histories with full timestamp and coverage.
        """
        runtime.check_key(key)
        if not 1 <= limit <= 1000:
            raise ValueError("limit은 1~1000이어야 합니다.")
        return {"timeline": runtime.client.ticket_timeline(key, limit=limit, include_children=include_children),
                "limit": limit}

    def get_children(key: str) -> dict:
        """Read current direct children of an Epic or Task-tier ticket; returns children.
        Reuses the parent membership and issue caches shared with dialogs, Task and WBS.
        Direct children are not all descendants: inspect each Task's children for its SubTasks.
        Check response partial/missing before treating an empty list or derived count as complete.
        """
        runtime.check_key(key)
        return {"children": runtime.client.ticket_children(key)}

    def get_epic(key: str) -> dict:
        """Read current Epic metadata plus its Task-tier/SubTask tree; returns epic and tree.
        Child status, assignee and dates are current values, not period-end snapshots.
        Use get_epic_progress for WBS Story Point progress; avoid summing parent and SubTask
        work as independent deliverables. Check partial/missing before asserting full coverage.
        """
        runtime.check_key(key)
        return {"epic": runtime.client.epic_metadata(key), "tree": runtime.client.epic_tree(key)}

    def search_users(query: str = "", limit: Annotated[int, Field(ge=1, le=50, strict=True)] = 8) -> dict:
        """Resolve a person by name/username; returns verified identities with matchContext hints.
        limit is 1..50 (default 8); coverage is bounded, without a global total or pagination.
        mayHaveMore=true signals saturation; null means Jira supplied no coverage evidence.
        Refine by full name/company/username before selecting.
        Even an unsaturated page is not a complete roster or proof of global name uniqueness.
        matchContext separately reports configured membership, sameModule with the caller, and
        recentLookupAt/recentSelectionAt/recentMentionAt (Unix seconds, last 7 days).
        Mention recency means an explicit UI mention selection, not proof a comment was posted.
        Null flags/times and contextAvailable distinguish unknown context from available evidence.
        These are relevance hints, not identity proof or permission grants. Do not select a homonym
        solely because they are recent/configured/in the same module; use request context or ask.
        If contextChanged=true, the session changed during this lookup; repeat the search before choosing.
        Use actual returned IDs in JQL and get_workload. Search results do not update user history.
        For authoring, use the verified ID in a mention's data-id; @displayName alone is plain text.
        get_content_guide(skill=ltm-task-authoring,path=references/people-and-links.md) gives exact markup.
        """
        from app.domain.person_context import search_people
        return search_people(runtime.client, runtime.settings, query, limit)

    def get_user(user_id: str = "") -> dict:
        """Read a verified Jira identity/badge by user_id; empty user_id means the logged-in user.
        Returns identity/display fields, or null for an unknown explicit user. Resolve display-name
        ambiguity with search_users first. This lookup does not enumerate module membership or activity.
        A successful explicit user_id lookup records local recency for the current caller;
        self lookups and passive UI badge rendering do not record it.
        """
        from app.domain.person_context import capture_person_context, record_person_event
        context = capture_person_context(runtime.client, runtime.settings) if user_id else None
        result = (runtime.client.user_badge(user_id, strict_auth=True) if user_id
                  else runtime.client.current_user_for_authorization())
        if context is not None and result and result.get("id"):
            record_person_event(runtime.client, runtime.settings, result["id"], "lookup", expected_stamp=context)
        return result

    def list_options(kind: Literal["task_types", "child_types", "create_fields", "edit_fields", "transitions",
                                    "priorities", "components", "epics", "link_types"],
                     key: str = "", query: str = "", issue_type: str = "", field_id: str = "",
                     start_at: Annotated[int, Field(ge=0)] = 0,
                     max_results: Annotated[int, Field(ge=1, le=50)] = 20) -> dict:
        """Discover actual Jira types, editable fields, transition IDs and allowed values before writing.
        Returns options; child_types/edit_fields/transitions require key. query filters only epics.
        Epic suggestions are capped at 20, not an exhaustive Epic inventory; use search_issues for that.
        components are Jira Components, distinct from local people roster modules. Never infer a
        transition ID or supported link type from a display name or a prior ticket's metadata.
        create_fields requires issue_type (actual type name). Exhaust field pages via nextStartAt/hasMore;
        each field includes required/default flags and exact inputFields mappings, valid only for inputTools:
        parent_key belongs to create_subtask and epic_key to create_task. To inspect full defaultValue and
        allowedValues, set field_id and paginate again (paginationKind=allowedValues).
        Selection pages do not replace reading all field pages. Responses fit 8KiB; IDs are never shortened.
        Managed project/issuetype/reporter are supplied by LTM/Jira. unsupportedRequired is page-local:
        fields without an MCP input or Jira default require the Jira UI, not invented custom arguments.
        field_id/start_at/max_results apply only to create_fields; other option lists retain their contracts.
        For transitions, fields lists Jira's screen and commentRequired reflects Jira's required comment.
        Completion comments are recommended, not mandatory unless commentRequired=true; empty adds none.
        Each screen field's inputFields maps to transition_ticket arguments. Required worklog means
        at least one of body.days/hours/minutes must be positive, using the user's actual work duration.
        An empty inputFields means LTM cannot submit that field; required unsupported fields need Jira.
        """
        client = runtime.client
        if kind == "create_fields":
            from app.local_mcp.create_options import create_options
            return create_options(client, runtime.settings, issue_type, field_id, start_at, max_results)
        if kind in ("child_types", "edit_fields", "transitions"):
            runtime.check_key(key)
            method = {"child_types": client.child_types, "edit_fields": lambda key: client.editmeta(key, strict=True),
                      "transitions": lambda key: client.transitions(key, strict=True)}[kind]
            options = method(key)
            if kind == "transitions":
                mapping = {"worklog": ["body.days", "body.hours", "body.minutes"],
                           "assignee": ["body.assignee"], "resolution": ["body.resolution"],
                           "comment": ["body.commentHtml"]}
                for option in options:
                    for field in option["fields"]["fields"]:
                        field["inputFields"] = mapping.get(field.get("system") or field["id"], [])
            return {"options": options}
        methods = {"task_types": client.task_types, "priorities": client.priorities,
                   "components": lambda: client.components(strict=True),
                   "link_types": lambda: client.link_types(strict=True),
                   "epics": lambda: client.epic_options(query)}
        return {"options": methods[kind]()}

    def get_workload(user_id: str, done_days: int = 7, assigned_window: str = "all") -> dict:
        """Read one person's current workload: returns statistics and tickets in open/inProgress/done7d buckets.
        Resolve user_id first; the caller must have access to this person. done_days accepts 7/14/28
        rolling days, not calendar weeks/months; done7d retains that name for all three choices.
        Done uses current Done status and recent resolved date (updated fallback when resolved is absent).
        assigned_window=1w/1m/all filters only open/in-progress by updated within 7/30 days or all.
        Counts group Task/SubTask/VoC; hr is cumulative Jira timespent in hours, not hours logged in the window.
        SubTasks inherit their parent Epic. Inspect partial/missingParents/errors before totals;
        this is not a historical assignee report or an exact calendar-period completion ledger.
        """
        runtime.require_person_access(user_id)
        if done_days not in runtime.client.WL_DONE_DAYS or assigned_window not in runtime.client.WL_ASSIGNED_WINDOWS:
            raise ValueError("done_days는 7/14/28, assigned_window는 1w/1m/all이어야 합니다.")
        return {"statistics": runtime.client.workload_person(user_id, done_days, assigned_window),
                "tickets": runtime.client.workload_tickets(user_id, done_days, assigned_window)}

    def get_attachments(key: str) -> dict:
        """List one ticket's current attachments, including id, filename, size, MIME and download URL.
        Returns attachments; use read_attachment with its verified ID for actual bytes.
        This metadata list is not attachment content and cannot establish facts inside a document.
        """
        runtime.check_key(key)
        return {"attachments": runtime.client.ticket_attachments(key)}

    def get_documents(key: str) -> dict:
        """List one ticket's linked remote documents/URLs; returns documents with link IDs.
        A stored link is not fetched content. For configured Confluence documents, follow with
        read_confluence_document; retain IDs for delete_document. Use get_ticket_context for issue links.
        """
        runtime.check_key(key)
        return {"documents": runtime.client.ticket_documents(key)}

    for fn in (search_issues, get_ticket, get_comments, get_comment_source, get_history, get_children, get_epic,
               search_users, get_user, list_options, get_workload, get_attachments, get_documents):
        register(fn, read_only=True)
