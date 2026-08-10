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


def test_volume_split_is_respread_after_the_assigner_overwrites_it():
    """자식 담당의 주인이 Assigner 로 옮겨 가면서(§5-c) '골고루' 가드가 덮어쓰기 **뒤편**에
    남았다 — 실측(생성 스위트 STR1): Refiner 가 고루 나눈 테이블 29건이 제안으로 전부
    skcc.x1210 이 됐다. 배정이 바뀐 뒤 한 번 더 봐야 한다."""
    from app.agent.workflow.agents.assigner import merge_assignments
    from app.agent.workflow.agents.refiner import spread_volume_split
    draft = {"mode": "task", "items": [{
        "summary": "[Catalog] 메타데이터 미등록 테이블 등록", "type": "Task",
        "components": ["Catalog"],
        "children": [{"summary": f"테이블 {n} 등록", "assignee": "skcc.i2044"}
                     for n in range(1, 6)]}]}
    merged = merge_assignments(draft, [{"index": 0, "user": "skcc.x1210",
                                        "reasons": "Catalog 소속 · 진행중 2건",
                                        "children": [{"index": j, "user": "skcc.x1210"}
                                                     for j in range(5)]}])
    kids = merged["items"][0]["children"]
    assert {c["assignee"] for c in kids} == {"skcc.x1210"}, "제안이 한 사람으로 뭉친 상태"
    spread_volume_split(merged["items"])
    assert len({c["assignee"] for c in kids}) > 1, kids


def test_user_named_child_owners_survive_the_respread():
    """지정은 결정이고 배분은 제안이다 — 골고루가 사용자의 지정을 덮으면 안 된다."""
    from app.agent.workflow.agents.refiner import spread_volume_split
    items = [{"summary": "[Catalog] 등록", "components": ["Catalog"],
              "children": [{"summary": f"{n}", "assignee": "skcc.x1210",
                            "assignee_source": "user"} for n in range(3)]}]
    assert spread_volume_split(items) is False
    assert {c["assignee"] for c in items[0]["children"]} == {"skcc.x1210"}


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


def test_parentless_subtask_mode_is_demoted_to_task_and_folded():
    """부모로 삼을 티켓이 없는데 mode=subtask 로 내면 **만들 수 없는 초안**이다.

    실측(생성 스위트 STR1): "테이블 30개 등록, 사람 나눠서" 에 최상위 Sub-Task 8건이
    부모 없이 올라왔다. 지금까지 승격(task→subtask)만 있고 강등이 없어 그대로 통과했다.
    강등되면 뒤의 번호 접기가 이어받아 'Task 하나 + Sub-Task N' 이 된다."""
    out = {"questions": [], "mode": "subtask", "rationale": "",
           "items": [{"summary": f"메타데이터 미등록 테이블 등록 - 테이블 {n}",
                      "type": "Sub-Task", "components": ["Catalog"]} for n in range(1, 9)]}
    r = Refiner().apply({}, out)
    d = r["draft"]
    assert d["mode"] == "task", d["mode"]
    assert len(d["items"]) == 1, [i["summary"] for i in d["items"]]
    assert len(d["items"][0].get("children") or []) == 8
    assert not any(i.get("parent") for i in d["items"])


def test_a_lone_parentless_subtask_does_not_reach_the_card_as_a_subtask():
    """실측(생성 스위트 RULE1): "부모는 없어도 돼" 에 답변은 '만들 수 없다' 였는데
    초안에는 부모 없는 Sub-Task 가 그대로 실려 승인 카드까지 올라갔다."""
    out = {"questions": [], "mode": "subtask", "rationale": "",
           "items": [{"summary": "[ETL] 서브태스크 생성", "type": "Sub-Task"}]}
    r = Refiner().apply({}, out)
    assert all(i["type"] != "Sub-Task" for i in r["draft"]["items"]), r["draft"]["items"]
    assert "부모가 이미 있어야" in r["draft"]["rationale"]


