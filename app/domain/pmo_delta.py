"""Deterministic current-versus-previous PMO briefing facts.

MAXIS/Dify may explain these facts, but it never recalculates them.  Every
number is derived from the same WBS rollup payload that the dashboard renders.
The wording deliberately distinguishes observed changes from causes that still
need investigation.
"""

from __future__ import annotations

from datetime import datetime
from typing import Iterable, Mapping


def _number(mapping: Mapping, key: str) -> float:
    value = mapping.get(key, 0)
    if isinstance(value, bool):
        return 0.0
    try:
        return round(float(value or 0), 1)
    except (TypeError, ValueError):
        return 0.0


def _snapshot_projection(data: Mapping) -> dict:
    rollup = data.get("rollup") if isinstance(data.get("rollup"), Mapping) else {}
    pmo = rollup.get("pmo") if isinstance(rollup.get("pmo"), Mapping) else data.get("pmo")
    pmo = pmo if isinstance(pmo, Mapping) else {}
    modules = rollup.get("modules") if isinstance(rollup.get("modules"), list) else data.get("modules")
    modules = modules if isinstance(modules, list) else []
    return {
        "pmo": {
            key: _number(pmo, key)
            for key in ("progressPct", "rawDoneSp", "rawTotalSp", "rawMockSp")
        },
        "modules": [
            {
                "id": str(row.get("id") or ""),
                "progressPct": _number(row, "progressPct"),
                "rawDoneSp": _number(row, "rawDoneSp"),
                "rawTotalSp": _number(row, "rawTotalSp"),
                "rawMockSp": _number(row, "rawMockSp"),
            }
            for row in modules if isinstance(row, Mapping) and row.get("id")
        ],
    }


def snapshot(data: Mapping) -> dict:
    """Return the compact full-fidelity metric stored in cache history."""
    projected = _snapshot_projection(data)
    return {
        "contract": "pmo-snapshot.v1",
        "pmo": projected["pmo"],
        "modules": projected["modules"],
        "generatedAt": str(data.get("generatedAt") or ""),
    }


def _signature(data: Mapping) -> tuple:
    projected = _snapshot_projection(data)
    pmo = projected["pmo"]
    modules = tuple(
        (row["id"], row["progressPct"], row["rawDoneSp"], row["rawTotalSp"], row["rawMockSp"])
        for row in sorted(projected["modules"], key=lambda item: item["id"])
    )
    return tuple(pmo[key] for key in sorted(pmo)) + modules


def _previous(history: Iterable[Mapping], current: Mapping) -> Mapping | None:
    rows = [row for row in history if isinstance(row, Mapping)
            and isinstance(row.get("metric"), Mapping)]
    if not rows:
        return None
    current_signature = _signature(current)
    # Reopening a dashboard often records the same snapshot repeatedly. Prefer
    # the most recent actual change; use the latest identical row if no change exists.
    for row in rows:
        if _signature(row["metric"]) != current_signature:
            return row
    return rows[0]


def _timestamp(value: object) -> str:
    try:
        return datetime.fromtimestamp(float(value)).astimezone().isoformat(timespec="minutes")
    except (TypeError, ValueError, OSError):
        return ""


def build(current: Mapping, history: Iterable[Mapping]) -> dict:
    """Build typed delta facts. Narrative causes remain explicitly unconfirmed."""
    cur = _snapshot_projection(current)
    previous_row = _previous(history, current)
    if previous_row is None:
        return {
            "contract": "pmo-delta-brief.v1",
            "status": "first_snapshot",
            "baselineAt": "",
            "facts": [],
            "changedModules": [],
            "risks": ["비교 기준 스냅샷이 아직 없어 변화 추세를 판단할 수 없음"],
            "actions": ["다음 조회부터 동일 산식의 이전 스냅샷과 비교"],
            "calculationAuthority": "ltm",
        }

    prev = _snapshot_projection(previous_row["metric"])
    fields = (
        ("progressPct", "전체 진척률", "%p"),
        ("rawDoneSp", "완료 Story Point", "SP"),
        ("rawTotalSp", "전체 Story Point", "SP"),
        ("rawMockSp", "mock Story Point", "SP"),
    )
    facts = []
    for key, label, unit in fields:
        before, after = prev["pmo"][key], cur["pmo"][key]
        facts.append({
            "metric": key,
            "label": label,
            "before": before,
            "current": after,
            "delta": round(after - before, 1),
            "unit": unit,
        })

    prior_modules = {row["id"]: row for row in prev["modules"]}
    module_changes = []
    for row in cur["modules"]:
        prior = prior_modules.get(row["id"])
        if prior is None:
            module_changes.append({"id": row["id"], "before": None,
                                   "current": row["progressPct"], "delta": None,
                                   "status": "new_baseline"})
            continue
        delta = round(row["progressPct"] - prior["progressPct"], 1)
        if delta:
            module_changes.append({"id": row["id"], "before": prior["progressPct"],
                                   "current": row["progressPct"], "delta": delta,
                                   "status": "changed"})
    module_changes.sort(key=lambda row: abs(row["delta"] or 0), reverse=True)

    delta_by_key = {row["metric"]: row["delta"] for row in facts}
    progress_delta = delta_by_key["progressPct"]
    risks = []
    actions = []
    if progress_delta < 0:
        risks.append("전체 진척률 하락 관측 — 분모 변경·일정 조정·데이터 보정 중 원인 확인 필요")
    if delta_by_key["rawTotalSp"] > 0:
        risks.append("전체 Story Point 증가 관측 — 신규 범위 또는 추정 보정 여부 확인 필요")
    if delta_by_key["rawMockSp"] > 0:
        risks.append("mock Story Point 증가 관측 — 실측 Story Point 전환 계획 확인 필요")
    if module_changes:
        actions.append("변화 폭이 큰 모듈의 Epic·Task 변경 내역 검토")
    if not risks and not module_changes and not any(delta_by_key.values()):
        risks.append("이전 스냅샷 대비 집계값 변화 없음")
    if risks and not actions:
        actions.append("관측 변화의 Jira 원인 자료를 추가 조회한 뒤 판단")

    status = "improved" if progress_delta > 0 else "regressed" if progress_delta < 0 else "unchanged"
    return {
        "contract": "pmo-delta-brief.v1",
        "status": status,
        "baselineAt": _timestamp(previous_row.get("takenAt")),
        "facts": facts,
        "changedModules": module_changes[:5],
        "risks": risks,
        "actions": actions,
        "calculationAuthority": "ltm",
    }


__all__ = ["build", "snapshot"]
