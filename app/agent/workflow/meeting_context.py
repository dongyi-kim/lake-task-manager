"""Deterministic meeting-note ambiguity handling.

Meeting notes commonly contain Jira mentions, a partial Korean name plus an honorific, and
locally coined acronyms.  Those are identity and business-definition questions, not good
places for an LLM to guess.  This module lets the normal retrieval pass run first, then turns
only the still-unresolved values into one compact interview.
"""

from __future__ import annotations

import hashlib
import re
from typing import Mapping, Sequence, TypedDict

from app.agent.workflow.state import last_user_text, request_text


_TITLE = r"(?:TL|PL|PM|PO|EM|M|파트장|그룹장|본부장|팀장|실장|부장|차장|과장|대리|선임|책임|수석|매니저|리더|님|씨)"
_GENERIC_TOPIC_NOISE = {
    "DL", "HTML", "HTTP", "HTTPS", "JIRA", "JSON", "LAKE", "LTM", "POC",
    "TL", "PL", "PM", "PO", "EM", "UI", "URL", "UX",
}
_MEETING_RE = re.compile(r"회의(?:록|\s*기록|\s*메모|\s*결정|\s*후속|\s*내용|\s*요약)?|실무회의|미팅", re.I)
_PERSON_LABEL_NOISE = {
    "사용자", "메모", "사용자메모", "결정", "결정메모", "정리", "정리메모", "첨부", "첨부문서",
    "본문", "제목", "배경", "작업범위", "완료조건", "담당", "기한", "요청", "회의", "회의록",
}
_PERSON_LABEL_NOISE.update({"참석", "참석자"})
_MEETING_TOPIC_NOISE = {
    "comment", "component", "confluence", "description", "docx", "document", "due", "epic", "external", "fields",
    "from", "jira", "labels", "meeting", "memo", "notes", "official", "optimizer", "priority", "reader",
    "summary", "task", "ticket", "writer",
}
NO_MEETING_ASSIGNMENT_REF = "meeting-source:none"


class MeetingAssignmentBinding(TypedDict):
    """One source-backed assignment bound to a stable authored work item."""

    item_id: str
    owner_id: str
    due: str
    source_evidence: dict[str, str]


_ASSIGNMENT_EVIDENCE_KEYS = (
    "kind", "record_id", "source_id", "item_id", "outcome_ref", "outcome_id",
    "meeting_assignment_ref", "owner_label", "owner_decision", "work",
)


def _bounded_assignment_evidence(record: Mapping[str, object]) -> dict[str, str]:
    raw = record.get("source_evidence")
    source = raw if isinstance(raw, Mapping) else {}
    evidence = {
        key: str(source.get(key) or record.get(key) or "").strip()
        for key in _ASSIGNMENT_EVIDENCE_KEYS
        if str(source.get(key) or record.get(key) or "").strip()
    }
    evidence.setdefault("kind", "meeting_minutes")
    evidence.setdefault("work", str(record.get("work") or "").strip())
    evidence.setdefault(
        "owner_decision", str(record.get("owner_decision") or "").strip(),
    )
    return {key: value[:240] for key, value in evidence.items() if value}


def _assignment_refs(row: Mapping[str, object]) -> set[str]:
    """Return only explicit stable outcome references carried by a typed row."""
    raw_refs = row.get("outcome_refs")
    refs = {
        str(value or "").strip()
        for value in (raw_refs if isinstance(raw_refs, Sequence)
                      and not isinstance(raw_refs, (str, bytes)) else ())
        if str(value or "").strip()
    }
    refs.update(
        str(row.get(key) or "").strip()
        for key in ("outcome_ref", "outcome_id")
        if str(row.get(key) or "").strip()
    )
    evidence = row.get("source_evidence")
    if isinstance(evidence, Mapping):
        evidence_refs = evidence.get("outcome_refs")
        if (isinstance(evidence_refs, Sequence)
                and not isinstance(evidence_refs, (str, bytes))):
            refs.update(
                str(value or "").strip() for value in evidence_refs
                if str(value or "").strip()
            )
        refs.update(
            str(evidence.get(key) or "").strip()
            for key in ("outcome_ref", "outcome_id")
            if str(evidence.get(key) or "").strip()
        )
    return refs


def _copied_meeting_assignment_ref(row: Mapping[str, object]) -> str:
    """Return only the dedicated Work wire identity, never a generic evidence id."""
    direct = str(row.get("meeting_assignment_ref") or "").strip()
    evidence = row.get("source_evidence")
    return direct or (str(evidence.get("meeting_assignment_ref") or "").strip()
                      if isinstance(evidence, Mapping) else "")


def _meeting_assignment_record_id(record: Mapping[str, object]) -> str:
    """Derive an order-independent opaque identity for one canonical source record."""
    evidence = record.get("source_evidence")
    source = evidence if isinstance(evidence, Mapping) else {}
    existing = next((str(source.get(key) or record.get(key) or "").strip()
                     for key in ("record_id", "source_id", "meeting_assignment_ref")
                     if str(source.get(key) or record.get(key) or "").strip()), "")
    if existing:
        return existing
    material = "\0".join(
        str(record.get(key) or "").strip()
        for key in ("owner", "work", "due", "owner_decision")
    )
    return "meeting-source:" + hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]


def meeting_assignment_source_catalog(state) -> list[dict[str, object]]:
    """Expose bounded copy-only source identities to the meeting Work projector."""
    catalog: list[dict[str, object]] = []
    for record in meeting_owner_records(state):
        record_id = _meeting_assignment_record_id(record)
        if not record_id:
            continue
        row: dict[str, object] = {
            "record_id": record_id,
            "work": str(record.get("work") or "")[:240],
            "owner_decision": str(record.get("owner_decision") or "")[:24],
            "owner_id": str(record.get("owner") or "")[:120],
            "due": str(record.get("due") or "")[:20],
        }
        outcome_refs = sorted(_assignment_refs(record))[:6]
        if outcome_refs:
            row["outcome_refs"] = outcome_refs
        catalog.append(row)
    return catalog