def test_demotion_does_not_touch_subtasks_that_do_have_a_parent():
    """강등은 **부모가 아무 데도 없을 때만**이다 — 실재하는 부모를 헤집으면 안 된다."""
    out = {"questions": [], "mode": "subtask", "rationale": "",
           "items": [{"summary": "회귀 테스트", "type": "Sub-Task", "parent": "DL-9093"},
                     {"summary": "성능 측정", "type": "Sub-Task", "parent": "DL-9094"}]}
    r = Refiner().apply({}, out)
    assert r["draft"]["mode"] == "subtask"
    assert all(i["type"] == "Sub-Task" for i in r["draft"]["items"])


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


def _kids(*kids, **over):
    """부모 하나 + 자식들 — 모듈 갈림 검사용 초안."""
    it = dict(_draft()["items"][0])
    it.update(over)
    it["children"] = [dict(k) for k in kids]
    return {"questions": [], "mode": "task", "rationale": "",
            "structure": "task_with_subtasks", "structure_source": "inferred",
            "items": [it]}


def test_a_child_in_another_module_is_promoted_to_a_sibling_task():
    """Sub-Task 는 **부모 컴포넌트로 집계된다** — 모듈이 다른 일을 자식으로 두면 Runtime
    일이 Catalog 로 계상되고, 티켓은 멀쩡해 보여서 아무 데서도 안 터진다(실측 STR2)."""
    out = _kids({"summary": "쿼리 엔진 인덱스 튜닝"}, {"summary": "리니지 뷰어 응답 측정"},
                summary="[Catalog] 리니지 뷰어 성능 측정", components=["Catalog"])
    r = Refiner().apply(_msg("리니지 뷰어 성능 측정하고 쿼리 엔진 인덱스도 손봐줘. 알아서"), out)
    items = r["draft"]["items"]
    assert len(items) == 2, "모듈이 다른 자식은 형제 Task 로 올라온다"
    assert items[1]["components"] == ["Runtime"] and items[1]["type"] == "Task"
    assert "parent" not in items[1]
    assert [c["summary"] for c in items[0]["children"]] == ["리니지 뷰어 응답 측정"], \
        "같은 모듈 자식은 그대로 자식이다"
    assert "워크로드" in str(r["draft"].get("rationale") or r.get("rationale") or ""), \
        "왜 나눴는지 사용자가 읽을 수 있어야 한다"


def test_a_child_whose_module_is_unclear_stays_a_child():
    """별칭 표에 없는 말은 **모르는 것**이다 — 넘겨짚어 올리면 그게 곧 오집계다."""
    out = _kids({"summary": "문서 정리하고 공유"}, {"summary": "회의 잡기"},
                components=["Catalog"])
    r = Refiner().apply(_msg("리니지 관련 정리 좀 해줘. 알아서"), out)
    assert len(r["draft"]["items"]) == 1
    assert len(r["draft"]["items"][0]["children"]) == 2


def test_a_shape_the_user_named_survives_the_module_split():
    """사용자가 'Sub-Task 로' 라고 말했으면 코드가 그 형태를 뒤집지 않는다."""
    out = _kids({"summary": "쿼리 엔진 인덱스 튜닝"}, components=["Catalog"])
    r = Refiner().apply(_msg("리니지 뷰어 건 서브태스크로 쪼개줘"), out)
    assert len(r["draft"]["items"]) == 1


def test_an_empty_component_is_filled_from_the_items_own_words():
    """컴포넌트가 비면 담당 찾기가 **전사 명단**으로 넓어진다(§5-e 와 같은 갈래)."""
    out = {"questions": [], "mode": "task", "rationale": "",
           "structure": "single_task", "structure_source": "inferred",
           "items": [{"summary": "쿼리 엔진 버전 올리기", "type": "Task",
                      "description": "<h3>배경</h3><p>x</p>"}]}
    r = Refiner().apply(_msg("쿼리 엔진 버전 올려줘. 알아서"), out)
    assert r["draft"]["items"][0]["components"] == ["Runtime"]


