"""
기능2: PMO_VIT 현안 트래킹 — Component(모듈)별 그룹핑.
- 진척 = Root 현안의 자손 티켓 "개수 기반"(done/total). (WBS 의 SP 기반과 목적 분리)
- 자손 소식(Created/Done/Resolved) + Root 코멘트 공유.
- 중복 제거: 상위(조상)가 이미 PMO_VIT 로 노출되면 그 자손 현안은 스킵.
데이터는 JiraClient.vit_issues (mock=합성 / local·prod=JQL labels=PMO_VIT).
각 현안은 tree(자손 트리)를 포함 → 프론트 [자세히] 에서 렌더.
"""

from datetime import date, datetime, timedelta


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
        counts = {}
        for n in flat:
            counts[n["type"]] = counts.get(n["type"], 0) + 1
        it["progress"] = {"done": done, "total": total,
                          "pct": round(done / total * 100, 1) if total else 0.0}
        it["counts"] = counts
        it["news"] = _news_from(flat, cutoff)
        issues.append(it)

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
