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
from app.agent.workflow.state import Intent                          # noqa: E402


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


# ── 초안이 비지 않게: 코드가 채우는 것들 ────────────────────────────
def test_component_is_filled_from_the_title_prefix():
    """제목 규약이 '[모듈] …' 이다 — 필드에 빠뜨리면 워크로드 집계에서 통째로 사라진다."""
    r = _applied(item={"summary": "[ETL] 적재 재시도 로직 추가", "components": []})
    assert r["draft"]["items"][0]["components"] == ["ETL"]


def test_an_unknown_prefix_is_not_forced_into_a_component():
    r = _applied(item={"summary": "[긴급] 뭔가 하기", "components": []})
    assert not r["draft"]["items"][0].get("components")


def test_empty_child_owners_get_spread_across_the_module():
    """'사람 나눠서' 라고 한 일에 담당이 하나도 없으면 나눈 의미가 없다.
    Assigner 는 상위 items 만 보므로(자식은 그 뒤에 생긴다) 여기서 코드가 채운다."""
    r = _applied(item={"components": ["ETL"], "children": [
        {"summary": "1~10번 테이블 등록"}, {"summary": "11~20번 테이블 등록"},
        {"summary": "21~30번 테이블 등록"}]})
    owners = [c.get("assignee") for c in r["draft"]["items"][0]["children"]]
    assert all(owners) and len(set(owners)) >= 2, owners
    from app.infra.settings import load_people
    assert set(owners) <= set(load_people()["ETL"])


def test_explicit_child_owners_are_left_alone():
    """사용자가 '성능 측정은 x1402' 라고 지정한 것을 코드가 뒤엎으면 안 된다."""
    r = _applied(item={"components": ["ETL"], "children": [
        {"summary": "성능 측정", "assignee": "skcc.x1042"},
        {"summary": "가이드 작성"}]})
    kids = r["draft"]["items"][0]["children"]
    assert kids[0]["assignee"] == "skcc.x1042"
    assert kids[1]["assignee"] and kids[1]["assignee"] != "skcc.x1042", "빈 것만 채운다"


def test_saying_it_will_split_without_children_is_flagged():
    """'나눠서 진행한다'고 판단해 놓고 children 이 없으면 그건 판단이 아니라 말뿐이다."""
    r = _applied(structure="task_with_subtasks", structure_why="여러 사람이 나눠서")
    assert "확인 필요" in r["draft"]["rationale"]


def test_a_creation_request_never_turns_into_an_edit_of_someone_elses_ticket():
    """조사에서 비슷한 티켓이 나왔다고 그걸 고치면, 부탁받은 생성은 사라지고
    시키지도 않은 수정이 승인 카드에 오른다(실측)."""
    out = {"questions": [], "mode": "task", "items": [dict(_draft()["items"][0])],
           "change": {"key": "DL-9090", "summary": "제목 바꾸기"}, "rationale": ""}
    r = Refiner().apply({"intent": Intent.PLAN_WORK}, out)
    assert not r["change_plan"], r["change_plan"]
    assert "변경하지 않았다" in r["draft"]["rationale"]
    assert r["draft"]["items"], "생성 초안은 그대로 남아야 한다"


def test_an_explicit_modify_request_still_produces_a_change_plan():
    out = {"questions": [], "mode": "task", "items": [],
           "change": {"key": "DL-9090", "priority": "P1-Critical"}, "rationale": ""}
    r = Refiner().apply({"intent": Intent.MODIFY}, out)
    assert r["change_plan"].get("key") == "DL-9090"


def test_promoting_to_an_epic_stops_when_one_of_that_name_already_exists():
    """Epic 은 진척 보고 단위다 — 중복이 생기면 둘 다 영원히 60% 에서 멈춘다."""
    out = {"questions": [], "mode": "epic", "rationale": "",
           "items": [{"summary": "[ETL] 쿼리 성능 개선", "type": "Epic", "epic_name": "쿼리개선"}]}
    r = Refiner().apply({}, out)
    assert not r["draft"]["items"], "중복 Epic 을 그대로 만들면 안 된다"
    q = r["questions"][0]
    assert q["kind"] == "choice" and q["field"] == "epic"
    assert "Epic 격상 보류" in r["draft"]["rationale"]