def test_the_body_gate_and_the_code_share_one_vague_dod_list():
    """같은 규칙을 두 벌로 적으면 **더 관대한 쪽이 사고를 낸다**(§5-e).

    배터리가 재는 "판정 방법 없는 완료 조건"과 코드가 고치는 그것은 한 목록이어야 한다.
    """
    # ★ 배터리 모듈을 **import 하지 않는다** — 그 모듈은 import 시점에 LLM 모델 환경변수를
    #   덮어써서(도구로 쓰라고 그렇게 만들어져 있다) 다른 테스트가 깨진다. 실제로 처음에
    #   import 로 짰다가 `test_settings_put_stores_the_model_in_the_right_slot` 이 죽었다.
    #   보려는 것은 "두 벌로 적지 않았나" 이므로 소스를 읽으면 충분하다.
    from pathlib import Path
    src = (Path(__file__).resolve().parent.parent / "tools" / "agent_create_suite.py") \
        .read_text(encoding="utf-8")
    assert "from app.agent.workflow.agents.refiner import DOD_VAGUE" in src, \
        "배터리는 refiner 의 목록을 가져다 쓴다 — 여기에 다시 적으면 두 규칙이 갈린다"
    assert '_DOD_VAGUE = ("' not in src, "목록을 배터리에 다시 적었다"


def test_a_dod_row_with_a_verification_method_is_left_alone():
    """길게 쓴 완료 조건에는 판정 방법이 들어 있다 — 건드리면 오히려 나빠진다."""
    from app.agent.workflow.agents.refiner import _vague_dod
    assert _vague_dod(["테스트 완료"]) == ["테스트 완료"]
    assert _vague_dod(["p95 응답시간 200ms 이하를 부하 테스트 리포트로 확인"]) == []


def test_choose_an_epic_does_not_mean_create_one():
    """"Epic 은 네가 골라줘"는 **고르라는 말**이다 — 위임이 격상 권한은 아니다.

    실측 STARR1: "Epic 은 네가 골라줘. … 알아서 진행해" 에 모델이 새 Epic 을 만들었다.
    새 Epic 은 진척 보고 단위가 하나 더 생기는 일이라 되돌리기가 가장 비싸다.
    """
    out = {"questions": [], "mode": "task", "rationale": "", "structure": "new_epic",
           "structure_source": "inferred",
           "items": [{"summary": "[ETL] 실시간 처리 파이프라인 개발", "type": "Epic",
                      "description": "<h3>배경</h3><p>x</p>"}]}
    r = Refiner().apply(_msg("실시간 처리 파이프라인 개발해줘. Epic 은 네가 골라줘. 알아서"), out)
    d = r["draft"]
    assert d["mode"] == "task", "새 Epic 을 만들지 않는다"
    assert d["items"][0].get("epic"), "고른 Epic 아래에 둔다"
    assert d["items"][0]["type"] != "Epic"


def test_stripping_orphan_subtasks_never_empties_the_draft():
    """"부모는 나중에" 로 떼어 내는 분기는 **남는 게 있을 때만** 뗀다.

    실측 STR1: 전부가 부모 없는 Sub-Task 였더니 뗀 결과가 초안 0건이었다 — 답변은
    "부모 티켓을 생성하여 진행하겠습니다"라고 말하고 승인할 것은 없는 먹통.
    """
    out = {"questions": [], "mode": "task", "rationale": "", "items": [
        {"summary": "테이블 1 등록", "type": "Sub-Task"},
        {"summary": "테이블 2 등록", "type": "Sub-Task"}]}
    r = Refiner().apply(_msg("테이블 30개 등록해줘. 사람 나눠서. 알아서"), out)
    got = r["draft"]["items"]
    assert got, "떼어 내서 0건이 될 바에는 Task 로 강등한다"
    assert all((i.get("type") or "") != "Sub-Task" for i in got), got


def test_an_orphan_subtask_is_still_stripped_when_a_parent_remains():
    """부모가 초안 안에 같이 있으면 원래대로 뗀다 — 위 수정이 이 갈래를 덮으면 안 된다."""
    out = {"questions": [], "mode": "task", "rationale": "", "items": [
        {"summary": "[ETL] 상위 작업", "type": "Task"},
        {"summary": "테이블 1 등록", "type": "Sub-Task"}]}
    r = Refiner().apply(_msg("작업 만들어줘. 알아서"), out)
    got = r["draft"]["items"]
    assert [i["summary"] for i in got] == ["[ETL] 상위 작업"], got


