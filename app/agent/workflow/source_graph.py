"""Bounded source/entity graph expansion for deterministic research acquisition.

Lexical search finds source roots.  It is not an entity traversal contract: a child or
directly linked validation ticket may omit one of the root's keywords and still carry the
latest decisive observation.  This module follows only verified one-hop Jira edges, keeps
the configured-scope predicate supplied by the caller, and returns an explicitly bounded
coverage record.  It never labels a bounded traversal as complete.
"""

from __future__ import annotations

from typing import Callable, TypedDict
import re


class SourceEntityEdge(TypedDict):
    root_key: str
    target_key: str
    kind: str
    relation: str
    summary: str
    updated: str


_VALIDATION_RELATION_RE = re.compile(
    r"검증|확인|시험|테스트|호환|적합|승인|기준|"
    r"validat|verif|test|compatib|qualif|acceptance|readiness|proof|poc",
    re.I,
)


def expansion_root_keys(ticket_details: list[dict], *, preferred_keys=(),
                        max_roots: int = 2) -> list[str]:
    """Choose structural roots without turning every search hit into a graph crawl."""
    rows = [row for row in ticket_details if isinstance(row, dict) and not row.get("error")]
    verified = {
        str(row.get("key") or "").strip().upper() for row in rows
        if str(row.get("key") or "").strip()
    }
    roots = [
        str(key or "").strip().upper() for key in preferred_keys
        if str(key or "").strip().upper() in verified
    ]
    roots += [
        str(row.get("key") or "").strip().upper()
        for row in rows
        if str(row.get("type") or row.get("issuetype") or "").strip().casefold() == "epic"
    ]
    # A single exact materialized ticket can itself own Sub-Tasks or direct validation links.
    if not roots and len(rows) == 1:
        roots = [str(rows[0].get("key") or "").strip().upper()]
    return [key for key in dict.fromkeys(roots) if key][:max(0, int(max_roots or 0))]


def collect_one_hop_edges(
        client, root_keys: list[str], *, allowed_key: Callable[[str], bool],
        scan_cap_per_root: int = 24) -> tuple[list[SourceEntityEdge], int]:
    """Collect direct children and actual issue links, never lexical mentions or siblings."""
    edges: list[SourceEntityEdge] = []
    seen: set[tuple[str, str]] = set()
    neighbor_reads = 0
    # ``ticket_related`` is itself a bounded provider contract with a 20-row default.
    # Keep the shared expansion at or below that limit instead of silently widening it.
    per_root = max(1, min(int(scan_cap_per_root or 24), 20))

    def add(root: str, row, kind: str) -> None:
        if not isinstance(row, dict):
            return
        target = str(row.get("key") or "").strip().upper()
        if not target or target == root or not allowed_key(target):
            return
        identity = (root, target)
        if identity in seen:
            return
        seen.add(identity)
        edges.append(SourceEntityEdge(
            root_key=root,
            target_key=target,
            kind=kind,
            relation=str(row.get("rel") or kind).strip()[:120],
            summary=str(row.get("summary") or row.get("title") or "").strip()[:300],
            updated=str(row.get("updated") or "").strip()[:80],
        ))

    for root in root_keys[:2]:
        try:
            neighbor_reads += 1
            for row in list(client.ticket_children(root) or [])[:per_root]:
                add(root, row, "child")
        except Exception:
            pass
        try:
            neighbor_reads += 1
            for row in list(client.ticket_related(root, limit=per_root) or [])[:per_root]:
                # ``ticket_related`` also exposes body/comment mentions.  Only a Jira issue
                # link is a typed graph edge suitable for deterministic expansion.
                if str(row.get("via") or "").strip().casefold() == "link":
                    add(root, row, "link")
        except Exception:
            pass
    return edges, neighbor_reads


def select_validation_edges(
        edges: list[SourceEntityEdge], focus_terms, *, excluded_keys=(), cap: int = 2,
        ) -> tuple[list[SourceEntityEdge], int]:
    """Select relevant validation entities by relation plus request anchors.

    One exact request anchor is sufficient only on an actual child/link edge carrying a
    validation relation.  This recovers keyword-AND misses while an unrelated newer child
    with no subject overlap remains excluded.  Recency breaks equal-overlap ties.
    """
    terms = [str(term or "").strip().casefold() for term in focus_terms if str(term or "").strip()]
    excluded = {str(key or "").strip().upper() for key in excluded_keys}
    eligible: list[tuple[int, str, int, SourceEntityEdge]] = []
    for order, edge in enumerate(edges):
        if edge["target_key"] in excluded:
            continue
        material = " ".join((edge["summary"], edge["relation"])).casefold()
        overlap = sum(1 for term in terms if term in material)
        validation = bool(_VALIDATION_RELATION_RE.search(material))
        if overlap < 1 or not validation:
            continue
        eligible.append((overlap, edge["updated"], -order, edge))
    # Every eligible row already has a typed edge, validation relation, and subject anchor.
    # Within that safe set, the latest observation wins before extra lexical overlap.
    eligible.sort(key=lambda item: (item[1], item[0], item[2]), reverse=True)
    bounded = max(0, int(cap or 0))
    return [item[3] for item in eligible[:bounded]], len(eligible)


def bounded_entity_expansion(
        client, ticket_details: list[dict], focus_terms, *,
        allowed_key: Callable[[str], bool], excluded_keys=(), preferred_root_keys=(),
        cap: int = 2,
        ) -> tuple[list[str], dict]:
    """Return selected exact-read keys and an honest bounded coverage ledger."""
    roots = expansion_root_keys(ticket_details, preferred_keys=preferred_root_keys)
    edges, neighbor_reads = collect_one_hop_edges(
        client, roots, allowed_key=allowed_key,
    ) if roots else ([], 0)
    selected, eligible_count = select_validation_edges(
        edges, focus_terms, excluded_keys=excluded_keys, cap=cap,
    )
    keys = [edge["target_key"] for edge in selected]
    coverage = {
        "mode": "bounded_one_hop",
        "rootKeys": roots,
        "scannedCandidates": len(edges),
        "eligibleCandidates": eligible_count,
        "selectedKeys": keys,
        "cap": max(0, int(cap or 0)),
        "truncated": eligible_count > len(keys),
        # The underlying Jira helpers are intentionally capped and do not return a total.
        # Source-class pagination can be complete while this entity traversal is not.
        "complete": False,
        "callBudget": {
            "root_neighbor_reads": neighbor_reads,
            "expanded_detail_reads": len(keys),
        },
    }
    return keys, coverage


__all__ = [
    "SourceEntityEdge", "bounded_entity_expansion", "collect_one_hop_edges",
    "expansion_root_keys", "select_validation_edges",
]
