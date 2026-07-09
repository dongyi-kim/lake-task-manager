"""
기능3: 인력 워크로드 — 모듈별 인력의 진행중/최근7일 완료 티켓 수 (Epic/Task/Sub-task 분리).
인력 활동 요약은 별도 엔드포인트(/api/activity/{user}, 캐시)로 [+] 확장 시 조회.
"""

from datetime import datetime

from .names import real_name, staff_kind


def _avg(xs):
    return round(sum(xs) / len(xs), 2) if xs else 0.0


def build_workload(client, plan, people, jira_base="", generated_at=None):
    data = client.workload(plan, people)     # module -> [person dict]
    modules = []
    all_ip, all_done = [], []
    for m in plan["modules"]:
        # 본명(displayName 첫 어절) + 개발/운영(id 사번 접두) 파생 — 원본 비변형(캐시 공유 안전)
        rows = [dict(p, name=real_name(p.get("displayName") or p["id"]), kind=staff_kind(p["id"]))
                for p in data.get(m, [])]
        ip = [p["inProgress"]["task"] + p["inProgress"]["voc"] for p in rows]
        dn = [p["done7d"]["task"] + p["done7d"]["voc"] for p in rows]
        all_ip += ip
        all_done += dn
        modules.append({
            "module": m,
            "people": rows,
            "peopleCount": len(rows),
            "inProgressTotal": sum(ip),
            "done7dTotal": sum(dn),
            "avgInProgress": _avg(ip),      # 모듈 평균 (세로선)
            "avgDone7d": _avg(dn),
        })
    return {
        "generatedAt": generated_at or datetime.now().strftime("%Y-%m-%d %H:%M"),
        "projectKey": plan.get("project_key", "DL"),
        "jiraBase": jira_base,
        # 막대 최대값 = 데이터별 전체 최대값 (진행중/완료 각각). 모듈 평균은 막대 안 세로선으로 표시.
        "scaleInProgress": max(all_ip) if all_ip else 1,
        "scaleDone7d": max(all_done) if all_done else 1,
        "modules": modules,
    }
