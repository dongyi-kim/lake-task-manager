"""
기능2: PMO_VIT 현안 트래킹 — Component(모듈)별 그룹핑.
- 진척 = Root 현안의 자손 티켓 "개수 기반"(done/total). (WBS 의 SP 기반과 목적 분리)
- 자손 소식(Created/Done/Resolved) + Root 코멘트 공유.
- 중복 제거: 상위(조상)가 이미 PMO_VIT 로 노출되면 그 자손 현안은 스킵.
데이터는 JiraClient.vit_issues (mock=합성 / local·prod=JQL labels=PMO_VIT).
각 현안은 tree(자손 트리)를 포함 → 프론트 [자세히] 에서 렌더.
"""

from datetime import date, datetime, timedelta

from .names import real_name


def _flatten(nodes):
    out = []
    for n in nodes:
        out.append(n)
        out.extend(_flatten(n.get("children") or []))
    return out


def _news_from(flat, cutoff_iso, limit=8):
    """자손의 Created/Done/Resolved 소식 — 최근분만, 최신순."""
    ev = []
    for n in flat:
        base = {"key": n.get("key", ""), "title": n.get("summary", ""), "type": n["type"]}
        if n.get("created") and n["created"] >= cutoff_iso:
            ev.append(dict(base, date=n["created"], kind="created"))
        if n.get("resolved") and n["resolved"] >= cutoff_iso:
            ev.append(dict(base, date=n["resolved"], kind="resolved" if n["type"] == "Bug" else "done"))
    ev.sort(key=lambda e: e["date"], reverse=True)
    return ev[:limit]


def build_vit(client, plan, people, epic_prog=None, generated_at=None, news_days=21, jira_base=""):
    raw = client.vit_issues(plan, people, epic_prog)
    vit_keys = {it["key"] for it in raw}
    cutoff = (date.today() - timedelta(days=news_days)).isoformat()

    issues, skipped = [], 0
    for it in raw:
        # 상위가 이미 PMO_VIT 면 자손 현안은 스킵(중복 노출 방지)
        if any(a in vit_keys for a in (it.get("ancestors") or [])):
            skipped += 1
            continue
        flat = _flatten(it.get("tree") or [])
        total = len(flat)
        done = sum(1 for n in flat if n.get("statusCategory") == "done")
        inprog = sum(1 for n in flat if n.get("statusCategory") == "inprogress")
        counts = {}
        for n in flat:
            counts[n["type"]] = counts.get(n["type"], 0) + 1
        # 직계 자식(트리 최상위)만 표 리스트용으로 투영 (티켓·상태·시작일·종료일·담당자)
        children = [{"key": n.get("key"), "type": n.get("type"), "summary": n.get("summary"),
                     "assignee": n.get("assignee"), "status": n.get("status"),
                     "statusCategory": n.get("statusCategory"),
                     "created": n.get("created"), "resolved": n.get("resolved")}
                    for n in (it.get("tree") or [])]
        # 요약 dict 를 새로 구성(원본 it 은 캐시 공유 대상이라 변형 금지). tree/comments 는 /api/vit/{key} lazy.
        issues.append({
            "key": it["key"], "summary": it["summary"], "type": it["type"], "module": it.get("module"),
            "assignee": real_name(it.get("assignee")), "start": it.get("start"), "created": it.get("created"),
            "started": it.get("started"), "updated": it.get("updated"), "due": it.get("due"),
            "statusCategory": it["statusCategory"], "status": it["status"], "ancestors": it.get("ancestors"),
            "progress": {"done": done, "total": total, "pct": round(done / total * 100, 1) if total else 0.0},
            "counts": counts,
            "statusCounts": {"open": total - done - inprog, "inprogress": inprog, "done": done},
            "children": children,
            "news": _news_from(flat, cutoff),
        })

    # 결정적 정렬(updated 내림차순, 동률은 key 오름차순) → 조회 순서와 무관하게 mock==local 일치.
    issues.sort(key=lambda x: x["key"])
    issues.sort(key=lambda x: x.get("updated") or "", reverse=True)

    groups = {}
    for it in issues:
        groups.setdefault(it.get("module") or "Module 미지정", []).append(it)
    modules = [{"module": m, "issues": groups.get(m, [])} for m in plan["modules"]]
    for m in groups:
        if m not in plan["modules"]:
            modules.append({"module": m, "issues": groups[m]})

    return {
        "generatedAt": generated_at or datetime.now().strftime("%Y-%m-%d %H:%M"),
        "projectKey": plan.get("project_key", "LAKE"),
        "jiraBase": jira_base,
        "modules": modules,
        "summary": {
            "total": len(issues),
            "skippedDup": skipped,
            "byModule": {g["module"]: len(g["issues"]) for g in modules},
        },
    }


def vit_detail(client, plan, people, key, epic_prog=None):
    """단일 현안의 자손 트리 + 코멘트 ([자세히] 지연 로딩). 조립된 VIT 목록(캐시) 재사용.
    코멘트는 리스트 경로에서 안 받으므로 여기서 해당 key 만 lazy 조회(mock/local 공용)."""
    for it in client.vit_issues(plan, people, epic_prog):
        if it.get("key") == key:
            comments = it.get("comments")
            if comments is None:                       # local/prod: 리스트에서 코멘트 미조회 → lazy
                comments = client.issue_comments(key)
            comments = [dict(c, author=real_name(c.get("author"))) for c in (comments or [])]
            return {"key": key, "tree": it.get("tree") or [], "comments": comments}
    return {"key": key, "tree": [], "comments": []}
