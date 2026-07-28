"""
다운스트림 롤업 — 순수 계산.
Epic 진척률(재료) + wbs_config.yaml(매핑/가중치/일정) → WBS/Module/PMO 조합 + Gantt JSON.

출력 형태는 데모 프론트(window.LAKE_DEMO)와 동일해 `app/static` 의 Gantt 를 그대로 재사용한다.
"""

from datetime import date, datetime, timedelta


def _as_date(v):
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    return datetime.strptime(str(v), "%Y-%m-%d").date()


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


def build(plan, epic_prog, generated_at=None):
    """
    plan       : 정규화된 plan dict (wbs_config.yaml → project_key, modules, epics{id:name}, wbs[])
    epic_prog  : {epic_key -> {doneSp,totalSp,mockSp,progressPct, name?}}
    """
    epic_names = plan.get("epics", {}) or {}

    def prog(key):
        p = epic_prog.get(key, {})
        return {
            "key": key,
            "name": p.get("name") or epic_names.get(key, key),
            "doneSp": round(p.get("doneSp", 0.0), 1),
            "totalSp": round(p.get("totalSp", 0.0), 1),
            "mockSp": round(p.get("mockSp", 0.0), 1),
            "progressPct": round(p.get("progressPct", 0.0), 1),
        }

    # 참여하는 Epic 전체
    epics_out = {}
    for w in plan["wbs"]:
        for e in w["epics"]:
            if e["key"] not in epics_out:
                epics_out[e["key"]] = prog(e["key"])

    all_starts, all_ends = [], []
    wbs_out = []
    for w in plan["wbs"]:
        w_start, w_end = _as_date(w["start"]), _as_date(w["end"])
        all_starts.append(w_start)
        all_ends.append(w_end)
        n = len(w["epics"])
        wsum = sum(e["weight"] for e in w["epics"]) or 1.0    # 상대 가중치 → 자동 정규화
        parts = []
        eff_sp = 0.0
        wbs_pct = 0.0
        for i, e in enumerate(w["epics"]):
            ep = epics_out[e["key"]]
            es, ee = _epic_occurrence_dates(w_start, w_end, i, n)
            nw = e["weight"] / wsum                            # 정규화 가중치 (합=1)
            parts.append({
                "epicKey": e["key"],
                "epicName": ep["name"],
                "weight": e["weight"],                         # 원본(정수) — 표시용
                "weightNorm": round(nw, 3),
                "epicPct": ep["progressPct"],
                "contribution": round(ep["progressPct"] * nw, 1),
                "start": _iso(es),
                "end": _iso(ee),
            })
            wbs_pct += ep["progressPct"] * nw
            eff_sp += nw * ep["totalSp"]
        wbs_out.append({
            "id": w["id"],
            "moduleId": w["module"],
            "name": w["name"],
            "start": _iso(w_start),
            "end": _iso(w_end),
            "progressPct": round(wbs_pct, 1),
            "effectiveSp": round(eff_sp, 1),
            "epics": parts,
        })

    modules_out = []
    for module_id in plan["modules"]:
        m_wbs = [w for w in wbs_out if w["moduleId"] == module_id]
        if not m_wbs:
            continue
        m_pct = _wavg([(w["progressPct"], w["effectiveSp"]) for w in m_wbs])
        epic_ids = {e["epicKey"] for w in m_wbs for e in w["epics"]}
        modules_out.append({
            "id": module_id,
            "name": module_id,
            "progressPct": round(m_pct, 1),
            "effectiveSp": round(sum(w["effectiveSp"] for w in m_wbs), 1),
            "start": _iso(min(_as_date(w["start"]) for w in m_wbs)),
            "end": _iso(max(_as_date(w["end"]) for w in m_wbs)),
            "wbsIds": [w["id"] for w in m_wbs],
            "rawDoneSp": round(sum(epics_out[e]["doneSp"] for e in epic_ids), 1),
            "rawTotalSp": round(sum(epics_out[e]["totalSp"] for e in epic_ids), 1),
            "rawMockSp": round(sum(epics_out[e]["mockSp"] for e in epic_ids), 1),
        })

    pmo_pct = _wavg([(m["progressPct"], m["effectiveSp"]) for m in modules_out])
    raw_done = sum(e["doneSp"] for e in epics_out.values())
    raw_total = sum(e["totalSp"] for e in epics_out.values())
    raw_mock = sum(e["mockSp"] for e in epics_out.values())

    return {
        "generatedAt": generated_at or datetime.now().strftime("%Y-%m-%d %H:%M"),
        "projectKey": plan.get("project_key", "LAKE"),
        "timeline": {"start": _iso(min(all_starts)), "end": _iso(max(all_ends))},
        "modules": [{"id": m["id"], "name": m["name"]} for m in modules_out],
        "epics": epics_out,
        "wbs": wbs_out,
        "rollup": {
            "pmo": {
                "progressPct": round(pmo_pct, 1),
                "rawDoneSp": round(raw_done, 1),
                "rawTotalSp": round(raw_total, 1),
                "rawMockSp": round(raw_mock, 1),
                "moduleCount": len(modules_out),
                "wbsCount": len(wbs_out),
                "epicCount": len(epics_out),
            },
            "modules": modules_out,
        },
    }
