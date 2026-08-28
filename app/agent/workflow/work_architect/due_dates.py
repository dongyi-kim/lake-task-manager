"""Deterministic deadline parsing and projection for Work Architect drafts."""

from __future__ import annotations

import re as _re
from datetime import date, timedelta

from app.agent.workflow.state import last_user_text, request_text
from app.agent.workflow.work_architect.context import current_request_boundary_text


_WEEKDAYS = {"월": 0, "화": 1, "수": 2, "목": 3, "금": 4, "토": 5, "일": 6}
_ISO_DATE = r"(?<!\d)(20\d{2}-\d{2}-\d{2})(?!\d)"
_DUE_WORD = r"(?:마감(?:일)?|기한|due\s*date|duedate|due)"


def valid_iso_date(value: str) -> bool:
    """Return whether ``value`` is a real calendar date in canonical ISO form."""
    try:
        return date.fromisoformat(str(value or "")).isoformat() == value
    except (TypeError, ValueError):
        return False


def explicit_due_candidates(text: str) -> list[str]:
    """Extract only dates explicitly bound to a deadline, preserving first-seen order."""
    source = str(text or "")
    found: list[tuple[int, str]] = []
    patterns = (
        rf"{_DUE_WORD}\s*(?:은|는|이|가|을|를|:|=|로|으로)?\s*{_ISO_DATE}",
        rf"{_ISO_DATE}\s*(?:을|를|로|으로|까지)?\s*{_DUE_WORD}",
        rf"{_ISO_DATE}\s*까지(?:로|를|는|은)?",
    )
    for pattern in patterns:
        for match in _re.finditer(pattern, source, _re.I):
            value = next((group for group in match.groups()
                          if group and _re.fullmatch(r"20\d{2}-\d{2}-\d{2}", group)), "")
            if valid_iso_date(value):
                found.append((match.start(), value))
    ordered = []
    for _position, value in sorted(found):
        if value not in ordered:
            ordered.append(value)
    return ordered


def explicit_due_instruction_status(state: dict) -> tuple[str, str]:
    """Classify the authoritative current-turn due instruction."""
    frozen = request_text(state).strip()
    latest = last_user_text(state).strip()
    continuation = bool(state.get("turn_continuation"))
    source = latest if continuation and latest and latest != frozen else frozen
    due_labeled = bool(_re.search(_DUE_WORD, source, _re.I))
    deadline_form = bool(_re.search(rf"{_ISO_DATE}\s*까지", source))
    if due_labeled and _re.search(
            r"(?:제거|삭제|없애|비워|미설정|설정하지\s*말|없(?:음|이))|"
            r"(?:clear|unset|remove)", source, _re.I):
        return "clear", ""
    raw_dates = _re.findall(_ISO_DATE, source)
    ambiguous_clause = bool(_re.search(
        rf"{_DUE_WORD}[^.!?\n]{{0,32}}{_ISO_DATE}[^.!?\n]{{0,16}}"
        rf"(?:또는|혹은|중|or|/)\s*{_ISO_DATE}", source, _re.I,
    ) or _re.search(
        rf"{_ISO_DATE}[^.!?\n]{{0,16}}(?:또는|혹은|or|/)\s*{_ISO_DATE}"
        rf"[^.!?\n]{{0,32}}{_DUE_WORD}", source, _re.I,
    ))
    if ambiguous_clause:
        return "ambiguous", ""

    values = explicit_due_candidates(source)
    if len(values) > 1:
        return "ambiguous", ""
    if len(values) == 1:
        return "valid", values[0]
    if (due_labeled or deadline_form) and raw_dates:
        return "invalid", str(raw_dates[0])

    if continuation and latest:
        messages = list(state.get("messages") or [])
        last_human_index = next((index for index in range(len(messages) - 1, -1, -1)
                                 if getattr(messages[index], "type", "") == "human"), -1)
        previous = messages[last_human_index - 1] if last_human_index > 0 else None
        match = _re.fullmatch(_ISO_DATE, latest)
        if (match and previous is not None
                and getattr(previous, "type", "") in ("ai", "assistant")
                and _re.search(_DUE_WORD, str(getattr(previous, "content", "") or ""), _re.I)):
            value = match.group(1)
            return ("valid", value) if valid_iso_date(value) else ("invalid", value)
    return "absent", ""