def test_subtasks_hung_off_an_epic_are_demoted_to_tasks():
    """Jira 에서 **Epic 밑에는 Sub-Task 를 못 단다** — 실재 검사만으로는 안 걸린다.

    실측 STR1: 모델이 Epic DL-5982 를 부모로 지목한 Sub-Task 10건을 냈다. 답변에서는
    스스로 "Epic이라 부모로 적합하지 않다"고 적으면서 초안에는 그대로 실었다 —
    생성에서 100% 실패할 초안이 승인 카드까지 올라간다.
    """
    out = {"questions": [], "mode": "subtask", "rationale": "", "items": [
        {"summary": "테이블 1 등록", "type": "Sub-Task", "parent": "DL-5982"},
        {"summary": "테이블 2 등록", "type": "Sub-Task", "parent": "DL-5982"}]}
    r = Refiner().apply(_msg("메타데이터 미등록 테이블 등록해줘. 알아서"), out)
    got = r["draft"]["items"]
    assert got, "★ 나쁜 초안을 고치려다 **초안 없음**을 만들면 안 된다(실측 STR1)"
    assert r["draft"]["mode"] == "task"
    assert all((i.get("type") or "") != "Sub-Task" for i in got), got
    assert all("parent" not in i for i in got)
    assert all(i.get("epic") == "DL-5982" for i in got), \
        "사용자가 말한 것은 '저 밑에서 진행하자'다 — Epic Link 로 옮긴다"
    assert "Epic" in str(r["draft"].get("rationale") or r.get("rationale") or ""), \
        "왜 Task 로 냈는지 사용자가 읽을 수 있어야 한다"


def test_a_lone_task_labelled_new_epic_is_still_treated_as_lumped():
    """구조 **이름**이 아니라 산출물 **모양**으로 판정한다.

    실측 STARR1: 같은 요청이 실행마다 `single_task` / `new_epic` 으로 갈렸고, 가드가
    앞의 것만 봐서 뒤의 실행은 통째로 비껴갔다. 자식 없는 Task 하나짜리 `new_epic` 은
    그 자체로 앞뒤가 안 맞기도 하다 — Epic 은 여러 일을 묶으려고 만드는 것이다.
    """
    out = {"questions": [], "mode": "task", "rationale": "",
           "structure": "new_epic", "structure_source": "inferred",
           "items": [{"summary": "통계 파이프라인 개발", "type": "Task",
                      "description": "<h3>배경</h3><p>x</p>"}]}
    r = Refiner().apply(_msg("통계정보를 생성하는 파이프라인을 개발해야해"), out)
    # 위임을 안 했으니 물어야 한다 — 뭉갠 채로 조용히 통과하면 안 된다
    assert r["questions"], "구조 이름이 new_epic 이어도 뭉갠 것은 뭉갠 것이다"


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


def test_a_scope_without_exclusions_is_flagged_but_never_invented():
    """knowledge/07: '하지 않는 것을 적는 게 절반이다'. 제외가 빠지면 리뷰 때마다 '이것도
    포함인가요?'가 반복된다(DRAFT-COMPARISON 갭 ③ — 체커만 있고 가드가 없었다).
    무엇을 빼는지는 사용자만 아는 것이라 **채워 넣지는 않는다** — 알리기만 한다."""
    out = {"questions": [], "mode": "task", "rationale": "",
           "items": [{"summary": "[ETL] 적재 배치 재시도 로직 추가", "type": "Task",
                      "description": "<h3>작업 범위</h3><ul><li>포함: 재시도 로직</li></ul>"}]}
    r = Refiner().apply(_msg("재시도 로직 추가해줘"), out)
    assert "하지 않는 것" in r["draft"]["rationale"]
    assert "제외" not in r["draft"]["items"][0]["description"], "지어내지는 않는다"


def test_a_scope_that_states_exclusions_is_not_flagged():
    out = {"questions": [], "mode": "task", "rationale": "",
           "items": [{"summary": "[ETL] 적재 배치 재시도 로직 추가", "type": "Task",
                      "description": "<h3>작업 범위</h3><ul><li>포함: 재시도 로직</li>"
                                     "<li>제외: 알림 채널 개편</li></ul>"}]}
    r = Refiner().apply(_msg("재시도 로직 추가해줘"), out)
    assert "하지 않는 것" not in r["draft"]["rationale"]


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


