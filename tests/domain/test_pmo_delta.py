from __future__ import annotations

from app.domain import pmo_delta


def _data(progress=50, done=10, total=20, mock=0, a=40, b=60):
    return {
        "generatedAt": "2026-09-04 10:00",
        "rollup": {
            "pmo": {"progressPct": progress, "rawDoneSp": done,
                    "rawTotalSp": total, "rawMockSp": mock},
            "modules": [
                {"id": "A", "progressPct": a, "rawDoneSp": 4,
                 "rawTotalSp": 10, "rawMockSp": 0},
                {"id": "B", "progressPct": b, "rawDoneSp": 6,
                 "rawTotalSp": 10, "rawMockSp": 0},
            ],
        },
    }


def test_first_snapshot_is_explicitly_not_a_trend():
    got = pmo_delta.build(_data(), [])
    assert got["status"] == "first_snapshot"
    assert got["facts"] == []
    assert got["calculationAuthority"] == "ltm"


def test_delta_numbers_have_exact_parity_with_rollup_values():
    old = pmo_delta.snapshot(_data(progress=40, done=8, total=20, mock=2, a=20, b=60))
    got = pmo_delta.build(
        _data(progress=50, done=11, total=22, mock=3, a=40, b=60),
        [{"metric": old, "takenAt": 1_788_500_000}],
    )
    by_key = {row["metric"]: row for row in got["facts"]}
    assert by_key["progressPct"] == {
        "metric": "progressPct", "label": "전체 진척률", "before": 40.0,
        "current": 50.0, "delta": 10.0, "unit": "%p",
    }
    assert by_key["rawDoneSp"]["delta"] == 3.0
    assert by_key["rawTotalSp"]["delta"] == 2.0
    assert by_key["rawMockSp"]["delta"] == 1.0
    assert got["changedModules"][0]["id"] == "A"
    assert got["changedModules"][0]["delta"] == 20.0
    assert got["status"] == "improved"


def test_repeated_identical_snapshots_do_not_hide_last_actual_change():
    current = _data(progress=50)
    same = pmo_delta.snapshot(current)
    older = pmo_delta.snapshot(_data(progress=45))
    got = pmo_delta.build(current, [
        {"metric": same, "takenAt": 30},
        {"metric": same, "takenAt": 20},
        {"metric": older, "takenAt": 10},
    ])
    assert next(row for row in got["facts"] if row["metric"] == "progressPct")["delta"] == 5.0


def test_negative_progress_is_an_observation_not_an_invented_cause():
    got = pmo_delta.build(
        _data(progress=30),
        [{"metric": pmo_delta.snapshot(_data(progress=50)), "takenAt": 10}],
    )
    assert got["status"] == "regressed"
    assert any("원인 확인 필요" in row for row in got["risks"])
    assert all("원인은" not in row for row in got["risks"])
