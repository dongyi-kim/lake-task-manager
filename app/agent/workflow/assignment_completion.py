"""분담형 Task의 미완료 Sub-Task와 담당자를 결정적으로 집계한다.

"누가 교육을 아직 안 했나"는 사람의 최근 활동을 묻는 질문이 아니다. 주제와 일치하는
상위 Task를 찾고, 그 Task의 직계 Sub-Task 전체를 열어 statusCategory와 assignee를 묶어야
한다. 이 반복 조회를 LLM tool 선택에 맡기면 첫 검색 결과 한 건만 보고 끝나므로 코드가 한다.
"""

from __future__ import annotations

import re


_INCOMPLETE_RE = re.compile(
    r"미완료|미수료|안\s*(?:했|한|끝|됐|된)|못\s*(?:했|한|끝)|완료하지\s*않|"
    r"수료하지\s*않|남(?:았|은)"
)
_PEOPLE_RE = re.compile(r"누가|누구|누구누구|누가누가|사람|인원|담당자")
_WORK_RE = re.compile(r"Task|Sub-?Task|태스크|테스크|티켓|작업|교육|수강|수료", re.I)
_DROP_RE = re.compile(
    r"(?:누가누가|누구누구|누가|누구|어떤\s*사람|사람들?|인원|담당자|"
    r"미완료(?:했나|인가|인지)?|미수료(?:했나|인가|인지)?|완료하지\s*않은|"
    r"수료하지\s*않은|안\s*한|못\s*한|남은|"
    r"Task들?|Sub-?Task들?|태스크들?|테스크들?|티켓들?|작업들?|"
    r"궁금해|궁금합니다|알려줘|알려주세요|찾아줘|찾아주세요|있나|있어|인가|인지|했나)",
    re.I,
)
_TOKEN_DROP = {"관련", "대한", "해당", "현황", "목록", "전체", "현재", "이번", "우리"}


def asks_incomplete_assignees(text: str) -> bool:
    """질문의 축이 '주제별 미완료 작업의 사람 목록'인지 판정한다."""
    value = str(text or "")
    return bool(_INCOMPLETE_RE.search(value) and _PEOPLE_RE.search(value)
                and _WORK_RE.search(value))


def completion_topic(text: str, keywords: list | None = None) -> str:
    """검색용 주제만 남긴다. 모델 keyword가 있으면 원문과 합쳐 오탈자 회복에 쓴다."""
    sources = [" ".join(str(x) for x in (keywords or []) if str(x).strip()), str(text or "")]
    candidates = []
    for source in sources:
        cleaned = _DROP_RE.sub(" ", source)
        cleaned = re.sub(r"[^0-9A-Za-z가-힣._-]+", " ", cleaned)
        toks = [x for x in cleaned.split() if len(x) >= 2 and x not in _TOKEN_DROP]
        value = " ".join(toks).strip()
        if value and value not in candidates:
            candidates.append(value)
    return candidates[0] if candidates else ""


def _tokens(text: str) -> list[str]:
    value = _DROP_RE.sub(" ", str(text or ""))
    return [x.casefold() for x in re.findall(r"[0-9A-Za-z가-힣._-]{2,}", value)
            if x not in _TOKEN_DROP]


def _score(title: str, topic: str) -> float:
    """긴 parent 제목에서도 핵심 낱말이 많이 겹치는 후보를 우선한다."""
    title_fold = re.sub(r"\s+", "", str(title or "").casefold())
    toks = _tokens(topic)
    if not title_fold or not toks:
        return 0.0
    hits = sum(1 for token in toks if re.sub(r"\s+", "", token) in title_fold)
    score = hits / len(toks)
    topic_fold = "".join(toks)
    if topic_fold and topic_fold in title_fold:
        score += 0.5
    return score


def _parent_key_of(key: str) -> str:
    try:
        from app.agent.tools._ctx import client
        fields = (client().get_issue_light(key) or {}).get("fields") or {}
        return str((fields.get("parent") or {}).get("key") or "")
    except Exception:
        return ""


