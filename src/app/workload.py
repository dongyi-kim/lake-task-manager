"""
기능3: 인력 워크로드 — 모듈별 인력의 진행중/최근7일 완료 티켓 수 (Epic/Task/Sub-task 분리).
인력 활동 요약은 별도 엔드포인트(/api/activity/{user}, 캐시)로 [+] 확장 시 조회.
"""

from datetime import datetime


def _ptotal(p):
    return p["inProgress"]["task"] + p["inProgress"]["voc"] + p["done7d"]["task"] + p["done7d"]["voc"]


def build_workload(client, plan, people, jira_base="", generated_at=None):
    data = client.workload(plan, people)     # module -> [person dict]
    modules = []
    gmax = 0
    for m in plan["modules"]:
        rows = data.get(m, [])
        for p in rows:
            gmax = max(gmax, _ptotal(p))
        modules.append({
            "module": m,
            "people": rows,
            "peopleCount": len(rows),
            "inProgressTotal": sum(p["inProgress"]["task"] + p["inProgress"]["voc"] for p in rows),
            "done7dTotal": sum(p["done7d"]["task"] + p["done7d"]["voc"] for p in rows),
        })
    return {
        "generatedAt": generated_at or datetime.now().strftime("%Y-%m-%d %H:%M"),
        "projectKey": plan.get("project_key", "DL"),
        "jiraBase": jira_base,
        "maxTotal": gmax or 1,        # 모든 인력 공유 x축 최대값
        "categories": ["ipTask", "ipVoc", "doneTask", "doneVoc"],
        "modules": modules,
    }