def test_a_thin_body_does_not_let_a_new_build_slip_through_as_one_task():
    """본문 신호만 보면 **모델이 본문을 얇게 쓸수록 가드가 헐거워진다** — 뭉갠 초안은 대개
    본문도 얇으니 거꾸로 된 판정이다. 실측(생성 스위트 STARR1 재발): 파이프라인 신규 구축이
    DoD 2불릿·단계낱말 0으로 나와 위 가드를 그대로 통과했다. 원 요청은 모델이 못 바꾼다."""
    body = ('<h3>배경</h3><p>StarRocks 조회 최적화를 위해 통계가 필요하다</p>'
            '<h3>완료 조건 (DoD)</h3><ul data-type="taskList">'
            '<li data-checked="false">통계 생성 확인</li>'
            '<li data-checked="false">쿼리 플랜 반영 확인</li></ul>')
    out = {"questions": [], "mode": "task", "rationale": "",
           "structure": "single_task", "structure_source": "inferred",
           "items": [{"summary": "[ETL] 실시간 수집 파이프라인 개발", "type": "Task",
                      "description": body}]}
    assert body.count("data-checked") < 5, "본문 신호로는 안 걸리는 초안이어야 의미가 있다"
    r = Refiner().apply(_msg("실시간 수집 파이프라인을 개발해야 해"), out)
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


def test_folding_survives_a_couple_of_odd_titles():
    """전원일치를 요구하면 **30개 중 하나만 어긋나도 접기가 통째로 무산된다** — 실측
    STR1 은 같은 요청이 8건·30건·1+30 으로 매번 다르게 나온다. 최빈 몸통이 2건 이내를
    남기고 덮으면 접고, 몸통이 다른 것은 독립 Task 로 그대로 둔다(오차 허용이지 그룹핑이
    아니다)."""
    rows = [{"summary": f"[Catalog] 메타데이터 미등록 테이블 {i} 등록", "type": "Task",
             "components": ["Catalog"], "description": ""} for i in range(1, 9)]
    rows.append({"summary": "[Catalog] 등록 결과 검수 보고", "type": "Task",
                 "components": ["Catalog"], "description": ""})
    out = {"questions": [], "mode": "task", "rationale": "",
           "structure": "multiple_tasks", "structure_source": "inferred", "items": rows}
    r = Refiner().apply(_msg("테이블 8개 등록하고 결과 보고도. 알아서"), out)
    d = r["draft"]["items"]
    assert len(d) == 2, [i["summary"] for i in d]
    assert len(d[0].get("children") or []) == 8
    assert "검수 보고" in d[1]["summary"], "몸통이 다른 것까지 빨려 들어가면 안 된다"


def test_three_unrelated_tasks_are_not_folded_together():
    """오차 허용이 그룹핑이 되면 서로 다른 산출물이 한 Task 밑으로 빨려 들어간다."""
    out = {"questions": [], "mode": "task", "rationale": "",
           "structure": "multiple_tasks", "structure_source": "inferred",
           "items": [{"summary": "[ETL] 적재 배치 재시도 로직 추가", "type": "Task"},
                     {"summary": "[Runtime] 쿼리 타임아웃 상향", "type": "Task"},
                     {"summary": "[Catalog] 스키마 변경 알림 추가", "type": "Task"}]}
    r = Refiner().apply(_msg("세 가지 해줘. 알아서"), out)
    assert len(r["draft"]["items"]) == 3


def test_structure_is_filled_from_the_shape_when_the_model_omits_it():
    """모델이 structure 를 빠뜨리면 **구조 가드 둘이 조용히 꺼진다** — 하향 편향은
    single_task 를, 산출 어긋남 보정은 task_with_subtasks 를 키로 보기 때문이다
    (실측 STR1 4회 중 2회가 구조 미지정이었고 그때 두 가드 다 안 돌았다).
    채우는 것은 의도 추측이 아니라 **산출물 모양의 기술**이다."""
    with_kids = {"questions": [], "mode": "task", "rationale": "",
                 "items": [{"summary": "[ETL] 재시도 로직 추가", "type": "Task",
                            "children": [{"summary": "a"}, {"summary": "b"}]}]}
    assert Refiner().apply(_msg("재시도 로직 추가해줘"),
                           with_kids)["draft"]["structure"] == "task_with_subtasks"
    alone = {"questions": [], "mode": "task", "rationale": "",
             "items": [{"summary": "[ETL] 재시도 로직 추가", "type": "Task"}]}
    assert Refiner().apply(_msg("재시도 로직 추가해줘"),
                           alone)["draft"]["structure"] == "single_task"


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