def test_a_genuinely_new_epic_is_not_blocked():
    out = {"questions": [], "mode": "epic", "rationale": "",
           "items": [{"summary": "[ETL] 사내 표준 스키마 레지스트리 이관", "type": "Epic",
                      "epic_name": "레지스트리이관"}]}
    r = Refiner().apply({}, out)
    assert r["draft"]["items"], "겹치지 않으면 막을 이유가 없다"


def test_subtask_parent_is_filled_even_when_the_model_used_subtask_mode():
    """mode=subtask 로 내면서 parent 만 빠뜨리면 검증에서 통째로 반려된다(실측 PAR1)."""
    out = {"questions": [], "mode": "subtask", "rationale": "",
           "items": [{"summary": "성능 측정", "type": "Sub-Task"},
                     {"summary": "가이드 작성", "type": "Sub-Task"}]}
    r = Refiner().apply({"mentioned_keys": ["DL-9090"]}, out)
    assert all(i.get("parent") == "DL-9090" for i in r["draft"]["items"]), r["draft"]["items"]


def test_one_title_holding_two_deliverables_is_flagged():
    """'A 및 B' 는 대개 티켓 둘이다 — 쪼개는 판단은 사람이 하되 조용히 넘어가지는 않는다."""
    r = _applied(item={"summary": "[Workbench] 성능 측정 및 인덱스 조정"})
    assert "두 가지 일이" in r["draft"]["rationale"]


def test_a_split_parent_with_children_is_not_flagged():
    """이미 children 으로 쪼갠 것은 제목에 '및' 이 있어도 문제가 아니다."""
    r = _applied(item={"summary": "[ETL] 수집 및 적재 정비",
                       "children": [{"summary": "수집 정비"}, {"summary": "적재 정비"}]})
    assert "두 가지 일이" not in r["draft"]["rationale"]


# ── 이미 있는 티켓에 Sub-Task 붙이기 ────────────────────────────────
def test_named_parent_gets_subtasks_directly_not_a_wrapper_task():
    """'DL-9090 에 서브태스크 추가해줘' 는 그 티켓 **아래**에 붙이라는 뜻이다.
    감싸는 새 Task 를 만들면 사용자가 말한 티켓은 그대로 두고 껍데기가 하나 더 생긴다(실측)."""
    from langchain_core.messages import HumanMessage
    out = {"questions": [], "mode": "task", "rationale": "",
           "items": [{"summary": "[Workbench] 성능 측정 및 가이드", "type": "Task",
                      "children": [{"summary": "성능 측정 수행"},
                                   {"summary": "사용 가이드 작성"}]}]}
    st = {"mentioned_keys": ["DL-9090"],
          "messages": [HumanMessage(content="DL-9090 에 서브태스크 추가해줘. 알아서")]}
    r = Refiner().apply(st, out)
    rows = r["draft"]["items"]
    assert r["draft"]["mode"] == "subtask"
    assert len(rows) == 2 and all(i["type"] == "Sub-Task" for i in rows)
    assert all(i["parent"] == "DL-9090" for i in rows)
    assert "감싸는 Task" in r["draft"]["rationale"]


def test_children_stay_children_when_the_parent_does_not_exist_yet():
    """새 일을 쪼개는 것은 여전히 children 이다 — 위 규칙이 이걸 헤집으면 안 된다."""
    from langchain_core.messages import HumanMessage
    out = {"questions": [], "mode": "task", "rationale": "",
           "items": [{"summary": "[ETL] 테이블 30개 등록", "type": "Task", "components": ["ETL"],
                      "children": [{"summary": "1~15번"}, {"summary": "16~30번"}]}]}
    st = {"messages": [HumanMessage(content="테이블 30개 등록. 사람 나눠서 쪼개줘")]}
    r = Refiner().apply(st, out)
    assert r["draft"]["mode"] == "task"
    assert len(r["draft"]["items"]) == 1 and len(r["draft"]["items"][0]["children"]) == 2


