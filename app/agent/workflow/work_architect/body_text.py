"""HTML-preserving text utilities for Work Architect drafts."""

from __future__ import annotations

import re as _re

from app.agent.workflow.work_architect.context import current_request_boundary_text


def base_title(value: str) -> str:
    """Return a title without numeric split markers or execution-stage suffixes."""
    value = _re.sub(r"\d+", "", value or "")
    value = _re.sub(
        r"\s*[-–—:]?\s*(?:설계|구현|검증|테스트|연동|모니터링|문서화|배포|개발)"
        r"(?:\s*단계)?\s*$", "", value.strip(),
    ).strip()
    return _re.sub(r"\s{2,}", " ", value).strip(" -–—:#")


def display_base_title(value: str) -> str:
    """Strip stage suffixes while retaining user-visible facts and numbers."""
    value = str(value or "").strip()
    value = _re.sub(
        r"\s*[-–—:]?\s*(?:설계|구현|검증|테스트|연동|모니터링|문서화|배포|개발)"
        r"(?:\s*단계)?\s*$", "", value,
    ).strip()
    return _re.sub(r"\s{2,}", " ", value).strip(" -–—:#")


def draft_full_text(draft: dict, cap: int = 4000) -> str:
    """Render the complete draft context used by follow-up agents and auditors."""
    if not draft or not draft.get("items"):
        return ""
    rows = [f"mode={draft.get('mode')} · structure={draft.get('structure') or '?'}"]
    for index, item in enumerate(draft.get("items") or []):
        head = [f"[{index}] {item.get('type', '')} — {item.get('summary', '')}"]
        for key, label in (("epic", "상위"), ("parent", "부모"), ("components", "모듈"),
                           ("labels", "라벨"), ("duedate", "마감"), ("priority", "우선순위"),
                           ("assignee", "담당")):
            value = item.get(key)
            if value:
                head.append(f"{label}={value if not isinstance(value, list) else ', '.join(map(str, value))}")
        rows.append("  ".join(head))
        if item.get("description"):
            rows.append("  본문:\n  " + str(item["description"]).replace("\n", "\n  "))
        for child in item.get("children") or []:
            if isinstance(child, dict):
                rows.append(f"  └ Sub-Task: {child.get('summary', '')}"
                            + (f" (담당 {child.get('assignee')})" if child.get("assignee") else ""))
    return "\n".join(rows)[:cap]


def merge_refs(description: str, refs: list) -> str:
    """Merge verified reference rows into the one canonical 참고 section."""
    fresh = "".join(row for key, row in refs if key not in (description or ""))
    if not fresh:
        return description
    match = _re.search(
        r"(<h3>\s*참고\s*</h3>\s*<ul[^>]*>)(.*?)(</ul>)",
        description or "", _re.S | _re.I,
    )
    if match:
        return description[:match.end(2)] + fresh + description[match.end(2):]
    return (description or "") + "<h3>참고</h3><ul>" + fresh + "</ul>"


def drop_empty_sections(description: str) -> str:
    """Remove headings whose list or paragraph has no content."""
    if not description:
        return description
    output = _re.sub(r"<(ul|ol)>\s*(?:<li>\s*</li>\s*)*</\1>", "", description)
    output = _re.sub(r"<p>\s*(?:&nbsp;)?\s*</p>", "", output)
    output = _re.sub(r"<h([1-6])>[^<]*</h\1>\s*(?=(<h[1-6]>|$))", "", output)
    return output.strip()


def drop_subtask_ticket_refs(description: str) -> str:
    """Remove redundant references from Sub-Tasks whose parent carries the context."""
    return _re.sub(
        r"(<h3>\s*참고(?:\s*(?:사항|자료|문서))?\s*</h3>\s*<ul[^>]*>)"
        r"(.*?)(</ul>)", "", description or "", flags=_re.S | _re.I,
    )


def drop_unverified_refs(description: str, allowed_keys: set, allowed_urls: set) -> tuple:
    """Retain only references verified by the Research Analyst."""
    removed = []
    keys = {str(key).upper() for key in (allowed_keys or set()) if str(key)}
    urls = {str(url).strip() for url in (allowed_urls or set()) if str(url).strip()}

    def clean(match):
        head, body, tail = match.group(1), match.group(2), match.group(3)
        kept = []
        for row in _re.findall(r"<li[^>]*>.*?</li>", body, _re.S):
            found_keys = {key.upper() for key in
                          _re.findall(r"\b[A-Z][A-Z0-9]*-\d+\b", row, _re.I)}
            found_urls = {url.strip() for url in
                          _re.findall(r"href=[\"']([^\"']+)[\"']", row, _re.I)}
            if (found_keys and found_keys & keys) or (found_urls and found_urls & urls):
                kept.append(row)
            else:
                removed.append(_re.sub(r"<[^>]+>", "", row).strip()[:50])
        return head + "".join(kept) + tail

    output = _re.sub(
        r"(<h3>\s*참고(?:\s*(?:사항|자료|문서))?\s*</h3>\s*<ul[^>]*>)"
        r"(.*?)(</ul>)", clean, description or "", flags=_re.S | _re.I,
    )
    return output, removed


