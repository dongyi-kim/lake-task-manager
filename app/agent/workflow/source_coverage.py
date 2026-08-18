"""Pure source-request coverage ledger derived from QueryPlan/QueryRunner state.

This module owns classification and accounting only. User-visible rendering and external
URL provenance policy remain with ResultIntegrator.
"""

from __future__ import annotations

import re as _re

from app.agent.workflow.state import Intent, last_user_text, request_text


_SOURCE_COVERAGE_ORDER = (
    "jira", "comments", "confluence",
    "external_web", "external_github",
    "external_web_official", "external_github_official",
    "external", "external_official",
)
_SOURCE_COVERAGE_LABELS = {
    "jira": "Jira 티켓",
    "comments": "Jira 댓글",
    "confluence": "Confluence/wiki",
    "external_web": "외부 웹 자료",
    "external_github": "GitHub 자료",
    "external_web_official": "외부 공식 웹 자료",
    "external_github_official": "공식 GitHub 자료",
    "external": "외부 웹/GitHub 자료",
    "external_official": "외부 공식 자료",
}
_EXTERNAL_SOURCE_COVERAGE_CLASSES = frozenset({
    "external", "external_official",
    "external_web", "external_github",
    "external_web_official", "external_github_official",
})
_OFFICIAL_EXTERNAL_SOURCE_COVERAGE_CLASSES = frozenset({
    "external_official", "external_web_official", "external_github_official",
})
_EXTERNAL_SOURCE_QUERY_CLASSES = {
    "external": frozenset({"web", "github"}),
    "external_official": frozenset({"web", "github"}),
    "external_web": frozenset({"web"}),
    "external_web_official": frozenset({"web"}),
    "external_github": frozenset({"github"}),
    "external_github_official": frozenset({"github"}),
}


def _requested_source_classes(state) -> tuple[str, ...]:
    """Return only source classes the user explicitly named for a read/research request.

    A ticket or comment *write* must not accidentally become a retrieval completeness
    contract.  The read-language gate keeps this projection narrow, while ``Intent.ASK``
    remains a read by definition.  Generic ``문서`` is deliberately not enough to mean
    Confluence; the user must name Confluence/wiki (or the corresponding Korean spelling).
    """
    value = " ".join(part for part in (
        request_text(state).strip(), last_user_text(state).strip(),
    ) if part).strip()
    if not value:
        return ()
    is_read = str(state.get("intent") or "") == Intent.ASK or bool(_re.search(
        r"(?:조사|조회|검색|찾아|확인해|근거(?:\s*(?:중심|기반|자료))?|"
        r"research|investigat|look\s*up|search)", value, _re.I,
    ))
    if not is_read:
        return ()

    comments = bool(_re.search(r"(?:댓글|코멘트|\bcomments?\b)", value, _re.I))
    ticket = bool(_re.search(r"(?:티켓|이슈|\btickets?\b|\bissues?\b)", value, _re.I))
    jira_named = bool(_re.search(r"(?:\bJira\b|지라)", value, _re.I))
    confluence = bool(_re.search(
        r"(?:\bConfluence\b|컨플루언스|\bwiki\b|위키)", value, _re.I,
    ))
    generic_external = bool(_re.search(
        r"(?:외부|\bexternal\b|\bpublic\b)", value, _re.I,
    ))
    web_named = bool(_re.search(r"(?:\bweb\b|웹)", value, _re.I))
    github_named = bool(_re.search(r"(?:\bgithub\b|깃허브)", value, _re.I))
    external = generic_external or web_named or github_named
    official = bool(_re.search(r"(?:공식|\bofficial\b)", value, _re.I))

    requested = {
        "jira": ticket or (jira_named and not comments),
        "comments": comments,
        "confluence": confluence,
        "external_web": external and web_named and not official,
        "external_github": external and github_named and not official,
        "external_web_official": external and web_named and official,
        "external_github_official": external and github_named and official,
        # A generic public-source request remains one aggregate contract. Only a subtype
        # explicitly named by the user is split into an independently disclosed row.
        "external": external and not (web_named or github_named) and not official,
        "external_official": external and not (web_named or github_named) and official,
    }
    return tuple(source for source in _SOURCE_COVERAGE_ORDER if requested[source])