def test_subtasks_can_have_different_parents_in_one_batch():
    """'DL-9093 이랑 DL-9094 둘 다' — 항목마다 부모가 다른 것이 정상이다."""
    out = {"questions": [], "mode": "subtask", "rationale": "",
           "items": [{"summary": "회귀 테스트", "type": "Sub-Task", "parent": "DL-9093"},
                     {"summary": "회귀 테스트", "type": "Sub-Task", "parent": "DL-9094"}]}
    r = Refiner().apply({"mentioned_keys": ["DL-9093", "DL-9094"]}, out)
    assert {i["parent"] for i in r["draft"]["items"]} == {"DL-9093", "DL-9094"}
    rows = as_bulk_items(r["draft"])
    assert all(i.get("parent") for i in rows), rows


# ── 형태를 누가 정했나: 말했으면 따르고, 열려 있으면 확인한다 ────────
def _msg(text, **extra):
    from langchain_core.messages import HumanMessage
    return {"messages": [HumanMessage(content=text)], **extra}


def test_shape_words_are_detected_by_code_not_guessed():
    """같은 문장을 모델이 매번 다르게 읽지 않도록, 낱말 판정은 코드가 한다."""
    from app.agent.workflow.agents.refiner import shape_hint
    assert shape_hint(_msg("이거 에픽으로 크게 잡아줘"))[0] == "new_epic"
    assert shape_hint(_msg("DL-9090 서브태스크로 쪼개줘"))[0] == "subtask"
    assert shape_hint(_msg("테스크 하나만 만들어줘"))[0] == "single_task"
    assert shape_hint(_msg("메타데이터 등록 작업이 필요해"))[0] == "", "형태를 안 말했으면 열려 있다"


def test_an_inferred_split_asks_the_user_to_confirm_the_shape():
    """티켓 하나로 끝날 일을 다섯 개로 쪼개 놓고 승인만 받는 것은 사용자가 원한 게 아닐 수 있다."""
    out = {"questions": [], "mode": "task", "rationale": "",
           "structure": "multiple_tasks", "structure_source": "inferred",
           "items": [dict(_draft()["items"][0]), dict(_draft()["items"][0])]}
    r = Refiner().apply(_msg("리니지 성능 개선이 필요해"), out)
    q = r["questions"][0]
    assert q["kind"] == "choice" and "이 형태로 진행할까요" in q["question"]
    assert "추천" in q["options"][0] and len(q["options"]) >= 2
    assert r["draft"]["items"], "확인을 받되 초안은 그대로 보여 준다"


def test_a_shape_the_user_named_is_not_questioned():
    """사용자가 말한 것을 되묻는 것은 취조다."""
    out = {"questions": [], "mode": "task", "rationale": "",
           "structure": "task_with_subtasks", "structure_source": "inferred",
           "items": [dict(_draft(item={"children": [{"summary": "a"}]})["items"][0])]}
    r = Refiner().apply(_msg("DL-9090 서브태스크로 쪼개줘"), out)
    assert not r["questions"], r["questions"]
    assert r["draft"]["structure_source"] == "user_specified", "코드가 확정한다"


def test_delegation_still_beats_the_shape_question():
    """'알아서' 라고 했으면 형태도 알아서 — 위임이 이긴다."""
    out = {"questions": [], "mode": "task", "rationale": "",
           "structure": "multiple_tasks", "structure_source": "inferred",
           "items": [dict(_draft()["items"][0]), dict(_draft()["items"][0])]}
    r = Refiner().apply(_msg("리니지 성능 개선 필요해. 알아서 해줘"), out)
    assert not r["questions"]


def test_a_plain_single_task_is_not_questioned():
    """기본값(티켓 하나)은 갈림이 없다 — 물을 이유가 없다."""
    out = {"questions": [], "mode": "task", "rationale": "",
           "structure": "single_task", "structure_source": "inferred",
           "items": [dict(_draft()["items"][0])]}
    r = Refiner().apply(_msg("체크박스 하나 추가해줘"), out)
    assert not r["questions"]