def drop_unlinked_refs(description: str) -> tuple:
    """Drop reference rows that contain neither a ticket key nor a link."""
    removed = []

    def clean(match):
        head, body, tail = match.group(1), match.group(2), match.group(3)
        kept = []
        for row in _re.findall(r"<li[^>]*>.*?</li>", body, _re.S):
            if _re.search(r"\b[A-Z][A-Z0-9]*-\d+\b", row) or "<a " in row:
                kept.append(row)
            else:
                removed.append(_re.sub(r"<[^>]+>", "", row).strip()[:30])
        return head + "".join(kept) + tail

    output = _re.sub(
        r"(<h3>\s*참고(?:\s*(?:사항|자료|문서))?\s*</h3>\s*<ul[^>]*>)"
        r"(.*?)(</ul>)", clean, description or "", flags=_re.S | _re.I,
    )
    return output, removed


_ANCHOR_OPAQUE_TAGS = frozenset({"a", "code", "pre", "script", "style"})
_ANCHOR_OPAQUE_TOKEN = _re.compile(r"(\{\{[^{}\r\n]{1,300}\}\}|`[^`\r\n]*`)")


def html_tag_end(value: str, start: int) -> int:
    """Find a tag end without treating ``>`` in a quoted attribute as the end."""
    quote = ""
    for index in range(start + 1, len(value)):
        char = value[index]
        if quote:
            if char == quote:
                quote = ""
        elif char in ("'", '"'):
            quote = char
        elif char == ">":
            return index
    return -1


def map_visible_body_text(value: str, transform) -> str:
    """Transform authored visible text without rewriting identities or markup."""
    source = str(value or "")
    output, protected, cursor = [], [], 0
    while cursor < len(source):
        if source[cursor] == "<":
            end = html_tag_end(source, cursor)
            if end >= 0:
                tag = source[cursor:end + 1]
                parsed = _re.match(r"<\s*(/?)\s*([A-Za-z][A-Za-z0-9:-]*)", tag)
                if parsed:
                    closing, name = bool(parsed.group(1)), parsed.group(2).casefold()
                    if closing and name in _ANCHOR_OPAQUE_TAGS:
                        for stack_index in range(len(protected) - 1, -1, -1):
                            if protected[stack_index] == name:
                                del protected[stack_index:]
                                break
                    if (not closing and name in _ANCHOR_OPAQUE_TAGS
                            and not tag.rstrip().endswith("/>")):
                        protected.append(name)
                output.append(tag)
                cursor = end + 1
                continue
            output.append("<" if protected else transform("<"))
            cursor += 1
            continue
        end = source.find("<", cursor)
        if end < 0:
            end = len(source)
        text = source[cursor:end]
        if protected:
            output.append(text)
        else:
            pieces = _ANCHOR_OPAQUE_TOKEN.split(text)
            output.extend(piece if index % 2 else transform(piece)
                          for index, piece in enumerate(pieces))
        cursor = end
    return "".join(output)


def visible_body_text(value: str) -> str:
    """Return only text eligible for anchor insertion and coverage checks."""
    visible = []

    def collect(text: str) -> str:
        visible.append(text)
        return text

    map_visible_body_text(value, collect)
    return " ".join(visible)


def topic_drift(state, items: list) -> str:
    """Warn when no unique term from the current request survives in the draft."""
    request = current_request_boundary_text(state)
    if not request or not items:
        return ""
    try:
        from app.agent.tools._ident import find_identifiers
        terms = {str(term).strip().rstrip(".,;:()[]") for term in find_identifiers(request)}
    except Exception:
        terms = set()
    common = {"task", "story", "bug", "feature", "improvement", "epic", "jira",
              "test", "data", "table", "api", "the", "and", "pipeline", "with",
              "for", "this"}
    terms |= {word for word in _re.findall(r"[A-Za-z][A-Za-z0-9_.-]{3,}", request)
              if word.lower().rstrip(".,;:()[]") not in common}
    terms = {str(term).strip().rstrip(".,;:()[]") for term in terms if str(term).strip()}
    try:
        from app.agent.workflow.agents.query_specialist import _known_user_tokens
        people = _known_user_tokens()
    except Exception:
        people = set()
    terms = {term for term in terms
             if not _re.fullmatch(r"[A-Za-z][A-Za-z0-9]*-\d+", term)
             and term.casefold() not in people}
    labels = {str(value).strip().lower() for item in items for value in (item.get("labels") or [])}
    terms = {term for term in terms
             if term and term.lower() not in labels and term.lower() not in common}
    if not terms:
        return ""
    haystack = " ".join(
        str(item.get("summary") or "") + " "
        + visible_body_text(str(item.get("description") or ""))
        for item in items
    ).lower()
    if any(term.lower() in haystack for term in terms):
        return ""
    shown = ", ".join(sorted(terms)[:4])
    return (f"(확인 필요: 원 요청의 고유어({shown})가 제목·본문에 없다 — 요청과 다른 "
            "주제의 티켓일 수 있다. Epic 본문을 따라간 것은 아닌지 검토)")


__all__ = [
    "base_title",
    "display_base_title",
    "draft_full_text",
    "drop_empty_sections",
    "drop_subtask_ticket_refs",
    "drop_unlinked_refs",
    "drop_unverified_refs",
    "html_tag_end",
    "map_visible_body_text",
    "merge_refs",
    "topic_drift",
    "visible_body_text",
]
