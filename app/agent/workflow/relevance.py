"""근거가 '존재함'과 현재 요청에 '관련 있음'을 구분하는 결정 규칙."""

from __future__ import annotations

import re

_NEGATIVE = re.compile(
    r"관련(?:이|은|성(?:이)?)?\s*(?:없|낮)|직접(?:적(?:인)?)?\s*관련[^.\n]{0,12}(?:없|아니)|"
    r"무관|연관(?:이|은|성(?:이)?)?\s*(?:없|낮)|관련되지\s*않|"
    r"다른\s*(?:주제|작업|방향|방향성)")

_GENERIC = {"epic", "task", "story", "bug", "jira", "ltm", "lake", "manager",
            "etl", "catalog", "runtime", "workbench", "dataops", "observability", "devops",
            "티켓", "작업", "업무", "추가", "수정", "변경", "구현", "개발", "요청"}


def negative_relation(text: str) -> bool:
    return bool(_NEGATIVE.search(str(text or "").lower()))


def evidence_is_relevant(evidence: dict) -> bool:
    """Historian이 무관/관련 없음이라고 명시한 항목은 참고 근거로 승격하지 않는다."""
    return isinstance(evidence, dict) and not negative_relation(evidence.get("why") or "")


def discriminating_keywords(keywords) -> list[str]:
    out = []
    for raw in keywords or []:
        word = str(raw or "").strip().lower()
        if len(word) >= 2 and word not in _GENERIC and word not in out:
            out.append(word)
    return out


def matches_focus(text: str, keywords) -> bool:
    focus = discriminating_keywords(keywords)
    return not focus or any(k in str(text or "").lower() for k in focus)


__all__ = ["discriminating_keywords", "evidence_is_relevant", "matches_focus",
           "negative_relation"]
