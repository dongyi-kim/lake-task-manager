"""
기능3: 인력 워크로드 — 모듈별 인력의 진행중/최근7일 완료 티켓 수 (Epic/Task/Sub-task 분리).
인력 활동 요약은 별도 엔드포인트(/api/activity/{user}, 캐시)로 [+] 확장 시 조회.
"""

from datetime import datetime

from app.domain.names import real_name, staff_kind


def _avg(xs):
    return round(sum(xs) / len(xs), 2) if xs else 0.0


def workload_modules(people):
    """워크로드의 모듈 목록은 **people.yaml(모듈→인력)** 이 소스다(파일 순서 유지).
    wbs_config(plan) 의 모듈은 WBS 롤업 전용 — 인력이 없는 모듈도 워크로드엔 떠야 하고,
    반대로 WBS 는 없지만 인력만 있는 모듈도 있을 수 있어 두 목록을 섞지 않는다."""
    return list(people.keys())


def build_workload(client, plan, people, jira_base="", generated_at=None):
    data = client.workload(plan, people)     # module -> [person dict]
    modules = []
    all_ip, all_done = [], []
    for m in workload_modules(people):
        # 본명(displayName 첫 어절) + 개발/운영(id 사번 접두) 파생 — 원본 비변형(캐시 공유 안전)
        rows = [dict(p, name=real_name(p.get("displayName") or p["id"]), kind=staff_kind(p["id"]))
                for p in data.get(m, [])]
        # 헤더/칩 합계는 티켓 수(count) 기준(전 카테고리 합). 막대 메트릭·스케일·모듈평균은 프론트에서 계산.
        # 미착수(open)는 막대엔 안 섞고 배지·합계로만 노출(진행중 막대/모듈평균은 종전대로 유지).
        op = [sum(p.get("open", {}).get("count", {}).values()) for p in rows]
        ip = [sum(p["inProgress"]["count"].values()) for p in rows]
        dn = [sum(p["done7d"]["count"].values()) for p in rows]
        all_ip += ip
        all_done += dn
        modules.append({
            "module": m,
            "people": rows,
            "peopleCount": len(rows),
            "openTotal": sum(op),           # 미착수(To Do) 합계
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

def build_workload_shell(client, plan, people, jira_base="", generated_at=None):
    """골격 — 모듈 목록 + **인력 로스터(명단)**. 통계는 안 붙는다(사람 by 사람으로 뒤에 개별 로딩).
    이름은 **캐시된 displayName** 만 쓴다(상류에 안 붙어 빠르다) — 없으면 id, 개별 로딩이 채워 준다."""
    def roster(m):
        rows = []
        for pid in people.get(m, []):
            dn = client.display_name_cached(pid)     # 캐시 전용(상류 조회 없음)
            rows.append({"id": pid, "name": real_name(dn) if dn else pid, "kind": staff_kind(pid)})
        return rows
    mods = workload_modules(people)
    # 기본 표시 모듈 = **세션 사용자가 속한 모듈**(1인 로컬 앱이라 '켠 사람'이 곧 대상).
    # 사번(id) 대소문자 무시로 매칭. 못 찾으면 None → 프론트가 첫 모듈로 폴백.
    my_module = None
    try:
        me = ((client.current_user() or {}).get("id") or "").strip().lower()
        if me:
            for m in mods:
                if any((pid or "").strip().lower() == me for pid in people.get(m, [])):
                    my_module = m
                    break
    except Exception:
        my_module = None
    return {
        "generatedAt": generated_at or datetime.now().strftime("%Y-%m-%d %H:%M"),
        "projectKey": plan.get("project_key", "DL"),
        "jiraBase": jira_base,
        "myModule": my_module,
        "modules": [{"module": m, "peopleCount": len(people.get(m, [])), "people": roster(m)}
                    for m in mods],
    }


def build_workload_person(client, pid):
    """인력 **한 명**의 통계 행 — 로스터 행에 채워 넣을 값(이름·개발/운영·집계)."""
    b = client.workload_person(pid)
    return dict(b, name=real_name(b.get("displayName") or pid), kind=staff_kind(pid))


def build_workload_module(client, plan, people, module):
    """모듈 하나만 — 인력별 번들은 개별 캐시(workload:{env}:{pid})라 모듈끼리 겹쳐도 낭비 없음."""
    sub = {module: people.get(module, [])}
    rows_raw = client.workload({"modules": [module]}, sub).get(module, [])
    rows = [dict(p, name=real_name(p.get("displayName") or p["id"]), kind=staff_kind(p["id"]))
            for p in rows_raw]
    op = [sum(p.get("open", {}).get("count", {}).values()) for p in rows]
    ip = [sum(p["inProgress"]["count"].values()) for p in rows]
    dn = [sum(p["done7d"]["count"].values()) for p in rows]
    return {"module": module, "people": rows, "peopleCount": len(rows),
            "openTotal": sum(op), "inProgressTotal": sum(ip), "done7dTotal": sum(dn),
            "avgInProgress": _avg(ip), "avgDone7d": _avg(dn)}