# ── 경로별 프롬프트 조립 ────────────────────────────────────────────
def test_section_titles_used_for_pruning_really_exist():
    """제목이 하나라도 어긋나면 **조용히 아무것도 안 빠진다** — 그러면 최적화가
    사라진 줄도 모르고 토큰만 계속 나간다. 제목 존재를 테스트가 지킨다."""
    from app.agent.prompts.roles import SYSTEM_REFINER, sections
    from app.agent.workflow.agents.refiner import _CREATE_ONLY, _MODIFY_ONLY
    have = set(sections(SYSTEM_REFINER))
    for t in _CREATE_ONLY + _MODIFY_ONLY:
        assert t in have, f"refiner.md 에 '## {t}' 절이 없다"


def test_modify_turns_drop_the_creation_only_sections():
    """기존 티켓의 필드를 바꾸는 턴에 '어떻게 쪼갤 것인가'·'본문 4섹션' 지시는
    판단에 쓰이지 않으면서 매 호출 2천 토큰을 태운다."""
    from app.agent.workflow.agents.refiner import _role_md
    md = _role_md({"intent": Intent.MODIFY})
    assert "Splitting rules" not in md and "Choosing the SHAPE" not in md
    assert "Modify path" in md, "변경 경로 지시는 남아야 한다"
    # 초안을 고치는 modify 턴은 생성 지시가 필요하다 — 빼면 안 된다.
    md2 = _role_md({"intent": Intent.MODIFY, "draft": {"items": [{"summary": "s"}]}})
    assert "Splitting rules" in md2


def test_creation_turns_keep_every_creation_section():
    """초안을 만드는 턴에서는 품질이 먼저다 — 생성 지시를 빼지 않는다."""
    from app.agent.workflow.agents.refiner import _role_md
    md = _role_md({"intent": Intent.PLAN_WORK})
    for t in ("Choosing the SHAPE", "Splitting rules", "Description quality",
              "EPIC creation", "Title conventions"):
        assert t in md


# ── 정성 판독(실 LLM)에서 잡힌 결함의 회귀 ──────────────────────────────────
def test_repeated_sentence_is_folded():
    """같은 문장을 두 번 쓰면 접는다 — 표·목록의 정당한 반복은 놔둔다.

    실측: "아래 카드에서 확인 후 승인해 주세요."가 문단 끝과 그다음 줄에 각각 나왔다.
    모델은 자기가 두 번 썼다는 걸 모르므로 프롬프트로 막을 종류가 아니다.
    """
    from app.agent.workflow.agents.responder import _dedupe_sentences

    got = _dedupe_sentences(
        "DL-101 의 마감을 옮길 계획입니다. 아래 카드에서 확인 후 승인해 주세요.\n\n"
        "아래 카드에서 확인 후 승인해 주세요.")
    assert got.count("아래 카드에서 확인 후 승인해 주세요") == 1
    assert "마감을 옮길 계획입니다" in got

    table = "| 티켓 | 상태 |\n| DL-1 | 진행 중입니다 |\n| DL-2 | 진행 중입니다 |"
    assert _dedupe_sentences(table) == table
    lst = "- DL-1 은 진행 중입니다.\n- DL-2 는 진행 중입니다."
    assert _dedupe_sentences(lst) == lst


