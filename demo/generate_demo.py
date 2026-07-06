"""
Lake Task Manager — 데모용 가상 데이터 생성기 (간트/타임라인 버전)
===================================================================
Module → WBS Task(Start~End 일정) → Epic → Story(SP) 계층의 가상 데이터를
만들고, Epic 진척률(SP 롤업)과 WBS/Module/PMO 다운스트림 롤업을 계산해
`demo/data.js` (window.LAKE_DEMO) 로 출력한다.

- stdlib only, 고정 seed → 결정적(재현 가능).
- 각 WBS Task 는 start/end 일정을 가지며(간트 바의 가로 길이),
  그 안을 진척률(Σ완료SP/Σ총SP)로 채운다.
- Epic 진척률 규칙은 ../jira_test.py:epic_progress 와 동일:
    * 완료 판정 = statusCategory.key == "done"  (상태명 하드코딩 금지)
    * SP=0 은 수학적으로 무해, 추가로 Bug/Ops 이슈타입은 이중 안전장치로 제외
    * mock 라벨 SP 는 분모에 포함하되 별도 항목으로 가시화

실행:  python demo/generate_demo.py
"""

import json
import os
import random
from datetime import date, datetime, timedelta

# ---- ../jira_test.py 와 동일한 도메인 규칙 ----
EXCLUDE_ISSUETYPES = {"Bug", "Ops", "운영"}
MOCK_LABEL = "mock"
SEED = 42
PROJECT_KEY = "LAKE"

# ============================================================
# 1. 조직 구조 (Data Lake 플랫폼) — Module 7 / WBS 15 / Epic 18
#    일부 Epic 은 여러 모듈이 다른 가중치로 공유(N:M).
# ============================================================
MODULES = [
    ("M1", "ETL"),
    ("M2", "Catalog"),
    ("M3", "Runtime"),
    ("M4", "Workbench"),
    ("M5", "SDK"),
    ("M6", "DevOps"),
    ("M7", "Observability"),
]

EPICS = {
    "E-ETL-CDC": "CDC 실시간 파이프라인",
    "E-ETL-BATCH": "배치 적재 프레임워크",
    "E-EVENT-SCHEMA": "이벤트 스트림 스키마",
    "E-CAT-REGISTRY": "카탈로그 레지스트리",
    "E-META-SCHEMA": "공통 메타데이터 스키마",
    "E-LINEAGE": "데이터 리니지 추적",
    "E-RT-ENGINE": "쿼리 실행 엔진",
    "E-RT-CACHE": "결과 캐시/가속",
    "E-API-GW": "공통 API 게이트웨이",
    "E-WB-NOTEBOOK": "노트북/쿼리 워크스페이스",
    "E-WB-DASH": "대시보드/시각화",
    "E-SDK-PY": "Python SDK",
    "E-SDK-JAVA": "JVM SDK",
    "E-AUTH-MODEL": "인증/권한 모델",
    "E-DO-IAC": "인프라 IaC 표준화",
    "E-DO-CICD": "CI/CD 파이프라인",
    "E-OB-METRICS": "메트릭/모니터링 스택",
    "E-OB-LOG": "로그/트레이싱",
}