def _meeting_identity_issue(index: int, expected: str, actual: str,
                            message: str) -> dict:
    return {"index": index, "field": "meeting_assignment_ref",
            "source": "meeting_assignment", "expected": expected,
            "actual": actual, "message": message}


def _assignment_item_id(row: Mapping[str, object]) -> str:
    """Return an explicit source item identity, including bounded evidence envelopes."""
    item_id = str(row.get("item_id") or "").strip()
    evidence = row.get("source_evidence")
    if not item_id and isinstance(evidence, Mapping):
        item_id = str(evidence.get("item_id") or "").strip()
    return item_id


def _commit_unique_assignment_matches(
        proposals: Mapping[int, Sequence[int]], chosen: dict[int, int],
        used_records: set[int]) -> bool:
    """Commit only one-to-one proposals; ambiguous identities remain unbound."""
    singletons = {
        item_index: candidates[0]
        for item_index, candidates in proposals.items()
        if len(candidates) == 1 and item_index not in chosen
        and candidates[0] not in used_records
    }
    record_counts: dict[int, int] = {}
    for record_index in singletons.values():
        record_counts[record_index] = record_counts.get(record_index, 0) + 1
    committed = False
    for item_index, record_index in singletons.items():
        if record_counts[record_index] != 1:
            continue
        chosen[item_index] = record_index
        used_records.add(record_index)
        committed = True
    return committed


def meeting_assignment_bindings(
        items: Sequence[Mapping[str, object]],
        source_records: Sequence[Mapping[str, object]],
        canonical_people: Mapping[str, str]) -> list[MeetingAssignmentBinding]:
    """Bind canonical meeting assignments to stable item ids without product knowledge.

    The function is deliberately pure: callers provide authored items, source-derived
    assignment records, and the already-resolved display-name-to-account map.  Exact source
    item ids win, followed by an exact outcome reference and an explicit meeting source
    record id.  A one-item/one-record legacy envelope is structurally unambiguous.  Every
    multi-item relation without one of those identities remains unbound: mutable title,
    assignee, deadline, and collection order never choose a source record.  Authored
    assignee and deadline values remain observable verification values for the Auditor.
    """
    canonical_ids: dict[str, str] = {}
    for label, raw_id in canonical_people.items():
        owner_id = str(raw_id or "").strip()
        if not owner_id:
            continue
        canonical_ids[owner_id.casefold()] = owner_id
        if str(label or "").strip():
            canonical_ids[str(label).strip().casefold()] = owner_id
    records = [record for record in source_records if isinstance(record, Mapping)]
    eligible = [
        (index, item)
        for index, item in enumerate(items)
        if isinstance(item, Mapping)
        and str(item.get("assignee_source") or "").strip()
        in {"user", "user_unassigned"}
        and str(item.get("item_id") or "").strip()
    ]
    eligible_indexes = {index for index, _item in eligible}
    chosen: dict[int, int] = {}
    used_records: set[int] = set()
    if any(_copied_meeting_assignment_ref(row) for row in items
           if isinstance(row, Mapping)):
        # Reuse the Work/Auditor wire resolver; do not maintain a second source-id matcher.
        source_mapping, _issues = meeting_assignment_source_mapping(items, records)
        chosen.update({index: record_index for index, record_index in source_mapping.items()
                       if index in eligible_indexes})
        used_records.update(chosen.values())
    else:
        # Legacy typed envelopes predate the dedicated Work wire. Exact item/outcome ids
        # remain valid; prose and scalar owner/due fields never participate.
        for proposals in (
            {item_index: [record_index for record_index, record in enumerate(records)
                          if _assignment_item_id(record)
                          == str(item.get("item_id") or "").strip()]
             for item_index, item in eligible},
            {item_index: [record_index for record_index, record in enumerate(records)
                          if record_index not in used_records
                          and _assignment_refs(item)
                          and _assignment_refs(item) == _assignment_refs(record)]
             for item_index, item in eligible if item_index not in chosen},
        ):
            _commit_unique_assignment_matches(proposals, chosen, used_records)
        remaining_items = [index for index, _item in eligible if index not in chosen]
        remaining_records = [index for index in range(len(records))
                             if index not in used_records]
        if len(remaining_items) == len(remaining_records) == 1:
            chosen[remaining_items[0]] = remaining_records[0]

    bindings: list[MeetingAssignmentBinding] = []
    for item_index, item in eligible:
        record_index = chosen.get(item_index)
        if record_index is None:
            continue
        record = records[record_index]
        item_id = str(item.get("item_id") or "").strip()
        decision = str(record.get("owner_decision") or "").strip()
        raw_owner = str(record.get("owner") or "").strip()
        if decision == "unassigned":
            owner_id = ""
        elif decision == "assigned" and raw_owner.casefold() in canonical_ids:
            owner_id = canonical_ids[raw_owner.casefold()]
        else:
            continue
        bindings.append({
            "item_id": item_id,
            "owner_id": owner_id,
            "due": str(record.get("due") or "").strip(),
            "source_evidence": _bounded_assignment_evidence(record),
        })
    return bindings