# ── Q3: 주제 가드·섹션 통일·참고 불릿 가드·하향 편향 (STARR 실측 사고의 회귀) ──
def test_topic_drift_is_flagged_and_blocks_the_reviewer_bypass():
    """원 요청의 고유어가 제목·본문에 없으면 경고 + Reviewer 단건 우회 금지 신호."""
    st = _msg("이번엔 마감을 9월로", request_text="starrocks puffin ndv 통계 파이프라인 개발")
    out = {"questions": [], "mode": "task", "rationale": "",
           "structure": "single_task", "structure_source": "user_specified",
           "items": [{"summary": "[ETL] 증분 적재용 최소 기능 파이프라인 1차 구현",
                      "type": "Task", "description": "<h3>배경</h3><p>증분 적재</p>"}]}
    r = Refiner().apply(st, out)
    assert r["draft"].get("topic_drift") is True
    assert "고유어" in r["draft"]["rationale"]


def test_topic_kept_produces_no_drift_warning():
    st = _msg("진행해", request_text="starrocks puffin ndv 통계 파이프라인 개발")
    out = {"questions": [], "mode": "task", "rationale": "",
           "structure": "single_task", "structure_source": "user_specified",
           "items": [{"summary": "[ETL] StarRocks Puffin NDV 통계 생성 파이프라인 구축",
                      "type": "Task", "description": ""}]}
    r = Refiner().apply(st, out)
    assert not r["draft"].get("topic_drift")


def test_unlinked_reference_bullets_are_dropped_from_the_body():
    """링크도 키도 없는 참고 불릿(날조 문서 제목)은 코드가 뺀다 — 실측: '아키텍처 결정 기록'."""
    out = {"questions": [], "mode": "task", "rationale": "",
           "items": [{"summary": "s", "type": "Task",
                      "description": '<h3>참고</h3><ul><li>DL-9072 — 관련</li>'
                                     '<li>아키텍처 결정 기록</li><li>스프린트 회의록</li></ul>'}]}
    r = Refiner().apply(_msg("작업 만들어줘"), out)
    d = r["draft"]["items"][0]["description"]
    assert "아키텍처 결정 기록" not in d and "스프린트 회의록" not in d
    assert "DL-9072" in d and "출처 없는 항목" in r["draft"]["rationale"]


def test_understructured_single_task_gets_a_shape_question():
    """설계·구현·검증이 다 든 단일 Task(하향 편향)는 확인 질문을 받는다 — 실측: 파이프라인
    신규 구축이 DoD 6불릿짜리 한 덩어리로 나왔다."""
    body = ("<h3>배경</h3><p>파이프라인 설계 후 구현하고 연동 검증과 모니터링까지</p>"
            "<h3>완료 조건 (DoD)</h3><ul data-type=\"taskList\">"
            + "".join(f'<li data-checked="false">단계 {i}</li>' for i in range(6)) + "</ul>")
    out = {"questions": [], "mode": "task", "rationale": "",
           "structure": "single_task", "structure_source": "inferred",
           "items": [{"summary": "[ETL] 통계 파이프라인 개발", "type": "Task",
                      "description": body}]}
    r = Refiner().apply(_msg("통계 파이프라인 개발해야 해"), out)
    assert r["questions"] and "Sub-Task" in str(r["questions"][0].get("options"))


def test_numbered_volume_split_tasks_are_collapsed_into_children():
    """번호만 다른 Task N개(분량 분할 오판)는 코드가 1 Task + Sub-Task 로 접는다 —
    실측 재발: '테이블 1~5' Task 5개. 번호가 제목 중간에 있어도 잡는다."""
    out = {"questions": [], "mode": "task", "rationale": "",
           "structure": "multiple_tasks", "structure_source": "inferred",
           "items": [{"summary": f"[Catalog] 메타데이터 미등록 테이블 {i} 등록",
                      "type": "Task", "assignee": f"skcc.x{i}", "description": ""}
                     for i in range(1, 6)]}
    r = Refiner().apply(_msg("테이블 30개 등록, 사람 나눠서. 알아서"), out)
    d = r["draft"]
    assert len(d["items"]) == 1 and len(d["items"][0]["children"]) == 5
    assert d["structure"] == "task_with_subtasks"


