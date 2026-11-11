"""Read catalog uses the running JiraClient and its existing projections/snapshots."""

import json
from typing import Annotated, Literal

from pydantic import Field

from app.jira.jql import _split_order, tokenize
from app.jira.jql_budget import budgeted_query, current_budget
from app.services.ticket_commands import CommandError
from app.local_mcp.projections import issue_projection, ticket_projection


def register_reads(register, runtime):
    def search_issues(jql: str, start_at: int = 0, max_results: int = 50,
                      snapshot_id: str | None = None, detail: Literal["full", "summary"] = "full",
                      include_description: bool = False,
                      strategy: Literal["auto", "off", "people", "full"] = "auto",
                      max_parts: Annotated[int, Field(strict=True, ge=1, le=64)] = 64) -> dict:
        """Search current Jira issues with exact JQL inside configured search projects.
        Returns issues, total, hasMore, nextStartAt and snapshotId; max_results is 1..100,
        start_at is zero-based. Follow every page with the same JQL and returned snapshotId
        (when present) before reporting a complete count. There is no implicit total cap.
        For calendar reports use explicit date boundaries and distinguish resolved from updated;
        a recent update does not prove completion, and current assignee is not historical ownership.
        For progress summaries, reuse returned metadata and request needed bodies in a batch;
        add get_ticket(detail=read) or get_comments only for missing/rendered body or discussion evidence.
        Use get_evidence_page(kind=comments) if older or complete discussion is needed; field history
        is for questions about changes or exact event timing, not routine discovery of every result.
        Issue url is a user-facing Jira link; self is a REST URL. Caches are shared with the UI.
        Prefer detail=summary for discovery: it trims metadata avatars and custom fields in the response.
        Description is excluded by default. For needed candidates use JQL key in (...) and
        include_description=true to collect complete raw bodies in the same Jira search, without
        per-ticket GET fan-out or fetching attachments/issue links. Other field selection is unchanged.
        The description switch is independent of detail: true includes the body even with summary.
        Raw description retains Jira markup;
        use get_ticket(read/edit) when a rendered/editable representation is actually needed.
        Preserve include_description and cache_mode with the snapshot on following pages; changing
        either starts a new first-page search. A snapshot lasts 30 minutes from creation, separately
        from its source age. snapshotFrozen=true continues that fixed result and does not establish
        another live validation; use a new first-page search when new current evidence is required.
        descriptionMissingKeys identifies requested bodies not returned;
        a returned null/empty description is a known empty body. Do not re-read fields already obtained.
        strategy=off uses one whole JQL (possibly many pages), people groups proven people filters,
        full queries individual logical parts, auto keeps the existing safe grouping. max_parts caps
        distinct physical JQL, not HTTP pages or retries; known over-budget plans fall back to whole
        search. Keep strategy and max_parts unchanged with the cursor. execution describes the plan
        and actual physicalQueryCount; this is not the total Jira REST attempt count.
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
            **({"strategy": strategy, "max_parts": max_parts} if strategy != "auto" or max_parts != 64 else {}),
            snapshot_id=snapshot_id, **({"fields": runtime.client._issue_fields(light=True) + ",description",
                                       "light": False} if include_description else {}))
        base = runtime.settings.jira_base.rstrip("/")
        # Add navigation metadata without changing the shared snapshot/issue cache.
        issues = []
        for issue in page.get("issues") or []:
            projected = issue_projection(issue) if detail == "summary" else {
                **issue, "fields": {key: value for key, value in issue["fields"].items() if key != "description"}}
            if include_description and "description" in issue["fields"]:
                projected["fields"]["description"] = issue["fields"]["description"]
            if not include_description and isinstance(projected.get("renderedFields"), dict):
                projected["renderedFields"] = {key: value for key, value in projected["renderedFields"].items()
                                               if key != "description"}
            issues.append({**projected, "url": base + "/browse/" + issue["key"]})
        result = {**page, "issues": issues}
        if include_description:
            result.update(descriptionRequested=True, descriptionMissingKeys=[
                issue["key"] for issue in page.get("issues") or [] if "description" not in issue["fields"]])
        return result

    @budgeted_query
    def get_tickets(keys: Annotated[list[str], Field(min_length=1, max_length=100)],
                    include_description: bool = False,
                    strategy: Literal["auto", "off", "people", "full"] = "auto",
                    max_parts: Annotated[int, Field(strict=True, ge=1, le=64)] = 64) -> dict:
        """Read 1..100 known Jira keys together, using batched search rather than per-ticket GETs.
        Use search_issues to discover candidates, get_tickets when keys are already known,
        and get_ticket(read/edit) for one rendered/editable description. Other metadata uses
        the existing search field selection. Descriptions are excluded by default; enable
        include_description only for the needed key set to receive complete raw Jira markup.
        Keys are normalized and deduplicated; issues preserve requested order. Returned
        missingKeys means not returned (possibly unavailable or not visible), never proof of
        nonexistence. complete=false requires reporting the missing scope. Missing requested
        bodies appear separately in descriptionMissingKeys; returned null/empty is known empty.
        A batch uses Jira search pages if the server applies a smaller page cap; it never
        fetches individual issues. Split larger key sets into batches of at most 100.
        strategy and max_parts have the same meaning as search_issues and apply to the whole batch.
        """
        requested = list(dict.fromkeys(key.upper() for key in keys))
        for key in requested:
            runtime.check_key(key)
        query = "key in (" + ",".join(json.dumps(key) for key in sorted(requested)) + ") ORDER BY key ASC"
        rows, missing_bodies, start, snapshot, seen = {}, set(), 0, None, set()
        while True:
            page = search_issues(query, start_at=start, max_results=100, snapshot_id=snapshot,
                                 include_description=include_description, strategy=strategy, max_parts=max_parts)
            for issue in page["issues"]:
                if issue["key"] not in requested:
                    raise ValueError("요청하지 않은 티켓이 다건 조회에 포함되었습니다.")
                rows[issue["key"]] = issue
            missing_bodies.update(page.get("descriptionMissingKeys") or [])
            if not page["hasMore"]:
                break
            next_start = page.get("nextStartAt")
            if type(next_start) is not int or next_start <= start or next_start in seen or next_start >= len(requested):
                raise ValueError("다건 조회의 페이지 위치가 전진하지 않습니다.")
            seen.add(start)
            start, snapshot = next_start, page.get("snapshotId")
        missing = [key for key in requested if key not in rows]
        result = {"issues": [rows[key] for key in requested if key in rows], "requestedKeys": requested,
                  "returned": len(rows), "missingKeys": missing, "complete": not missing,
                  "execution": {**page.get("execution", {}), **current_budget().diagnostics()}}
        if include_description:
            result.update(descriptionRequested=True,
                          descriptionMissingKeys=[key for key in requested if key in missing_bodies])
        return result

    def get_ticket(key: str, fresh: bool = False, detail: Literal["full", "summary", "read", "edit"] = "full") -> dict:
        """Read one current ticket's metadata and complete description by Jira key.
        Returns the ticket view, including descriptionHtml for reading and descriptionEditHtml
        for safe editing. fresh=true waits for an upstream refresh; default reuses UI caches.
        For one ticket combine metadata/description with relevant recent discussion. For several
        needed bodies prefer get_tickets(include_description=true), reusing already returned fields.
        If older discussion or a complete review is needed, use get_evidence_page(kind=comments).
        Read field history only when the question requires change chronology or unresolved discrepancies.
        This is the current ticket, not a past snapshot.
        A Done status alone does not establish when work completed or which claimed deliverables remain valid.
        Choose summary for metadata, read for one complete display body, or edit for one complete
        editable body. These modes avoid duplicate body representations. Default full is unchanged.
        """
        runtime.check_key(key)
        result = runtime.client.ticket_view(key, fresh=fresh)
        if result is None:
            raise ValueError("티켓을 찾을 수 없거나 조회 권한이 없습니다.")
        return ticket_projection(result, detail)

    def get_comments(key: str, limit: int = 100) -> dict:
        """Read recent discussion for one ticket, newest first; limit=1..1000.
        Pair with ticket metadata/description already obtained in batch or through get_ticket.
        The UI cache holds at most sourceLimit=20 comments even when a larger limit is requested.
        Returns comments, returnedCount, limit, sourceLimit and conservative mayHaveMore.
        If this preview cannot answer the question, or a complete review is required, use
        get_evidence_page(kind=comments) for older discussion with real pagination. Do not infer
        that no cancellation/exclusion exists from a preview when mayHaveMore=true.
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
        Use for change chronology, not as the default source for major progress or deliverables;
        start those summaries with get_ticket and get_comments.
        Returns timeline and limit. Includes creation, selected field changes and only the latest
        10 comment events; include_children defaults false. Explicit true adds direct children's status changes, not all
        descendant discussion. This is a bounded view without pagination or a completeness guarantee.
        For exact event timestamps/source coverage, use get_evidence_page(kind=history) for each
        required key. Discussion itself uses kind=comments; neither mode automatically reads children.
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
        matchContext separately reports configured membership, sameModule with the cached Jira identity, and local
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
        A successful explicit user_id lookup records recency for this local installation;
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
        Resolve user_id first; Jira permissions apply to the returned tickets. done_days accepts 7/14/28
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

    for fn in (search_issues, get_tickets, get_ticket, get_comments, get_comment_source, get_history, get_children, get_epic,
               search_users, get_user, list_options, get_workload, get_attachments, get_documents):
        register(fn, read_only=True)
