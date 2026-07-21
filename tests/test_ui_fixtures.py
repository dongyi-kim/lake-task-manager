# -*- coding: utf-8 -*-
"""UI 회귀 검증 픽스처(DL-9000 Epic) 가드.

픽스처는 "UI 를 고칠 때 열어볼 티켓"이므로 두 가지가 깨지면 안 된다.
1) 검증 포인트별 티켓이 고정 키로 존재하고, 각자 노리는 데이터를 실제로 갖고 있을 것.
2) WBS·현안·워크로드 **집계에 섞이지 않을 것**(섞이면 대시보드 숫자가 오염된다).
"""
from app.world import get_world


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


def test_fixtures_excluded_from_dashboards():
    """대시보드 격리 — Epic 이 wbs_config 밖 + PMO_VIT 라벨 없음 + people.yaml 밖 담당자."""
    from app.settings import load_people, load_wbs_config

    plan = load_wbs_config()
    epic_keys = {e["key"] for t in plan["wbs"] for e in t["epics"]}
    assert "DL-9000" not in epic_keys, "픽스처 Epic 이 WBS 롤업에 들어감"

    w = _w()
    fx = [i for i in w.issues.values() if i["key"].startswith("DL-90")]
    assert not any("PMO_VIT" in i["labels"] for i in fx), "픽스처가 현안 트래킹에 노출됨"

    people = {u for members in (load_people() or {}).values() for u in (members or [])}
    assert not any(i["assignee"] in people for i in fx), "픽스처가 인력 워크로드에 집계됨"