def _source_coverage_rows(state, source_class: str) -> tuple[list[dict], list[dict]]:
    expected = _EXTERNAL_SOURCE_QUERY_CLASSES.get(source_class, {source_class})
    planned = [row for row in ((state.get("query_plan") or {}).get("queries") or [])
               if isinstance(row, dict)
               and str(row.get("source") or "").strip().casefold() in expected]
    executed = [row for row in (state.get("query_results") or [])
                if isinstance(row, dict)
                and str(row.get("source") or "").strip().casefold() in expected]
    return planned, executed


def _missing_planned_query_ids(planned, executed) -> list[str]:
    """Return planned source/query identities with no matching QueryRunner row.

    Counts are not enough: two planned web queries and one successful web result still leave
    one acquisition unperformed. Matching source as well as id prevents a same-id result from
    another provider satisfying the wrong source contract.
    """
    remaining = [
        (str(row.get("source") or "").strip().casefold(),
         str(row.get("id") or "").strip())
        for row in executed if isinstance(row, dict)
    ]
    missing: list[str] = []
    for row in planned:
        if not isinstance(row, dict):
            continue
        source = str(row.get("source") or "").strip().casefold()
        query_id = str(row.get("id") or "").strip()
        identity = (source, query_id)
        if identity in remaining:
            remaining.remove(identity)
        else:
            missing.append(query_id or f"{source}:missing-id")
    return missing


def _source_result_hit_count(source_class: str, result: dict) -> int:
    if not isinstance(result, dict) or result.get("error"):
        return 0
    public_rows = [
        row for row in (result.get("results") or [])
        if isinstance(row, dict)
        and _re.match(r"^https?://[^\s]+$", str(row.get("url") or "").strip(), _re.I)
    ]
    if source_class in _OFFICIAL_EXTERNAL_SOURCE_COVERAGE_CLASSES:
        return sum(
            1 for row in public_rows
            if (
                row.get("official") is True
                or str(row.get("official") or "").strip().casefold() == "true"
            )
        )
    if source_class in _EXTERNAL_SOURCE_COVERAGE_CLASSES:
        return len(public_rows)
    fields = {
        "jira": ("tickets", "ticketDetails"),
        "comments": ("comments",),
        "confluence": ("documents", "documentBodies"),
    }.get(source_class, ())
    counts = [len(result.get(field) or []) for field in fields
              if isinstance(result.get(field), list)]
    return max(counts or [0])


def _materialization_failures(source_class: str, result: dict) -> tuple[list[str], list[str]]:
    """Return opened-source failures separately from lightweight search coverage.

    A search hit proves only that a candidate was listed.  When QueryRunner then fails to
    open the selected ticket or document, that candidate cannot become claim evidence even
    though the search phase itself returned rows.
    """
    if not isinstance(result, dict):
        return [], []
    errors = [str(value or "").strip() for value in result.get("materializationErrors") or []
              if str(value or "").strip()]
    fields = {
        "jira": ("ticketDetails",),
        "comments": ("ticketDetails",),
        "confluence": ("documentBodies",),
    }.get(source_class, ())
    identities: list[str] = []
    for field in fields:
        for row in result.get(field) or []:
            if not isinstance(row, dict) or not row.get("error"):
                continue
            errors.append(str(row.get("error") or "").strip())
            identity = str(
                row.get("key") or row.get("id") or row.get("title") or ""
            ).strip()
            identity = _re.sub(r"[\r\n{}]+", " ", identity)[:120]
            if identity and identity not in identities:
                identities.append(identity)
    return list(dict.fromkeys(error for error in errors if error)), identities[:8]


def _materialized_hit_count(source_class: str, result: dict) -> int | None:
    """Count usable exact-open rows, or ``None`` when no materialization was attempted."""
    if not isinstance(result, dict):
        return None
    if source_class in {"jira", "comments"} and isinstance(result.get("ticketDetails"), list):
        return sum(
            1 for row in result["ticketDetails"]
            if isinstance(row, dict) and not row.get("error") and row.get("key")
        )
    if source_class == "confluence" and isinstance(result.get("documentBodies"), list):
        return sum(
            1 for row in result["documentBodies"]
            if isinstance(row, dict) and not row.get("error")
            and str(row.get("text") or row.get("body") or "").strip()
        )
    return None


