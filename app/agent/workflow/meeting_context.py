"""Deterministic meeting-note ambiguity handling.

Meeting notes commonly contain Jira mentions, a partial Korean name plus an honorific, and
locally coined acronyms.  Those are identity and business-definition questions, not good
places for an LLM to guess.  This module lets the normal retrieval pass run first, then turns
only the still-unresolved values into one compact interview.
"""

from __future__ import annotations

import re

from app.agent.workflow.state import last_user_text, request_text


_TITLE = r"(?:TL|PL|PM|PO|EM|M|파트장|그룹장|본부장|팀장|실장|부장|차장|과장|대리|선임|책임|수석|매니저|리더|님|씨)"
_KNOWN_TECH = {
    "API", "CDC", "DAG", "DL", "ETL", "HTML", "HTTP", "HTTPS", "JIRA", "JSON",
    "LAKE", "LTM", "NDV", "POC", "SQL", "TL", "PL", "PM", "PO", "EM", "UI", "URL", "UX",
}


def is_meeting_request(state) -> bool:
    text = f"{request_text(state)} {last_user_text(state)}"
    return any(word in text for word in ("회의록", "회의 결정", "회의 후속", "실무회의"))


def _person_tokens(text: str) -> list[str]:
    found: list[str] = []

    def add(value: str) -> None:
        value = str(value or "").strip()
        if value and value not in found:
            found.append(value)

    for name, _number in re.findall(r"\{\{\s*([가-힣A-Za-z]{1,20})\s*:\s*(\d+)\s*\}\}", text):
        add(name)
    for name in re.findall(r"(?<![\w@])@([가-힣]{2,5})(?![\w])", text):
        add(name)
    for name in re.findall(
            rf"(?<![가-힣])([가-힣]{{1,5}})\s*{_TITLE}(?=\s|은|는|이|가|을|를|:|,|$)", text):
        add(name)
    return found


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
    return bound


def resolved_people(state) -> dict[str, str]:
    """Resolve every meeting person that can be resolved without guessing."""
    original, latest = request_text(state), last_user_text(state)
    names = _person_tokens(original)
    bindings = _explicit_user_bindings(latest, names)
    try:
        from app.agent.tools.people_tools import find_person, remember_person
    except Exception:
        return bindings

    for name, uid in bindings.items():
        remember_person(name, uid)
    for name in names:
        if name in bindings:
            continue
        try:
            result = find_person.invoke({"name": name}) or {}
        except Exception:
            continue
        uid = str(result.get("resolved") or "").strip()
        if uid and not result.get("ambiguous"):
            bindings[name] = uid
    return bindings


def _uncertain_terms(text: str) -> list[str]:
    terms: list[str] = []
    for term in re.findall(r"(?<![A-Za-z0-9])([A-Z][A-Z0-9-]{1,9})(?![A-Za-z0-9])", text):
        if term.upper() in _KNOWN_TECH or term.isdigit() or term in terms:
            continue
        nearby = re.search(
            rf"{re.escape(term)}.{{0,80}}(?:뜻|정의|기준).{{0,60}}(?:없|미정|확정되지|모르|확인)|"
            rf"{re.escape(term)}.{{0,100}}(?:자료|조사).{{0,60}}(?:확정되지|모르|확인)",
            text, re.I | re.S,
        )
        if nearby:
            terms.append(term)
    return terms


def _term_is_defined(term: str, state) -> bool:
    latest = last_user_text(state)
    original = request_text(state)
    if latest != original:
        match = re.search(rf"(?<![A-Za-z0-9]){re.escape(term)}(?![A-Za-z0-9])\s*"
                          rf"(?:은|는|:|=)\s*(.{{3,180}})", latest,
                          re.I | re.S)
        if match and not re.search(r"확인\s*필요|모르|미정|없", match.group(1)[:80]):
            return True
    evidence = "\n".join(str(state.get(key) or "") for key in
                         ("pre_survey", "topic_dossier", "situation", "web_context"))
    match = re.search(rf"(?<![A-Za-z0-9]){re.escape(term)}(?![A-Za-z0-9])\s*"
                      rf"(?:은|는|:|=)\s*(.{{3,160}})", evidence,
                      re.I | re.S)
    return bool(match and not re.search(r"확인\s*필요|정의가?\s*필요|미정|없", match.group(1)[:80]))


def unresolved_questions(state) -> list[dict]:
    """Questions left after internal/external research; empty means the workflow may continue."""
    if not is_meeting_request(state):
        return []
    original = request_text(state)
    latest = last_user_text(state)
    names = _person_tokens(original)
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
    return questions[:3]


def needs_research_interview(state) -> bool:
    """Whether a meeting turn must research before it is allowed to draft or answer."""
    if not is_meeting_request(state) or (state.get("situation") or "").strip():
        return False
    original = request_text(state)
    return bool(_person_tokens(original) or _uncertain_terms(original))


__all__ = ["is_meeting_request", "needs_research_interview", "resolved_people",
           "unresolved_questions"]