def test_duedate_change_against_the_users_word_is_flagged():
    """"미뤄 줘"인데 현재 마감보다 **앞** 날짜면 확인을 요청한다.

    실측: DL-101(마감 2026-08-27)에 "다음 주 금요일로 미뤄 줘" → 2026-08-14 를 아무 말
    없이 카드에 올렸다. 사용자가 현재 마감을 기억하고 말하는 일은 드물다.
    """
    import re

    from app.agent.workflow.agents import refiner as R

    got = {}

    class _FakeTicket:
        def invoke(self, args):
            return {"key": args["key"], "duedate": "2026-08-27", "summary": "x"}

    real = R._relative_due
    R._relative_due = lambda t: "2026-08-14"
    try:
        import app.agent.tools as T
        keep = T.BY_NAME.get("get_ticket")
        T.BY_NAME["get_ticket"] = _FakeTicket()
        try:
            state = {"intent": "modify", "messages": [], "request_text": "",
                     "mentioned_keys": ["DL-101"]}
            from langchain_core.messages import HumanMessage
            state["messages"] = [HumanMessage(content="DL-101 마감을 다음 주 금요일로 미뤄줘")]
            out = {"change": {"key": "DL-101", "duedate": "2026-08-14"}}
            plan, _qs = R._change_plan(state, out, [], [])
            got = plan
        finally:
            if keep is not None:
                T.BY_NAME["get_ticket"] = keep
    finally:
        R._relative_due = real

    assert got.get("key") == "DL-101", got
    assert re.search(r"확인 필요.*2026-08-27.*반대", got.get("why") or "", re.S), got.get("why")


def test_structure_reason_line_appears_once_and_matches_the_card():
    """`(구조: …)` 근거 줄은 **한 줄**이고 카드 헤더의 structure_why 와 같아야 한다.

    실측: Reviewer 반려로 재작성이 돌면 구조 이유가 바뀌는데, 앞선 왕복에서 붙은 옛
    줄이 남아 승인 카드에 서로 다른 두 이유가 떴다(헤더는 새 것, 근거 줄은 옛 것).
    """
    import re

    from app.agent.workflow.agents.refiner import Refiner

    state = {"intent": "plan_work", "messages": [], "draft": {}}
    out = {"mode": "task", "structure": "single_task",
           "structure_why": "단일 산출물이라 Task 하나면 된다",
           "rationale": "(구조: task_with_subtasks — 옛 왕복에서 남은 이유)",
           "items": [{"type": "Task", "summary": "[ETL] 적재 재시도 로직 추가",
                      "description": "<h3>배경</h3><p>x</p>", "components": ["ETL"]}]}
    got = Refiner().apply(state, out)
    rat = (got.get("draft") or {}).get("rationale") or ""
    lines = re.findall(r"\(구조: [^\n]*\)", rat)
    assert len(lines) == 1, f"구조 줄이 {len(lines)}개다: {rat}"
    assert "옛 왕복에서 남은 이유" not in rat, rat
    assert (got["draft"].get("structure_why") or "") in lines[0], (
        f"카드 헤더({got['draft'].get('structure_why')})와 근거 줄({lines[0]})이 다르다")


def test_the_split_falls_back_to_the_dod_when_the_llm_call_comes_back_empty():
    """보정 호출은 LLM 한 방이라 레이트리밋·흔들림으로 그냥 실패한다. 그러면 다단계 규모가
    **조용히 단일 Task 로 남았다**(실측 STARR1: 같은 케이스가 실행마다 통과/실패로 뒤집혔다).

    knowledge/07 이 이미 규정한다 — "DoD 가 5개를 넘고 서로 다른 단계라면 그건 DoD 가 아니라
    Sub-Task 목록이다". 판단이 문서에 있으니 코드가 집행한다."""
    from app.agent.workflow.agents.refiner import _children_from_dod
    staged = {"description":
              '<h3>완료 조건 (DoD)</h3><ul data-type="taskList">'
              '<li data-checked="false">대상 테이블 선정 및 수집 범위 설계</li>'
              '<li data-checked="false">Puffin NDV 통계 생성 job 구현</li>'
              '<li data-checked="false">StarRocks 플랜 반영 연동 검증</li>'
              '<li data-checked="false">실행 절차 문서화</li></ul>'}
    got = [c["summary"] for c in _children_from_dod(staged)]
    assert len(got) == 4 and "구현" in " ".join(got) and "검증" in " ".join(got), got
    # 진짜 DoD 는 건드리지 않는다 — 단계가 안 갈리면 그건 완료 조건이지 할 일 목록이 아니다
    real = {"description":
            '<h3>완료 조건 (DoD)</h3><ul data-type="taskList">'
            '<li data-checked="false">비교표 문서화</li>'
            '<li data-checked="false">p95 2초 미만 측정</li></ul>'}
    assert _children_from_dod(real) == []
    assert _children_from_dod({}) == []