def _source_result_candidate_count(source_class: str, result: dict) -> int:
    """Count returned public candidates before an official-provenance gate."""
    if source_class not in _OFFICIAL_EXTERNAL_SOURCE_COVERAGE_CLASSES \
            or not isinstance(result, dict) \
            or result.get("error"):
        return 0
    return len([
        row for row in (result.get("results") or [])
        if isinstance(row, dict)
        and _re.match(r"^https?://[^\s]+$", str(row.get("url") or "").strip(), _re.I)
    ])


def _embedded_comment_coverage_rows(state) -> tuple[list[dict], list[dict]]:
    """Return Jira rows whose materialized ticket details supplied comment arrays."""
    executed = []
    for row in state.get("query_results") or []:
        if not isinstance(row, dict) or str(row.get("source") or "") != "jira":
            continue
        result = row.get("result")
        if not isinstance(result, dict):
            continue
        details = result.get("ticketDetails")
        if not isinstance(details, list) or not any(
                isinstance(detail, dict) and isinstance(detail.get("comments"), list)
                for detail in details):
            continue
        executed.append(row)
    executed_ids = {str(row.get("id") or "").strip() for row in executed}
    planned = [
        row for row in ((state.get("query_plan") or {}).get("queries") or [])
        if isinstance(row, dict) and str(row.get("source") or "") == "jira"
        and (not executed_ids or str(row.get("id") or "").strip() in executed_ids)
    ]
    return planned, executed


def _embedded_comment_hit_count(query_results) -> int:
    """Count comments opened with ticket details even without a comment-search row."""
    count = 0
    for row in query_results or []:
        if not isinstance(row, dict) or str(row.get("source") or "") != "jira":
            continue
        result = row.get("result") or {}
        if not isinstance(result, dict):
            continue
        for detail in result.get("ticketDetails") or []:
            if isinstance(detail, dict):
                count += len([item for item in (detail.get("comments") or [])
                              if isinstance(item, dict)])
    return count


def _source_coverage_error_kind(source_class: str, errors: list[str]) -> str:
    value = " ".join(str(error or "") for error in errors).casefold()
    if _re.search(
            r"(?:미설정|not\s+configured|configuration|confluence_base|"
            r"search\.(?:jira\.projects|confluence\.spaces)|검색\s*범위.*(?:지정|설정))",
            value, _re.I):
        return "config_error"
    if (source_class in _EXTERNAL_SOURCE_COVERAGE_CLASSES or _re.search(
            r"(?:provider|blocked|막혀|unavailable|timeout|timed\s*out|connection|"
            r"connect|network|dns|certificate|\bssl\b|\b(?:401|403|5\d\d)\b|"
            r"인증|권한|접근\s*실패)", value, _re.I)):
        return "provider_error"
    return "execution_error"