def meeting_assignment_source_mapping(
        items: Sequence[Mapping[str, object]],
        source_records: Sequence[Mapping[str, object]]) -> tuple[dict[int, int], list[dict]]:
    """Resolve source ids only when a second typed identity proves the sibling relation."""
    roots = [row for row in items if isinstance(row, Mapping)]
    records = [row for row in source_records if isinstance(row, Mapping)]
    if not roots or not records:
        return {}, []
    refs = [_copied_meeting_assignment_ref(row) for row in roots]
    record_ids = [_meeting_assignment_record_id(row) for row in records]
    if len(roots) == len(records) == 1:
        if not refs[0] or refs[0] == record_ids[0]:
            return {0: 0}, []

    candidates: dict[int, list[int]] = {
        item_index: [
            record_index for record_index, record_id in enumerate(record_ids)
            if ref and ref == record_id
        ]
        for item_index, ref in enumerate(refs)
        if ref != NO_MEETING_ASSIGNMENT_REF
    }
    selected = {index: matches[0] for index, matches in candidates.items()
                if len(matches) == 1}
    record_counts = {record_index: list(selected.values()).count(record_index)
                     for record_index in selected.values()}
    item_conflicts = {
        item_index for item_index, record_index in selected.items()
        if _assignment_item_id(roots[item_index])
        and _assignment_item_id(records[record_index])
        and _assignment_item_id(roots[item_index])
        != _assignment_item_id(records[record_index])
    }
    outcome_conflicts = {
        item_index for item_index, record_index in selected.items()
        if _assignment_refs(roots[item_index]) and _assignment_refs(records[record_index])
        and _assignment_refs(roots[item_index]) != _assignment_refs(records[record_index])
    }
    identity_matches = {
        item_index for item_index, record_index in selected.items()
        if (_assignment_item_id(roots[item_index])
            and _assignment_item_id(roots[item_index])
            == _assignment_item_id(records[record_index]))
        or (_assignment_refs(roots[item_index])
            and _assignment_refs(roots[item_index]) == _assignment_refs(records[record_index]))
    }
    identity_conflicts = item_conflicts | outcome_conflicts
    mapping = {
        item_index: record_index for item_index, record_index in selected.items()
        if record_counts.get(record_index) == 1
        and item_index in identity_matches and item_index not in identity_conflicts
    }

    issues: list[dict] = []
    for item_index, ref in enumerate(refs):
        if ref == NO_MEETING_ASSIGNMENT_REF:
            continue
        matches = candidates.get(item_index, [])
        if len(matches) != 1:
            issues.append(_meeting_identity_issue(
                item_index, "one explicit source record id", ref or "missing",
                "회의 source identity가 없거나 알 수 없어 담당자·기한을 연결할 수 없다"))
        elif record_counts.get(matches[0]) != 1:
            issues.append(_meeting_identity_issue(
                item_index, "unique source record id", ref,
                "같은 회의 source identity를 여러 sibling이 재사용했다"))
        elif item_index in identity_conflicts:
            issues.append(_meeting_identity_issue(
                item_index, "source record with the same explicit item/outcome identity", ref,
                "meeting source ref와 root item/outcome identity가 서로 모순된다"))
        elif item_index not in identity_matches:
            issues.append(_meeting_identity_issue(
                item_index, "source record verified by typed outcome/item identity", ref,
                "meeting source ref의 sibling 관계를 검증할 typed outcome/item identity가 없다"))
    expected_none = max(0, len(roots) - len(records))
    none_indexes = [index for index, ref in enumerate(refs)
                    if ref == NO_MEETING_ASSIGNMENT_REF]
    if len(none_indexes) > expected_none:
        issues.extend(_meeting_identity_issue(
            index, f"exactly {expected_none} no-source roots", NO_MEETING_ASSIGNMENT_REF,
            "회의 source record가 있는 root를 no-source로 표시할 수 없다")
            for index in none_indexes)
    used_once = set(mapping.values())
    if len(used_once) != len(records):
        issues.append(_meeting_identity_issue(
            -1, f"{len(records)} source records bound exactly once",
            f"{len(used_once)} uniquely bound",
            "회의 source records와 최종 sibling roots가 일대일 대응하지 않는다"))
    return mapping, issues


def meeting_request_text(state) -> str:
    """Return the original meeting request across a research-interview continuation turn."""
    current = request_text(state)
    latest = last_user_text(state)
    human = [str(getattr(message, "content", "") or "").strip()
             for message in (state.get("messages") or [])
             if getattr(message, "type", "") == "human"]
    prior = [value for value in human[:-1] if _MEETING_RE.search(value)]
    if state.get("turn_continuation") and prior:
        return prior[-1]
    direct = [value for value in (current, latest) if _MEETING_RE.search(value)]
    if direct:
        return max(direct, key=len)
    return current


def is_meeting_request(state) -> bool:
    text = f"{meeting_request_text(state)} {last_user_text(state)}"
    return bool(_MEETING_RE.search(text))


def meeting_subject(state) -> str:
    """Extract the stable technical subject from a titled meeting note when present."""
    original = meeting_request_text(state)
    for line in original.splitlines():
        value = re.sub(r"^\s*#{1,6}\s*", "", line).strip()
        value = re.sub(r"^\d{4}[-./]\d{1,2}[-./]\d{1,2}\s+", "", value)
        match = re.match(
            r"([A-Za-z][A-Za-z0-9.+-]*(?:\s+[A-Za-z][A-Za-z0-9.+-]*){1,5})"
            r"\s+(?:도입\s*)?(?:실무)?회의(?:록)?(?:\s|$)",
            value,
        )
        if match:
            return match.group(1).strip()
    # Informal minutes often have no title and may contain an attachment filename.  A file
    # name is transport metadata, not the meeting topic: using ``notes.docx`` as a search
    # subject previously pulled an unrelated attachment UI fixture.  Keep only public-looking
    # technology terms in their original spelling and preserve their first-seen order.
    material = re.sub(r"(?i)\b[^\s`/\\]+\.(?:docx?|pdf|txt|md|xlsx?|pptx?)\b", " ", original)
    material = re.sub(
        r"(?<![A-Z0-9-])[A-Z][A-Z0-9]*-\d+(?!\d)|\bskcc\.[a-z]\d+\b",
        " ", material, flags=re.I,
    )
    terms: list[str] = []
    for token in re.findall(r"[A-Za-z][A-Za-z0-9.+-]{2,}", material):
        low = token.lower().strip(".+-")
        if low in _MEETING_TOPIC_NOISE or low in {
                value.lower() for value in _GENERIC_TOPIC_NOISE}:
            continue
        if token not in terms:
            terms.append(token)
    return " ".join(terms[:3])


