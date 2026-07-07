"""
아주 작은 JQL 필터 — 우리 클라이언트가 실제로 보내는 4종만 지원.
  1) "Epic Link" = KEY
  2) project = LAKE AND labels = "PMO_VIT" [ORDER BY updated DESC]
  3) assignee = "X" AND statusCategory = "In Progress"
  4) assignee = "X" AND statusCategory = Done AND resolved >= -7d
world.issues(내부 형태)를 필터해 key 리스트를 정렬 반환.
"""

import re
from datetime import timedelta

_CAT = {"to do": "todo", "in progress": "inprogress", "done": "done",
        "new": "todo", "indeterminate": "inprogress"}


def _val(clause):
    # `field <op> value` 에서 value 추출 (따옴표 제거)
    m = re.split(r"\s*(=|>=|<=|>|<)\s*", clause, maxsplit=1)
    if len(m) < 3:
        return None, None, None
    field = m[0].strip().strip('"').lower()
    op = m[1]
    v = m[2].strip().strip('"').strip("'")
    return field, op, v


def filter_keys(world, jql):
    jql = jql.strip()
    order_desc = "order by updated desc" in jql.lower()
    jql = re.sub(r"(?i)\s+order\s+by\s+.*$", "", jql).strip()
    clauses = re.split(r"(?i)\s+and\s+", jql)

    today = world.today
    preds = []
    for c in clauses:
        field, op, v = _val(c)
        if field is None:
            continue
        if field == "epic link":
            preds.append(lambda it, v=v: it["epicKey"] == v)
        elif field == "project":
            preds.append(lambda it, v=v: it["project"].lower() == v.lower())
        elif field == "labels":
            preds.append(lambda it, v=v: v in it["labels"])
        elif field == "assignee":
            preds.append(lambda it, v=v: it["assignee"] == v)
        elif field == "statuscategory":
            cat = _CAT.get(v.lower(), v.lower())
            preds.append(lambda it, cat=cat: it["statusCategory"] == cat)
        elif field == "resolved" and op == ">=" and v.lstrip("-").rstrip("dwmh").isdigit():
            days = int(re.sub(r"[^\d]", "", v))
            preds.append(lambda it, days=days: bool(it["resolved"]) and (today - it["resolved"]).days <= days)
        # 그 외 절은 무시(안전)

    matched = [it for it in world.issues.values() if all(p(it) for p in preds)]
    matched.sort(key=lambda it: it["updated"], reverse=True) if order_desc else \
        matched.sort(key=lambda it: it["key"])
    return [it["key"] for it in matched]
