"""Pure projection of completed QueryRunner artifacts into bounded write evidence.

The module owns source identity and selection mechanics only.  It does not decide whether a query
plan is complete, whether a candidate is an exact duplicate, or whether a write may proceed.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import re
from typing import Sequence


_ACTION_LABELS = {
    "create": "생성",
    "comment": "댓글 작성",
    "update": "필드 변경",
    "query": "조회",
}


@dataclass(frozen=True)
class TicketEvidenceIndex:
    """Ordered Jira identities and their bounded evidence projections."""

    rows: list[dict]
    ordered_keys: list[str]


@dataclass(frozen=True)
class DocumentEvidenceIndex:
    """URL/page-id bound document evidence plus the legacy related-doc projection."""

    rows: list[dict]
    related_docs: list[dict]


@dataclass(frozen=True)
class ExternalEvidenceIndex:
    """External search evidence and the unbounded material count used in diagnostics."""

    rows: list[dict]
    material_count: int


def ledger_text(value, limit: int = 420) -> str:
    """Keep deterministic ledger cells compact without interpreting their content."""
    return re.sub(r"\s+", " ", str(value or "")).strip()[:limit]


def _ledger_date(item: dict) -> str:
    for field in ("observed_at", "updated", "modified", "created", "date", "published"):
        value = str((item or {}).get(field) or "").strip()
        if value:
            return value[:80]
    return ""


def _query_provenance(row: dict) -> str:
    """Render exact QueryRunner scope/query metadata attached to one compact result."""
    result = (row or {}).get("result") or {}
    parts = [f"QueryPlan {row.get('source') or 'query'}:{row.get('id') or '-'}"]
    for field in ("scopeProjects", "scopeSpaces", "pages", "total", "returned", "query",
                  "canonicalJql", "canonicalCql", "artifactId"):
        value = result.get(field)
        if value not in (None, "", []):
            rendered = json.dumps(value, ensure_ascii=False) if isinstance(value, (list, dict)) \
                else str(value)
            parts.append(f"{field}={rendered}")
    return ledger_text(" · ".join(parts))


def _append_observation(target: list[dict], source: str, text: str,
                        observed_at: str = "") -> None:
    value = ledger_text(text)
    if not value:
        return
    row = {"source": source, "text": value, "observed_at": str(observed_at or "")[:80]}
    identity = (row["source"], row["text"], row["observed_at"])
    if not any((old.get("source"), old.get("text"), old.get("observed_at")) == identity
               for old in target):
        target.append(row)


def bind_materialized_ticket_sources(query_results: Sequence[dict],
                                     ticket_details: Sequence[dict]) -> list[dict]:
    """Append the persisted detail sidecar as one synthetic QueryRunner provenance row."""
    rows = [row for row in query_results if isinstance(row, dict)]
    details = [dict(row) for row in ticket_details
               if isinstance(row, dict) and not row.get("error")]
    if details:
        rows.append({
            "id": "materialized-ticket-sources",
            "source": "jira",
            "result": {
                "artifactId": "materialized_ticket_sources",
                "ticketDetails": details,
            },
        })
    return rows


def _ticket_entry(ticket_rows: dict[str, dict], ticket_order: list[str], key,
                  title="", url="") -> dict | None:
    exact = str(key or "").strip().upper()
    if not exact:
        return None
    if exact not in ticket_rows:
        ticket_rows[exact] = {
            "key": exact,
            "title": ledger_text(title, 300) or exact,
            "url": str(url or "").strip()[:1000],
            "_material": [],
            "_queries": [],
            "_opened": False,
        }
        ticket_order.append(exact)
    current = ticket_rows[exact]
    if title and (not current.get("title") or current.get("title") == exact):
        current["title"] = ledger_text(title, 300)
    if url and not current.get("url"):
        current["url"] = str(url).strip()[:1000]
    return current


def _index_ticket_query_row(query_row: dict, ticket_rows: dict[str, dict],
                            ticket_order: list[str]) -> None:
    result = query_row.get("result") or {}
    provenance = _query_provenance(query_row)
    for hit in result.get("tickets") or []:
        if not isinstance(hit, dict):
            continue
        entry = _ticket_entry(
            ticket_rows, ticket_order, hit.get("key"),
            hit.get("summary") or hit.get("title"), hit.get("url") or hit.get("self"),
        )
        if entry:
            entry["_queries"].append(provenance)
    for detail in result.get("ticketDetails") or []:
        if not isinstance(detail, dict) or detail.get("error"):
            continue
        entry = _ticket_entry(
            ticket_rows, ticket_order, detail.get("key"),
            detail.get("summary") or detail.get("title"), detail.get("url") or detail.get("self"),
        )
        if not entry:
            continue
        entry["_opened"] = True
        entry["_queries"].append(provenance)
        _append_observation(entry["_material"], "description", detail.get("description"),
                            _ledger_date(detail))
        for comment in detail.get("comments") or []:
            if not isinstance(comment, dict):
                continue
            author = ledger_text(comment.get("author"), 80)
            body = comment.get("body") or comment.get("html") or comment.get("snippet")
            text = f"{author}: {body}" if author and body else body
            _append_observation(entry["_material"], "comment", text, _ledger_date(comment))
    for comment in result.get("comments") or []:
        if not isinstance(comment, dict):
            continue
        entry = _ticket_entry(
            ticket_rows, ticket_order, comment.get("ticketKey") or comment.get("key"),
            comment.get("ticketSummary") or comment.get("summary"),
        )
        if not entry:
            continue
        entry["_queries"].append(provenance)
        author = ledger_text(comment.get("author"), 80)
        body = comment.get("snippet") or comment.get("body") or comment.get("html")
        text = f"{author}: {body}" if author and body else body
        _append_observation(entry["_material"], "comment", text, _ledger_date(comment))


def index_ticket_sources(query_results: Sequence[dict], *, target_keys: set[str],
                         duplicate_key: str, action: str) -> TicketEvidenceIndex:
    """Bind ticket hits, opened details, comments, and materialized sidecars by exact key."""
    ticket_rows: dict[str, dict] = {}
    ticket_order: list[str] = []
    for query_row in query_results:
        _index_ticket_query_row(query_row, ticket_rows, ticket_order)

    action_label = _ACTION_LABELS[action]
    evidence = []
    for key in ticket_order:
        source = ticket_rows[key]
        observations = list(source.pop("_material"))[:3]
        queries = list(dict.fromkeys(source.pop("_queries")))
        opened = bool(source.pop("_opened"))
        if queries:
            _append_observation(observations, "query", " | ".join(queries))
        is_duplicate = key == duplicate_key
        is_target = action in {"comment", "update"} and key in target_keys and opened
        evidence.append({
            **source,
            "why": (
                "요청의 핵심 대상·행동과 issue type이 일치하고 open 상태인 기존 티켓이다."
                if is_duplicate else
                (f"사용자가 지정한 {action_label} 대상이며 QueryRunner가 원본을 열어 확인했다."
                 if is_target else
                 f"설정된 Jira 검색 범위에서 반환된 {action_label} 검토 후보이다.")
            ),
            "confidence": "high" if is_duplicate or is_target else "unknown",
            "fitness": "direct" if is_duplicate or is_target else "unknown",
            "limitations": (
                "" if is_duplicate else
                ("대상 원본이며, 새 값이나 댓글 내용은 사용자 요청과 별도 초안 계약에서만 확정한다."
                 if is_target else
                 f"검색 일치만으로 {action_label} 요청의 직접 근거인지 확정하지 않았다.")
            ),
            "observations": observations[:4],
        })
    return TicketEvidenceIndex(rows=evidence, ordered_keys=ticket_order)


def _document_entry(item: dict, rows: list[dict], aliases: dict[str, dict],
                    titles: dict[str, list[dict]]) -> dict | None:
    title = ledger_text(item.get("title"), 300)
    url = str(item.get("url") or "").strip()[:1000]
    doc_id = str(item.get("id") or "").strip()
    identities = [f"url:{url}" if url else "", f"id:{doc_id}" if doc_id else ""]
    current = next((aliases[value] for value in identities if value in aliases), None)
    if current is None and not any(identities) and title:
        matches = titles.get(title) or []
        current = matches[0] if len(matches) == 1 else None
    if current is None:
        identity = title or doc_id or url
        if not identity:
            return None
        current = {
            "key": identity,
            "title": title or identity,
            "url": url,
            "_bodies": [],
            "_excerpts": [],
            "_queries": [],
        }
        rows.append(current)
    elif title and (not current.get("title") or current.get("title") == current.get("key")):
        current["title"] = title
        current["key"] = title
    if url and not current.get("url"):
        current["url"] = url
    for identity in identities:
        if identity:
            aliases[identity] = current
    if title and current not in titles.setdefault(title, []):
        titles[title].append(current)
    return current


def index_document_sources(query_results: Sequence[dict], *, action: str) -> DocumentEvidenceIndex:
    """Bind document hits and bodies only by URL/page id, with unambiguous title fallback."""
    rows: list[dict] = []
    aliases: dict[str, dict] = {}
    titles: dict[str, list[dict]] = {}
    for query_row in query_results:
        result = query_row.get("result") or {}
        provenance = _query_provenance(query_row)
        for hit in result.get("documents") or []:
            if not isinstance(hit, dict):
                continue
            entry = _document_entry(hit, rows, aliases, titles)
            if not entry:
                continue
            entry["_queries"].append(provenance)
            _append_observation(entry["_excerpts"], "document",
                                hit.get("excerpt") or hit.get("snippet"), _ledger_date(hit))
        for body in result.get("documentBodies") or []:
            if not isinstance(body, dict) or body.get("error"):
                continue
            entry = _document_entry(body, rows, aliases, titles)
            if not entry:
                continue
            entry["_queries"].append(provenance)
            _append_observation(entry["_bodies"], "document", body.get("text") or body.get("body"),
                                _ledger_date(body))

    action_label = _ACTION_LABELS[action]
    evidence = []
    related_docs = []
    for source in rows:
        bodies = list(source.pop("_bodies"))
        observations = (bodies + list(source.pop("_excerpts")))[:3]
        queries = list(dict.fromkeys(source.pop("_queries")))
        if queries:
            _append_observation(observations, "query", " | ".join(queries))
        evidence.append({
            **source,
            "why": (
                "설정된 Confluence 검색 범위에서 반환되어 본문까지 확인한 문서 후보이다."
                if bodies else "설정된 Confluence 검색 범위에서 반환된 문서 후보이다."
            ),
            "confidence": "unknown",
            "fitness": "unknown",
            "limitations": f"문서 내용만으로 {action_label} 요청의 직접 근거인지 확정하지 않았다.",
            "observations": observations[:4],
        })
        related_docs.append({"title": source["title"], "url": source.get("url") or ""})
    return DocumentEvidenceIndex(rows=evidence, related_docs=related_docs)


def index_external_sources(query_results: Sequence[dict], *, action: str) -> ExternalEvidenceIndex:
    """Project web and repository hits independently without inferring internal state."""
    action_label = _ACTION_LABELS[action]
    evidence = []
    material_count = 0
    for query_row in query_results:
        if query_row.get("source") not in ("web", "github"):
            continue
        result = query_row.get("result") or {}
        for hit in result.get("results") or []:
            if not isinstance(hit, dict):
                continue
            title = ledger_text(hit.get("title") or hit.get("name"), 300)
            url = str(hit.get("url") or "").strip()[:1000]
            identity = title or url
            if not identity:
                continue
            observations = []
            _append_observation(observations, "external",
                                hit.get("snippet") or hit.get("description"), _ledger_date(hit))
            provenance = _query_provenance(query_row)
            if hit.get("official"):
                provenance += " · official=true"
            _append_observation(observations, "query", provenance)
            evidence.append({
                "key": identity,
                "title": title or identity,
                "url": url,
                "why": (f"QueryPlan {query_row.get('source')}:{query_row.get('id') or '-'}에서 "
                        "반환된 외부 자료이다."),
                "confidence": "high" if hit.get("official") else "unknown",
                "fitness": "unknown",
                "limitations": f"외부 자료이며 내부 {action_label} 대상의 현재 상태를 증명하지 않는다.",
                "observations": observations,
            })
            material_count += 1
    return ExternalEvidenceIndex(rows=evidence, material_count=material_count)


def _research_score(row: dict, terms: Sequence[str]) -> int:
    identity = f"{row.get('key', '')} {row.get('title', '')}".casefold()
    observations = " ".join(
        str(observation.get("text") or "")
        for observation in (row.get("observations") or []) if isinstance(observation, dict)
    ).casefold()
    return sum((6 if term in identity else 2 if term in observations else 0) for term in terms)


def _research_bounded(groups: Sequence[Sequence[dict]], duplicate_key: str,
                      terms: Sequence[str]) -> list[dict]:
    ranked_groups = []
    for group_index, group in enumerate(groups):
        ranked = sorted(
            enumerate(group),
            key=lambda pair: (
                0 if duplicate_key and pair[1].get("key") == duplicate_key else 1,
                -_research_score(pair[1], terms),
                pair[0],
            ),
        )
        ranked_groups.append([(group_index, index, row) for index, row in ranked])

    selected, identities = [], set()

    def add(entry) -> None:
        if entry is None:
            return
        identity = (entry[0], entry[1])
        if identity in identities or len(selected) >= 8:
            return
        identities.add(identity)
        selected.append(entry[2])

    for ranked in ranked_groups:
        add(ranked[0] if ranked else None)
    remaining = [entry for ranked in ranked_groups for entry in ranked
                 if (entry[0], entry[1]) not in identities]
    remaining.sort(key=lambda entry: (
        0 if duplicate_key and entry[2].get("key") == duplicate_key else 1,
        -_research_score(entry[2], terms), entry[0], entry[1],
    ))
    for entry in remaining:
        add(entry)
    return selected


def select_bounded_sources(ticket_rows: list[dict], document_rows: list[dict],
                           external_rows: list[dict], *, duplicate_key: str,
                           target_keys: set[str], research_focus: bool,
                           research_terms: Sequence[str]) -> list[dict]:
    """Apply the stable eight-source cap with deterministic cross-source coverage."""
    ticket_rows = list(ticket_rows)
    document_rows = list(document_rows)
    external_rows = list(external_rows)
    if duplicate_key or target_keys:
        ticket_rows.sort(key=lambda row: (
            0 if row.get("key") == duplicate_key else
            1 if row.get("key") in target_keys else 2
        ))
    groups = ((ticket_rows, 5), (document_rows, 2), (external_rows, 1))
    if research_focus:
        return _research_bounded(tuple(group for group, _cap in groups), duplicate_key,
                                 research_terms)

    evidence = []
    for group, cap in groups:
        evidence.extend(group[:cap])
    if len(evidence) < 8:
        for group, cap in groups:
            for row in group[cap:]:
                evidence.append(row)
                if len(evidence) == 8:
                    break
            if len(evidence) == 8:
                break
    return evidence


def _situation(*, duplicate_key: str, duplicate: dict, ticket_count: int,
               document_count: int, external_count: int, action: str) -> str:
    action_label = _ACTION_LABELS[action]
    if duplicate_key:
        return (
            f'{duplicate_key} "{duplicate.get("title") or duplicate_key}"가 요청의 핵심 대상·행동, '
            "issue type과 일치하고 Done/Closed가 아닌 기존 티켓으로 확인됐다. "
            "새 중복 티켓을 만들기 전에 이 티켓을 우선한다."
        )
    if ticket_count + document_count + external_count:
        if action == "query":
            return (
                "설정된 검색 범위와 페이지 조건에 따라 QueryPlan 조회를 완료했고 "
                f"티켓 {ticket_count}건, 문서 {document_count}건, 외부 자료 "
                f"{external_count}건을 원본 출처와 함께 전달한다. 각 행은 조회된 관찰이며 "
                "검색 일치만으로 별도 결론이나 현재 상태를 추론하지 않았다."
            )
        return (
            "설정된 검색 범위와 페이지 조건에 따라 QueryPlan 조회를 완료했고 "
            f"티켓 {ticket_count}건, 문서 {document_count}건, 외부 자료 "
            f"{external_count}건을 원본 출처와 함께 전달한다. 이 항목들은 {action_label} "
            "초안의 근거 장부이며, 검색 일치만으로 새 사실을 추론하지 않았다."
        )
    if action == "create":
        return (
            "현재 설정된 검색 범위와 페이지 조건에서 요청과 직접 중복되는 사내 티켓·문서를 "
            "확인하지 못했다. 신규 초안으로 진행한다."
        )
    if action == "query":
        return (
            "설정된 검색 범위와 페이지 조건의 QueryPlan은 완료됐지만 전달할 원본 행은 "
            "확인되지 않았다. 이 결과를 범위 밖 자료의 부재로 확대 해석하지 않는다."
        )
    return (
        f"설정된 검색 범위에서 {action_label} 대상의 검증된 원본을 확보하지 못했다. "
        "완료된 조회로 간주하지 않고 기존 안전 조사 경로를 유지한다."
    )


def build_completed_write_ledger(query_results: Sequence[dict], *,
                                 materialized_ticket_details: Sequence[dict],
                                 target_keys: set[str], duplicate_key: str,
                                 action: str, research_focus: bool = False,
                                 research_terms: Sequence[str] = ()) -> dict:
    """Build the stable completed-write return contract from already acquired artifacts."""
    rows = bind_materialized_ticket_sources(query_results, materialized_ticket_details)
    tickets = index_ticket_sources(
        rows, target_keys=target_keys, duplicate_key=duplicate_key, action=action,
    )
    documents = index_document_sources(rows, action=action)
    external = index_external_sources(rows, action=action)
    evidence = select_bounded_sources(
        tickets.rows, documents.rows, external.rows,
        duplicate_key=duplicate_key, target_keys=target_keys,
        research_focus=research_focus, research_terms=research_terms,
    )
    duplicate = next((row for row in tickets.rows if row.get("key") == duplicate_key), {})
    return {
        "situation": _situation(
            duplicate_key=duplicate_key,
            duplicate=duplicate,
            ticket_count=len(tickets.ordered_keys),
            document_count=len(documents.rows),
            external_count=external.material_count,
            action=action,
        ),
        "evidence": evidence,
        "related_docs": documents.related_docs,
        "epic_candidate": "",
        "already_exists": bool(duplicate_key),
        "_deterministic_passthrough": True,
    }


__all__ = [
    "build_completed_write_ledger",
    "bind_materialized_ticket_sources",
    "index_document_sources",
    "index_external_sources",
    "index_ticket_sources",
    "ledger_text",
    "select_bounded_sources",
]
