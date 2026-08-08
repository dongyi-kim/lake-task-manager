# -*- coding: utf-8 -*-
"""초안의 **구조·배치 규율** — 어떤 모양으로 만들고, 어디에 두고, 누구에게 나누는가.

여기서 지키는 것은 문장이 아니라 **코드가 보장하는 것**이다. 프롬프트로 지시한 규칙은
모델이 흔들리면 무너지지만, 이 파일이 지키는 것들은 모델이 무엇을 내든 성립해야 한다:

  · Sub-Task 는 승인 한 번으로 부모와 함께 실제 티켓이 된다(글로만 남지 않는다)
  · Epic Link 는 정말 Epic 일 때만 붙는다
  · 컴포넌트는 하나, 담당은 골고루, 신규 라벨은 표시된다

문장 품질(본문 4섹션·참조 관계 설명)은 `tools/agent_draft_eval.py` 가 실 LLM 으로 본다.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JIRA_ENV", "mock")

pytest.importorskip("langgraph", reason="requirements-agent.txt 미설치")

from app.agent import approval                                      # noqa: E402
from app.agent.workflow import graph as G                           # noqa: E402
from app.agent.workflow.agents.refiner import (Refiner, as_bulk_items,  # noqa: E402
                                               child_items)


@pytest.fixture(autouse=True)
def fake(monkeypatch, tmp_path):
    monkeypatch.setenv("LAKE_AGENT_PROVIDER", "fake")
    import app.infra.settings as S
    monkeypatch.setattr(S, "CACHE_DIR", tmp_path)
    approval.clear()
    yield
    approval.clear()


def _draft(**over):
    d = {"mode": "task", "items": [
        {"summary": "[ETL] 남은 토픽 Avro 전환 마무리", "type": "Task",
         "description": "<h3>배경</h3><p>DL-9072 후속</p>", "components": ["ETL"]}]}
    d["items"][0].update(over.pop("item", {}))
    d.update(over)
    return d


# ── children: 글이 아니라 티켓이 된다 ───────────────────────────────
def test_children_are_flattened_with_their_parent_index():
    """Sub-Task 는 부모 키가 있어야 만들어진다 — 부모가 아직 없으니 index 로 묶어 둔다."""
    d = _draft(item={"children": [{"summary": "topic-a 전환", "assignee": "skcc.x1042"},
                                  {"summary": "topic-b 전환"}]})
    rows = child_items(d)
    assert [r["parent_index"] for r in rows] == [0, 0]
    assert all(r["type"] == "Sub-Task" for r in rows)
    assert rows[0]["assignee"] == "skcc.x1042" and "assignee" not in rows[1]
    # 부모 배치(as_bulk_items)에는 Sub-Task 가 섞이지 않는다 — 섞이면 검증에서 통째로 반려된다
    assert all(i["type"] != "Sub-Task" for i in as_bulk_items(d))
    assert "children" not in as_bulk_items(d)[0]


def test_approval_fingerprint_covers_the_children():
    """화면에 보인 것과 만들어지는 것이 어긋나면 HITL 이 무의미하다 — 지문에 자식도 들어간다."""
    d = _draft(item={"children": [{"summary": "topic-a 전환"}]})
    tok = G._propose({"thread_id": "t1", "draft": d})["approval_token"]
    rec = approval.peek(tok)
    assert len(rec["payload"].get("children") or []) == 1
    assert rec["fp"] == approval.fingerprint(
        {"mode": "task", "items": as_bulk_items(d), "children": child_items(d)})


def test_one_approval_creates_the_whole_tree():
    """부모 생성 → 그 키로 Sub-Task 연쇄. 토큰 하나가 트리 전체를 보증한다."""
    from app.agent import tools as T
    from app.agent.tools._ctx import client
    d = _draft(item={"epic": "DL-101", "children": [
        {"summary": "topic-a 전환", "assignee": "skcc.x1042"},
        {"summary": "topic-b 전환", "assignee": "skcc.x1103"}]})
    tok = G._propose({"thread_id": "t-tree", "draft": d})["approval_token"]
    approval.approve(tok, "t-tree")
    r = T.BY_NAME["create_tickets"].invoke(
        {"mode": "task", "items": as_bulk_items(d), "children": child_items(d),
         "approval_token": tok})
    assert r.get("ok"), r
    keys = [c["key"] for c in r["created"]]
    assert len(keys) == 3, r["created"]
    kids = client().ticket_children(keys[0]) or []
    assert {k["key"] for k in kids} == set(keys[1:]), kids


def test_children_are_not_created_when_the_parent_failed():
    """부모가 없으면 자식도 만들지 않는다 — Jira 에는 롤백이 없어 반쯤 만든 트리가 가장 나쁘다."""
    d = _draft(item={"type": "없는타입", "children": [{"summary": "topic-a 전환"}]})
    tok = G._propose({"thread_id": "t-bad", "draft": d})["approval_token"]
    approval.approve(tok, "t-bad")
    from app.agent import tools as T
    r = T.BY_NAME["create_tickets"].invoke(
        {"mode": "task", "items": as_bulk_items(d), "children": child_items(d),
         "approval_token": tok})
    assert not r.get("ok")
    assert not (r.get("created") or []), r


# ── 배치 가드: Epic · 컴포넌트 · 라벨 ──────────────────────────────
def _applied(**over):
    out = {"questions": [], "mode": "task", "items": [dict(_draft(**over)["items"][0])],
           "rationale": ""}
    out.update({k: v for k, v in over.items() if k in ("structure", "structure_why")})
    return Refiner().apply({}, out)


def test_epic_link_is_dropped_when_the_key_is_not_an_epic():
    """실측: 모델이 Task(DL-9072)를 '기존 에픽'이라 답했다. 타입 확인은 판단이 아니라 조회다."""
    r = _applied(item={"epic": "DL-9072"})
    assert r["draft"]["items"][0]["epic"] == ""
    assert "Epic 이 아니" in r["draft"]["rationale"]


def test_a_real_epic_survives():
    r = _applied(item={"epic": "DL-101", "components": ["ETL"]})
    assert r["draft"]["items"][0]["epic"] == "DL-101"


def test_epic_module_mismatch_is_reported_not_silently_fixed():
    """어느 쪽이 틀렸는지는 사람이 판단한다 — 조용히 붙이면 남의 진척률이 오염된다."""
    r = _applied(item={"epic": "DL-101", "components": ["Workbench"]})   # DL-101 은 ETL Epic
    assert r["draft"]["items"][0]["epic"] == "DL-101", "고치지는 않는다"
    assert "확인 필요" in r["draft"]["rationale"]


def test_only_one_component_survives():
    """컴포넌트가 둘이면 워크로드가 이중 계상된다(knowledge/03)."""
    r = _applied(item={"components": ["ETL", "Catalog"]})
    assert r["draft"]["items"][0]["components"] == ["ETL"]
    assert "별도 티켓" in r["draft"]["rationale"]


def test_new_labels_are_flagged_but_not_blocked():
    """막지 않는다 — 사용자가 승인 화면에서 판단한다. 다만 조용히 새 라벨이 생기지는 않는다."""
    r = _applied(item={"labels": ["backend", "완전히새로운라벨"]})
    assert r["draft"]["items"][0]["labels"] == ["backend", "완전히새로운라벨"]
    assert r["draft"].get("new_labels") == ["완전히새로운라벨"]


# ── 분량 분할은 골고루 ──────────────────────────────────────────────
def test_volume_split_subtasks_get_spread_across_the_module():
    """'사람 나눠서' 라고 한 일을 한 사람에게 몰면 쪼갠 의미가 없다."""
    r = _applied(item={"components": ["ETL"], "children": [
        {"summary": "topic-a 전환", "assignee": "skcc.x1042"},
        {"summary": "topic-b 전환", "assignee": "skcc.x1042"},
        {"summary": "topic-c 전환", "assignee": "skcc.x1042"}]})
    owners = [c["assignee"] for c in r["draft"]["items"][0]["children"]]
    assert len(set(owners)) >= 2, owners
    from app.infra.settings import load_people
    assert set(owners) <= set(load_people()["ETL"]), "로스터 밖 사람을 지어내면 안 된다"


def test_functional_subtasks_keep_their_own_owners():
    """기능이 다른 일은 각자 그 일의 사람에게 — 골고루 규칙이 이걸 헤집으면 안 된다."""
    kids = [{"summary": "스키마 호환성 검증 스크립트 작성", "assignee": "skcc.x1042"},
            {"summary": "컨슈머 모니터링 대시보드 추가", "assignee": "skcc.i2011"},
            {"summary": "전환 후 회귀 테스트", "assignee": "skcc.x1103"}]
    r = _applied(item={"components": ["ETL"], "children": [dict(k) for k in kids]})
    assert [c["assignee"] for c in r["draft"]["items"][0]["children"]] == \
        [k["assignee"] for k in kids]


# ── 구조 판단은 드러나 있어야 한다 ──────────────────────────────────
def test_structure_choice_is_recorded_where_a_human_can_review_it():
    r = _applied(structure="task_with_subtasks",
                 structure_why="토픽 3개는 같은 산출물의 분량 분할")
    assert r["draft"]["structure"] == "task_with_subtasks"
    assert "분량 분할" in r["draft"]["rationale"]
