"""Generic scoped hierarchy registration."""

from app.jira import hierarchy_service


def register_hierarchy(register, runtime):
    def get_descendants(root_key: str, include_root: bool = True,
                        start_at: int = 0, max_results: int = 100,
                        missing_start_at: int = 0) -> dict:
        """Read the current flat hierarchy below an Epic, Task or SubTask, recursively.
        Returns issues containing key,parentKey,depth,epicKey,issue (light fields without subtasks), plus
        total/returned/hasMore/nextStartAt, complete/partial and per-branch missing reasons.
        Assignee/reporter keep only supplied accountId,name,key,displayName,self,timeZone identity
        fields; avatar/email/active and other profile extras are omitted. Null/unknown identities
        are not invented. Other light fields, custom fields and actual parent context remain.
        include_root defaults true; root depth is zero and parentKey is null for this traversal.
        Task descendants are discovered even when the Task itself has not recently changed.
        epicKey inherits through Task to SubTask. No period/current-assignee filter is applied.
        max_results is 1..100; start_at is zero-based. Follow all pages and deduplicate keys.
        Missing branches have independent missing_start_at pagination with the same max_results:
        follow nextMissingStartAt until missingHasMore=false; missingTotal counts the whole ledger.
        The combined data page is capped at 8 KiB, so either page can be shorter than max_results.
        An oversized single issue keeps its exact identity/relationship with projectionOmissions
        and a get_ticket details pointer; do not treat omitted fields as empty values.
        Discovery has no hidden ticket cap; pagination is a live hierarchy, not a frozen snapshot.
        total counts returned-scope visible rows; when partial=true it is a lower bound.
        Hidden Jira membership cannot be discovered; missing lists known denied/failed branches.
        This discovers membership/current fields, not descendant comments or history. For progress,
        reuse returned fields, batch needed bodies with search_issues(include_description=true), and
        add get_comments only for relevant discussion; page older discussion with
        get_evidence_page(kind=comments) when needed. Do not routinely read every descendant's
        field history; request kind=history for specific change/timing questions only.
        Parent/SubTask rows are separate hierarchy levels, not automatically additive work units.
        Task child membership and issue caches are shared with UI. Epic membership uses scoped
        project JQL through the shared search cache; no unscoped Agile fallback is performed.
        Parent subtasks arrays are omitted because the paged hierarchy already provides membership.
        """
        runtime.check_key(root_key)
        return hierarchy_service.get_descendants(runtime.client, root_key, projects=runtime.projects(),
            include_root=include_root, start_at=start_at, max_results=max_results,
            missing_start_at=missing_start_at)

    register(get_descendants, read_only=True)