def _requested_source_coverage(state) -> list[dict]:
    """Project explicit source requirements into a bounded typed coverage ledger."""
    coverage = []
    for source_class in _requested_source_classes(state):
        planned, executed = _source_coverage_rows(state, source_class)
        hits = sum(_source_result_hit_count(source_class, row.get("result") or {})
                   for row in executed)
        candidate_hits = sum(
            _source_result_candidate_count(source_class, row.get("result") or {})
            for row in executed
        )
        if source_class == "comments" and not hits:
            embedded_planned, embedded_executed = _embedded_comment_coverage_rows(state)
            if embedded_executed:
                hits = _embedded_comment_hit_count(embedded_executed)
                # The Jira materialization is the comments acquisition in this fallback.
                # Its error/completeness metadata must therefore govern the comments row.
                planned = [*planned, *embedded_planned]
                executed = [*executed, *embedded_executed]
        errors = [str((row.get("result") or {}).get("error") or "")
                  for row in executed if isinstance(row.get("result"), dict)
                  and (row.get("result") or {}).get("error")]
        materialization_errors: list[str] = []
        materialization_failures: list[str] = []
        materialized_counts: list[int] = []
        for row in executed:
            result = row.get("result") or {}
            failed, identities = _materialization_failures(source_class, result)
            materialization_errors.extend(failed)
            for identity in identities:
                if identity not in materialization_failures:
                    materialization_failures.append(identity)
            count = _materialized_hit_count(source_class, result)
            if count is not None:
                materialized_counts.append(count)
        errors.extend(materialization_errors)
        incomplete_results = [row.get("result") or {} for row in executed
                              if isinstance(row.get("result"), dict)
                              and ((row.get("result") or {}).get("incomplete") is True
                                   or (row.get("result") or {}).get("complete") is False)]
        missing_query_ids = _missing_planned_query_ids(planned, executed)
        incomplete_reason = next((
            str(result.get("incompleteReason") or "").strip()
            for result in incomplete_results if result.get("incompleteReason")
        ), ("complete_false" if incomplete_results else
            "missing_query_result" if missing_query_ids and executed else ""))
        # A partial hit list is not complete evidence. Error/completeness metadata outranks
        # non-zero rows so a truncated first page cannot silently become source coverage.
        if errors:
            status = _source_coverage_error_kind(source_class, errors)
        elif incomplete_results or (missing_query_ids and executed):
            status = "incomplete"
        elif hits:
            status = "covered"
        elif (source_class in _OFFICIAL_EXTERNAL_SOURCE_COVERAGE_CLASSES
              and candidate_hits):
            status = "unverified_official"
        elif not planned and not executed:
            status = "not_planned"
        elif planned and not executed:
            status = "not_executed"
        else:
            status = "zero_hits"
        entity_rows = [
            (row.get("result") or {}).get("entityCoverage")
            for row in executed
            if isinstance(row.get("result"), dict)
            and isinstance((row.get("result") or {}).get("entityCoverage"), dict)
        ] if source_class == "jira" else []
        entity_complete = bool(entity_rows) and all(
            row.get("complete") is True and row.get("truncated") is not True
            for row in entity_rows
        )
        coverage.append({
            "source_class": source_class,
            "label": _SOURCE_COVERAGE_LABELS[source_class],
            "status": status,
            "planned_queries": len(planned),
            "executed_queries": len(executed),
            "result_hits": hits,
            **({"candidate_hits": candidate_hits}
               if source_class in _OFFICIAL_EXTERNAL_SOURCE_COVERAGE_CLASSES else {}),
            "usable_as_evidence": status == "covered",
            **({
                "materialized_hits": sum(materialized_counts),
                "materialization_complete": not materialization_errors,
            } if materialized_counts or materialization_errors else {}),
            **({
                "materialization_failure_count": len(materialization_errors),
                "materialization_failed_identities": materialization_failures,
            } if materialization_errors else {}),
            **({
                # Source pagination and entity traversal answer different questions. A
                # green Jira row proves the scoped JQL completed; bounded child/link
                # expansion remains explicitly non-complete unless its own provider says so.
                "entity_coverage_status": "complete" if entity_complete else "bounded",
                "entity_coverage_complete": entity_complete,
                "entity_roots": sum(len(row.get("rootKeys") or []) for row in entity_rows),
                "entity_selected": sum(len(row.get("selectedKeys") or []) for row in entity_rows),
                "entity_truncated": any(row.get("truncated") is True for row in entity_rows),
            } if entity_rows else {}),
            **({"incomplete_reason": (
                "materialization_failed" if materialization_errors else incomplete_reason
            )} if materialization_errors or incomplete_reason else {}),
            **({"missing_query_ids": missing_query_ids} if missing_query_ids else {}),
        })
    return coverage[:len(_SOURCE_COVERAGE_ORDER)]


__all__ = [
    "_EXTERNAL_SOURCE_COVERAGE_CLASSES",
    "_EXTERNAL_SOURCE_QUERY_CLASSES",
    "_OFFICIAL_EXTERNAL_SOURCE_COVERAGE_CLASSES",
    "_SOURCE_COVERAGE_LABELS",
    "_SOURCE_COVERAGE_ORDER",
    "_embedded_comment_coverage_rows",
    "_embedded_comment_hit_count",
    "_missing_planned_query_ids",
    "_requested_source_classes",
    "_requested_source_coverage",
    "_source_coverage_error_kind",
    "_source_coverage_rows",
    "_source_result_candidate_count",
    "_source_result_hit_count",
]