def test_stage_split_tasks_are_collapsed_too():
    """단계 낱말만 다른 Task 들(…설계/…구현/…검증)도 같은 산출물 — 접는다."""
    out = {"questions": [], "mode": "task", "rationale": "",
           "structure": "task_with_subtasks", "structure_source": "inferred",
           "items": [{"summary": f"[ETL] NDV 통계 파이프라인 {w}", "type": "Task",
                      "description": ""} for w in ("설계", "구현", "검증")]}
    r = Refiner().apply(_msg("파이프라인 개발. 알아서"), out)
    d = r["draft"]
    assert len(d["items"]) == 1 and len(d["items"][0]["children"]) == 3


def test_functionally_different_tasks_are_not_collapsed():
    """기능 분화(모듈·산출물이 다른 Task)는 접지 않는다 — 접으면 STR2 가 망가진다."""
    out = {"questions": [], "mode": "task", "rationale": "",
           "structure": "multiple_tasks", "structure_source": "user_specified",
           "items": [{"summary": "[Workbench] 성능 측정 리포트 작성", "type": "Task", "description": ""},
                     {"summary": "[Runtime] 쿼리 인덱스 조정", "type": "Task", "description": ""},
                     {"summary": "[Catalog] 사용 가이드 작성", "type": "Task", "description": ""}]}
    r = Refiner().apply(_msg("성능 측정하고 인덱스도 손보고 가이드도. 알아서"), out)
    assert len(r["draft"]["items"]) == 3


def test_relative_due_is_computed_by_code_not_the_model():
    """상대 날짜("다음주 수요일")는 코드가 계산한다 — 모델 산술이 요일을 틀렸다(실측:
    같은 질문에 수요일과 일요일을 번갈아 냈다). 과거로 떨어지면 다가오는 그 요일로."""
    from datetime import date, timedelta
    from app.agent.workflow.agents.refiner import _relative_due
    d = date.fromisoformat(_relative_due("마감 다음주 수요일로 미루고"))
    assert d.weekday() == 2 and d > date.today()
    f = date.fromisoformat(_relative_due("이번 주 금요일까지"))
    assert f.weekday() == 4 and f >= date.today()
    assert _relative_due("그냥 미뤄줘") == ""
    assert _relative_due("내일까지") == (date.today() + timedelta(days=1)).isoformat()


def test_epic_typed_items_promote_the_mode_to_epic():
    """"새 Epic 만들어줘"에 모델이 type=Epic 항목을 내면서 mode 를 task 로 두면 —
    epic 경로를 못 타 validate_bulk 가 거부하고 승인 카드 없이 죽었다(실측 Round K)."""
    out = {"questions": [], "mode": "task", "rationale": "",
           "structure": "new_epic", "structure_source": "user_specified",
           "items": [{"summary": "[DataOps] 데이터 품질 모니터링", "type": "Epic",
                      "epic_name": "품질모니터링", "description": ""}]}
    r = Refiner().apply(_msg("데이터 품질 모니터링 Epic 만들어줘. 알아서"), out)
    assert r["draft"]["mode"] == "epic"
    assert len(r["draft"]["items"]) == 1


def test_adding_one_more_item_keeps_the_pending_draft_items():
    """승인 전 초안에 "하나 더 추가"를 요청했는데 모델이 새 항목만 내면 —
    기존 항목이 통째로 사라진다(실측 Round O). 코드가 병합해 유지한다."""
    prev = {"mode": "task", "items": [{"summary": "[Catalog] 카탈로그 검색 성능 개선",
                                       "type": "Task", "description": "본문"}]}
    out = {"questions": [], "mode": "task", "rationale": "",
           "items": [{"summary": "[Catalog] 성능 측정 대시보드 구축", "type": "Task",
                      "description": "본문"}]}
    r = Refiner().apply(_msg("좋아. 하나 더 추가해줘 — 성능 측정 대시보드 구축",
                             draft=prev, turns=1), out)
    sums = [i["summary"] for i in r["draft"]["items"]]
    assert len(sums) == 2, sums
    assert "검색 성능 개선" in sums[0] and "대시보드" in sums[1]