def test_a_missing_background_is_filled_from_the_original_request():
    """보정 호출이 빈손이어도 **배경만은 남는다.**

    배경은 지어낼 것이 없다 — 왜 이 일을 하느냐는 사용자가 이미 말했고, 그 문장이 원
    요청이다. 실측(STARR1): 20케이스 한 실행에서 이 한 건('배경' 섹션 없음)으로 떨어졌는데
    따로 3회 돌리면 3회 다 통과했다 — 케이스가 아니라 보정 호출이 흔들린 것이다.
    """
    out = {"questions": [], "mode": "task", "rationale": "",
           "structure": "single_task", "structure_source": "inferred",
           "items": [{"summary": "[ETL] 통계 파이프라인 개발", "type": "Task",
                      "description": "<h3>작업 범위</h3><ul><li>포함: 구현</li>"
                                     "<li>제외: 최적화</li></ul>"}]}
    # ★ "알아서" 를 붙인다 — 위임이 아니면 구조 확인 **질문**이 붙고, 질문이 있는 턴은
    #   본문 보정을 돌리지 않는다(초안이 버려질 수 있어 왕복을 아낀다). 실사용 STARR1 의
    #   2턴도 "알아서 진행해"라 이 경로다.
    r = Refiner().apply(_msg("starrocks puffin ndv 통계정보 파이프라인을 개발해야해. 알아서"), out)
    body = r["draft"]["items"][0]["description"]
    assert "<h3>배경</h3>" in body, body[:200]
    assert "starrocks" in body.lower(), "배경은 원 요청에서 온다 — 지어내지 않는다"


def test_every_thin_body_gets_a_background_not_just_the_first():
    """배경 채우기는 **LLM 호출이 없다 — 전 항목에 건다.**

    실측(STR2): 초안이 4건으로 갈린 실행에서 둘 이상이 얇았는데 첫 건만 고쳐진 채 나갔다
    (`[2] '배경' 섹션 없음`). 공짜인 수리를 아낄 이유가 없다.
    """
    thin = {"type": "Task",
            "description": "<h3>작업 범위</h3><ul><li>포함: a</li><li>제외: b</li></ul>"}
    out = {"questions": [], "mode": "task", "rationale": "", "structure": "multiple_tasks",
           "structure_source": "inferred",
           "items": [dict(thin, summary="[ETL] 하나"), dict(thin, summary="[ETL] 둘"),
                     dict(thin, summary="[ETL] 셋")]}
    r = Refiner().apply(_msg("적재 파이프라인 정리해줘. 알아서"), out)
    bodies = [i["description"] for i in r["draft"]["items"]]
    assert all("<h3>배경</h3>" in b for b in bodies), bodies


def test_a_draft_that_vanishes_leaves_a_trace_and_a_question():
    """★ **아무것도 없이 끝내지 않는다.**

    모델은 항목을 냈는데 가드들을 지나며 전부 걷힌 실행이 있었다(실측 STARR1: 답변은
    "Epic을 제안합니다"인데 items 가 비고 질문도 0건). 사용자에게는 실패가 아니라 먹통이다.
    되살리지는 않는다 — 왜 걷혔는지 모른 채 되살리면 가드가 막으려던 것이 그대로 나간다.
    """
    out = {"questions": [], "mode": "subtask", "rationale": "", "items": [
        {"summary": "고아 서브태스크", "type": "Sub-Task", "parent": "DL-99999"}]}
    r = Refiner().apply(_msg("서브태스크 하나만 만들어줘. 부모는 없어도 돼"), out)
    if not r["draft"]["items"]:                      # 전부 걷힌 경로일 때만 단언한다
        assert r["questions"], "초안이 0건이면 최소한 다음 수를 물어야 한다"
        assert "제외" in str(r["draft"].get("rationale") or ""), \
            "무엇이 사라졌는지 기록이 남아야 사후에 추적할 수 있다"
