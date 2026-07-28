# -*- coding: utf-8 -*-
"""UI 회귀 검증 픽스처(DL-9000 Epic) 가드.

픽스처는 "UI 를 고칠 때 열어볼 티켓"이므로 두 가지가 깨지면 안 된다.
1) 검증 포인트별 티켓이 고정 키로 존재하고, 각자 노리는 데이터를 실제로 갖고 있을 것.
2) WBS·현안·워크로드 **집계에 섞이지 않을 것**(섞이면 대시보드 숫자가 오염된다).
"""
from app.mock.world import get_world


def _w():
    return get_world()


def test_fixture_epic_and_children_exist():
    w = _w()
    epic = w.issues["DL-9000"]
    assert epic["type"] == "Epic"
    kids = [i for i in w.issues.values() if i.get("epicKey") == "DL-9000"]
    assert len(kids) >= 10
    # 제목이 곧 검증 항목이라 [UI] 접두어를 강제한다
    assert all(i["summary"].startswith("[UI]") for i in kids)


def test_fixture_data_actually_present():
    """각 픽스처가 '노리는 데이터'를 정말 갖고 있는지 — 빈 껍데기면 검증용으로 무의미."""
    w = _w()
    assert w.issues["DL-9004"]["links"], "관련 Task 픽스처에 링크 없음"
    assert len(w.issues["DL-9006"]["attachments"]) >= 3, "첨부 픽스처에 첨부 없음"
    assert len(w.issues["DL-9007"]["comments"]) >= 4, "코멘트 픽스처에 코멘트 없음"
    assert w.issues["DL-9010"]["changelog"], "타임라인 픽스처에 변경이력 없음"
    assert w.issues["DL-9008"]["due"] < w.today, "마감초과 픽스처가 초과 상태가 아님"
    # 설명 없는 Sub-Task ↔ 설명 있는 부모 (상위 설명 펼침 검증용)
    assert w.issues["DL-9013"]["description"].strip() == ""
    assert w.issues["DL-9013"]["parentKey"] == "DL-9012"
    assert w.issues["DL-9012"]["description"].strip() != ""
    # Heading 4 레벨
    assert all(h in w.issues["DL-9002"]["description"] for h in ("h1.", "h2.", "h3.", "h4."))


def test_fixtures_reachable_from_all_three_dashboards():
    """찾기 쉬워야 한다 — WBS(TEST 모듈)·현안(PMO_VIT)·워크로드(TEST 인력) 세 경로 모두에서 도달 가능."""
    from app.infra.settings import load_people, load_wbs_config
    from app.mock.world import FIX_MODULE, FIX_USERS

    plan = load_wbs_config()
    assert FIX_MODULE in plan["modules"], "WBS 에 TEST 모듈이 없음"
    epic_keys = {e["key"] for t in plan["wbs"] if t["module"] == FIX_MODULE for e in t["epics"]}
    assert "DL-9000" in epic_keys, "TEST 모듈 WBS 가 픽스처 Epic 을 안 가리킴"

    assert "PMO_VIT" in _w().issues["DL-9000"]["labels"], "픽스처 Epic 이 현안에 안 뜸"

    people = load_people() or {}
    assert set(people.get(FIX_MODULE) or []) == set(FIX_USERS), "워크로드 TEST 모듈 인력 불일치"


def test_test_module_contains_only_fixtures():
    """TEST 모듈에 랜덤 데이터가 섞이면 픽스처를 찾는 의미가 없다 — 생성기가 이 모듈을 건너뛰어야 한다."""
    w = _w()
    from app.mock.world import FIX_MODULE

    in_test = [i for i in w.issues.values() if i["module"] == FIX_MODULE]
    assert in_test, "TEST 모듈이 비어 있음"
    assert all(i["key"].startswith("DL-90") for i in in_test), \
        "TEST 모듈에 랜덤 생성 티켓이 섞임 — gen_modules 에서 TEST 를 제외했는지 확인"


def test_fixtures_absent_from_prod_config():
    """prod 배포 config 는 별도 파일 — 거기엔 TEST 모듈이 없어야 한다."""
    import pathlib

    import yaml

    prod = pathlib.Path(__file__).resolve().parents[2] / "config" / "wbs_config.yaml"
    if not prod.exists():          # 개발 repo 단독 체크아웃이면 스킵
        return
    raw = yaml.safe_load(prod.read_text(encoding="utf-8")) or {}
    mods = {m.get("module") for m in (raw.get("modules") or [])}
    assert "TEST" not in mods, "운영 배포 config 에 TEST 모듈이 들어감"
