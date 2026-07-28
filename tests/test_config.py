"""config 로더/검증 (실제 wbs_config.yaml + 검증 규칙)."""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.infra import settings   # noqa: E402


def test_real_plan_loads_and_validates():
    plan = settings.load_plan()
    assert plan["project_key"] == "DL"
    # 실 모듈 7 + UI 픽스처용 TEST 1 (dev config 한정 — app.world.FIX_MODULE)
    assert len([m for m in plan["modules"] if m != "TEST"]) == 7
    assert len(plan["wbs"]) >= 10
    # 가중치는 상대값 — 합=1 강제 아님. 양수이기만 하면 됨. epic 키는 DL-xxxx.
    for w in plan["wbs"]:
        assert sum(e["weight"] for e in w["epics"]) > 0
        assert all(e["key"].startswith("DL-") for e in w["epics"])


def test_people_loads():
    people = settings.load_people()
    assert set(people.keys()) <= set(settings.load_plan()["modules"])
    assert all(isinstance(v, list) and v for v in people.values())


def test_nonunit_weights_ok():
    # 정수/비1 합도 허용 (자동 정규화)
    ok = {"project_key": "DL", "modules": ["A"],
          "wbs": [{"id": "A-1", "module": "A", "name": "w", "start": "2026-01-01",
                   "end": "2026-02-01", "epics": [{"key": "DL-1", "weight": 6},
                                                  {"key": "DL-2", "weight": 4}]}]}
    assert settings.validate_plan(ok) is True


def test_zero_weight_rejected():
    bad = {"project_key": "DL", "modules": ["A"],
           "wbs": [{"id": "A-1", "module": "A", "name": "w", "start": "2026-01-01",
                    "end": "2026-02-01", "epics": [{"key": "DL-1", "weight": 0}]}]}
    with pytest.raises(ValueError):
        settings.validate_plan(bad)


def test_bad_dates_rejected():
    bad = {"project_key": "DL", "modules": ["A"],
           "wbs": [{"id": "A-1", "module": "A", "name": "w", "start": "2026-03-01",
                    "end": "2026-02-01", "epics": [{"key": "DL-1", "weight": 1}]}]}
    with pytest.raises(ValueError):
        settings.validate_plan(bad)