def test_a_plain_edit_request_does_not_resurrect_removed_items():
    """추가 요청이 아닌 수정 요청에는 병합이 발동하지 않는다(빼 달라는 요청을 되살리면 안 된다)."""
    prev = {"mode": "task", "items": [{"summary": "[Catalog] A 작업", "type": "Task"},
                                      {"summary": "[Catalog] B 작업", "type": "Task"}]}
    out = {"questions": [], "mode": "task", "rationale": "",
           "items": [{"summary": "[Catalog] A 작업", "type": "Task"}]}
    r = Refiner().apply(_msg("B 는 빼줘", draft=prev, turns=1), out)
    assert len(r["draft"]["items"]) == 1


def test_the_module_prefix_is_added_to_titles_when_the_model_forgets():
    """제목의 `[모듈]` 접두는 검색이 걸리는 관행이다(knowledge/01) — 긴 재료를
    붙여넣으면 모델이 빠뜨린다(실측 Round P). 코드가 붙인다."""
    out = {"questions": [], "mode": "task", "rationale": "",
           "items": [{"summary": "검색 응답시간 지표 대시보드 노출", "type": "Task",
                      "components": ["Catalog"]},
                     {"summary": "[ETL] 이미 붙어 있으면 그대로", "type": "Task",
                      "components": ["ETL"]}]}
    r = Refiner().apply(_msg("회의 메모에서 할 일 뽑아서 티켓 만들어줘. 알아서"), out)
    sums = [i["summary"] for i in r["draft"]["items"]]
    assert sums[0].startswith("[Catalog] "), sums
    assert sums[1] == "[ETL] 이미 붙어 있으면 그대로"


def test_only_the_fields_the_user_asked_for_are_changed():
    """마감만 미뤄 달라고 했는데 우선순위까지 카드에 얹히면 모르고 승인한다(실측 Round P)."""
    out = {"questions": [], "mode": "task", "items": [], "rationale": "",
           "change": {"key": "DL-9090", "duedate": "2026-08-14", "priority": "P3-Minor"}}
    r = Refiner().apply(_msg("두 번째 거 마감을 다음 주 금요일로 미뤄줘", intent=Intent.MODIFY), out)
    ch = r["change_plan"].get("changes") or {}
    assert "duedate" in ch and "priority" not in ch, ch


def test_empty_body_sections_are_removed():
    """참고에 실을 것이 없으면 섹션째 지운다 — 헤딩만 남은 '참고'가 티켓에 박제됐다(실측 S4)."""
    from app.agent.workflow.agents.refiner import _drop_empty_sections
    d = ("<h3>배경</h3><p>왜 하는가</p><h3>완료 조건 (DoD)</h3><ul><li>검증</li></ul>"
         "<h3>참고</h3><ul></ul>")
    out = _drop_empty_sections(d)
    assert "참고" not in out
    assert "배경" in out and "완료 조건" in out and "검증" in out


def test_data_fixture_labels_are_dropped():
    """배치 재료로 기존 라벨 목록을 주니 모델이 데이터 관리용 표식을 집었다(실측:
    카탈로그 검색 티켓에 `ui-fixture`). 그 필터로 조회하는 화면이 오염된다.
    일반 라벨은 건드리지 않는다 — 적절성은 사용자가 카드에서 판단한다."""
    out = {"questions": [], "mode": "task", "rationale": "",
           "items": [{"summary": "[Catalog] 검색 성능 개선", "type": "Task",
                      "components": ["Catalog"],
                      "labels": ["ui-fixture", "tbl-lineage_ui", "성능"]}]}
    r = Refiner().apply(_msg("카탈로그 검색 성능 개선 티켓 만들어줘. 알아서"), out)
    labels = r["draft"]["items"][0].get("labels") or []
    assert "ui-fixture" not in labels and "tbl-lineage_ui" not in labels
    assert "성능" in labels, "일반 라벨은 남아야 한다"