def _person_tokens(text: str) -> list[str]:
    found: list[str] = []
    located: list[tuple[int, str]] = []

    def add(value: str) -> None:
        value = str(value or "").strip()
        if value and value not in found:
            found.append(value)

    for match in re.finditer(r"\{\{\s*([가-힣A-Za-z]{1,20})\s*:\s*(\d+)\s*\}\}", text):
        located.append((match.start(), match.group(1)))
    for match in re.finditer(r"(?<![\w@])@([가-힣]{2,5})(?![\w])", text):
        located.append((match.start(), match.group(1)))
    for match in re.finditer(
            rf"(?<![가-힣])([가-힣]{{2,5}})\s*{_TITLE}(?=\s|의|은|는|이|가|을|를|:|[,.;!?)]|$)", text):
        located.append((match.start(), match.group(1)))
    attribution_patterns = (
        rf"(?im)^\s*from\s*[:：]\s*(?:@|\{{\{{)?([가-힣]{{1,5}})(?::\d+\}}\}})?\s*{_TITLE}?",
        rf"(?i)\bby\s+(?:@|\{{\{{)?([가-힣]{{1,5}})(?::\d+\}}\}})?\s*{_TITLE}?",
        rf"(?m)^\s*(?:[-*]\s*)?(?:@|\{{\{{)?([가-힣]{{2,5}})(?::\d+\}}\}})?\s*{_TITLE}?\s*[:：]",
        rf"(?<![가-힣])([가-힣]{{1,5}}?)\s*{_TITLE}?\s*(?:의\s*)?의견(?=\s|[:：—–-]|$)",
    )
    for pattern in attribution_patterns:
        for match in re.finditer(pattern, text):
            located.append((match.start(), match.group(1)))
    for _position, name in sorted(located):
        if re.sub(r"\s+", "", name) not in _PERSON_LABEL_NOISE:
            add(name)
    return found


def _person_requires_resolution(text: str, name: str) -> bool:
    """Return false when every occurrence is an explicitly rejected, irrelevant opinion."""
    lines = [line for line in str(text or "").splitlines() if name in line]
    if not lines:
        return True
    rejected = re.compile(
        r"(?:의견|제안|안건).{0,100}(?:보류|채택하지|결론\s*안|결정하지|반영하지|제외|필요\s*없)|"
        r"(?:신원|누구인지).{0,40}(?:필요\s*없|무관)", re.I,
    )
    return not all(rejected.search(line) for line in lines)


def _explicit_user_bindings(latest: str, names: list[str]) -> dict[str, str]:
    """Return identities the user explicitly supplied in the latest interview answer."""
    ids = re.findall(r"\bskcc\.[a-z]\d{2,8}\b", latest, re.I)
    bound: dict[str, str] = {}
    for name in names:
        # ``준서TL은 skcc.x1103`` and ``skcc.x1103 이준서`` are both common.
        before = re.search(
            rf"{re.escape(name)}\s*{_TITLE}?\s*(?:은|는|이|가)?\s*[:=]?[\s,]*(skcc\.[a-z]\d{{2,8}})",
            latest, re.I,
        )
        if before:
            bound[name] = before.group(1)
            continue
        after = next((uid for uid in ids if re.search(
            rf"{re.escape(uid)}\s*[,(]?\s*[가-힣]*{re.escape(name)}(?:\s|이고|이며|님|$)", latest,
            re.I,
        )), "")
        if after:
            bound[name] = after
    # An answer to one unresolved-person question may give only ``skcc.xNNNN FullName``.
    if len(names) == 1 and len(ids) == 1:
        bound.setdefault(names[0], ids[0])
    # Keep the full display spelling from an interview answer as an alias too.  The meeting
    # note may say only ``준서TL`` while the answer says ``skcc.x1103 이준서``.  Without this
    # alias the final prose can leak ``이준서(skcc.x1103)`` instead of one mention badge.
    for uid in ids:
        after = re.search(
            rf"{re.escape(uid)}\s*[,(/]?\s*([가-힣]{{2,5}}?)(?:이고|이며|님)?(?=\s|[),.]|$)",
            latest, re.I,
        )
        before = re.search(
            rf"(?<![가-힣])([가-힣]{{2,5}})\s*[(/]?\s*{re.escape(uid)}(?=[)\s,.]|$)",
            latest, re.I,
        )
        full_name = (after or before)
        if full_name:
            bound.setdefault(full_name.group(1), uid)
    return bound


def resolved_people(state) -> dict[str, str]:
    """Resolve every meeting person that can be resolved without guessing."""
    original, latest = meeting_request_text(state), last_user_text(state)
    names = [name for name in _person_tokens(original)
             if _person_requires_resolution(original, name)]
    bindings = _explicit_user_bindings(latest, names)
    try:
        from app.agent.tools.people_tools import find_person, recall_person, remember_person
    except Exception:
        return bindings

    for name, uid in bindings.items():
        remember_person(name, uid)
    def alias_binding(name: str) -> str:
        matches = {uid for alias, uid in bindings.items()
                   if alias == name or alias.endswith(name) or name.endswith(alias)}
        return next(iter(matches)) if len(matches) == 1 else ""

    # Resolve longer full names before partial aliases.  ``최하은`` can safely establish
    # ``하은``; the reverse order costs a second directory query and is less specific.
    for name in sorted(names, key=lambda value: (-len(value), names.index(value))):
        if name in bindings:
            continue
        cached = recall_person(name) or alias_binding(name)
        if cached:
            bindings[name] = cached
            remember_person(name, cached)
            continue
        try:
            result = find_person.invoke({"name": name}) or {}
        except Exception:
            continue
        uid = str(result.get("resolved") or "").strip()
        # Korean particles are sometimes attached directly to an @mention (``@이다은은``).
        # Preserve legitimate names ending in the same syllable; trim only after the full token has no
        # directory candidate and the trimmed token resolves.
        if not uid and not (result.get("candidates") or []) \
                and len(name) >= 3 and name[-1:] in ("은", "는", "이", "가", "을", "를"):
            trimmed = name[:-1]
            try:
                retry = find_person.invoke({"name": trimmed}) or {}
            except Exception:
                retry = {}
            retry_uid = str(retry.get("resolved") or "").strip()
            if retry_uid and not retry.get("ambiguous"):
                bindings[name] = retry_uid
                bindings[trimmed] = retry_uid
                continue
        if uid and not result.get("ambiguous"):
            bindings[name] = uid
            remember_person(name, uid)
            for alias in names:
                if alias not in bindings and (name.endswith(alias) or alias.endswith(name)):
                    bindings[alias] = uid
                    remember_person(alias, uid)
    return bindings