# WBS Task: (wbsId, moduleId, 이름, start, end, [(epicId, weight), ...])
#   weight 는 WBS 내 합=1.0. 같은 Epic 이 여러 WBS/Module 에서 다른 weight 로 참여(N:M).
#   start/end = 간트 바의 시작~종료 (프로젝트 연간 일정, 2026)
WBS_TASKS = [
    ("W-ETL-1", "M1", "실시간 수집 체계", "2026-01-06", "2026-05-29",
        [("E-ETL-CDC", 0.6), ("E-EVENT-SCHEMA", 0.4)]),
    ("W-ETL-2", "M1", "배치 적재 표준화", "2026-03-02", "2026-08-14",
        [("E-ETL-BATCH", 0.7), ("E-META-SCHEMA", 0.3)]),

    ("W-CAT-1", "M2", "카탈로그 구축", "2026-02-02", "2026-07-31",
        [("E-CAT-REGISTRY", 0.6), ("E-META-SCHEMA", 0.4)]),
    ("W-CAT-2", "M2", "리니지 연동", "2026-05-04", "2026-10-30",
        [("E-LINEAGE", 0.5), ("E-META-SCHEMA", 0.5)]),

    ("W-RT-1", "M3", "쿼리 실행 엔진", "2026-01-19", "2026-06-30",
        [("E-RT-ENGINE", 0.7), ("E-EVENT-SCHEMA", 0.3)]),
    ("W-RT-2", "M3", "쿼리 가속", "2026-04-01", "2026-09-30",
        [("E-RT-CACHE", 0.6), ("E-RT-ENGINE", 0.4)]),
    ("W-RT-3", "M3", "API 게이트웨이", "2026-06-01", "2026-11-30",
        [("E-API-GW", 0.8), ("E-RT-ENGINE", 0.2)]),

    ("W-WB-1", "M4", "쿼리 워크스페이스", "2026-03-16", "2026-09-15",
        [("E-WB-NOTEBOOK", 0.7), ("E-API-GW", 0.3)]),
    ("W-WB-2", "M4", "대시보드", "2026-06-15", "2026-12-15",
        [("E-WB-DASH", 0.6), ("E-AUTH-MODEL", 0.4)]),

    ("W-SDK-1", "M5", "Python SDK", "2026-02-16", "2026-07-17",
        [("E-SDK-PY", 0.7), ("E-API-GW", 0.3)]),
    ("W-SDK-2", "M5", "JVM SDK & 인증", "2026-05-18", "2026-11-13",
        [("E-SDK-JAVA", 0.6), ("E-AUTH-MODEL", 0.4)]),

    ("W-DO-1", "M6", "플랫폼 IaC", "2026-01-05", "2026-06-05",
        [("E-DO-IAC", 0.6), ("E-AUTH-MODEL", 0.4)]),
    ("W-DO-2", "M6", "CI/CD", "2026-04-06", "2026-10-09",
        [("E-DO-CICD", 0.7), ("E-DO-IAC", 0.3)]),

    ("W-OB-1", "M7", "모니터링 스택", "2026-03-02", "2026-08-31",
        [("E-OB-METRICS", 0.6), ("E-META-SCHEMA", 0.4)]),
    ("W-OB-2", "M7", "로그/리니지", "2026-06-01", "2026-12-11",
        [("E-OB-LOG", 0.5), ("E-LINEAGE", 0.5)]),
]

SP_CHOICES = [1, 2, 3, 5, 8]


# ============================================================
# 2. Story 생성 (Epic 별) — 결정적
# ============================================================
def _gen_stories(rng, epic_id, maturity):
    stories = []
    n = rng.randint(4, 8)
    for i in range(1, n + 1):
        key = f"{epic_id}-{i}"
        roll = rng.random()
        if roll < 0.12:
            stories.append({
                "key": key, "summary": f"버그 수정 #{i}",
                "type": "Bug", "sp": 0,
                "statusCategory": rng.choice(["todo", "inprogress", "done"]),
                "labels": [],
            })
        elif roll < 0.28:
            stories.append({
                "key": key, "summary": f"[Mock] 추정 작업 #{i}",
                "type": "Story", "sp": rng.choice([3, 5, 8]),
                "statusCategory": "todo",
                "labels": [MOCK_LABEL],
            })
        else:
            sp = rng.choice(SP_CHOICES)
            if rng.random() < maturity:
                status = "done"
            else:
                status = "inprogress" if rng.random() < 0.45 else "todo"
            stories.append({
                "key": key, "summary": f"작업 #{i}",
                "type": "Story", "sp": sp,
                "statusCategory": status,
                "labels": [],
            })
    return stories