def authoritative_explicit_due(state: dict) -> str:
    """Return the sole explicit deadline inside the current request boundary."""
    frozen = request_text(state).strip()
    latest = last_user_text(state).strip()
    continuation = bool(state.get("turn_continuation"))
    status, status_value = explicit_due_instruction_status(state)
    if status == "valid":
        return status_value
    if status in {"ambiguous", "invalid", "clear"}:
        return ""
    frozen_values = explicit_due_candidates(frozen)
    messages = list(state.get("messages") or [])

    current_followup = continuation and latest and latest != frozen
    if current_followup:
        latest_values = explicit_due_candidates(latest)
        if len(latest_values) == 1:
            return latest_values[0]
        if len(latest_values) > 1:
            return ""

        last_human_index = next((index for index in range(len(messages) - 1, -1, -1)
                                 if getattr(messages[index], "type", "") == "human"), -1)
        answer = (str(getattr(messages[last_human_index], "content", "") or "").strip()
                  if last_human_index >= 0 else "")
        match = _re.fullmatch(_ISO_DATE, answer)
        previous = messages[last_human_index - 1] if last_human_index > 0 else None
        if (answer == latest and match and previous is not None
                and getattr(previous, "type", "") in ("ai", "assistant")
                and _re.search(_DUE_WORD, str(getattr(previous, "content", "") or ""), _re.I)
                and valid_iso_date(match.group(1))):
            return match.group(1)

        prior_items = [row for row in ((state.get("draft") or {}).get("items") or [])
                       if isinstance(row, dict)]
        prior_due = (str(prior_items[0].get("duedate") or "").strip()
                     if len(prior_items) == 1 else "")
        if not _re.search(_DUE_WORD, latest, _re.I) and valid_iso_date(prior_due):
            return prior_due
        return ""
    return frozen_values[0] if len(frozen_values) == 1 else ""


def global_exact_due_for_roots(state: dict, root_count: int) -> str:
    """Return one exact date only when the user explicitly scopes it to every root."""
    if root_count < 2:
        return ""
    due = authoritative_explicit_due(state)
    if not due:
        return ""
    source = current_request_boundary_text(state)
    universal = (
        r"(?:둘\s*다|모두|전부|공통(?:으로|의)?|"
        r"각\s*(?:Task|태스크|테스크|티켓|항목)|"
        r"\d+\s*(?:건|개)\s*(?:모두|다))"
    )
    scoped = bool(
        _re.search(rf"{universal}[^.!?\n]{{0,60}}{_DUE_WORD}", source, _re.I)
        or _re.search(rf"{_DUE_WORD}[^.!?\n]{{0,60}}{universal}", source, _re.I)
    )
    return due if scoped else ""


def normalize_due_rationale(rationale: str, due: str) -> str:
    """Remove model-authored deadline reasoning and append the authoritative user fact."""
    text = str(rationale or "")
    text = _re.sub(
        rf"[^.\n;!?]*(?:{_DUE_WORD}|{_ISO_DATE})[^.\n;!?]*(?:[.\n;!?]|$)",
        " ", text, flags=_re.I,
    )
    text = "\n".join(line.strip() for line in text.splitlines() if line.strip()).strip()
    canonical = f"사용자 지정 마감일 {due} 그대로 적용."
    return f"{text}\n{canonical}".strip()


def relative_due(text: str) -> str:
    """Resolve supported Korean relative deadlines to ``YYYY-MM-DD``."""
    compact = (text or "").replace(" ", "")
    today = date.today()
    duration = _re.search(r"(?<!\d)(\d{1,2})\s*주(?:\s*(?:정도|동안|이내|내))?", text or "")
    if duration and not _re.search(rf"{_re.escape(duration.group(0))}\s*전", text or ""):
        return (today + timedelta(days=7 * int(duration.group(1)))).isoformat()
    if "내일" in compact:
        return (today + timedelta(days=1)).isoformat()
    if "모레" in compact:
        return (today + timedelta(days=2)).isoformat()
    if "이번주까지" in compact or "금주까지" in compact:
        monday = today - timedelta(days=today.weekday())
        result = monday + timedelta(days=4)
        if result < today:
            result += timedelta(days=7)
        return result.isoformat()
    match = _re.search(r"(다음\s*주|이번\s*주|담주|차주)([월화수목금토일])요일", text or "") \
        or _re.search(r"(다음주|이번주|담주|차주)([월화수목금토일])", compact)
    if not match:
        return ""
    weekday = _WEEKDAYS[match.group(2)]
    monday = today - timedelta(days=today.weekday())
    base = monday if match.group(1).replace(" ", "") == "이번주" else monday + timedelta(days=7)
    result = base + timedelta(days=weekday)
    if result < today:
        result += timedelta(days=7)
    return result.isoformat()


def apply_relative_due_to_single_draft(state: dict, items: list) -> str:
    """Apply an exact/relative due only where root scope is deterministic."""
    rows = [row for row in (items or []) if isinstance(row, dict)]
    if not rows or len(rows) != len(items or []):
        return ""
    due_status, _due_value = explicit_due_instruction_status(state)
    if due_status in {"ambiguous", "invalid", "clear"}:
        if len(rows) == 1:
            rows[0]["duedate"] = ""
        return ""
    due = authoritative_explicit_due(state)
    if due and len(rows) == 1:
        rows[0]["duedate"] = due
        return due
    global_due = global_exact_due_for_roots(state, len(rows))
    if global_due:
        for row in rows:
            row["duedate"] = global_due
        return global_due
    if len(rows) != 1:
        return ""
    due = relative_due(current_request_boundary_text(state))
    if due:
        rows[0]["duedate"] = due
    return due


__all__ = [
    "apply_relative_due_to_single_draft",
    "authoritative_explicit_due",
    "explicit_due_candidates",
    "explicit_due_instruction_status",
    "global_exact_due_for_roots",
    "normalize_due_rationale",
    "relative_due",
    "valid_iso_date",
]