def _uncertain_terms(text: str) -> list[str]:
    terms: list[str] = []
    asks_ambiguity_interview = bool(re.search(
        r"모호한[^\n.]{0,40}용어|용어[^\n.]{0,60}(?:확정|물어|질문)|"
        r"(?:뜻|정의)[^\n.]{0,60}(?:조사|자료|확정|물어|질문)",
        text, re.I,
    ))
    for term in re.findall(r"(?<![A-Za-z0-9])([A-Z][A-Z0-9-]{1,9})(?![A-Za-z0-9])", text):
        # Do not maintain a product/acronym allowlist. Every candidate follows the same
        # contract: research may establish its definition via ``_term_is_defined``; only a
        # still-unresolved meeting-local meaning reaches the interview.
        if (term.isdigit() or term in terms
                or re.fullmatch(r"[A-Z][A-Z0-9]*-\d+", term)):
            continue
        nearby = re.search(
            rf"{re.escape(term)}.{{0,80}}(?:뜻|정의|기준).{{0,60}}(?:없|미정|확정되지|모르|확인)|"
            rf"{re.escape(term)}.{{0,100}}(?:자료|조사).{{0,60}}(?:확정되지|모르|확인)",
            text, re.I | re.S,
        )
        if nearby or asks_ambiguity_interview:
            terms.append(term)
    return terms


def _term_is_defined(term: str, state) -> bool:
    latest = last_user_text(state)
    original = meeting_request_text(state)
    if latest != original:
        match = re.search(rf"(?<![A-Za-z0-9]){re.escape(term)}(?![A-Za-z0-9])\s*"
                          rf"(?:은|는|:|=)\s*(.{{3,180}})", latest,
                          re.I | re.S)
        if match and not _definition_is_uncertain(match.group(1)[:100]):
            return True
    evidence = "\n".join(str(state.get(key) or "") for key in
                         ("pre_survey", "topic_dossier", "situation", "web_context"))
    match = re.search(rf"(?<![A-Za-z0-9]){re.escape(term)}(?![A-Za-z0-9])\s*"
                      rf"(?:은|는|:|=)\s*(.{{3,160}})", evidence,
                      re.I | re.S)
    return bool(match and not _definition_is_uncertain(match.group(1)[:100]))


def _definition_is_uncertain(text: str) -> bool:
    """Distinguish an unknown definition from a rule containing a negative condition."""
    return bool(re.search(
        r"확인\s*(?:이\s*)?필요|정의가?\s*필요|(?:뜻|정의)(?:을|를|이|가)?\s*모르|"
        r"알\s*수\s*없|확정되지\s*않|미정|기록(?:이|은|에는)?\s*없",
        str(text or ""), re.I,
    ))


def prune_resolved_gaps(state, gaps: list[str]) -> list[str]:
    """Remove meeting gaps that the current interview answer already resolved."""
    if not is_meeting_request(state):
        return [str(g) for g in (gaps or []) if str(g).strip()]
    defined = {term for term in _uncertain_terms(meeting_request_text(state))
               if _term_is_defined(term, state)}
    return [str(g) for g in (gaps or [])
            if str(g).strip() and not any(term in str(g) for term in defined)]


def prune_resolved_reply_gaps(state, text: str) -> str:
    """Remove only stale unresolved bullets for terms defined in the interview answer.

    Source limitations may still truthfully say that a document omitted the definition;
    this function touches only user-facing ``미결·검증``/``확인 필요`` sections.  Other
    unresolved work in the same section remains intact.
    """
    if not is_meeting_request(state):
        return str(text or "")
    defined = {term for term in _uncertain_terms(meeting_request_text(state))
               if _term_is_defined(term, state)}
    if not defined:
        return str(text or "")
    value = str(text or "")
    pattern = re.compile(
        r"(?ms)^(###\s*(?:미결[·ㆍ\s/-]*검증|확인\s*필요)\s*$\n)(.*?)(?=^###\s|\Z)")

    def clean(match: re.Match) -> str:
        kept = []
        for line in match.group(2).splitlines():
            stale = any(term in line for term in defined) and bool(re.search(
                r"뜻|정의|기록.{0,12}없|확인\s*필요|미확정|확정.{0,12}못|알\s*수\s*없",
                line, re.I,
            ))
            if not stale:
                kept.append(line)
        if not any(line.strip() for line in kept):
            return ""
        return match.group(1) + "\n".join(kept).strip() + "\n\n"

    return re.sub(r"\n{3,}", "\n\n", pattern.sub(clean, value)).strip()