def epic_progress(epic_id, stories):
    """단일 Epic 의 SP 기반 진척률. ../jira_test.py:epic_progress 와 동일 규칙."""
    total_sp = 0.0
    done_sp = 0.0
    mock_sp = 0.0
    counted = 0
    excluded = 0
    for s in stories:
        if s["type"] in EXCLUDE_ISSUETYPES:      # 이중 안전장치
            excluded += 1
            continue
        sp = s["sp"] or 0
        total_sp += sp
        counted += 1
        if MOCK_LABEL in s["labels"]:
            mock_sp += sp
        if s["statusCategory"] == "done":        # 완료 판정 = statusCategory
            done_sp += sp
    pct = (done_sp / total_sp * 100) if total_sp > 0 else 0.0
    return {
        "key": epic_id,
        "name": EPICS[epic_id],
        "doneSp": round(done_sp, 1),
        "totalSp": round(total_sp, 1),
        "mockSp": round(mock_sp, 1),
        "progressPct": round(pct, 1),
        "countedIssues": counted,
        "excludedIssues": excluded,
        "stories": stories,
    }


# ============================================================
# 3. 롤업 (WBS → Module → PMO) + 일정
# ============================================================
def _d(s):
    return datetime.strptime(s, "%Y-%m-%d").date()


def _iso(d):
    return d.isoformat()


def _wavg(pairs):
    wsum = sum(w for _, w in pairs)
    if wsum <= 0:
        return 0.0
    return sum(p * w for p, w in pairs) / wsum


def _epic_occurrence_dates(w_start, w_end, i, n):
    """WBS 일정 안에서 i번째 Epic 이 차지하는 하위 구간(간트 nesting)."""
    span = (w_end - w_start).days
    s_off = int(span * (0.05 + 0.30 * (i / max(n, 1))))
    e_off = int(span * (0.05 + 0.20 * ((n - 1 - i) / max(n, 1))))
    es = w_start + timedelta(days=s_off)
    ee = w_end - timedelta(days=e_off)
    if ee <= es:
        ee = es + timedelta(days=14)
    return es, ee


def build():
    rng = random.Random(SEED)

    epics = {}
    for epic_id in EPICS:
        maturity = rng.uniform(0.1, 0.9)
        stories = _gen_stories(rng, epic_id, maturity)
        epics[epic_id] = epic_progress(epic_id, stories)

    all_starts, all_ends = [], []

    wbs_out = []
    for wbs_id, module_id, name, w_start_s, w_end_s, epic_weights in WBS_TASKS:
        w_start, w_end = _d(w_start_s), _d(w_end_s)
        all_starts.append(w_start)
        all_ends.append(w_end)
        n = len(epic_weights)
        parts = []
        eff_sp = 0.0
        for i, (epic_id, weight) in enumerate(epic_weights):
            ep = epics[epic_id]
            es, ee = _epic_occurrence_dates(w_start, w_end, i, n)
            parts.append({
                "epicKey": epic_id,
                "epicName": ep["name"],
                "weight": weight,
                "epicPct": ep["progressPct"],
                "contribution": round(ep["progressPct"] * weight, 1),
                "start": _iso(es),
                "end": _iso(ee),
            })
            eff_sp += weight * ep["totalSp"]
        wbs_pct = _wavg([(p["epicPct"], p["weight"]) for p in parts])
        wbs_out.append({
            "id": wbs_id,
            "moduleId": module_id,
            "name": name,
            "start": w_start_s,
            "end": w_end_s,
            "progressPct": round(wbs_pct, 1),
            "effectiveSp": round(eff_sp, 1),
            "epics": parts,
        })

    modules_out = []
    for module_id, module_name in MODULES:
        m_wbs = [w for w in wbs_out if w["moduleId"] == module_id]
        m_pct = _wavg([(w["progressPct"], w["effectiveSp"]) for w in m_wbs])
        m_eff = sum(w["effectiveSp"] for w in m_wbs)
        m_start = min(_d(w["start"]) for w in m_wbs)
        m_end = max(_d(w["end"]) for w in m_wbs)
        epic_ids = {e["epicKey"] for w in m_wbs for e in w["epics"]}
        modules_out.append({
            "id": module_id,
            "name": module_name,
            "progressPct": round(m_pct, 1),
            "effectiveSp": round(m_eff, 1),
            "start": _iso(m_start),
            "end": _iso(m_end),
            "wbsIds": [w["id"] for w in m_wbs],
            "rawDoneSp": round(sum(epics[e]["doneSp"] for e in epic_ids), 1),
            "rawTotalSp": round(sum(epics[e]["totalSp"] for e in epic_ids), 1),
            "rawMockSp": round(sum(epics[e]["mockSp"] for e in epic_ids), 1),
        })

    pmo_pct = _wavg([(m["progressPct"], m["effectiveSp"]) for m in modules_out])
    raw_done = sum(e["doneSp"] for e in epics.values())
    raw_total = sum(e["totalSp"] for e in epics.values())
    raw_mock = sum(e["mockSp"] for e in epics.values())

    proj_start = min(all_starts)
    proj_end = max(all_ends)

    data = {
        "generatedAt": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "projectKey": PROJECT_KEY,
        "seed": SEED,
        "timeline": {"start": _iso(proj_start), "end": _iso(proj_end)},
        "modules": [
            {"id": m[0], "name": m[1]} for m in MODULES
        ],
        "epics": epics,
        "wbs": wbs_out,
        "rollup": {
            "pmo": {
                "progressPct": round(pmo_pct, 1),
                "rawDoneSp": round(raw_done, 1),
                "rawTotalSp": round(raw_total, 1),
                "rawMockSp": round(raw_mock, 1),
                "moduleCount": len(MODULES),
                "wbsCount": len(WBS_TASKS),
                "epicCount": len(EPICS),
            },
            "modules": modules_out,
        },
    }
    return data