def lookup_incomplete_assignees(text: str, keywords: list | None = None) -> dict:
    """주제와 일치하는 Task의 모든 직계 Sub-Task를 담당자별로 집계한다.

    검색에서 함께 나온 무관 티켓은 반환하지 않는다. 최종 답변은 이 결과만 사용하므로
    "제외했다"는 내부 판단 과정도 사용자에게 노출되지 않는다.
    """
    from app.agent.tools._ctx import client, search_projects
    from app.agent.tools.search_tools import search_work_history

    topic = completion_topic(text, keywords)
    if not search_projects():
        return {"kind": "incomplete_assignees", "topic": topic, "parents": [],
                "people": [], "unassigned": [],
                "error": "search.jira.projects가 비어 있어 Jira 조회 범위를 확정할 수 없습니다."}
    if not topic:
        return {"kind": "incomplete_assignees", "topic": "", "parents": [],
                "people": [], "unassigned": [],
                "error": "미완료 여부를 확인할 업무 주제를 식별하지 못했습니다."}

    queries = [topic]
    raw_topic = completion_topic(text, [])
    if raw_topic and raw_topic not in queries:
        queries.append(raw_topic)
    found = {}
    for query in queries[:2]:
        try:
            result = search_work_history.invoke({"query": query, "limit": 20}) or {}
        except Exception:
            continue
        for row in result.get("jira") or []:
            key = str((row or {}).get("key") or "")
            if key:
                found.setdefault(key, row)

    parent_candidates = {}
    cli = client()
    for key, row in found.items():
        issue_type = str(row.get("issuetype") or "").casefold()
        if "sub" in issue_type:
            parent_key = _parent_key_of(key)
            if not parent_key:
                continue
            try:
                badge = cli.ticket_badge(parent_key) or {}
            except Exception:
                badge = {}
            if badge:
                parent_candidates[parent_key] = {
                    "key": parent_key, "title": badge.get("summary") or parent_key,
                }
        elif issue_type != "epic":
            parent_candidates[key] = {"key": key, "title": row.get("title") or key}

    ranked = sorted(
        ((_score(row.get("title") or "", topic), row) for row in parent_candidates.values()),
        key=lambda item: (-item[0], item[1]["key"]),
    )
    if not ranked or ranked[0][0] <= 0:
        return {"kind": "incomplete_assignees", "topic": topic, "parents": [],
                "people": [], "unassigned": [], "searched": len(found)}

    # 최고 후보와 핵심어 일치도가 가까운 parent만 본다. 낮은 점수의 '보안 고도화' 같은
    # 검색 부산물은 조회·출력 양쪽에서 제외한다.
    floor = max(0.34, ranked[0][0] - 0.20)
    parents = []
    for score, candidate in ranked[:8]:
        if score < floor:
            continue
        try:
            children = cli.ticket_children(candidate["key"]) or []
        except Exception:
            continue
        if not children:
            continue
        unfinished = [dict(child) for child in children
                      if str(child.get("statusCategory") or "").casefold() != "done"]
        parents.append({
            "key": candidate["key"], "summary": candidate["title"],
            "total": len(children), "done": len(children) - len(unfinished),
            "incomplete": unfinished, "matchScore": round(score, 3),
        })

    people_by_id, unassigned = {}, []
    for parent in parents:
        for child in parent["incomplete"]:
            row = {
                "key": child.get("key"), "summary": child.get("summary"),
                "status": child.get("status"), "due": child.get("due"),
                "parent": parent["key"],
            }
            uid = str(child.get("assigneeId") or "").strip()
            name = str(child.get("assignee") or "").strip()
            if not uid and not name:
                unassigned.append(row)
                continue
            identity = uid or "name:" + name
            person = people_by_id.setdefault(identity, {"id": uid, "name": name, "tickets": []})
            person["tickets"].append(row)

    people = sorted(people_by_id.values(), key=lambda row: (row.get("name") or row.get("id") or ""))
    return {"kind": "incomplete_assignees", "topic": topic, "parents": parents,
            "people": people, "unassigned": unassigned, "searched": len(found),
            "totalSubtasks": sum(int(p["total"]) for p in parents),
            "doneSubtasks": sum(int(p["done"]) for p in parents),
            "incompleteSubtasks": sum(len(p["incomplete"]) for p in parents)}


__all__ = ["asks_incomplete_assignees", "completion_topic", "lookup_incomplete_assignees"]