def canonicalize_reply_mentions(state, text: str) -> str:
    """Convert every resolved meeting-person spelling into one canonical mention token."""
    out = str(text or "")
    people = resolved_people(state)
    if not people:
        return out

    for name in sorted(people, key=len, reverse=True):
        uid = str(people[name] or "").strip()
        alias = str(name or "").strip()
        if not uid or not alias:
            continue
        token = f"{{{{mention:{uid}}}}}"
        out = re.sub(
            rf"\{{\{{\s*mention\s*:\s*{re.escape(alias)}(?:\s*:\s*\d+)?\s*\}}\}}",
            token, out, flags=re.I,
        )
        out = re.sub(
            rf"\{{\{{\s*{re.escape(alias)}\s*:\s*\d+\s*\}}\}}",
            token, out,
        )
        person = rf"@?{re.escape(alias)}(?:\s*{_TITLE})?"
        boundary = (r"(?=(?:에게|께서|으로|은|는|이|가|을|를|의|과|와|도|만|로)?"
                    r"(?:\s|[(,.):;]|$))")
        out = re.sub(rf"(?<![가-힣A-Za-z0-9_.]){person}{boundary}", token, out)

    for uid in dict.fromkeys(str(v) for v in people.values() if str(v).strip()):
        token = re.escape(f"{{{{mention:{uid}}}}}")
        rendered = re.escape(f"[~{uid}]")
        # A confirmed raw username is just another spelling of the same person.  Do not
        # rewrite inside an existing mention token (``:uid``) or Jira mention (``~uid``).
        out = re.sub(
            rf"(?<![A-Za-z0-9_.:~]){re.escape(uid)}(?![A-Za-z0-9_.])",
            f"{{{{mention:{uid}}}}}", out, flags=re.I,
        )
        out = re.sub(rf"(?:{token}|{rendered})(?:[ \t]*(?:{token}|{rendered}))+",
                     f"{{{{mention:{uid}}}}}", out)
        out = re.sub(rf"(?:{token}|{rendered})\s*\(\s*(?:{token}|{rendered})\s*\)",
                     f"{{{{mention:{uid}}}}}", out)
        # The model can carry a pre-interview ambiguity sentence into the resumed
        # answer even after the identity is confirmed. Remove only identity-specific
        # stale warnings; uncertainty about the person's work or evidence remains.
        out = re.sub(
            rf"(?mi)^\s*[-*]?\s*(?:{token}|{rendered})[^\n]{{0,80}}"
            rf"(?:정확한\s*)?(?:신원|동명이인|누구인지)[^\n]{{0,60}}"
            rf"(?:확인\s*필요|미확정|확정[^\n]{{0,12}}못)[^.\n]*\.?\s*$\n?",
            "", out,
        )
    out = re.sub(r"(?ms)^###\s*미결[·ㆍ\s/-]*검증\s*\n\s*(?=^###\s|\Z)", "", out)
    return out


def meeting_owner_records(state) -> list[dict[str, object]]:
    """Extract explicit owner/work/deadline records from authoritative minutes.

    A deadline-bearing assignment is safe to align mechanically. Review-only
    clauses without a deadline are excluded so a reviewer never becomes owner.
    """
    original = meeting_request_text(state)
    latest = last_user_text(state)
    people = resolved_people(state)
    names = [name for name in _person_tokens(original + "\n" + latest)
             if _person_requires_resolution(original + "\n" + latest, name)]
    records: list[dict[str, object]] = []

    def clean_work(segment: str, person_end: int | None = None) -> str:
        value = segment
        by = re.search(r"\s+by\s+(?:@|\{\{)?[가-힣]{1,5}", value, re.I)
        if by:
            value = value[:by.start()]
        elif person_end is not None:
            value = value[person_end:]
        value = re.split(r"(?<!\d)20\d{2}-\d{2}-\d{2}(?!\d)", value, maxsplit=1)[0]
        value = re.split(r"\s+[—–-]\s+담당|(?:은|는)?\s*(?:아직\s*)?담당(?:자|은|는|이|가)?", value, maxsplit=1)[0]
        value = re.sub(r"^\s*(?:은|는|이|가|을|를|의\s*의견)?\s*[:：—–-]?\s*", "", value)
        value = re.sub(r"\s*(?:까지|제가\s*맡음|제가\s*맡겠습니다|담당)$", "", value)
        return value.strip(" `.,:：-—–")

    sources = [original]
    if state.get("turn_continuation") and latest and latest != original:
        sources.append(latest)
    for raw in "\n".join(sources).splitlines():
        line = re.sub(r"^\s*(?:[-*]|\d+[.)])\s*", "", raw).strip()
        heading = re.search(r"담당[·ㆍ\s-]*기한\s*[:：]\s*", line)
        owner_heading = bool(heading)
        if heading:
            line = line[heading.end():]
        # Split a dense assignment line only when the next clause starts with another
        # person.  ``work by 하은님, 2026-08-25`` must stay one clause.
        segments = re.split(
            rf"\s*[,;]\s*(?=(?:@|\{{\{{)?[가-힣]{{1,5}}(?::\d+\}}\}})?\s*{_TITLE}?(?:은|는|이|가|\s*[—–:-]))",
            line,
        )
        for segment in segments:
            due = re.search(r"(?<!\d)(20\d{2}-\d{2}-\d{2})(?!\d)", segment)
            dash_owner = bool(re.search(r"\s[—–-]\s", segment))
            explicitly_unassigned = bool(re.search(
                r"미할당|담당(?:자|은|는|이|가)?.{0,24}(?:정하지\s*못|미정|없음|정해지지\s*않)",
                segment, re.I,
            ))
            if explicitly_unassigned:
                # Policy prose such as "담당이 정해지지 않은 일은 미할당으로" is not an
                # assignment row.  Require a concrete phrase before an ownership separator.
                if (not re.search(r"\s[—–-]\s", segment)
                        and re.search(r"담당.{0,30}(?:일|작업)(?:은|는)", segment)):
                    continue
                work = clean_work(segment)
                if work:
                    records.append({"owner": "", "work": work,
                                    "due": due.group(1) if due else "",
                                    "owner_decision": "unassigned"})
                continue
            if not (due or owner_heading or dash_owner or "담당" in segment or re.search(r"\bby\b", segment, re.I)):
                continue
            hits = []
            for name in names:
                patterns = (
                    rf"@\s*{re.escape(name)}",
                    rf"\{{\{{\s*{re.escape(name)}\s*:\s*\d+\s*\}}\}}",
                    rf"(?<![가-힣]){re.escape(name)}\s*{_TITLE}?",
                )
                hit = next((re.search(pattern, segment, re.I) for pattern in patterns
                            if re.search(pattern, segment, re.I)), None)
                uid = str(people.get(name) or "").strip()
                if hit and uid:
                    hits.append((hit.start(), -len(name), hit, uid))
            if not hits:
                continue
            _start, _length, hit, uid = sorted(hits)[0]
            before = segment[:hit.start()]
            after = segment[hit.end():]
            by_owner = bool(re.search(r"\bby\s*$", before, re.I) and due)
            clause_prefix = re.sub(
                r"^.*?(?:회의(?:록|\s*기록|\s*메모)?\s*[:：]|from\s*[:：])\s*",
                "", before, flags=re.I,
            )
            starts_clause = not clause_prefix.strip(" @{{")
            explicit_language = bool(re.search(r"제가\s*맡|\b담당\b|담당자", segment, re.I))
            if not (owner_heading or by_owner or (starts_clause and (due or dash_owner or explicit_language))):
                continue
            work = clean_work(segment, None if by_owner else hit.end())
            if not work or (re.search(r"리뷰|검토", work) and not due and not dash_owner):
                continue
            records.append({"owner": uid, "work": work,
                            "due": due.group(1) if due else "",
                            "owner_decision": "assigned"})
    # A continuation answer may decide only the missing owner (``미할당으로``) while
    # the unfinished original line carries the deadline and fuller deliverable spelling.
    # Merge those two fragments by work-term overlap instead of asking for the date again.
    original_due_rows: list[tuple[str, str]] = []
    for raw in original.splitlines():
        due = re.search(r"(?<!\d)(20\d{2}-\d{2}-\d{2})(?!\d)", raw)
        if not due:
            continue
        work = clean_work(re.sub(r"^\s*(?:[-*]|\d+[.)])\s*", "", raw).strip())
        if work:
            original_due_rows.append((work, due.group(1)))
    for row in records:
        if row.get("due"):
            continue
        terms = {term.casefold() for term in re.findall(r"[가-힣A-Za-z0-9_.-]{2,}", row["work"])}
        ranked = sorted(
            ((len(terms & {term.casefold() for term in re.findall(
                r"[가-힣A-Za-z0-9_.-]{2,}", work)}), len(work), work, due)
             for work, due in original_due_rows),
            reverse=True,
        )
        if ranked and ranked[0][0] >= max(1, min(2, len(terms))):
            _score, _length, work, due = ranked[0]
            row["due"] = due
            if len(work) > len(row["work"]):
                row["work"] = work
    # Repeated full-name aliases in an interview can point to the same source clause.
    unique = []
    seen = set()
    for row in records:
        key = (row["owner"], row["work"], row["due"], row.get("owner_decision", ""))
        if key not in seen:
            seen.add(key)
            record_id = _meeting_assignment_record_id(row)
            row["source_evidence"] = {
                "kind": "meeting_minutes", "record_id": record_id,
            }
            unique.append(row)
    return unique