# ============================================================
# 4. self-check
# ============================================================
def _self_check(data):
    for eid, e in data["epics"].items():
        assert 0 <= e["progressPct"] <= 100, f"{eid} pct out of range"
        assert e["doneSp"] <= e["totalSp"] + 1e-9, f"{eid} done>total"
        assert e["mockSp"] <= e["totalSp"] + 1e-9, f"{eid} mock>total"
    for w in data["wbs"]:
        wsum = sum(p["weight"] for p in w["epics"])
        assert abs(wsum - 1.0) < 1e-6, f"{w['id']} weights sum={wsum} (must be 1.0)"
        assert w["start"] < w["end"], f"{w['id']} start>=end"
    t = data["timeline"]
    assert t["start"] < t["end"]
    assert 0 <= data["rollup"]["pmo"]["progressPct"] <= 100
    return True


def main():
    data = build()
    _self_check(data)

    out_dir = os.path.dirname(os.path.abspath(__file__))
    out_path = os.path.join(out_dir, "data.js")
    payload = json.dumps(data, ensure_ascii=False, indent=2)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("// AUTO-GENERATED by generate_demo.py — 직접 편집 금지. 재생성: python demo/generate_demo.py\n")
        f.write("window.LAKE_DEMO = ")
        f.write(payload)
        f.write(";\n")

    pmo = data["rollup"]["pmo"]
    story_total = sum(len(e["stories"]) for e in data["epics"].values())
    t = data["timeline"]
    print(f"[OK] data.js written: {out_path}")
    print(f"     Modules={pmo['moduleCount']}  WBS={pmo['wbsCount']}  "
          f"Epics={pmo['epicCount']}  Stories={story_total}")
    print(f"     Timeline {t['start']} ~ {t['end']}")
    print(f"     PMO progress (weighted) = {pmo['progressPct']}%  |  "
          f"raw SP done/total = {pmo['rawDoneSp']}/{pmo['rawTotalSp']}  "
          f"(mock {pmo['rawMockSp']})")
    print("     self-check passed (pct 0-100, done<=total, WBS weight sum=1.0, start<end)")


if __name__ == "__main__":
    main()