def canonicalize_meeting_owner_table(state, text: str) -> str:
    """Align Markdown owner cells with explicit meeting assignments."""
    records = meeting_owner_records(state)
    if not records:
        return str(text or "")
    lines = str(text or "").splitlines()
    columns: dict[str, int] | None = None
    for index, line in enumerate(lines):
        if not re.match(r"^\s*\|.*\|\s*$", line):
            columns = None
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) < 3:
            continue
        if all(re.fullmatch(r":?-{3,}:?", cell) for cell in cells):
            continue
        owner_index = next((pos for pos, cell in enumerate(cells)
                            if re.search(r"담당|assignee|owner", cell, re.I)), -1)
        if owner_index >= 0:
            task_index = next((pos for pos, cell in enumerate(cells)
                               if re.search(r"작업|task|제목|summary|티켓", cell, re.I)
                               and pos != owner_index), 0)
            due_index = next((pos for pos, cell in enumerate(cells)
                              if re.search(r"기한|due|마감", cell, re.I)), -1)
            columns = {"task": task_index, "owner": owner_index, "due": due_index}
            continue
        if columns is None or max(columns.values()) >= len(cells):
            continue
        task = cells[columns["task"]]
        due = cells[columns["due"]] if columns["due"] >= 0 else ""
        if not re.fullmatch(r"20\d{2}-\d{2}-\d{2}", due):
            due = ""
        candidates = [row for row in records if due and row["due"] == due]
        # This legacy Markdown surface carries no item/outcome/source id. Never recover an
        # owner by title-token overlap (or table order): equal sibling summaries made that
        # silently choose record zero. Source-bound approval paths must project typed ids;
        # an identity-free row remains fail-closed unless its source deadline is unique.
        if len(candidates) != 1:
            # An owner row without one explicit assignment changes accountability. Drop it
            # instead of preserving a model-invented reviewer/requester assignment.
            lines[index] = ""
            continue
        owner = candidates[0]["owner"]
        cells[columns["owner"]] = f"{{{{mention:{owner}}}}}" if owner else "미할당"
        lines[index] = "| " + " | ".join(cells) + " |"
    return "\n".join(lines)


def attendee_mentions(state) -> list[str]:
    """Return unique resolved meeting attendees in their original note order."""
    people = resolved_people(state)
    out: list[str] = []
    for name in _person_tokens(meeting_request_text(state)):
        uid = str(people.get(name) or "").strip()
        if uid and uid not in out:
            out.append(uid)
    return out


def meeting_requester_instructors(state) -> list[str]:
    """Return confirmed requester/instructor identities, never speakers by position alone."""
    material = meeting_request_text(state)
    latest = last_user_text(state)
    if state.get("turn_continuation") and latest != material:
        material += "\n" + latest
    people = resolved_people(state)
    out: list[str] = []
    for line in material.splitlines():
        if not re.search(r"요청[·ㆍ\s/-]*지시자|요청자|지시자|(?:의\s*)?지시\s*[:：]", line):
            continue
        for name, uid in people.items():
            if name in line and uid and uid not in out:
                out.append(uid)
        for uid in re.findall(r"\bskcc\.[a-z]\d{2,8}\b", line, re.I):
            if uid not in out:
                out.append(uid)
    return out


def _create_from_meeting(text: str, state) -> bool:
    intent = str(state.get("intent") or "")
    return bool(
        ("plan_work" in intent or re.search(r"(?:Task|티켓|테스크).{0,30}(?:만들|생성|초안)", text, re.I)
         or re.search(r"(?:만들|생성).{0,30}(?:Task|티켓|테스크)", text, re.I))
    )


def _missing_owner_questions(state) -> list[dict]:
    """Find deadline-bearing work whose owner was omitted from unfinished minutes."""
    original = meeting_request_text(state)
    if not _create_from_meeting(original, state):
        return []
    latest = last_user_text(state)
    resolved = resolved_people(state)
    questions: list[dict] = []
    for raw in original.splitlines():
        line = re.sub(r"^\s*(?:[-*]|\d+[.)])\s*", "", raw).strip()
        due = re.search(r"(?<!\d)(20\d{2}-\d{2}-\d{2})(?!\d)", line)
        missing_marker = re.search(
            r"\bby\s*(?:\.{2,}|…)|담당.{0,30}(?:쓰다|누락|없|미정|정하지\s*못|정해지지\s*않)",
            line, re.I,
        )
        if not due or not missing_marker or re.search(r"미할당", line):
            continue
        has_owner = any(name in line and uid for name, uid in resolved.items())
        if has_owner:
            continue
        work = re.split(r"\s+by\s*|\s+[—–-]\s+담당|(?:은|는)?\s*담당", line,
                        maxsplit=1, flags=re.I)[0]
        work = re.split(r"(?<!\d)20\d{2}-\d{2}-\d{2}(?!\d)", work, maxsplit=1)[0]
        work = work.strip(" `.,:：-—–")
        terms = [term for term in re.findall(r"[가-힣A-Za-z0-9.+-]{2,}", work)
                 if term not in ("자료", "정리", "작업")]
        latest_matches = sum(1 for term in terms if term in latest) >= max(1, min(2, len(terms)))
        if latest_matches and ("미할당" in latest or any(uid in latest for uid in resolved.values())):
            continue
        questions.append({
            "question": (f"'{work}'의 담당자가 회의록에 없습니다. 담당 username을 지정하거나 "
                         "미할당으로 둘지 알려 주세요."),
            "kind": "choice", "field": f"owner:{work}",
            "options": ["미할당으로 생성"], "required_input": True,
            "why_required": "Task 담당자를 임의 추천하면 회의 결정과 다른 책임이 기록됨",
        })
    return questions


def unresolved_questions(state) -> list[dict]:
    """Questions left after internal/external research; empty means the workflow may continue."""
    if not is_meeting_request(state):
        return []
    original = meeting_request_text(state)
    latest = last_user_text(state)
    names = [name for name in _person_tokens(original)
             if _person_requires_resolution(original, name)]
    resolved = resolved_people(state)
    questions: list[dict] = []

    try:
        from app.agent.tools.people_tools import find_person
    except Exception:
        find_person = None
    for name in names:
        if name in resolved:
            continue
        result = {}
        if find_person is not None:
            try:
                result = find_person.invoke({"name": name}) or {}
            except Exception:
                result = {}
        candidates = result.get("candidates") or []
        options = []
        for candidate in candidates[:5]:
            uid = str(candidate.get("id") or "").strip()
            label = str(candidate.get("display") or candidate.get("name") or uid).strip()
            module = str(candidate.get("module") or "").strip()
            if uid:
                options.append(f"{label} ({uid}{', ' + module if module else ''})")
        questions.append({
            "question": (f"회의록의 '{name}'을 한 사람으로 확정할 수 없습니다. 정확한 username을 "
                         "선택하거나 입력해 주세요."),
            "kind": "choice" if options else "text",
            "field": f"person:{name}",
            "options": options,
            "required_input": True,
            "why_required": "댓글 멘션·담당자·소유자를 다른 사람으로 기록할 수 있음",
        })

    for term in _uncertain_terms(original):
        if _term_is_defined(term, state):
            continue
        questions.append({
            "question": f"내부 기록과 외부 자료에서도 '{term}'의 이 회의 기준 정의를 확정하지 못했습니다. 뜻과 판정 조건을 알려 주세요.",
            "kind": "text", "field": f"term:{term}", "options": [],
            "required_input": True,
            "why_required": "회의에서만 쓰는 기준을 추측하면 요약·댓글·티켓 내용이 달라짐",
        })
    questions.extend(_missing_owner_questions(state))
    return questions[:3]


def needs_research_interview(state) -> bool:
    """Whether a meeting turn must research before it is allowed to draft or answer."""
    if not is_meeting_request(state) or (state.get("situation") or "").strip():
        return False
    original = meeting_request_text(state)
    return bool(_person_tokens(original) or _uncertain_terms(original))


__all__ = ["attendee_mentions", "canonicalize_meeting_owner_table",
           "canonicalize_reply_mentions", "is_meeting_request",
           "NO_MEETING_ASSIGNMENT_REF",
           "meeting_assignment_bindings", "meeting_assignment_source_catalog",
           "meeting_assignment_source_mapping",
           "meeting_owner_records",
           "meeting_request_text", "meeting_requester_instructors", "meeting_subject",
           "needs_research_interview", "prune_resolved_gaps",
           "resolved_people", "unresolved_questions"]
