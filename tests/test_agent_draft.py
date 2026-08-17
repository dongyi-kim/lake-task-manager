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
from app.agent.workflow.agents.work_architect import (WorkArchitect, as_bulk_items,  # noqa: E402
                                               child_items, _change_plan,
                                               _align_modules_from_summary,
                                               _enforce_agreed_structure,
                                               _ensure_split_exclusions,
                                               _repair_split_scope)
from app.agent.workflow.state import Intent, MAX_REFINE_TURNS        # noqa: E402


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
    return WorkArchitect().apply({}, out)


def test_epic_link_is_dropped_when_the_key_is_not_an_epic():
    """실측: 모델이 Task(DL-9072)를 '기존 에픽'이라 답했다. 타입 확인은 판단이 아니라 조회다."""
    r = _applied(item={"epic": "DL-9072"})
    assert r["draft"]["items"][0]["epic"] == ""
    assert "Epic 이 아니" in r["draft"]["rationale"]


def test_a_real_epic_survives():
    from langchain_core.messages import HumanMessage
    out = {"questions": [], "mode": "task", "rationale": "", "items": [
        {**dict(_draft()["items"][0]), "epic": "DL-101", "components": ["ETL"]}]}
    r = WorkArchitect().apply({"mentioned_keys": ["DL-101"],
                         "messages": [HumanMessage(content="DL-101 에픽 아래에 만들어줘")]}, out)
    assert r["draft"]["items"][0]["epic"] == "DL-101"


def test_inferred_epic_module_mismatch_is_removed():
    """추론한 배치가 모듈과 다르면 남의 진척률을 오염시키기 전에 연결을 비운다."""
    r = _applied(item={"epic": "DL-101", "components": ["Workbench"]})   # DL-101 은 ETL Epic
    assert r["draft"]["items"][0]["epic"] == ""
    assert "연결을 뺐다" in r["draft"]["rationale"]


def test_verified_same_module_epic_choice_survives_when_user_delegates_the_choice():
    """`Epic은 네가 골라줘`는 verified 후보 중 선택 권한을 준 것 — 고유어 불일치만으로 제거 금지."""
    from langchain_core.messages import HumanMessage
    item = {**dict(_draft()["items"][0]),
            "summary": "[ETL] starrocks puffin ndv 통계정보 파이프라인 개발",
            "epic": "DL-102", "components": ["ETL"]}
    out = {"questions": [], "mode": "task", "rationale": "", "items": [item],
           "structure": "single_task", "structure_source": "inferred"}
    state = {"messages": [HumanMessage(
        content="starrocks puffin ndv 통계정보 파이프라인 개발. Epic은 네가 골라줘. 알아서")],
        "situation": "verified candidates supplied"}
    got = WorkArchitect().apply(state, out)
    assert got["draft"]["items"][0]["epic"] == "DL-102"


def test_an_explicit_epic_wins_even_when_module_metadata_differs():
    from langchain_core.messages import HumanMessage
    out = {"questions": [], "mode": "task", "rationale": "", "items": [
        {**dict(_draft()["items"][0]), "epic": "DL-102", "components": ["Workbench"]}]}
    state = {"mentioned_keys": ["DL-101"],
             "messages": [HumanMessage(content="DL-101 에픽 아래에 Task로 만들어줘")]}
    r = WorkArchitect().apply(state, out)
    assert r["draft"]["items"][0]["epic"] == "DL-101"


def test_an_inferred_cross_project_epic_is_removed():
    r = _applied(item={"epic": "JIRA820-139", "components": ["Workbench"]})
    assert r["draft"]["items"][0]["epic"] == ""
    assert "write project" in r["draft"]["rationale"]


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
    """자식 담당의 주인이 PeopleAdvisor 로 옮겨 가면서(§5-c) '골고루' 가드가 덮어쓰기 **뒤편**에
    남았다 — 실측(생성 스위트 STR1): WorkArchitect 가 고루 나눈 테이블 29건이 제안으로 전부
    skcc.x1210 이 됐다. 배정이 바뀐 뒤 한 번 더 봐야 한다."""
    from app.agent.workflow.agents.people_advisor import merge_assignments
    from app.agent.workflow.agents.work_architect import spread_volume_split
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
    from app.agent.workflow.agents.work_architect import spread_volume_split
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
    PeopleAdvisor 는 상위 items 만 보므로(자식은 그 뒤에 생긴다) 여기서 코드가 채운다."""
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


def test_explicit_new_work_subtask_shape_overrides_a_single_task(monkeypatch):
    """사용자가 단계별 Sub-Task 를 지정했으면 모델의 single_task 판단보다 사용자가 이긴다.

    실측 언어 비교 S1: 네 암 모두 응답에는 하위 작업 3건을 썼지만 승인 카드 children 은
    0건이었다. `structure_source=user_specified` 표지만 고치고 실제 구조는 안 고친 탓이다.
    """
    import app.agent.workflow.agents.work_architect as mod
    monkeypatch.setattr(mod, "_split_into_children", lambda _state, _item: [
        {"summary": "NDV Batch Job 설계"},
        {"summary": "NDV Batch Job 구현"},
        {"summary": "NDV Batch Job 검증"},
    ])
    out = {"questions": [], "mode": "task", "rationale": "",
           "structure": "single_task", "structure_source": "inferred",
           "items": [dict(_draft()["items"][0])]}
    r = WorkArchitect().apply(_msg("단계별 Sub-Task 로 나눠줘. 알아서"), out)
    d = r["draft"]
    assert d["structure"] == "task_with_subtasks"
    assert d["structure_source"] == "user_specified"
    assert [c["summary"] for c in d["items"][0]["children"]] == [
        "NDV Batch Job 설계", "NDV Batch Job 구현", "NDV Batch Job 검증"]


def test_explicit_new_tree_does_not_attach_to_a_parent_the_user_never_named():
    """새 부모 Task를 요청했는데 모델이 임의의 기존 부모 아래 Sub-Task만 내면 안 된다."""
    out = {"questions": [], "mode": "subtask", "rationale": "",
           "structure": "task_with_subtasks", "structure_source": "user_specified",
           "items": [{"summary": f"[ETL] Iceberg Puffin NDV Batch Job {stage}",
                      "type": "Sub-Task", "parent": "DL-9090", "description": ""}
                     for stage in ("설계", "구현", "검증")]}
    r = WorkArchitect().apply(_msg("새 Batch Job을 단계별 Sub-Task 로 나눠줘. 알아서"), out)
    d = r["draft"]
    assert d["mode"] == "task"
    assert d["structure"] == "task_with_subtasks"
    assert len(d["items"]) == 1 and len(d["items"][0].get("children") or []) == 3
    assert all(not c.get("parent") for c in d["items"][0]["children"])


def test_blocking_questions_suppress_the_competing_draft():
    """답이 없어서 묻는 턴에 임의 초안을 함께 내면 질문과 승인 카드가 서로 모순된다."""
    out = {"questions": [{"question": "어느 범위까지 할까요?", "kind": "choice",
                          "options": ["널 비율만", "전체 품질 규칙"], "field": "scope"}],
           "mode": "task", "rationale": "",
           "structure": "single_task", "structure_source": "inferred",
           "items": [dict(_draft()["items"][0])]}
    r = WorkArchitect().apply(_msg("데이터 품질 개선 작업 하나 만들어줘"), out)
    assert r["questions"]
    assert not r["draft"]["items"], "질문에 답하기 전 임의 초안은 승인 카드에 오르면 안 된다"


def test_delegation_keeps_the_draft_and_suppresses_optional_questions():
    """`알아서`는 안전한 선택 재량을 위임하므로 preference 질문은 되묻지 않는다."""
    out = {"questions": [{"question": "범위를 고를까요?", "kind": "choice",
                          "options": ["최소", "전체"], "field": "scope",
                          "required_input": False, "why_required": ""}],
           "mode": "task", "rationale": "",
           "structure": "single_task", "structure_source": "inferred",
           "items": [dict(_draft()["items"][0])]}
    r = WorkArchitect().apply(_msg("신규 등록 테이블의 널 비율 품질 점검 작업 만들어줘. 알아서"), out)
    assert not r["questions"]
    assert r["draft"]["items"]


def test_delegation_preserves_required_input_questions_and_withholds_the_draft():
    """`알아서`도 정확한 대상처럼 행위 성립에 필요한 사용자 입력을 대신하지 않는다."""
    out = {"questions": [{"question": "어느 데이터 품질 규칙을 바꿀까요?", "kind": "text",
                          "options": [], "field": "target", "required_input": True,
                          "why_required": "대상을 모르면 변경 범위와 완료 조건을 확정할 수 없음"}],
           "mode": "task", "rationale": "",
           "structure": "single_task", "structure_source": "inferred",
           "items": [dict(_draft()["items"][0])]}
    r = WorkArchitect().apply(_msg("데이터 품질 규칙 바꿔줘. 알아서"), out)
    assert len(r["questions"]) == 1
    assert r["questions"][0]["required_input"] is True
    assert r["questions"][0]["why_required"]
    assert not r["draft"]["items"], "필수 입력 전의 임의 payload는 승인 카드에 오르면 안 된다"


def test_delegated_new_task_with_children_drops_epic_and_risk_preference_questions():
    """새 Task를 단계별로 나누는 요청은 기존 부모나 잠재 리스크 확인이 없어도 초안 가능."""
    out = {"questions": [
        {"question": "이 작업을 어느 Epic 아래에 배치할까요?", "kind": "choice",
         "options": ["DL-100", "최상위 Task"], "field": "epic", "required_input": True,
         "why_required": "상위 컨텍스트와 보고 단위가 필요"},
        {"question": "기술적 리스크 검증이 끝났나요?", "kind": "text", "options": [],
         "field": "risk", "required_input": True,
         "why_required": "잠재 리스크를 완료 조건에 반영할지 확인 필요"},
    ], "mode": "task", "rationale": "", "structure": "task_with_subtasks",
        "structure_source": "user_specified", "items": [dict(_draft()["items"][0])]}
    state = _msg("Puffin NDV Batch Job 구현 Task를 단계별 Sub-Task로 나눠줘. 알아서")

    result = WorkArchitect().apply(state, out)

    assert not result["questions"]
    assert result["draft"]["items"]


def test_empty_model_result_recovers_a_concrete_delegated_single_task():
    state = _msg("Workbench 쿼리 편집기에 단축키 도움말 팝업 추가해줘. 알아서 초안 잡아줘",
                 situation="직접 일치하는 내부 이력 없음", intent=Intent.PLAN_WORK)
    result = WorkArchitect().apply(state, {
        "questions": [], "mode": "task", "items": [], "rationale": "",
    })

    assert not result["questions"]
    item = result["draft"]["items"][0]
    assert "단축키 도움말 팝업" in item["summary"]
    assert item["type"] == "Task"
    assert all(section in item["description"] for section in
               ("배경", "작업 범위", "완료 조건"))
    assert "UI 테스트" in item["description"] and "화면 증빙" in item["description"]
    assert result["draft"]["construction"] == "literal_delegated"


def test_concrete_delegated_work_skips_the_model_and_keeps_runtime_guards(monkeypatch):
    from app.agent.workflow.agents.base import StructuredAgent

    model_calls = []

    def model_node(_self):
        def run(_state):
            model_calls.append(True)
            raise AssertionError("deterministic delegated work must not call the model")
        return run

    monkeypatch.setattr(StructuredAgent, "node", model_node)
    state = _msg("카탈로그 화면 상단 필터에 '내 모듈만' 체크박스 하나 추가. 알아서",
                 situation="직접 일치하는 내부 이력 없음", intent=Intent.PLAN_WORK)

    result = WorkArchitect().node()(state)

    assert model_calls == []
    assert not result["questions"]
    assert result["draft"]["construction"] == "literal_delegated"
    assert "내 모듈만" in result["draft"]["items"][0]["summary"]


def test_compound_delegated_request_recovers_cross_module_sibling_deliverables():
    from app.agent.workflow.agents.work_architect import _recover_delegated_creation

    state = _msg(
        "리니지 뷰어 성능 측정하고, 결과 따라 쿼리 엔진 인덱스도 손봐야 해. "
        "그리고 사용 가이드도 써야 하고. 알아서 초안 잡아줘",
        situation="직접 일치하는 내부 이력 없음", intent=Intent.PLAN_WORK,
    )

    rows = _recover_delegated_creation(state)
    assert [row["summary"] for row in rows] == [
        "[Catalog] 리니지 뷰어 성능 측정",
        "[Runtime] 쿼리 엔진 인덱스 조정",
        "[Catalog] 사용 가이드 작성",
    ]
    assert all(row["type"] == "Task" and not row.get("children") for row in rows)


def test_delegated_literal_under_explicit_epic_keeps_parent_out_of_title_and_rationale(
        monkeypatch):
    """PAR2: an explicit parent is metadata; only the literal deliverable is the title."""
    from app.agent.workflow.agents.base import StructuredAgent

    def model_node(_self):
        def run(_state):
            raise AssertionError("explicit-parent literal creation must not call the model")
        return run

    monkeypatch.setattr(StructuredAgent, "node", model_node)
    monkeypatch.setattr(
        "app.agent.workflow.agents.work_architect._explicit_parent_epic",
        lambda _state: "DL-101",
    )
    monkeypatch.setattr(
        "app.agent.workflow.agents.work_architect._is_epic",
        lambda key: key == "DL-101",
    )
    monkeypatch.setattr(
        "app.agent.workflow.agents.work_architect._known_components", lambda: {"ETL"})
    state = _msg(
        "DL-101 에픽 아래에 CDC 재처리 배치 개선 Task 하나 만들어줘. 알아서",
        situation="DL-101은 조회로 검증된 Epic", intent=Intent.PLAN_WORK,
        mentioned_keys=["DL-101"], module="ETL",
    )

    result = WorkArchitect().node()(state)

    assert not result["questions"]
    draft = result["draft"]
    item = draft["items"][0]
    assert item["summary"] == "[ETL] CDC 재처리 배치 개선"
    assert item["epic"] == "DL-101"
    assert draft["structure_why"] == "사용자가 위임한 구체 작업을 최소 실행 범위로 복원"
    assert "실시간 처리 안정화" not in draft["structure_why"]


def test_invalid_existing_subtask_parent_uses_deterministic_hierarchy_interview(
        monkeypatch):
    """SUB1/SUB3: verified ticket tier must not consume a Work model call."""
    from app.agent.workflow.agents.base import StructuredAgent

    def model_node(_self):
        def run(_state):
            raise AssertionError("invalid parent tier is deterministic runtime metadata")
        return run

    monkeypatch.setattr(StructuredAgent, "node", model_node)
    monkeypatch.setattr(
        "app.agent.workflow.agents.work_architect._ticket_exists", lambda _key: True)
    monkeypatch.setattr(
        "app.agent.workflow.agents.work_architect._can_parent_subtask", lambda _key: False)
    monkeypatch.setattr(
        "app.agent.workflow.agents.work_architect._ticket_kind", lambda _key: "Sub-Task")
    state = _msg(
        "DL-9095 이거 혼자 하기엔 커. 단계별로 서브태스크로 쪼개줘. 알아서",
        situation="DL-9095는 검증된 Sub-Task", intent=Intent.PLAN_WORK,
        mentioned_keys=["DL-9095"],
    )

    result = WorkArchitect().node()(state)

    assert not (result.get("draft") or {}).get("items")
    assert len(result["questions"]) == 1
    question = result["questions"][0]
    assert question["required_input"] is True
    assert "부모가 될 수 없습니다" in question["question"]
    assert "실제 상위 Task 아래" in question["options"][0]


def test_delegated_under_scale_epic_is_downgraded_to_grounded_task_without_model(
        monkeypatch):
    """STR3: explicit Epic wording does not waive the reporting-unit criteria."""
    from langchain_core.messages import HumanMessage
    from app.agent.workflow.agents.base import StructuredAgent

    def model_node(_self):
        def run(_state):
            raise AssertionError("deterministic Epic downgrade must not call the model")
        return run

    monkeypatch.setattr(StructuredAgent, "node", model_node)
    monkeypatch.setattr(
        "app.agent.workflow.agents.work_architect._known_components", lambda: {"ETL"})
    monkeypatch.setattr(
        "app.agent.workflow.agents.work_architect._pick_parent_epic",
        lambda _summary, _module="": None,
    )
    original = "쿼리 성능 개선을 대대적으로 해보자. 에픽으로 크게 잡아줘"
    state = {
        "messages": [
            HumanMessage(content=original),
            HumanMessage(content="기간은 2주 정도고 ETL 쪽만 손볼 거야. 알아서 진행해"),
        ],
        # A revised follow-up may become the active request root even though the literal
        # first turn still owns the requested shape.  The recovery must use message history.
        "request_text": "기간은 2주 정도고 ETL 쪽만 손볼 거야. 알아서 진행해",
        "situation": "직접 일치하는 내부 이력 없음",
        "intent": Intent.PLAN_WORK,
        "turns": 1,
    }

    result = WorkArchitect().node()(state)

    assert not result["questions"]
    draft = result["draft"]
    assert draft["mode"] == "task" and draft["structure"] == "single_task"
    assert draft["structure_source"] == "inferred"
    assert "Epic 격상 보류" in draft["rationale"]
    assert "4주 이상 기간 근거 없음" in draft["rationale"]
    item = draft["items"][0]
    assert item["type"] == "Task" and item["tier"] == "task"
    assert item["summary"] == "[ETL] 쿼리 성능 개선"
    assert item["components"] == ["ETL"]
    assert not item.get("assignee")
    assert all(token not in item["description"] for token in ("%", "ms", "초 이내"))


def test_delegated_concrete_draft_keeps_duplicate_as_evidence_instead_of_reinterview():
    state = _msg(
        "리니지 뷰어 성능 측정하고, 결과 따라 쿼리 엔진 인덱스도 손봐야 해. "
        "그리고 사용 가이드도 써야 하고. 알아서 초안 잡아줘",
        situation="기존 작업과 일부 범위가 겹침", intent=Intent.PLAN_WORK,
        already_exists=True,
        evidence=[{"key": "DL-9090", "title": "[Workbench] 데이터 리니지 뷰어 1차 오픈",
                   "why": "남은 성능 측정과 문서 정리가 언급됨"}],
        keywords=["리니지", "성능", "가이드"],
    )

    result = WorkArchitect().node()(state)

    assert not result["questions"]
    assert len(result["draft"]["items"]) == 3
    assert result["draft"]["structure"] == "multiple_tasks"
    assert all("DL-9090" in row["description"] for row in result["draft"]["items"])
    assert all(row["description"].count("data-checked") >= 2
               for row in result["draft"]["items"])


def test_empty_model_result_reuses_normal_volume_partitioning_for_delegated_work():
    state = _msg("메타데이터 미등록 테이블 30개를 등록해야 해. 사람 나눠서 진행하게 만들어줘. 알아서",
                 situation="직접 일치하는 내부 이력 없음", intent=Intent.PLAN_WORK)
    result = WorkArchitect().apply(state, {
        "questions": [], "mode": "task", "items": [], "rationale": "",
    })

    assert not result["questions"]
    parent = result["draft"]["items"][0]
    assert "30개 등록" in parent["summary"]
    assert "해야 해" not in parent["summary"]
    assert len(parent.get("children") or []) >= 2
    assert all(child.get("assignee") for child in parent["children"])


def test_named_existing_parent_subtasks_skip_model_and_keep_each_assignee(monkeypatch):
    from app.agent.workflow.agents.base import StructuredAgent

    def model_node(_self):
        def run(_state):
            raise AssertionError("explicit parent, deliverables, and assignees need no model")
        return run

    monkeypatch.setattr(StructuredAgent, "node", model_node)
    state = _msg(
        "DL-9090 밑에 서브태스크 3개 만들어줘: 성능 측정은 x1402, "
        "가이드 작성은 x1450, 회귀 테스트는 x1042. 알아서",
        situation="DL-9090 확인", intent=Intent.PLAN_WORK, mentioned_keys=["DL-9090"],
    )

    result = WorkArchitect().node()(state)
    rows = result["draft"]["items"]

    assert not result["questions"] and len(rows) == 3
    assert [row["parent"] for row in rows] == ["DL-9090"] * 3
    assert [row["assignee"] for row in rows] == ["skcc.x1402", "skcc.x1450", "skcc.x1042"]


def test_delegated_subtask_without_a_deliverable_asks_then_converges_to_one_child():
    """ASKD2: 부모와 개수만으로는 실행할 일이 없다. `알아서`도 내용을 발명할 권한이 아니다."""
    from langchain_core.messages import HumanMessage

    first = _msg("DL-9090 아래에 Sub-Task 하나 만들어줘. 내용은 알아서",
                 intent=Intent.PLAN_WORK, mentioned_keys=["DL-9090"], situation="조사 완료")
    generic = {"questions": [], "mode": "subtask", "rationale": "", "items": [{
        "summary": "[Workbench] 데이터 리니지 뷰어 하위 작업", "type": "Sub-Task",
        "parent": "DL-9090", "description": "<h3>작업 범위</h3><p>기능 개선</p>"}]}
    turn1 = WorkArchitect().apply(first, generic)
    assert turn1["questions"] and not turn1["draft"]["items"]
    assert any(w in turn1["questions"][0]["question"] for w in ("내용", "작업", "목적"))

    second = dict(first)
    second["messages"] = [
        HumanMessage(content="DL-9090 아래에 Sub-Task 하나 만들어줘. 내용은 알아서"),
        HumanMessage(content="리니지 뷰어 성능 회귀 테스트를 추가해줘"),
    ]
    second["draft"] = turn1["draft"]
    concrete = {"questions": [], "mode": "subtask", "rationale": "", "items": [{
        "summary": "[Workbench] 리니지 뷰어 성능 회귀 테스트 추가", "type": "Sub-Task",
        "parent": "DL-9090", "description": "<h3>작업 범위</h3><p>회귀 테스트 추가</p>"}]}
    turn2 = WorkArchitect().apply(second, concrete)
    assert not turn2["questions"] and len(turn2["draft"]["items"]) == 1
    assert "회귀" in turn2["draft"]["items"][0]["summary"]


def test_required_input_question_survives_the_refinement_limit():
    """인터뷰 상한은 취향 질문을 멈추는 장치이지 필수값을 추측하는 허가가 아니다."""
    out = {"questions": [{"question": "댓글을 남길 티켓은 무엇인가요?", "kind": "text",
                          "options": [], "field": "target", "required_input": True,
                          "why_required": "댓글 write target이 없음"}],
           "mode": "task", "rationale": "", "items": []}
    state = _msg("그 티켓에 댓글 남겨줘. 알아서")
    state["turns"] = MAX_REFINE_TURNS
    r = WorkArchitect().apply(state, out)
    assert r["questions"] and r["questions"][0]["required_input"] is True
    assert not r["draft"]["items"]


def test_postcheck_catches_a_subtask_reply_with_an_empty_card():
    """자연어와 승인 카드가 갈라진 S1을 grounding=0으로 통과시키지 않는다."""
    from app.agent.workflow import postcheck
    state = _msg("단계별 Sub-Task 로 나눠줘. 알아서",
                 playbook="task_create", questions=[],
                 draft={"items": [dict(_draft()["items"][0])]})
    bad = postcheck.check(state, "### 하위 작업\n1. 설계\n2. 구현\n3. 검증")
    assert any("자식이 0건" in x for x in bad), bad


def test_postcheck_accepts_the_same_reply_when_children_exist():
    from app.agent.workflow import postcheck
    item = dict(_draft()["items"][0])
    item["children"] = [{"summary": "설계"}, {"summary": "구현"}, {"summary": "검증"}]
    state = _msg("단계별 Sub-Task 로 나눠줘. 알아서",
                 playbook="task_create", questions=[], draft={"items": [item]})
    assert not postcheck.check(state, "### 하위 작업\n1. 설계\n2. 구현\n3. 검증")


def test_postcheck_accepts_top_level_subtask_batch_as_the_requested_subtasks():
    from app.agent.workflow import postcheck
    state = {"draft": {"mode": "subtask", "items": [
        {"type": "Sub-Task", "summary": "성능 측정", "parent": "DL-9090"},
        {"type": "Sub-Task", "summary": "가이드 작성", "parent": "DL-9090"}]},
        "questions": [], "playbook": "subtask_bulk"}
    assert not postcheck.check(state, "### 하위 작업\n1. 성능 측정\n2. 가이드 작성")


def test_a_creation_request_never_turns_into_an_edit_of_someone_elses_ticket():
    """조사에서 비슷한 티켓이 나왔다고 그걸 고치면, 부탁받은 생성은 사라지고
    시키지도 않은 수정이 승인 카드에 오른다(실측)."""
    out = {"questions": [], "mode": "task", "items": [dict(_draft()["items"][0])],
           "change": {"key": "DL-9090", "summary": "제목 바꾸기"}, "rationale": ""}
    r = WorkArchitect().apply({"intent": Intent.PLAN_WORK}, out)
    assert not r["change_plan"], r["change_plan"]
    assert "변경하지 않았다" in r["draft"]["rationale"]
    assert r["draft"]["items"], "생성 초안은 그대로 남아야 한다"


def test_an_explicit_modify_request_still_produces_a_change_plan():
    out = {"questions": [], "mode": "task", "items": [],
           "change": {"key": "DL-9090", "priority": "P1-Critical"}, "rationale": ""}
    r = WorkArchitect().apply({"intent": Intent.MODIFY}, out)
    assert r["change_plan"].get("key") == "DL-9090"


def test_exact_current_turn_mutation_replaces_stale_creation_draft():
    stale = {"questions": [], "mode": "task", "rationale": "이전 조사 기반",
             "items": [{"summary": "[ETL] 이전 fdc 조사 작업", "type": "Bug"}],
             "change": {}}
    state = _msg(
        "이건 그만. DL-9203의 priority만 P4-Trivial로 바꾸는 승인 전 초안을 보여줘.",
        intent=Intent.MODIFY, mentioned_keys=["DL-9203"],
    )
    result = WorkArchitect().apply(state, stale)

    assert not result["draft"]["items"]
    assert result["change_plan"]["key"] == "DL-9203"
    assert result["change_plan"]["changes"] == {"priority": "P4-Trivial"}
    assert "fdc" not in result["change_plan"]["why"].lower()
    assert "priority" in result["change_plan"]["why"]


def test_cancelling_a_comment_does_not_discard_the_replacement_field_change():
    """The latest request wins even when it names the write operation being cancelled."""
    text = ("그 댓글도 취소. 최종 요청은 제목만 "
            "'[Catalog] Puffin NDV 결과 템플릿 정리'로 변경하는 거야. "
            "다른 변경 없이 승인 전 초안만 보여줘.")
    out = {"questions": [], "mode": "task", "items": [],
           "change": {"key": "DL-9203",
                      "summary": "[Catalog] Puffin NDV 결과 템플릿 정리"},
           "rationale": ""}
    r = WorkArchitect().apply(_msg(text, intent=Intent.MODIFY,
                                   mentioned_keys=["DL-9203"]), out)
    assert not r["questions"]
    assert r["change_plan"]["changes"] == {
        "summary": "[Catalog] Puffin NDV 결과 템플릿 정리",
    }
    assert not r["change_plan"].get("comment")


def test_promoting_to_an_epic_stops_when_one_of_that_name_already_exists():
    """Epic 은 진척 보고 단위다 — 중복이 생기면 둘 다 영원히 60% 에서 멈춘다."""
    out = {"questions": [], "mode": "epic", "rationale": "",
           "items": [{"summary": "[ETL] 쿼리 성능 개선", "type": "Epic", "epic_name": "쿼리개선"}]}
    r = WorkArchitect().apply({}, out)
    assert not r["draft"]["items"], "중복 Epic 을 그대로 만들면 안 된다"
    q = r["questions"][0]
    assert q["kind"] == "choice" and q["field"] == "epic"
    assert "Epic 격상 보류" in r["draft"]["rationale"]


def test_delegated_duplicate_epic_uses_the_existing_epic_without_reasking():
    """STR3: 안전 기본값이 하나인데 `알아서`를 받고도 선택 질문을 되풀이하지 않는다."""
    out = {"questions": [], "mode": "epic", "rationale": "",
           "structure": "new_epic", "structure_source": "user_specified",
           "items": [{"summary": "[ETL] 쿼리 성능 개선", "type": "Epic",
                      "epic_name": "쿼리개선", "components": ["ETL"],
                      "description": "<h3>배경</h3><p>쿼리 성능 개선</p>"}]}

    r = WorkArchitect().apply(
        _msg("쿼리 성능 개선을 대대적으로 해보자. 에픽으로 크게 잡아줘. "
             "기간은 2주 정도고 ETL 쪽만 손볼 거야. 알아서 진행해"), out)

    assert not r["questions"]
    assert r["draft"]["mode"] == "task"
    assert r["draft"]["structure"] == "single_task"
    assert r["draft"]["items"][0]["type"] == "Task"
    assert r["draft"]["items"][0]["epic"] == "DL-102"
    assert "epic_name" not in r["draft"]["items"][0]


def test_epic_request_without_duration_and_scale_evidence_is_downgraded_even_without_a_twin(monkeypatch):
    import app.agent.workflow.agents.work_architect as module
    monkeypatch.setattr(module, "_existing_epic_like", lambda _summary: None)
    monkeypatch.setattr(module, "_pick_parent_epic", lambda *_args: {
        "key": "DL-102", "summary": "쿼리 성능 개선", "module": "ETL"})
    out = {"questions": [], "mode": "epic", "rationale": "",
           "structure": "new_epic", "structure_source": "user_specified",
           "items": [{"summary": "[쿼리 성능] 대대적 개선", "type": "Epic",
                      "epic_name": "쿼리성능", "components": ["ETL"],
                      "description": "<h3>배경</h3><p>개선</p>"}]}

    got = WorkArchitect().apply(_msg(
        "쿼리 성능 개선을 에픽으로 크게 잡아줘. 기간은 2주고 ETL만. 알아서"), out)

    item = got["draft"]["items"][0]
    assert got["draft"]["mode"] == "task" and item["type"] == "Task"
    assert item["epic"] == "DL-102" and "epic_name" not in item
    assert "Epic 격상 보류" in got["draft"]["rationale"]


def test_delegated_epic_with_stale_issue_type_cannot_become_epic_under_epic():
    """STR3 actual: a repaired Task was reverted by stale `issue_type=Epic` at finalization."""
    out = {"questions": [], "mode": "task", "rationale": "",
           "structure": "new_epic", "structure_source": "user_specified",
           "items": [{"summary": "[ETL] 쿼리 성능 개선", "type": "Epic",
                      "issue_type": "Epic", "tier": "epic", "epic": "DL-102",
                      "epic_name": "쿼리개선", "components": ["ETL"],
                      "description": ("<h3>배경</h3><p>ETL 쿼리 성능 개선</p>"
                                      "<h3>작업 범위</h3><ul><li>포함: ETL 쿼리 개선</li>"
                                      "<li>제외: ETL 외 모듈</li></ul>"
                                      "<h3>완료 조건 (DoD)</h3><ul data-type=\"taskList\">"
                                      "<li data-checked=\"false\">측정 결과를 기록한다</li></ul>")}]}
    r = WorkArchitect().apply(
        _msg("쿼리 성능 개선을 에픽으로 크게 잡아줘. 기간은 2주고 ETL만. 알아서"), out)
    item = r["draft"]["items"][0]
    assert r["draft"]["mode"] == "task"
    assert item["type"] == item["issue_type"] == "Task"
    assert item["tier"] == "task" and item["epic"] == "DL-102"
    assert "epic_name" not in item


def test_followup_delegation_keeps_the_original_pipeline_multistage_signal():
    """STARR1: 둘째 턴의 `알아서`가 첫 요청의 pipeline 구조 신호를 지우지 않는다."""
    from langchain_core.messages import AIMessage, HumanMessage
    body = ('<h3>배경</h3><p>Puffin NDV pipeline 신규 개발</p>'
            '<h3>작업 범위</h3><ul><li>포함: 1차 구현</li><li>제외: 최적화</li></ul>'
            '<h3>완료 조건 (DoD)</h3><ul data-type="taskList">'
            '<li data-checked="false">pipeline 동작 확인</li></ul>')
    state = {
        "request_text": "Epic 은 네가 골라줘. 범위는 최소 기능 1차 구현까지. 알아서 진행해",
        "messages": [
            HumanMessage(content="starrocks puffin ndv 통계정보를 생성하는 파이프라인을 개발해야해"),
            AIMessage(content="여러 단계로 나뉠 수 있습니다. 구조를 선택해 주세요."),
            HumanMessage(content="Epic 은 네가 골라줘. 범위는 최소 기능 1차 구현까지. 알아서 진행해"),
        ],
        "turns": 1,
    }
    out = {"questions": [], "mode": "task", "rationale": "",
           "structure": "single_task", "structure_source": "inferred",
           "items": [{"summary": "[ETL] StarRocks Puffin NDV 통계정보 파이프라인 개발",
                      "type": "Task", "epic": "DL-102", "components": ["ETL"],
                      "description": body}]}

    got = WorkArchitect().apply(state, out)
    item = got["draft"]["items"][0]

    assert got["draft"]["structure"] == "task_with_subtasks"
    assert [c["summary"].rsplit(" — ", 1)[-1] for c in item["children"]] == [
        "설계", "구현", "검증"]


def test_a_genuinely_new_epic_is_not_blocked():
    out = {"questions": [], "mode": "epic", "rationale": "",
           "items": [{"summary": "[ETL] 사내 표준 스키마 레지스트리 이관", "type": "Epic",
                      "epic_name": "레지스트리이관"}]}
    r = WorkArchitect().apply({}, out)
    assert r["draft"]["items"], "겹치지 않으면 막을 이유가 없다"


def test_subtask_parent_is_filled_even_when_the_model_used_subtask_mode():
    """mode=subtask 로 내면서 parent 만 빠뜨리면 검증에서 통째로 반려된다(실측 PAR1)."""
    out = {"questions": [], "mode": "subtask", "rationale": "",
           "items": [{"summary": "성능 측정", "type": "Sub-Task"},
                     {"summary": "가이드 작성", "type": "Sub-Task"}]}
    r = WorkArchitect().apply({"mentioned_keys": ["DL-9090"]}, out)
    assert all(i.get("parent") == "DL-9090" for i in r["draft"]["items"]), r["draft"]["items"]


def test_parentless_subtask_mode_is_demoted_to_task_and_folded():
    """부모로 삼을 티켓이 없는데 mode=subtask 로 내면 **만들 수 없는 초안**이다.

    실측(생성 스위트 STR1): "테이블 30개 등록, 사람 나눠서" 에 최상위 Sub-Task 8건이
    부모 없이 올라왔다. 지금까지 승격(task→subtask)만 있고 강등이 없어 그대로 통과했다.
    강등되면 뒤의 번호 접기가 이어받아 'Task 하나 + Sub-Task N' 이 된다."""
    out = {"questions": [], "mode": "subtask", "rationale": "",
           "items": [{"summary": f"메타데이터 미등록 테이블 등록 - 테이블 {n}",
                      "type": "Sub-Task", "components": ["Catalog"]} for n in range(1, 9)]}
    r = WorkArchitect().apply({}, out)
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
    r = WorkArchitect().apply({}, out)
    assert all(i["type"] != "Sub-Task" for i in r["draft"]["items"]), r["draft"]["items"]
    assert "부모가 이미 있어야" in r["draft"]["rationale"]


def test_demotion_does_not_touch_subtasks_that_do_have_a_parent():
    """강등은 **부모가 아무 데도 없을 때만**이다 — 실재하는 부모를 헤집으면 안 된다."""
    out = {"questions": [], "mode": "subtask", "rationale": "",
           "items": [{"summary": "회귀 테스트", "type": "Sub-Task", "parent": "DL-9090"},
                     {"summary": "성능 측정", "type": "Sub-Task", "parent": "DL-9072"}]}
    r = WorkArchitect().apply({}, out)
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
    r = WorkArchitect().apply(st, out)
    rows = r["draft"]["items"]
    assert r["draft"]["mode"] == "subtask"
    assert len(rows) == 2 and all(i["type"] == "Sub-Task" for i in rows)
    assert all(i["parent"] == "DL-9090" for i in rows)
    assert "감싸는 Task" in r["draft"]["rationale"]


def test_children_stay_children_when_the_parent_does_not_exist_yet():
    """새 일을 인원 분할하면 실제 모듈 roster 크기의 children 으로 유지한다."""
    from langchain_core.messages import HumanMessage
    out = {"questions": [], "mode": "task", "rationale": "",
           "items": [{"summary": "[ETL] 테이블 30개 등록", "type": "Task", "components": ["ETL"],
                      "children": [{"summary": "1~15번"}, {"summary": "16~30번"}]}]}
    st = {"messages": [HumanMessage(content="테이블 30개 등록. 사람 나눠서 쪼개줘")]}
    r = WorkArchitect().apply(st, out)
    assert r["draft"]["mode"] == "task"
    assert len(r["draft"]["items"]) == 1
    assert len(r["draft"]["items"][0]["children"]) == 3


def test_subtasks_can_have_different_parents_in_one_batch():
    """Task-tier 부모가 여럿이면 항목마다 부모가 다른 것이 정상이다."""
    out = {"questions": [], "mode": "subtask", "rationale": "",
           "items": [{"summary": "회귀 테스트", "type": "Sub-Task", "parent": "DL-9047"},
                     {"summary": "회귀 테스트", "type": "Sub-Task", "parent": "DL-9062"}]}
    r = WorkArchitect().apply({"mentioned_keys": ["DL-9047", "DL-9062"]}, out)
    assert {i["parent"] for i in r["draft"]["items"]} == {"DL-9047", "DL-9062"}
    rows = as_bulk_items(r["draft"])
    assert all(i.get("parent") for i in rows), rows


def test_a_subtask_cannot_be_used_as_another_subtask_parent():
    """DL-9095는 이미 Sub-Task다. 텍스트로 거절하면서 payload는 남기는 모순을 막는다."""
    out = {"questions": [], "mode": "subtask", "rationale": "",
           "items": [{"summary": "설계", "type": "Sub-Task", "parent": "DL-9095"}]}
    r = WorkArchitect().apply(_msg("DL-9095를 단계별 서브태스크로 쪼개줘",
                             mentioned_keys=["DL-9095"]), out)
    assert not r["draft"]["items"]
    assert r["questions"] and "부모가 될 수 없습니다" in r["questions"][0]["question"]
    assert "Sub-Task" in r["draft"]["rationale"]


# ── 형태를 누가 정했나: 말했으면 따르고, 열려 있으면 확인한다 ────────
def _msg(text, **extra):
    from langchain_core.messages import HumanMessage
    return {"messages": [HumanMessage(content=text)], **extra}


def test_shape_words_are_detected_by_code_not_guessed():
    """같은 문장을 모델이 매번 다르게 읽지 않도록, 낱말 판정은 코드가 한다."""
    from app.agent.workflow.agents.work_architect import shape_hint
    assert shape_hint(_msg("이거 에픽으로 크게 잡아줘"))[0] == "new_epic"
    assert shape_hint(_msg("DL-9090 서브태스크로 쪼개줘"))[0] == "subtask"
    assert shape_hint(_msg("새 Batch Job을 단계별 Sub-Task 로 나눠줘"))[0] == \
        "task_with_subtasks"
    assert shape_hint(_msg("테스크 하나만 만들어줘"))[0] == "single_task"
    assert shape_hint(_msg("적재 지연 알림 임계값 조정 Task 만들어줘"))[0] == "single_task"
    assert shape_hint(_msg("리니지 3홉 확장 Story 만들어줘"))[0] == "single_task"
    assert shape_hint(_msg("테이블 30개를 사람 나눠서 진행하게 해줘"))[0] == \
        "task_with_subtasks", "반복 대상의 담당 분할은 한 Task의 분량 분할이다"
    assert shape_hint(_msg("메타데이터 등록 작업이 필요해"))[0] == "", "형태를 안 말했으면 열려 있다"


def test_volume_and_people_split_becomes_roster_sized_children_without_an_llm():
    """STR1 계약: 총량과 roster만 사용하고 존재하지 않는 table 이름은 지어내지 않는다."""
    from app.agent.workflow.agents.work_architect import _volume_partition_children
    item = {"summary": "[Catalog] 메타데이터 미등록 테이블 등록",
            "components": ["Catalog"]}
    kids = _volume_partition_children(
        _msg("메타데이터 미등록 테이블 30개를 사람 나눠서 진행해줘"), item)
    assert len(kids) == 2, kids
    assert {c["assignee"] for c in kids} == {"skcc.x1210", "skcc.i2044"}
    assert all("15개" in c["summary"] and "담당 묶음" in c["summary"] for c in kids)


def test_explicit_people_split_still_gets_module_alias_alignment():
    """사용자가 구조를 말해도 metadata alias→Catalog 보정은 건너뛰지 않는다."""
    body = ("<h3>배경</h3><p>미등록 메타데이터를 정리한다.</p>"
            "<h3>작업 범위</h3><ul><li>포함: 등록</li><li>제외: 스키마 변경</li></ul>"
            "<h3>완료 조건 (DoD)</h3><ul data-type=\"taskList\">"
            "<li data-checked=\"false\">등록 결과를 확인한다.</li></ul>")
    out = {"questions": [], "mode": "task", "rationale": "",
           "structure": "multiple_tasks", "structure_source": "inferred", "items": [{
               "summary": "[DataOps] 메타데이터 미등록 테이블 등록 - 10개씩 분담", "type": "Task",
               "components": ["DataOps"], "description": body}]}
    got = WorkArchitect().apply(
        _msg("메타데이터 미등록 테이블 30개를 사람 나눠서 진행해줘. 알아서"), out)
    item = got["draft"]["items"][0]
    assert item["components"] == ["Catalog"] and item["summary"].startswith("[Catalog]")
    assert "10개씩" not in item["summary"]
    assert got["draft"]["structure"] == "task_with_subtasks"
    assert len(item.get("children") or []) == 2
    assert all("담당 묶음" in c["summary"] for c in item["children"])
    assert all("테이블 1-" not in c["summary"] for c in item["children"])
    assert "중복·누락" in item["description"]
    assert got["draft"]["structure_why"] == \
        "같은 반복 대상을 module roster에 분량으로 나누라는 요청이다"


def test_rationale_never_claims_an_epic_was_selected_when_the_field_is_empty():
    out = {"questions": [], "mode": "task", "rationale":
           "작업은 분량으로 나눕니다. Epic은 가장 관련성이 높은 것을 선택했습니다.",
           "structure": "single_task", "structure_source": "inferred", "items": [{
               "summary": "[Catalog] 메타데이터 등록", "type": "Task", "epic": None,
               "components": ["Catalog"], "description":
               "<h3>배경</h3><p>등록 필요</p><h3>작업 범위</h3>"
               "<ul><li>포함: 등록</li><li>제외: 변경</li></ul>"
               "<h3>완료 조건 (DoD)</h3><ul data-type=\"taskList\">"
               "<li data-checked=\"false\">등록 결과를 검증한다.</li></ul>"}]}
    got = WorkArchitect().apply(_msg("메타데이터 등록 작업을 만들어줘"), out)
    assert "Epic은" not in got["draft"]["rationale"]
    assert "분량으로 나눕니다" in got["draft"]["rationale"]


def test_delegated_draft_does_not_repeat_suppressed_questions_in_rationale():
    out = {"questions": [], "mode": "task",
           "rationale": "단일 작업\n(사용자가 '알아서'라고 해서 기본값으로 채웠다: "
                        "범위는 무엇인가요?; 완료 조건은 무엇인가요?)",
           "structure": "single_task", "structure_source": "inferred", "items": [{
               "summary": "[Catalog] 메타데이터 등록", "type": "Task",
               "components": ["Catalog"], "description":
               "<h3>배경</h3><p>등록</p><h3>작업 범위</h3>"
               "<ul><li>포함: 등록</li><li>제외: 변경</li></ul>"
               "<h3>완료 조건 (DoD)</h3><ul data-type=\"taskList\">"
               "<li data-checked=\"false\">결과를 확인한다.</li></ul>"}]}
    got = WorkArchitect().apply(_msg("메타데이터 등록 작업 하나 만들어줘. 알아서"), out)
    assert "무엇인가요" not in got["draft"]["rationale"]


def test_delegated_concrete_request_does_not_block_on_background_dod_or_epic():
    """`required_input=true`도 모델 주장이다. 선택 정보가 blocker로 승격되면 안 된다."""
    body = ("<h3>배경</h3><p>팝업 추가 요청</p><h3>작업 범위</h3>"
            "<ul><li>포함: 팝업 추가</li><li>제외: 단축키 변경</li></ul>"
            "<h3>완료 조건 (DoD)</h3><ul data-type=\"taskList\">"
            "<li data-checked=\"false\">편집기에서 팝업 노출을 확인한다.</li></ul>")
    questions = [
        {"question": "사업 배경은 무엇인가요?", "kind": "text", "field": "",
         "required_input": True, "why_required": "배경 필요"},
        {"question": "완료 조건은 무엇인가요?", "kind": "text", "field": "",
         "required_input": True, "why_required": "DoD 필요"},
        {"question": "어느 Epic에 둘까요?", "kind": "choice", "field": "epic",
         "required_input": True, "why_required": "Epic 필요"},
    ]
    out = {"questions": questions, "mode": "task", "rationale": "",
           "structure": "single_task", "structure_source": "inferred", "items": [{
               "summary": "[Workbench] 쿼리 편집기 단축키 도움말 팝업 추가",
               "type": "Task", "components": ["Workbench"], "description": body}]}
    got = WorkArchitect().apply(
        _msg("Workbench 쿼리 편집기에 단축키 도움말 팝업 추가해줘. 알아서"), out)
    assert not got["questions"]
    assert len(got["draft"]["items"]) == 1


def test_delegated_request_still_asks_for_missing_target_and_exact_mutation():
    target_q = {"question": "어느 데이터셋을 대상으로 할까요?", "kind": "text",
                "field": "", "required_input": True,
                "why_required": "작업 대상을 식별할 수 없음"}
    got = WorkArchitect().apply(
        _msg("데이터 품질 작업 하나 만들어줘. 나머지는 알아서"),
        {"questions": [target_q], "mode": "task", "rationale": "", "items": []})
    assert got["questions"] and not got["draft"]["items"]

    got = WorkArchitect().apply(
        _msg("적재 지연 알림 임계값 조정 Task 만들어줘. 나머지는 알아서"),
        {"questions": [], "mode": "task", "rationale": "", "items": [dict(_draft()["items"][0])]})
    assert got["questions"] and "임계값" in got["questions"][0]["question"]


def test_slot_audit_infers_optional_ticket_quality_fields_but_asks_for_generic_target():
    from app.agent.workflow.agents.work_architect import _slot_audit
    concrete = _slot_audit(_msg("쿼리 편집기에 도움말 팝업 추가해줘. 알아서"))
    assert "범위(1차 목표): 비어 있음 → INFER" in concrete
    assert "Epic 배치: 비어 있음 → INFER" in concrete
    assert "완료 조건" in concrete and "→ INFER" in concrete
    vague = _slot_audit(_msg("데이터 품질 작업 하나 만들어줘. 알아서"))
    assert "주제·산출물: 비어 있음 → ASK" in vague


def test_comment_without_content_gets_a_deterministic_required_question():
    state = _msg("DL-9090에 댓글 남겨줘. 내용은 알아서",
                 intent=Intent.MODIFY, mentioned_keys=["DL-9090"])
    plan, questions = _change_plan(
        state,
        {"change": {"key": "DL-9090", "comment": "요청하신 대로 처리하겠습니다."},
         "rationale": ""}, [], [],
    )
    assert not plan
    assert questions and questions[0]["field"] == "comment"
    assert questions[0]["required_input"] is True


def test_data_quality_interview_waits_for_target_then_allows_the_draft():
    from langchain_core.messages import HumanMessage
    body = ("<h3>배경</h3><p>널 비율 확인 요청</p><h3>작업 범위</h3>"
            "<ul><li>포함: 널 비율 확인</li><li>제외: 다른 규칙</li></ul>"
            "<h3>완료 조건 (DoD)</h3><ul data-type=\"taskList\">"
            "<li data-checked=\"false\">결과표로 비율을 확인한다.</li></ul>")
    draft = {"questions": [], "mode": "task", "rationale": "",
             "structure": "single_task", "structure_source": "inferred",
             "items": [{"summary": "[DataOps] 널 비율 확인", "type": "Task",
                        "components": ["DataOps"], "description": body}]}
    first = "데이터 품질 개선 작업 하나 만들어줘"
    state = {"messages": [HumanMessage(content=first),
                          HumanMessage(content="널 비율 체크만 이번 주까지. 알아서")],
             "request_text": first, "situation": "조사 완료"}
    got = WorkArchitect().apply(state, draft)
    assert got["questions"] and not got["draft"]["items"]

    state["messages"].append(HumanMessage(content="Lake 배치 적재 테이블 30개 대상"))
    got = WorkArchitect().apply(state, draft)
    assert not got["questions"] and got["draft"]["items"]


def test_priority_aliases_normalize_to_the_jira_canonical_value():
    from app.agent.workflow.agents.work_architect import _missing_exact_mutation, _normalize_priority
    assert _normalize_priority("P3-Medium") == "P3-Minor"
    assert _normalize_priority("P1-High") == "P1-Critical"
    assert _missing_exact_mutation("임계값 조정. 우선순위 P1, 이번 주 금요일까지")
    assert not _missing_exact_mutation("임계값을 45분으로 조정. 우선순위 P1")


def test_ambiguous_assignee_name_lists_exact_usernames_instead_of_invalid_id_error():
    state = _msg("DL-9090 담당자를 동명이로 바꿔줘. 알아서",
                 intent=Intent.MODIFY, mentioned_keys=["DL-9090"])
    out = {"change": {"key": "DL-9090", "assignee": "동명이"}, "rationale": ""}
    plan, questions = _change_plan(state, out, [], [])
    options = " ".join(questions[0]["options"] if questions else [])
    assert not plan
    assert "test.same01" in options and "test.same02" in options
    assert "존재하지 않는 사번" not in questions[0]["question"]


def test_unverified_ticket_and_prompt_doc_references_are_removed():
    body = ("<h3>배경</h3><p>등록</p><h3>작업 범위</h3>"
            "<ul><li>포함: 등록</li><li>제외: 변경</li></ul>"
            "<h3>완료 조건 (DoD)</h3><ul data-type=\"taskList\">"
            "<li data-checked=\"false\">결과 확인</li></ul>"
            "<h3>참고</h3><ul><li>DL-5982 — 안정화 작업</li>"
            "<li><a href=\"(06-data-assets.md)\">프롬프트 내부 문서</a></li></ul>")
    out = {"questions": [], "mode": "task", "rationale": "",
           "structure": "single_task", "structure_source": "inferred", "items": [{
               "summary": "[Catalog] 메타데이터 등록", "type": "Task",
               "components": ["Catalog"], "description": body}]}
    got = WorkArchitect().apply(_msg("메타데이터 등록 작업을 만들어줘"), out)
    final_body = got["draft"]["items"][0]["description"]
    assert "DL-5982" not in final_body and "06-data-assets" not in final_body
    assert "<h3>참고</h3>" not in final_body


def test_volume_rationale_uses_the_calculated_partition_not_the_models_number():
    out = {"questions": [], "mode": "task",
           "rationale": "분할합니다. 각 Sub-Task는 10개의 테이블을 담당합니다.",
           "structure": "multiple_tasks", "structure_source": "inferred", "items": [{
               "summary": "[Catalog] 미등록 메타데이터 등록", "type": "Task",
               "components": ["Catalog"], "description": ""}]}
    got = WorkArchitect().apply(
        _msg("메타데이터 미등록 테이블 30개를 사람 나눠서 진행해줘. 알아서"), out)
    why = got["draft"]["rationale"]
    assert "10개" not in why
    assert "roster 2명" in why and "묶음당 15개" in why


def test_result_integrator_aligns_child_owners_and_drops_a_non_payload_epic():
    from app.agent.workflow.agents.result_integrator import _align_draft_claims
    items = [{"summary": "[Catalog] 등록", "epic": None, "children": [
        {"summary": "등록 — 담당 묶음 1/2 (15개)", "assignee": "skcc.i2044"},
        {"summary": "등록 — 담당 묶음 2/2 (15개)", "assignee": "skcc.x1210"}]}]
    text = ("**Epic**: DL-5452\n"
            "등록 — 담당 묶음 1/2 (15개)\n- 현재 담당: skcc.x1210\n"
            "등록 — 담당 묶음 2/2 (15개)\n- 현재 담당: skcc.i2044\n"
            "Epic DL-5452에 포함하여 관리할 수 있습니다. 단일 작업입니다. "
            "Epic과 Task를 생성할까요?")
    got = _align_draft_claims(text, {"draft": {"items": items}})
    assert "DL-5452" not in got
    assert "담당 묶음 1/2 (15개)\n- 현재 담당: skcc.i2044" in got
    assert "담당 묶음 2/2 (15개)\n- 현재 담당: skcc.x1210" in got
    assert "단일 작업입니다" in got


def test_result_integrator_adds_a_canonical_child_owner_table_when_reply_omits_it():
    from app.agent.workflow.agents.result_integrator import _align_draft_claims
    items = [{"summary": "[Catalog] 등록", "children": [
        {"summary": "등록 묶음 1", "assignee": "skcc.i2044"},
        {"summary": "등록 묶음 2", "assignee": "skcc.x1210"}]}]
    got = _align_draft_claims("등록 묶음 1\n등록 묶음 2\n### 승인 요청\n승인해 주세요.",
                              {"draft": {"items": items}})
    assert "### Sub-Task 담당" in got
    assert "| 등록 묶음 1 | [~skcc.i2044] |" in got
    assert got.index("### Sub-Task 담당") < got.index("### 승인 요청")


def test_child_owner_alignment_understands_titles_without_the_shared_technical_prefix():
    from app.agent.workflow.agents.result_integrator import _align_draft_claims
    items = [{"summary": "[ETL] NDV 파이프라인", "children": [
        {"summary": "StarRocks Puffin NDV 파이프라인 설계 완료", "assignee": "skcc.i2011"},
        {"summary": "StarRocks Puffin NDV 파이프라인 구현 및 테스트 완료",
         "assignee": "skcc.x1042"},
        {"summary": "StarRocks Puffin NDV 파이프라인 검증 및 보고서 작성",
         "assignee": "skcc.x1103"},
    ]}]
    text = ("1. **설계 완료** (담당자: skcc.x1103)\n"
            "2. **구현 및 테스트 완료** (담당자: skcc.x1103)\n"
            "3. **검증 및 보고서 작성** (담당자: skcc.x1103)")
    got = _align_draft_claims(text, {"draft": {"items": items}})
    assert "설계 완료** (담당자: skcc.i2011)" in got
    assert "구현 및 테스트 완료** (담당자: skcc.x1042)" in got
    assert "검증 및 보고서 작성** (담당자: skcc.x1103)" in got


def test_nested_children_are_called_subtasks_and_confirmed_owners_are_not_temporary():
    from app.agent.workflow.agents.result_integrator import _align_draft_claims
    items = [{"summary": "NDV 파이프라인", "children": [
        {"summary": "NDV 설계", "assignee": "skcc.i2011"}]}]
    got = _align_draft_claims("### 하위 Task\n1. NDV 설계\n- 담당자: skcc.i2011 (임시)",
                              {"draft": {"items": items}})
    assert "### Sub-Task" in got and "하위 Task" not in got and "임시" not in got


def test_alternate_is_not_described_as_both_an_alternate_and_not_considered():
    from app.agent.workflow.agents.result_integrator import _align_draft_claims
    items = [{"summary": "[Catalog] 필터 추가", "assignee": "skcc.x1210"}]
    got = _align_draft_claims(
        "- **대안**: skcc.i2044는 진행 중 13건으로 부하가 높아 대안으로 고려하지 않음.",
        {"draft": {"items": items}})
    assert "대안으로 고려하지" not in got and "검토할 수 있음" in got


def test_people_roster_removes_primary_from_alternates_and_repairs_loads():
    from app.agent.workflow.agents.people_advisor import _enforce_item_roster
    roster = ("[Workbench 로스터·부하]\n"
              "- skcc.x1402 김A — 진행중 14건 · 열림 10건\n"
              "- skcc.x1450 김B — 진행중 22건 · 열림 18건")
    row = {"index": 0, "user": "skcc.x1402", "reasons": ["진행중 99건"],
           "alternates": [{"user": "skcc.x1402", "why": "진행중 22건"},
                          {"user": "skcc.x1450", "why": "진행중 1건"}]}
    got = _enforce_item_roster(row, {"components": ["Workbench"]}, roster)
    assert got["reasons"] == ["진행중 14건"]
    assert got["alternates"] == [{"user": "skcc.x1450", "why": "진행중 22건"}]


def test_child_vague_dod_gets_a_verification_artifact_without_more_llm_calls():
    from app.agent.workflow.agents.work_architect import _sharpen_dod
    item = {"summary": "[ETL] 파이프라인", "type": "Task",
            "description": "<h3>완료 조건</h3><ul data-type=\"taskList\">"
                           "<li data-checked=\"false\">상위 결과를 확인한다</li></ul>",
            "children": [{"summary": "검증", "description":
                "<h3>완료 조건 (DoD)</h3><ul data-type=\"taskList\">"
                "<li data-checked=\"false\">정확성 검증 완료</li></ul>"}]}
    assert _sharpen_dod(_msg("파이프라인 개발"), [item])
    body = item["children"][0]["description"]
    assert "측정값" in body and "parent ticket" in body


def test_top_level_subtask_vague_dod_gets_a_verification_artifact_without_llm():
    from app.agent.workflow.agents.work_architect import _sharpen_dod
    items = [{"summary": "성능 측정", "type": "Sub-Task",
              "description": "<h3>완료 조건 (DoD)</h3><ul data-type=\"taskList\">"
                             "<li data-checked=\"false\">성능 테스트 완료</li></ul>"}]
    assert _sharpen_dod(_msg("성능 측정 서브태스크"), items)
    assert "측정값" in items[0]["description"] and "parent ticket" in items[0]["description"]


def test_subtask_vague_performance_and_document_review_dod_get_artifacts():
    from app.agent.workflow.agents.work_architect import _sharpen_dod
    items = [
        {"summary": "성능 측정", "type": "Sub-Task",
         "description": ("<h3>완료 조건 (DoD)</h3><ul data-type=\"taskList\">"
                         "<li data-checked=\"false\">성능 기준 충족</li></ul>")},
        {"summary": "사용 가이드", "type": "Sub-Task",
         "description": ("<h3>완료 조건 (DoD)</h3><ul data-type=\"taskList\">"
                         "<li data-checked=\"false\">문서 검토 완료</li></ul>")},
    ]
    assert _sharpen_dod(_msg("성능 측정과 사용 가이드 서브태스크"), items)
    assert "검증 기준·측정값·판정 결과" in items[0]["description"]
    assert "산출물 링크와 리뷰 결과" in items[1]["description"]


def test_one_vague_top_level_dod_is_sharpened_without_an_llm_call(monkeypatch):
    """구체 행과 섞인 모호한 한 줄도 놓치지 않으며, 보정에 model token을 쓰지 않는다."""
    import app.agent.workflow.agents.work_architect as module
    monkeypatch.setattr(module, "invoke_schema",
                        lambda *_args, **_kwargs: (_ for _ in ()).throw(
                            AssertionError("DoD sharpening must be deterministic")))
    item = {"summary": "[ETL] 적재 지연 알림 임계값 조정", "type": "Task",
            "description": ('<h3>완료 조건 (DoD)</h3><ul data-type="taskList">'
                            '<li data-checked="false">알림 테스트 완료</li>'
                            '<li data-checked="false">변경값과 리뷰 결과를 티켓에 기록한다</li>'
                            '</ul>')}
    assert module._sharpen_dod(_msg("알림 임계값 조정"), [item])
    assert "알림 테스트 완료" not in item["description"]
    assert "실행 로그와 테스트 결과" in item["description"]


def test_explicit_existing_parent_subtasks_recover_from_interpretation_only_output():
    from app.agent.workflow.agents.work_architect import WorkArchitect
    state = _msg("DL-9090 에 성능 측정이랑 사용 가이드 작성 서브태스크 추가해줘. 알아서")
    state.update({"mentioned_keys": ["DL-9090"], "situation": "조사 완료"})
    got = WorkArchitect().apply(state, {"interpretation": "두 서브태스크를 추가합니다.",
                                  "questions": [], "items": [], "mode": "task"})
    draft = got["draft"]
    assert draft["mode"] == "subtask" and len(draft["items"]) == 2
    assert all(i["parent"] == "DL-9090" and i["type"] == "Sub-Task" for i in draft["items"])
    assert "측정값" in draft["items"][0]["description"]
    assert "산출물 링크" in draft["items"][1]["description"]
    assert not got["questions"] and not got["interpretation"]


def test_post_investigation_interpretation_only_is_not_a_valid_empty_turn():
    from app.agent.workflow.agents.work_architect import WorkArchitect
    state = _msg("새 작업 만들어줘")
    state["situation"] = "조사 완료"
    got = WorkArchitect().apply(state, {"interpretation": "새 작업을 만들겠습니다.",
                                  "questions": [], "items": [], "mode": "task"})
    assert got["questions"] and not got["interpretation"]


def test_mvp_development_request_does_not_gain_an_unrequested_production_deployment_dod():
    from app.agent.workflow.agents.work_architect import _drop_unrequested_deployment_dod
    items = [{"summary": "NDV 파이프라인", "type": "Story",
              "description": ("<h3>완료 조건 (DoD)</h3><ul>"
                              "<li data-checked=\"false\">설계 문서 검토 완료</li>"
                              "<li data-checked=\"false\">운영 환경에 배포되었음을 배포 로그로 확인"
                              "</li></ul>")}]
    assert _drop_unrequested_deployment_dod(_msg("NDV 파이프라인 MVP 개발"), items)
    assert "운영 환경" not in items[0]["description"] and "배포 로그" not in items[0]["description"]
    assert "실행 로그와 테스트 결과" in items[0]["description"]


def test_poc_development_request_drops_an_unrequested_deployment_child():
    from app.agent.workflow.agents.work_architect import _drop_unrequested_deployment_dod
    items = [{"summary": "NDV 통계정보 생성 PoC", "type": "Task", "children": [
        {"summary": "Batch Job 설계", "description": ""},
        {"summary": "Batch Job 구현", "description": ""},
        {"summary": "Batch Job 테스트", "description": ""},
        {"summary": "Batch Job 배포", "description": ""},
    ]}]
    assert _drop_unrequested_deployment_dod(
        _msg("1차 PoC 범위로 Batch Job 구현 작업을 만들어줘"), items)
    assert [child["summary"] for child in items[0]["children"]] == [
        "Batch Job 설계", "Batch Job 구현", "Batch Job 테스트"]


def test_child_titles_keep_the_parent_technical_topic():
    from app.agent.workflow.agents.work_architect import _preserve_parent_topic_in_children
    items = [{"summary": "[ETL] StarRocks Puffin NDV 통계정보 파이프라인 개발",
              "children": [{"summary": "파이프라인 설계 완료"},
                           {"summary": "파이프라인 구현 완료"}]}]
    assert _preserve_parent_topic_in_children(items)
    assert items[0]["children"][0]["summary"] == \
        "StarRocks Puffin NDV 통계정보 파이프라인 설계 완료"
    assert all("StarRocks" in c["summary"] and "NDV" in c["summary"]
               for c in items[0]["children"])


def test_reply_does_not_call_an_actual_child_owner_excluded():
    from app.agent.workflow.agents.result_integrator import _align_draft_claims
    items = [{"summary": "[Catalog] 등록", "children": [
        {"summary": "등록 묶음", "assignee": "skcc.i2044"}]}]
    got = _align_draft_claims(
        "등록 묶음\n담당자: skcc.i2044\n- skcc.i2044는 부하가 높아 대안에서 제외했습니다.",
        {"draft": {"items": items}})
    assert "제외" not in got and "Sub-Task 담당으로 분량 배분됨" in got


def test_reply_does_not_call_payload_assignees_nonexistent():
    from app.agent.workflow.agents.result_integrator import _align_draft_claims
    items = [{"summary": "성능 측정", "type": "Sub-Task", "assignee": "skcc.x1402"},
             {"summary": "가이드 작성", "type": "Sub-Task", "assignee": "skcc.x1450"}]
    got = _align_draft_claims(
        "담당자로 지정된 x1402, x1450은 사내 기록에서 확인되지 않았습니다.",
        {"draft": {"items": items}})
    assert "확인되지" not in got
    assert "skcc.x1402" in got and "skcc.x1450" in got and "승인 payload" in got


def test_top_level_subtask_owner_lines_are_aligned_to_payload():
    from app.agent.workflow.agents.result_integrator import _align_draft_claims
    items = [{"summary": "성능 측정", "type": "Sub-Task", "assignee": "skcc.x1402"},
             {"summary": "가이드 작성", "type": "Sub-Task", "assignee": "skcc.x1450"}]
    text = ("### 성능 측정\n- 담당자: skcc.x1042\n"
            "### 가이드 작성\n- 담당자: skcc.x1045\n위 초안을 승인해 주세요.")
    got = _align_draft_claims(text, {"draft": {"items": items}})
    assert "skcc.x1042" not in got and "skcc.x1045" not in got
    assert got.count("skcc.x1402") >= 2 and got.count("skcc.x1450") >= 2
    assert "### 실제 담당자" in got


def test_top_level_owner_alignment_uses_bracketed_short_title_alias():
    from app.agent.workflow.agents.result_integrator import _align_draft_claims
    items = [{"summary": "[성능 측정] 데이터 리니지 뷰어 성능 측정",
              "type": "Sub-Task", "assignee": "skcc.x1402"}]
    got = _align_draft_claims(
        "## 담당자 근거\n- [0] 성능 측정: skcc.x1042 (진행 중 2건)",
        {"draft": {"items": items}})
    assert "skcc.x1042" not in got and "skcc.x1402" in got


def test_top_level_owner_alignment_uses_trailing_action_aliases():
    from app.agent.workflow.agents.result_integrator import _align_draft_claims
    items = [
        {"summary": "성능 측정 수행", "type": "Sub-Task", "assignee": "skcc.x1402"},
        {"summary": "가이드 작성 완료", "type": "Sub-Task", "assignee": "skcc.x1450"},
        {"summary": "회귀 테스트 수행", "type": "Sub-Task", "assignee": "skcc.x1042"},
    ]
    text = ("- **성능 측정**: skcc.x1402\n- **가이드 작성**: skcc.x1402\n"
            "- **회귀 테스트**: skcc.x1402")
    got = _align_draft_claims(text, {"draft": {"items": items}})
    assert "가이드 작성**: skcc.x1450" in got
    assert "회귀 테스트**: skcc.x1042" in got


def test_top_level_owner_alignment_matches_a_semantically_expanded_payload_title():
    from app.agent.workflow.agents.result_integrator import _align_draft_claims
    items = [
        {"summary": "성능을 측정하는 작업 수행", "type": "Sub-Task", "parent": "DL-9090",
         "assignee": "skcc.x1402"},
        {"summary": "가이드 문서 작성", "type": "Sub-Task", "parent": "DL-9090",
         "assignee": "skcc.x1450"},
        {"summary": "회귀 테스트 수행", "type": "Sub-Task", "parent": "DL-9090",
         "assignee": "skcc.x1042"},
    ]
    text = ("| 제목 | Epic |\n|---|---|\n| 성능 측정 | DL-9090 |\n"
            "- **성능 측정**: skcc.x1042\n- **가이드 작성**: skcc.x1045\n"
            "- **회귀 테스트**: skcc.x1045")
    got = _align_draft_claims(text, {"draft": {"items": items}})
    assert "성능 측정**: skcc.x1402" in got
    assert "가이드 작성**: skcc.x1450" in got
    assert "회귀 테스트**: skcc.x1042" in got
    assert "| 제목 | 부모 |" in got and "| 제목 | Epic |" not in got


def test_non_epic_payload_rewrites_false_epic_type_and_scope_claims():
    from app.agent.workflow.agents.result_integrator import _align_draft_claims
    items = [{"summary": "[ETL] NDV 파이프라인 개발", "type": "Task", "epic": None}]
    text = ("Epic을 새로 만들어야 하며 범위는 1차 구현까지입니다.\n"
            "- **Epic**: [ETL] NDV 파이프라인 개발\n- **마감**: 2026-09-30")
    got = _align_draft_claims(text, {"draft": {"items": items}})
    assert "Epic을 새로" not in got and "**Epic**" not in got
    assert "- **Task**: [ETL] NDV 파이프라인 개발" in got


def test_non_epic_payload_drops_a_false_negative_epic_draft_claim():
    from app.agent.workflow.agents.result_integrator import _align_draft_claims
    items = [{"summary": "[ETL] NDV 파이프라인 개발", "type": "Task", "epic": None}]
    got = _align_draft_claims(
        "Epic은 아직 생성되지 않았으며 초안 상태입니다. 담당자는 승인 후 조정합니다.",
        {"draft": {"items": items}})
    assert "Epic" not in got and "담당자는 승인 후 조정합니다" in got


def test_non_epic_payload_rewrites_a_false_epic_lead_role():
    from app.agent.workflow.agents.result_integrator import _align_draft_claims
    items = [{"summary": "NDV 파이프라인", "type": "Story", "epic": None,
              "assignee": "skcc.x1103"}]
    got = _align_draft_claims("skcc.x1103을 Epic의 총괄 담당자로 제안합니다.",
                              {"draft": {"items": items}})
    assert "Epic의 총괄" not in got and "Story 담당자" in got


def test_non_epic_payload_rewrites_unbulleted_epic_type_and_lead_role():
    from app.agent.workflow.agents.result_integrator import _align_draft_claims
    items = [{"summary": "NDV 파이프라인", "type": "Story", "epic": None,
              "assignee": "skcc.x1103"}]
    got = _align_draft_claims(
        "**Epic**: NDV 파이프라인\nEpic 총괄 담당자로 skcc.x1103을 제안합니다.",
        {"draft": {"items": items}})
    assert "Epic" not in got
    assert "**Story**: NDV 파이프라인" in got and "Story 담당자" in got


def test_non_epic_payload_drops_epic_name_requirements_from_the_reply():
    from app.agent.workflow.agents.result_integrator import _align_draft_claims
    items = [{"summary": "NDV 파이프라인", "type": "Task", "epic": None}]
    got = _align_draft_claims("Epic Name은 10자 이내로 설정해야 합니다.\n- **Task**: NDV 파이프라인",
                              {"draft": {"items": items}})
    assert "Epic Name" not in got and "**Task**" in got


def test_actual_child_owner_does_not_keep_a_workload_adjustment_warning():
    from app.agent.workflow.agents.result_integrator import _align_draft_claims
    items = [{"summary": "[ETL] NDV", "children": [
        {"summary": "NDV 설계 완료", "assignee": "skcc.i2011"}]}]
    got = _align_draft_claims(
        "1. **설계 완료**: 현재 담당 skcc.i2011 (부하 조정 필요)",
        {"draft": {"items": items}})
    assert "설계 완료" in got and "skcc.i2011" in got and "조정 필요" not in got


def test_reply_workload_numbers_follow_the_final_assignment_evidence():
    from app.agent.workflow.agents.result_integrator import _align_draft_claims
    state = {"draft": {"items": [{"summary": "NDV", "children": [
                {"summary": "NDV 설계", "assignee": "skcc.i2011"},
                {"summary": "NDV 검증", "assignee": "skcc.x1103"}]}]},
             "assignments": [{"index": 0, "user": "skcc.x1103",
                 "reasons": ["진행중 8건"],
                 "children": [{"index": 0, "user": "skcc.i2011", "why": "진행중 12건"},
                              {"index": 1, "user": "skcc.x1103", "why": "진행중 8건"}]}]}
    got = _align_draft_claims(
        "- NDV 설계 담당 skcc.i2011 (진행중 8건)\n"
        "- NDV 검증 담당 skcc.x1103 (진행 중인 작업 12건)", state)
    assert "skcc.i2011 (진행중 12건)" in got
    assert "skcc.x1103 (진행 중인 작업 8건)" in got


def test_responder_drops_experience_claim_when_assignment_only_has_workload_evidence():
    from app.agent.workflow.agents.result_integrator import _align_draft_claims
    state = {"draft": {"items": [{"summary": "[ETL] NDV", "assignee": "skcc.x1103"}]},
             "assignments": [{"user": "skcc.x1103", "reasons": ["진행중 8건"]}]}
    got = _align_draft_claims(
        "- **근거**: 진행 중인 작업이 8건으로 부하가 적으며, ETL 모듈 경험이 있음.", state)
    assert "진행 중인 작업이 8건" in got and "경험" not in got


def test_actual_dod_removes_body_incomplete_placeholder():
    from app.agent.workflow.agents.result_integrator import _align_draft_claims
    items = [{"summary": "[Catalog] 리니지 확장", "type": "Story",
              "description": "<h3>완료 조건 (DoD)</h3><ul>"
                             "<li>3홉 조회 테스트가 통과한다</li></ul>"}]
    got = _align_draft_claims("- **완료 조건 (DoD)**: [본문 미완성]", {"draft": {"items": items}})
    assert "본문 미완성" not in got and "3홉 조회 테스트가 통과한다" in got


def test_one_matching_child_title_does_not_hide_the_remaining_actual_dod():
    from app.agent.workflow.agents.result_integrator import _align_draft_claims
    items = [{"summary": "NDV 파이프라인", "type": "Story",
              "description": ("<h3>완료 조건 (DoD)</h3><ul>"
                              "<li>파이프라인 설계 완료</li>"
                              "<li>구현 테스트 통과</li>"
                              "<li>검증 보고서 배포</li></ul>")}]
    got = _align_draft_claims("### 하위 작업\n- 파이프라인 설계 완료", {"draft": {"items": items}})
    assert "### 실제 완료 조건" in got
    assert "구현 테스트 통과" in got and "검증 보고서 배포" in got


def test_empty_approval_heading_gets_an_action_sentence():
    from app.agent.workflow.agents.result_integrator import _fill_empty_approval_heading
    got = _fill_empty_approval_heading("초안입니다.\n## 승인 요청", {"draft": {"items": [{}]}})
    assert got.endswith("승인해 주세요.")


def test_done_change_plan_requires_reopen_as_a_separate_approval(monkeypatch):
    class _DoneClient:
        def ticket_badge(self, key):
            return {"key": key, "type": "Task", "status": "Resolved",
                    "statusCategory": "done"}

        def transitions(self, key):
            return [{"id": "4", "name": "Reopen Issue", "to": "Reopened",
                     "toCategory": "todo"}]

        def get_issue(self, key):
            return {"key": key, "fields": {
                "issuetype": {"name": "Task", "subtask": False},
                "status": {"name": "Resolved", "statusCategory": {"key": "done"}},
                "summary": "완료된 작업", "priority": {"name": "P3-Minor"}}}

        def issue_comments(self, key, limit):
            return []

    cli = _DoneClient()
    monkeypatch.setattr("app.agent.tools._ctx.client", lambda: cli)
    monkeypatch.setattr("app.agent.tools.search_tools.client", lambda: cli)
    monkeypatch.setattr("app.agent.tools.write_tools.client", lambda: cli)
    state = _msg("DL-9 우선순위를 P1으로 올려줘", intent=Intent.MODIFY,
                 mentioned_keys=["DL-9"])
    plan, questions = _change_plan(
        state, {"change": {"key": "DL-9", "priority": "P1-Critical"}, "rationale": ""},
        [], [])
    assert not plan
    assert questions and "Done" in questions[0]["question"]
    assert "Reopened" in questions[0]["options"][0]
    assert "새 승인" in questions[0]["options"][0]


def test_done_comment_only_change_plan_is_allowed(monkeypatch):
    class _DoneClient:
        def ticket_badge(self, key):
            return {"key": key, "type": "Task", "statusCategory": "done"}

        def get_issue(self, key):
            return {"key": key, "fields": {"issuetype": {"name": "Task"},
                                             "status": {"statusCategory": {"key": "done"}}}}

        def issue_comments(self, key, limit):
            return []

    cli = _DoneClient()
    monkeypatch.setattr("app.agent.tools._ctx.client", lambda: cli)
    monkeypatch.setattr("app.agent.tools.search_tools.client", lambda: cli)
    state = _msg("DL-9에 완료 후 회고 댓글 남겨줘", intent=Intent.MODIFY,
                 mentioned_keys=["DL-9"])
    plan, questions = _change_plan(
        state, {"change": {"key": "DL-9", "comment": "완료 후 회고"}, "rationale": ""},
        [], [])
    assert plan["comment"] == "완료 후 회고" and not plan["changes"]
    assert not questions


def test_irrelevant_historian_evidence_is_not_forced_into_description():
    body = ("<h3>배경</h3><p>단축키 도움말을 제공한다.</p>"
            "<h3>작업 범위</h3><ul><li>포함: 도움말 팝업</li><li>제외: 다른 UI</li></ul>"
            "<h3>완료 조건 (DoD)</h3><ul data-type=\"taskList\">"
            "<li data-checked=\"false\">팝업의 단축키 목록을 화면 검수로 확인한다.</li></ul>")
    out = {"questions": [], "mode": "task", "structure": "single_task",
           "structure_source": "inferred", "rationale": "", "items": [{
               "summary": "[Workbench] 단축키 도움말 팝업 추가", "type": "Task",
               "epic": None, "components": ["Workbench"], "description": body}]}
    state = _msg("Workbench 쿼리 편집기에 단축키 도움말 팝업 추가해줘",
                 intent=Intent.PLAN_WORK,
                 evidence=[{"key": "DL-5122", "title": "[Workbench] 동시성 이슈 해결",
                            "why": "같은 모듈이지만 현재 요청과 직접적인 관련은 없음"}])
    got = WorkArchitect().apply(state, out)
    assert "DL-5122" not in got["draft"]["items"][0]["description"]


def test_accepted_structure_restores_exact_titles_modules_and_order():
    state = _msg("이 구조로 진행한다", structure_ok=False, structure_plan=[
        {"summary": "[Catalog] 리니지 뷰어 성능 측정", "type": "Task",
         "components": ["Catalog"], "children": []},
        {"summary": "[Runtime] 쿼리 엔진 인덱스 최적화", "type": "Task",
         "components": ["Runtime"], "children": []},
    ])
    items = [
        {"summary": "[Workbench] 쿼리 엔진 인덱스 최적화", "components": ["Workbench"],
         "description": "인덱스 본문"},
        {"summary": "[Workbench] 리니지 뷰어 성능 측정", "components": ["Workbench"],
         "description": "성능 본문", "children": [{"summary": "모델이 덧붙인 자식"}]},
    ]
    assert _enforce_agreed_structure(state, items)
    assert [i["summary"] for i in items] == [
        "[Catalog] 리니지 뷰어 성능 측정", "[Runtime] 쿼리 엔진 인덱스 최적화"]
    assert [i["components"] for i in items] == [["Catalog"], ["Runtime"]]
    assert all("children" not in i for i in items)


def test_summary_alias_corrects_conflicting_component_and_prefix():
    items = [{"summary": "[Workbench] 쿼리 엔진 인덱스 최적화",
              "components": ["Workbench"]}]
    assert _align_modules_from_summary(items)
    assert items == [{"summary": "[Runtime] 쿼리 엔진 인덱스 최적화",
                      "components": ["Runtime"]}]


def test_split_tasks_cannot_own_sibling_scope_and_all_get_exclusions():
    def body(scope):
        return ("<h3>배경</h3><p>배경</p><h3>작업 범위</h3><ul>" + scope + "</ul>"
                "<h3>완료 조건 (DoD)</h3><ul data-type=\"taskList\">"
                "<li data-checked=\"false\">결과를 리뷰로 확인한다.</li></ul>")

    items = [
        {"summary": "[Catalog] 리니지 뷰어 성능 측정",
         "description": body("<li>포함: 성능 측정</li><li>포함: 쿼리 엔진 인덱스 최적화</li>")},
        {"summary": "[Runtime] 쿼리 엔진 인덱스 최적화",
         "description": body("<li>포함: 쿼리 엔진 인덱스 최적화</li>")},
        {"summary": "[Catalog] 리니지 뷰어 사용 가이드 작성",
         "description": body("<li>포함: 리니지 뷰어 사용 예시와 가이드</li>")},
    ]
    repaired = _repair_split_scope(items)
    _ensure_split_exclusions(items)
    assert repaired == ["[Catalog] 리니지 뷰어 성능 측정"]
    assert all("제외(별도 ticket)" in i["description"] for i in items)
    scope0 = items[0]["description"].split("완료 조건", 1)[0]
    assert "포함: 쿼리 엔진 인덱스" not in scope0


def test_an_inferred_split_asks_the_user_to_confirm_the_shape():
    """티켓 하나로 끝날 일을 다섯 개로 쪼개 놓고 승인만 받는 것은 사용자가 원한 게 아닐 수 있다."""
    out = {"questions": [], "mode": "task", "rationale": "",
           "structure": "multiple_tasks", "structure_source": "inferred",
           "items": [dict(_draft()["items"][0]), dict(_draft()["items"][0])]}
    r = WorkArchitect().apply(_msg("리니지 성능 개선이 필요해"), out)
    q = r["questions"][0]
    assert q["kind"] == "choice" and "이 형태로 진행할까요" in q["question"]
    assert "추천" in q["options"][0] and len(q["options"]) >= 2
    assert r["draft"]["items"], "확인을 받되 초안은 그대로 보여 준다"


def test_a_shape_the_user_named_is_not_questioned():
    """사용자가 말한 것을 되묻는 것은 취조다."""
    out = {"questions": [], "mode": "task", "rationale": "",
           "structure": "task_with_subtasks", "structure_source": "inferred",
           "items": [dict(_draft(item={"children": [{"summary": "a"}]})["items"][0])]}
    r = WorkArchitect().apply(_msg("DL-9090 서브태스크로 쪼개줘"), out)
    assert not r["questions"], r["questions"]
    assert r["draft"]["structure_source"] == "user_specified", "코드가 확정한다"


def test_delegation_still_beats_the_shape_question():
    """'알아서' 라고 했으면 형태도 알아서 — 위임이 이긴다."""
    out = {"questions": [], "mode": "task", "rationale": "",
           "structure": "multiple_tasks", "structure_source": "inferred",
           "items": [dict(_draft()["items"][0]), dict(_draft()["items"][0])]}
    r = WorkArchitect().apply(_msg("리니지 성능 개선 필요해. 알아서 해줘"), out)
    assert not r["questions"]


def test_a_small_delegated_change_is_collapsed_to_one_task():
    """단일 UI 변경이 임의 Epic과 설계/구현/검증 Sub-Task로 부풀지 않는다."""
    body = ("<h3>배경</h3><p>단축키 도움말이 필요하다.</p><h3>작업 범위</h3>"
            "<ul><li>포함: 팝업 추가</li><li>제외: 편집기 개편</li></ul>"
            "<h3>완료 조건 (DoD)</h3><ul data-type=\"taskList\">"
            "<li data-checked=\"false\">팝업 노출을 UI 테스트로 확인한다.</li></ul>")
    out = {"questions": [], "mode": "task", "rationale": "",
           "structure": "task_with_subtasks", "structure_source": "inferred",
           "items": [{"summary": "[Workbench] 단축키 도움말 팝업 추가", "type": "Task",
                      "components": ["Workbench"], "description": body,
                      "epic": "DL-5367", "children": [{"summary": "설계"}, {"summary": "검증"}]},
                     {"summary": "[Workbench] 사용자 피드백 보고서 작성", "type": "Task",
                      "components": ["Workbench"], "description": body}]}
    r = WorkArchitect().apply(_msg("Workbench 쿼리 편집기에 단축키 도움말 팝업 추가해줘. "
                             "알아서 초안 잡아줘"), out)
    assert len(r["draft"]["items"]) == 1
    assert not r["draft"]["items"][0].get("children")
    assert r["draft"]["structure"] == "single_task"
    assert not r["draft"]["items"][0].get("epic"), "무관한 inferred Epic도 제거돼야 한다"


def test_semantic_duplicate_tasks_keep_the_alias_matched_module():
    """쿼리 엔진 인덱스 초안이 Workbench/Catalog/Runtime 세 벌이면 Runtime만 남긴다."""
    from app.agent.workflow.agents.work_architect import _dedupe_semantic_items
    rows = [
        {"summary": "[Workbench] 쿼리 엔진 인덱스 조정", "components": ["Workbench"]},
        {"summary": "[Catalog] 쿼리 엔진 인덱스 최적화", "components": ["Catalog"]},
        {"summary": "[Runtime] 쿼리 엔진 인덱스 최적화", "components": ["Runtime"]},
        {"summary": "[Workbench] 리니지 뷰어 사용 가이드 작성", "components": ["Workbench"]},
    ]
    removed = _dedupe_semantic_items(
        _msg("리니지 뷰어 성능을 재고 쿼리 엔진 인덱스를 조정하고 사용 가이드를 쓴다"), rows)
    assert len(removed) == 2
    index_rows = [x for x in rows if "인덱스" in x["summary"]]
    assert len(index_rows) == 1 and index_rows[0]["components"] == ["Runtime"]


def test_placeholder_instructions_are_replaced_by_a_minimum_real_body():
    body = ("<h3>배경</h3><p>필요한 이유를 설명해 주세요.</p>"
            "<h3>작업 범위</h3><ul><li>구체적으로 적어주세요.</li></ul>"
            "<h3>완료 조건 (DoD)</h3><ul><li>명확한 완료 기준 필요</li></ul>")
    r = WorkArchitect().apply(_msg("리니지 3홉 확장 Story 만들어줘. 알아서"),
                        {"questions": [], "mode": "task", "rationale": "",
                         "items": [{"summary": "[Workbench] 리니지 3홉 확장", "type": "Story",
                                    "components": ["Workbench"], "description": body}]})
    desc = r["draft"]["items"][0]["description"]
    assert "설명해 주세요" not in desc and "적어주세요" not in desc
    assert all(x in desc for x in ("배경", "작업 범위", "완료 조건", "제외"))


@pytest.mark.parametrize("placeholder", [
    "이 작업의 배경을 설명하는 문장이 필요합니다.",
    "[기입 필요]",
    "리니지 확장이 필요한 이유를 설명합니다. 관련된 사건이나 요청을 명시합니다.",
    "구체적인 검증 방법을 추가해야 합니다.",
    "배경 정보는 추가 확인이 필요합니다.",
    "왜 이 일이 필요한지 설명이 필요합니다.",
    "명확한 완료 조건이 필요합니다.",
    "왜 이 일이 필요한지 2~4문장. 계기가 된 사건·요청·장애를 티켓 키와 함께.",
    "포함: 이번에 하는 것 / 제외: 이번에 하지 않는 것 / 검증 가능한 조건 1",
    "계기와 관련된 티켓 키를 추가해주세요.",
    "작업의 필요성을 명확히 설명해야 합니다.",
    "왜 이 작업이 필요한지 설명이 필요합니다.",
    "명확한 완료 기준이 필요합니다.",
])
def test_live_placeholder_variants_are_detected(placeholder):
    from app.agent.workflow.agents.work_architect import _has_placeholder_body
    assert _has_placeholder_body(f"<h3>배경</h3><p>{placeholder}</p>")


def test_generic_user_instruction_is_placeholder_but_specific_open_fact_is_not():
    from app.agent.workflow.agents.work_architect import _has_placeholder_body

    assert _has_placeholder_body("<p>포함: 사용자에게 확인 필요</p>")
    assert _has_placeholder_body("<p>세부 조건은 사용자와 협의 필요</p>")
    assert _has_placeholder_body("<p>사용자에게 기준을 물어보세요.</p>")
    assert not _has_placeholder_body("<p>성능 기준값은 운영팀 확인 필요</p>")
    assert not _has_placeholder_body(
        "<p>점검 대상 품질 룰과 항목별 통과 기준은 담당팀 확인 필요 — "
        "확정된 기준별 점검 결과를 티켓에 기록한다</p>")


def test_unspecified_quality_and_performance_criteria_are_specific_open_facts():
    from app.agent.workflow.agents.work_architect import _mark_unspecified_acceptance_criteria

    items = [{"description": (
        '<h3>완료 조건 (DoD)</h3><ul data-type="taskList">'
        '<li data-checked="false">명확한 품질 기준에 따라 점검이 완료되었음을 확인합니다.</li>'
        '<li data-checked="false">파이프라인의 성능이 요구사항을 충족함</li></ul>')}]
    assert _mark_unspecified_acceptance_criteria(_msg("품질 점검과 파이프라인을 만들어줘"), items)
    body = items[0]["description"]
    assert "품질 룰과 항목별 통과 기준은 담당팀 확인 필요" in body
    assert "성능 측정 지표와 목표값은 담당팀 확인 필요" in body


def test_user_supplied_performance_metric_is_not_replaced_by_an_open_fact():
    from app.agent.workflow.agents.work_architect import _mark_unspecified_acceptance_criteria

    items = [{"description": (
        '<h3>완료 조건 (DoD)</h3><ul data-type="taskList">'
        '<li data-checked="false">p95가 200ms 이하인 성능 기준을 충족함</li></ul>')}]
    assert not _mark_unspecified_acceptance_criteria(_msg("p95 200ms 이하로 만들어줘"), items)
    assert "담당팀 확인 필요" not in items[0]["description"]


def test_null_ratio_only_request_does_not_reopen_other_quality_rules_as_open_fact():
    from app.agent.workflow.agents.work_architect import (
        _dedupe_dod_rows, _mark_unspecified_acceptance_criteria,
    )

    items = [{"summary": "[ETL] 신규 30개 테이블 널 비율 체크", "description": (
        '<ul data-type="taskList">'
        '<li data-checked="false">요청한 30개 대상별 null ratio 측정값과 실패·제외 목록을 티켓에 기록해 확인한다</li>'
        '<li data-checked="false">모든 품질 룰 점검 완료</li></ul>')}]
    assert _mark_unspecified_acceptance_criteria(
        _msg("신규 30개 테이블은 널 비율만 체크해줘"), items)
    _dedupe_dod_rows(items)
    body = items[0]["description"]
    assert "품질 룰과 항목별 통과 기준" not in body
    assert body.count("null ratio 측정값") == 1


def test_workload_words_follow_the_actual_rank_not_the_model_claim():
    from app.agent.workflow.agents.result_integrator import _align_workload_claims

    state = {"assignments": [{"user": "skcc.a100", "reasons": ["진행 중 8건"]},
                              {"user": "skcc.a200", "reasons": ["진행 중 12건"]},
                              {"user": "skcc.a300", "reasons": ["진행 중 17건"]}]}
    text = ("- skcc.a100 (진행 중 99건으로 부하가 높음)\n"
            "- skcc.a200 (진행 중 99건으로 부하가 적음)\n"
            "- skcc.a300 (진행 중 99건으로 부하가 낮음)")
    got = _align_workload_claims(text, state)
    assert "8건으로 부하가 가장 낮음" in got
    assert "12건으로 부하가 중간 수준" in got
    assert "17건으로 부하가 가장 높음" in got


def test_workload_comparison_and_causal_korean_follow_the_numbers():
    from app.agent.workflow.agents.result_integrator import _align_workload_claims

    state = {"assignments": [{"user": "skcc.a100", "reasons": ["진행 중 10건"],
                              "alternates": [{"user": "skcc.a200", "why": "진행 중 13건"}]},
                             {"user": "skcc.a300", "reasons": ["진행 중 12건"]}]}
    text = ("- skcc.a100: 진행 중 10건으로, 대안인 skcc.a200의 13건보다 부하가 가장 높음.\n"
            "- skcc.a300: 상대적으로 부하가 적어 설계를 맡깁니다.")
    got = _align_workload_claims(text, state)
    assert "13건보다 부하가 더 낮음" in got
    assert "부하가 중간 수준이어서 설계를" in got
    assert "수준어" not in got


def test_inline_workload_numbers_are_enough_when_alternate_state_is_missing():
    from app.agent.workflow.agents.result_integrator import _align_workload_claims

    state = {"assignments": [{"user": "skcc.a100", "reasons": ["진행 중 10건"]}]}
    got = _align_workload_claims(
        "skcc.a100은 진행 중 10건으로, 대안의 13건보다 부하가 가장 높음.", state)
    assert "13건보다 부하가 더 낮음" in got


def test_unrequested_deployment_is_removed_from_child_scope_too():
    from app.agent.workflow.agents.work_architect import _drop_unrequested_deployment_dod

    items = [{"children": [{"description": (
        '<h3>작업 범위</h3><ul><li>파이프라인 코드 작성 및 배포</li></ul>'
        '<h3>완료 조건 (DoD)</h3><ul data-type="taskList">'
        '<li data-checked="false">코드 리뷰 결과를 기록한다</li></ul>')}]}]
    assert _drop_unrequested_deployment_dod(_msg("MVP를 구현해줘"), items)
    body = items[0]["children"][0]["description"]
    assert "배포" not in body and "테스트 환경 검증" in body


def test_responder_removes_resolved_review_feedback_and_child_absence_claim():
    from app.agent.workflow.agents.result_integrator import _align_draft_claims

    items = [{"summary": "NDV", "description": (
        '<h3>완료 조건 (DoD)</h3><ul data-type="taskList">'
        '<li data-checked="false">측정값과 판정 결과를 기록한다</li></ul>'),
        "children": [{"summary": "설계", "description": (
            '<h3>완료 조건 (DoD)</h3><ul data-type="taskList">'
            '<li data-checked="false">산출물 링크와 리뷰 결과를 기록한다</li></ul>')}]}]
    text = ("- 가이드 DoD가 검증 가능하지 않아 수정해야 합니다.\n"
            "- 자식 작업은 별도로 설정되지 않았습니다.")
    got = _align_draft_claims(text, {"draft": {"items": items}})
    assert "수정해야" not in got
    assert "자식 작업 1건이 설정되었습니다" in got


def test_responder_drops_unrequested_document_deployment_claim():
    from app.agent.workflow.agents.result_integrator import _align_draft_claims

    items = [{"summary": "가이드", "description": (
        '<h3>완료 조건 (DoD)</h3><ul data-type="taskList">'
        '<li data-checked="false">가이드 링크와 리뷰 결과를 기록한다</li></ul>')}]
    state = _msg("가이드 작성해줘")
    state["draft"] = {"items": items}
    got = _align_draft_claims("가이드가 최종 승인되고 배포됨", state)
    assert "배포" not in got and "parent ticket" in got


def test_unspecified_numeric_performance_targets_are_not_invented():
    from app.agent.workflow.agents.work_architect import _mark_unspecified_acceptance_criteria

    items = [{"summary": "쿼리 성능 개선", "description": (
        '<h3>완료 조건 (DoD)</h3><ul data-type="taskList">'
        '<li data-checked="false">성능이 20% 이상 향상됨</li>'
        '<li data-checked="false">실행 시간이 1분 이내로 단축됨</li></ul>')}]
    assert _mark_unspecified_acceptance_criteria(_msg("쿼리 성능을 개선해줘"), items)
    body = items[0]["description"]
    assert "20%" not in body and "1분" not in body
    assert "성능 측정 지표와 목표값은 담당팀 확인 필요" in body


def test_minimal_lineage_body_has_a_semantic_verification_condition():
    from app.agent.workflow.agents.work_architect import _minimal_grounded_body

    body = _minimal_grounded_body({"summary": "[Catalog] 리니지 3홉 확장 구현"})
    assert "업스트림·다운스트림 경로와 일치" in body
    assert "요청한 작업이 반영" not in body


def test_existing_parent_topic_is_prefixed_to_top_level_subtasks():
    from app.agent.workflow.agents.work_architect import _preserve_existing_parent_topic

    items = [{"type": "Sub-Task", "parent": "DL-9090", "summary": "성능 측정 수행"}]
    assert _preserve_existing_parent_topic(items)
    assert "데이터 리니지 뷰어" in items[0]["summary"]
    assert items[0]["summary"].startswith("[Workbench]")


def test_child_assignment_table_uses_payload_order_even_if_reply_swaps_people():
    from app.agent.workflow.agents.result_integrator import _align_child_owner_claims

    items = [{"children": [
        {"summary": "등록 묶음 1/2", "assignee": "skcc.a100"},
        {"summary": "등록 묶음 2/2", "assignee": "skcc.a200"}]}]
    got = _align_child_owner_claims(
        "- Sub-Task 1: skcc.a200\n- Sub-Task 2: skcc.a100", items)
    assert "| 등록 묶음 1/2 | [~skcc.a100] |" in got
    assert "| 등록 묶음 2/2 | [~skcc.a200] |" in got


def test_workload_causal_rewrite_does_not_make_najeumasa():
    from app.agent.workflow.agents.result_integrator import _align_workload_claims

    state = {"assignments": [{"user": "skcc.a100", "reasons": ["진행 중 8건"]},
                              {"user": "skcc.a200", "reasons": ["진행 중 12건"]}]}
    got = _align_workload_claims("skcc.a100은 부하가 낮아서 적합합니다.", state)
    assert "부하가 가장 낮아서" in got and "낮음아서" not in got


def test_normalized_dod_rows_are_deduplicated():
    from app.agent.workflow.agents.work_architect import _dedupe_dod_rows

    row = "성능 측정 지표와 목표값은 담당팀 확인 필요"
    items = [{"description": ('<ul data-type="taskList">'
                              f'<li data-checked="false">{row}</li>'
                              f'<li data-checked="false">{row}</li></ul>')}]
    assert _dedupe_dod_rows(items)
    assert items[0]["description"].count(row) == 1


def test_multiple_actual_dod_tables_always_include_every_item():
    from app.agent.workflow.agents.result_integrator import _ensure_dod_claims

    items = [
        {"summary": "성능 측정", "description": '<h3>완료 조건 (DoD)</h3><ul><li>A 기록</li></ul>'},
        {"summary": "사용 가이드", "description": '<h3>완료 조건 (DoD)</h3><ul><li>B 기록</li></ul>'}]
    got = _ensure_dod_claims("검토 의견에 B 기록이 필요합니다.", items)
    assert "| 성능 측정 | A 기록 |" in got
    assert "| 사용 가이드 | B 기록 |" in got


def test_data_lineage_story_cannot_drift_into_a_game_narrative():
    game_body = ("<h3>배경</h3><p>게임의 몰입감을 높이고 플레이어 경험을 개선한다.</p>"
                 "<h3>작업 범위</h3><ul><li>캐릭터 관계와 클라이맥스를 작성한다.</li>"
                 "<li>제외: 기존 결말 수정</li></ul><h3>완료 조건 (DoD)</h3>"
                 "<ul data-type=\"taskList\"><li data-checked=\"false\">게임 스토리 완료</li></ul>")
    got = WorkArchitect().apply(
        _msg("리니지 3홉 확장 Story 만들어줘. 스토리포인트 5. 알아서"),
        {"questions": [], "mode": "task", "rationale": "", "items": [{
            "summary": "[Catalog] 리니지 3홉 확장 구현", "type": "Story",
            "components": ["Catalog"], "description": game_body}]})
    desc = got["draft"]["items"][0]["description"]
    assert all(w not in desc for w in ("게임", "플레이어", "캐릭터", "클라이맥스", "결말"))
    assert "리니지 3홉 확장" in desc and "조회 테스트 결과" in desc


def test_reply_cannot_claim_unsupported_story_points_or_game_narrative():
    from langchain_core.messages import HumanMessage
    from app.agent.workflow.agents.result_integrator import _align_draft_claims
    state = {"messages": [HumanMessage(
        content="리니지 3홉 확장 Story 만들고 스토리포인트 5로 넣어줘")],
        "draft": {"items": [{"summary": "[Catalog] 리니지 3홉 확장 구현",
                              "type": "Story", "description": "데이터 리니지 확장"}]}}
    text = ("게임의 몰입감을 높이고 캐릭터의 결말을 작성합니다. "
            "Story Point는 5로 설정합니다.\n### 승인 요청\n승인해 주세요.")
    got = _align_draft_claims(text, state)
    assert all(w not in got for w in ("게임", "캐릭터", "결말", "5로 설정"))
    assert "Story Point 5 미포함" in got and "화면에서 직접 설정" in got
    assert got.count("Story Point 5 미포함") == 1


def test_workload_only_assignment_uses_the_actual_lower_load_candidate():
    from app.agent.workflow.agents.people_advisor import _normalize_workload_choice
    got = _normalize_workload_choice({
        "index": 0, "user": "skcc.i2101",
        "reasons": ["진행중 8건으로 부하가 상대적으로 낮음", "열림 7건"],
        "children": [],
        "alternates": [{"user": "skcc.i2130", "why": "진행중 6건으로 부하가 높음"}]})
    assert got["user"] == "skcc.i2130"
    assert got["reasons"] == [
        "검증된 관련 이력 근거 없음 · 진행중 6건으로 후보 중 현재 부하가 가장 낮아 임시 추천"
    ]
    assert got["alternates"][0]["user"] == "skcc.i2101"
    assert "8건" in got["alternates"][0]["why"] and "높음" in got["alternates"][0]["why"]


def test_literal_recovery_uses_deterministic_workload_assignment_without_inverting_counts():
    from app.agent.workflow.agents.people_advisor import _workload_only_assignments

    draft = {"construction": "literal_delegated", "items": [{
        "summary": "[Catalog] 미등록 테이블 30개 등록", "components": ["Catalog"],
        "children": [{"summary": "묶음 1"}, {"summary": "묶음 2"}],
    }]}
    load = ("[Catalog 로스터·부하]\n"
            "- skcc.x1210 A — 진행중 10건 · 열림 9건 · 최근 완료 4건\n"
            "- skcc.i2044 B — 진행중 13건 · 열림 13건 · 최근 완료 3건")

    got = _workload_only_assignments(draft, load)[0]

    assert got["user"] == "skcc.x1210"
    assert got["reasons"] == [
        "Catalog 로스터 · 진행중 10건 · 열림 9건 · 관련 이력 없음"]
    assert [child["user"] for child in got["children"]] == [
        "skcc.x1210", "skcc.i2044"]


def test_recent_completion_count_is_not_recast_as_relevant_experience():
    from app.agent.workflow.agents.people_advisor import _normalize_workload_choice
    got = _normalize_workload_choice({
        "index": 0, "user": "skcc.i2101",
        "reasons": ["진행중 8건", "최근 완료 11건으로 경험이 많음"],
        "children": [],
        "alternates": [{"user": "skcc.i2130", "why": "진행중 6건으로 부하가 높음"}]})
    assert got["user"] == "skcc.i2130"
    assert "6건" in got["reasons"][0]


def test_assigner_removes_experience_not_backed_by_the_history_table():
    from app.agent.workflow.agents.people_advisor import PeopleAdvisor
    state = {"trace": [], "draft": {"items": [{"summary": "[ETL] NDV", "children": [
                {"summary": "설계"}]}]},
             "similar_history": "",
             "roster_load": ("[ETL 로스터·부하]\n- skcc.x1103 A — 진행중 8건\n"
                             "- skcc.i2011 B — 진행중 12건")}
    out = {"assignments": [{"index": 0, "user": "skcc.x1103",
            "reasons": ["현재 업무가 상대적으로 적음", "ETL 모듈 경험이 있음"],
            "children": [{"index": 0, "user": "skcc.i2011",
                          "why": "설계 업무에 적합"}],
            "alternates": [{"user": "skcc.i2011", "why": "ETL 경험이 풍부함"}]}]}
    got = PeopleAdvisor().apply(state, out)["assignments"][0]
    assert got["reasons"] == ["진행중 8건"]
    assert got["children"][0]["why"] == "진행중 12건"
    assert got["alternates"][0]["why"] == "진행중 12건"


def test_assigner_drops_plain_roster_names_from_reasons_and_uses_measured_load():
    from app.agent.workflow.agents.people_advisor import PeopleAdvisor
    state = {"trace": [], "draft": {"items": [{
        "summary": "[ETL] NDV 통계 생성", "components": ["ETL"]}]},
        "similar_history": "",
        "roster_load": ("[ETL 로스터·부하]\n"
                        "- skcc.x1103 한지우 — 진행중 8건\n"
                        "- skcc.i2011 최하은 — 진행중 12건")}
    out = {"assignments": [{"index": 0, "user": "skcc.x1103",
                            "reasons": ["최하은이 DL-9202를 맡아 본 경험이 있음"]}]}

    got = PeopleAdvisor().apply(state, out)["assignments"][0]

    assert got["reasons"] == ["진행중 8건"]
    assert "최하은" not in str(got)


def test_assigner_replaces_a_cross_module_candidate_with_verified_roster():
    from app.agent.workflow.agents.people_advisor import PeopleAdvisor
    state = {"trace": [], "draft": {"items": [{
        "summary": "[Catalog] 테이블 정리", "components": ["Catalog"],
        "children": [{"summary": "1차"}, {"summary": "2차"}]}]},
        "similar_history": "",
        "roster_load": (
            "[Catalog 로스터·부하]\n"
            "- skcc.x1210 A — 진행중 10건 · 열림 2건 · 최근 완료 4건\n"
            "- skcc.i2044 B — 진행중 13건 · 열림 1건 · 최근 완료 3건\n"
            "[ETL 로스터·부하]\n"
            "- skcc.x1042 C — 진행중 8건 · 열림 3건 · 최근 완료 5건")}
    out = {"assignments": [{"index": 0, "user": "skcc.x1042",
            "reasons": ["ETL 모듈 진행중 8건"],
            "children": [{"index": 0, "user": "skcc.x1042", "why": "진행중 8건"}],
            "alternates": []}]}

    got = PeopleAdvisor().apply(state, out)["assignments"][0]

    assert got["user"] == "skcc.x1210"
    assert got["reasons"] == ["Catalog 로스터 · 진행중 10건 · 열림 2건"]
    assert [c["user"] for c in got["children"]] == ["skcc.x1210", "skcc.i2044"]
    assert got["alternates"][0]["user"] == "skcc.i2044"


def test_assigner_restores_an_omitted_item_without_another_model_call():
    from app.agent.workflow.agents.people_advisor import PeopleAdvisor
    state = {"trace": [], "draft": {"items": [{
        "summary": "[Catalog] 정리", "components": ["Catalog"]}]},
        "roster_load": ("[Catalog 로스터·부하]\n"
                        "- skcc.x1210 A — 진행중 2건 · 열림 3건 · 최근 완료 4건")}

    got = PeopleAdvisor().apply(state, {"assignments": []})["assignments"]

    assert len(got) == 1 and got[0]["user"] == "skcc.x1210"
    assert got[0]["reasons"] == ["Catalog 로스터 · 진행중 2건 · 열림 3건"]


def test_explicit_singular_task_drops_model_generated_stage_children():
    out = {"questions": [], "mode": "task", "rationale": "", "items": [{
        "summary": "[DataOps] 적재 지연 알림 임계값 조정", "type": "Task",
        "components": ["DataOps"], "description": "",
        "children": [{"summary": "설계"}, {"summary": "구현"}, {"summary": "검증"}]}]}
    got = WorkArchitect().apply(
        _msg("적재 지연 알림 임계값을 45분으로 조정하는 Task 만들어줘. "
             "P1, 금요일까지, 알아서"), out)
    item = got["draft"]["items"][0]
    assert got["draft"]["structure"] == "single_task"
    assert got["draft"]["structure_source"] == "user_specified"
    assert not item.get("children") and not got["questions"]


def test_explicit_before_after_value_is_preserved_in_single_task_body():
    body = ("<h3>배경</h3><p>적재 지연 알림 임계값 조정 요청</p>"
            "<h3>작업 범위</h3><ul><li>포함: 임계값을 45분으로 조정</li>"
            "<li>제외: 알림 채널 변경</li></ul>"
            '<h3>완료 조건 (DoD)</h3><ul data-type="taskList">'
            '<li data-checked="false">45분 경계 전후 알림 발생 여부를 실행 로그로 확인한다</li>'
            "</ul>")
    got = WorkArchitect().apply(
        _msg("적재 지연 알림 임계값을 30분에서 45분으로 조정하는 Task 만들어줘. "
             "우선순위 P1, 이번 주 금요일까지, 라벨은 hotfix. 알아서"),
        {"questions": [], "mode": "task", "rationale": "", "items": [{
            "summary": "[DataOps] 적재 지연 알림 임계값 조정", "type": "Task",
            "components": ["DataOps"], "description": body,
            "priority": "P1-Critical", "labels": ["hotfix"]}]},
    )

    description = got["draft"]["items"][0]["description"]
    assert "변경 전 값: 30분 / 변경 후 값: 45분" in description
    assert got["draft"]["items"][0]["priority"] == "P1-Critical"


def test_story_point_is_removed_from_create_payload_and_rationale():
    body = ("<h3>배경</h3><p>리니지 3홉 조회 범위를 확장한다.</p>"
            "<h3>작업 범위</h3><ul><li>포함: 3홉 조회</li><li>제외: 4홉 이상</li></ul>"
            "<h3>완료 조건 (DoD)</h3><ul data-type=\"taskList\">"
            "<li data-checked=\"false\">3홉 결과를 테스트 리포트로 확인한다.</li></ul>")
    got = WorkArchitect().apply(
        _msg("리니지 3홉 확장 Story 만들고 스토리포인트 5로 넣어줘. 알아서"),
        {"questions": [], "mode": "task",
         "rationale": "Story Point는 생성 후 5로 할당 예정.",
         "items": [{"summary": "[Catalog] 리니지 3홉 확장 구현", "type": "Story",
                    "story_points": 5, "components": ["Catalog"], "description": body}]})
    item = got["draft"]["items"][0]
    assert "story_points" not in item
    why = got["draft"]["rationale"]
    assert "할당 예정" not in why and "생성 payload 미지원" in why


def test_reply_uses_actual_payload_dod_when_model_omits_or_echoes_internal_note():
    from app.agent.workflow.agents.result_integrator import _ensure_dod_claims
    items = [{"summary": "[Workbench] 성능 측정", "description":
              '<h3>완료 조건 (DoD)</h3><ul data-type="taskList">'
              '<li data-checked="false">p95 측정 리포트를 parent ticket에 첨부한다.</li></ul>'}]
    got = _ensure_dod_claims(
        "### 초안\n- **완료 조건 (DoD)**:\n### 승인 요청\n승인해 주세요.", items)
    assert "### 실제 완료 조건" in got and "p95 측정 리포트" in got
    assert got.index("### 실제 완료 조건") < got.index("### 승인 요청")
    echoed = _ensure_dod_claims(
        "- **완료 조건 (DoD)**: [본문에서 작성 지시 placeholder를 실제 최소 본문으로 바꿨습니다.]",
        items)
    assert "placeholder" not in echoed and "p95 측정 리포트" in echoed
    missing = _ensure_dod_claims("- **완료 조건 (DoD)**: (데이터 누락)", items)
    assert "데이터 누락" not in missing and "p95 측정 리포트" in missing


def test_subtask_reference_section_is_removed_because_parent_already_owns_context():
    from app.agent.workflow.agents.work_architect import _drop_subtask_ticket_refs
    body = ('<h3>참고</h3><ul><li>DL-9090 — parent</li><li>DL-9095 — sibling</li>'
            '<li><a href="https://confluence.example/doc">설계 문서</a></li></ul>')
    got = _drop_subtask_ticket_refs(body)
    assert "DL-9090" not in got and "DL-9095" not in got
    assert "https://confluence.example/doc" not in got and "설계 문서" not in got
    assert "<h3>참고</h3>" not in got


def test_draft_reply_drops_ticket_keys_not_in_evidence_user_or_payload_relation():
    from app.agent.workflow.agents.result_integrator import _drop_unverified_reply_keys
    state = {"mentioned_keys": ["DL-9090"],
             "evidence": [{"key": "DL-5326", "title": "쿼리 튜닝"}]}
    items = [{"summary": "성능 측정", "parent": "DL-9090", "epic": "DL-9000"}]
    text = ("DL-9090 아래에 만듭니다. DL-5326은 조사 근거입니다. "
            "관련 DL-123과의 관계 설명이 필요합니다. DL-9000 Epic에 둡니다.")
    got = _drop_unverified_reply_keys(text, state, items)
    assert all(k in got for k in ("DL-9090", "DL-5326", "DL-9000"))
    assert "DL-123" not in got


def test_model_questions_plus_free_opinion_never_exceed_three():
    qs = [{"question": f"질문 {n}", "kind": "text", "options": []} for n in range(5)]
    r = WorkArchitect().apply({}, {"questions": qs, "mode": "task", "rationale": "", "items": []})
    assert len(r["questions"]) == 3


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
    r = WorkArchitect().apply(_msg("리니지 뷰어 성능 측정하고 쿼리 엔진 인덱스도 손봐줘. 알아서"), out)
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
    r = WorkArchitect().apply(_msg("리니지 관련 정리 좀 해줘. 알아서"), out)
    assert len(r["draft"]["items"]) == 1
    assert len(r["draft"]["items"][0]["children"]) == 2


def test_a_shape_the_user_named_survives_the_module_split():
    """사용자가 'Sub-Task 로' 라고 말했으면 코드가 그 형태를 뒤집지 않는다."""
    out = _kids({"summary": "쿼리 엔진 인덱스 튜닝"}, components=["Catalog"])
    r = WorkArchitect().apply(_msg("리니지 뷰어 건 서브태스크로 쪼개줘"), out)
    assert len(r["draft"]["items"]) == 1


def test_an_empty_component_is_filled_from_the_items_own_words():
    """컴포넌트가 비면 담당 찾기가 **전사 명단**으로 넓어진다(§5-e 와 같은 갈래)."""
    out = {"questions": [], "mode": "task", "rationale": "",
           "structure": "single_task", "structure_source": "inferred",
           "items": [{"summary": "쿼리 엔진 버전 올리기", "type": "Task",
                      "description": "<h3>배경</h3><p>x</p>"}]}
    r = WorkArchitect().apply(_msg("쿼리 엔진 버전 올려줘. 알아서"), out)
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
    assert "from app.agent.workflow.agents.work_architect import DOD_VAGUE" in src, \
        "배터리는 work_architect 의 목록을 가져다 쓴다 — 여기에 다시 적으면 두 규칙이 갈린다"
    assert '_DOD_VAGUE = ("' not in src, "목록을 배터리에 다시 적었다"


def test_a_dod_row_with_a_verification_method_is_left_alone():
    """길게 쓴 완료 조건에는 판정 방법이 들어 있다 — 건드리면 오히려 나빠진다."""
    from app.agent.workflow.agents.work_architect import _vague_dod
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
    r = WorkArchitect().apply(_msg("실시간 처리 파이프라인 개발해줘. Epic 은 네가 골라줘. 알아서"), out)
    d = r["draft"]
    assert d["mode"] == "task", "새 Epic 을 만들지 않는다"
    assert d["items"][0].get("epic"), "고른 Epic 아래에 둔다"
    assert d["items"][0]["type"] != "Epic"


def test_delegated_ndv_pipeline_prefers_the_same_module_query_performance_epic():
    from app.agent.workflow.agents.work_architect import _pick_parent_epic

    got = _pick_parent_epic("[ETL] StarRocks Puffin NDV 통계 파이프라인 개발", "ETL")

    assert got and got["key"] == "DL-102"


def test_stripping_orphan_subtasks_never_empties_the_draft():
    """"부모는 나중에" 로 떼어 내는 분기는 **남는 게 있을 때만** 뗀다.

    실측 STR1: 전부가 부모 없는 Sub-Task 였더니 뗀 결과가 초안 0건이었다 — 답변은
    "부모 티켓을 생성하여 진행하겠습니다"라고 말하고 승인할 것은 없는 먹통.
    """
    out = {"questions": [], "mode": "task", "rationale": "", "items": [
        {"summary": "테이블 1 등록", "type": "Sub-Task"},
        {"summary": "테이블 2 등록", "type": "Sub-Task"}]}
    r = WorkArchitect().apply(_msg("테이블 30개 등록해줘. 사람 나눠서. 알아서"), out)
    got = r["draft"]["items"]
    assert got, "떼어 내서 0건이 될 바에는 Task 로 강등한다"
    assert all((i.get("type") or "") != "Sub-Task" for i in got), got


def test_an_orphan_subtask_is_still_stripped_when_a_parent_remains():
    """부모가 초안 안에 같이 있으면 원래대로 뗀다 — 위 수정이 이 갈래를 덮으면 안 된다."""
    out = {"questions": [], "mode": "task", "rationale": "", "items": [
        {"summary": "[ETL] 상위 작업", "type": "Task"},
        {"summary": "테이블 1 등록", "type": "Sub-Task"}]}
    r = WorkArchitect().apply(_msg("작업 만들어줘. 알아서"), out)
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
    r = WorkArchitect().apply(_msg("DL-5982 에픽 아래에서 메타데이터 미등록 테이블 등록해줘. 알아서",
                             mentioned_keys=["DL-5982"]), out)
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
    r = WorkArchitect().apply(_msg("통계정보를 생성하는 파이프라인을 개발해야해"), out)
    # 위임을 안 했으니 물어야 한다 — 뭉갠 채로 조용히 통과하면 안 된다
    assert r["questions"], "구조 이름이 new_epic 이어도 뭉갠 것은 뭉갠 것이다"


def test_a_plain_single_task_is_not_questioned():
    """기본값(티켓 하나)은 갈림이 없다 — 물을 이유가 없다."""
    out = {"questions": [], "mode": "task", "rationale": "",
           "structure": "single_task", "structure_source": "inferred",
           "items": [dict(_draft()["items"][0])]}
    r = WorkArchitect().apply(_msg("체크박스 하나 추가해줘"), out)
    assert not r["questions"]


# ── Q3: 주제 가드·섹션 통일·참고 불릿 가드·하향 편향 (STARR 실측 사고의 회귀) ──
def test_topic_drift_is_flagged_and_blocks_the_reviewer_bypass():
    """원 요청의 고유어가 제목·본문에 없으면 경고 + Auditor 단건 우회 금지 신호."""
    st = _msg("이번엔 마감을 9월로", request_text="starrocks puffin ndv 통계 파이프라인 개발")
    out = {"questions": [], "mode": "task", "rationale": "",
           "structure": "single_task", "structure_source": "user_specified",
           "items": [{"summary": "[ETL] 증분 적재용 최소 기능 파이프라인 1차 구현",
                      "type": "Task", "description": "<h3>배경</h3><p>증분 적재</p>"}]}
    r = WorkArchitect().apply(st, out)
    assert r["draft"].get("topic_drift") is True
    assert "고유어" in r["draft"]["rationale"]


def test_topic_kept_produces_no_drift_warning():
    st = _msg("진행해", request_text="starrocks puffin ndv 통계 파이프라인 개발")
    out = {"questions": [], "mode": "task", "rationale": "",
           "structure": "single_task", "structure_source": "user_specified",
           "items": [{"summary": "[ETL] StarRocks Puffin NDV 통계 생성 파이프라인 구축",
                      "type": "Task", "description": ""}]}
    r = WorkArchitect().apply(st, out)
    assert not r["draft"].get("topic_drift")


def test_label_with_sentence_punctuation_is_not_treated_as_missing_topic():
    from app.agent.workflow.agents.work_architect import _topic_drift

    state = _msg(
        "적재 지연 알림 임계값을 45분으로 조정하는 Task 만들어줘. 라벨은 hotfix. 알아서"
    )
    items = [{
        "summary": "[Observability] 적재 지연 알림 임계값 조정",
        "description": "<p>임계값을 45분으로 조정</p>",
        "labels": ["hotfix"],
    }]
    assert _topic_drift(state, items) == ""


def test_unlinked_reference_bullets_are_dropped_from_the_body():
    """링크도 키도 없는 참고 불릿(날조 문서 제목)은 코드가 뺀다 — 실측: '아키텍처 결정 기록'."""
    out = {"questions": [], "mode": "task", "rationale": "",
           "items": [{"summary": "s", "type": "Task",
                      "description": '<h3>참고</h3><ul><li>DL-9072 — 관련</li>'
                                     '<li>아키텍처 결정 기록</li><li>스프린트 회의록</li></ul>'}]}
    r = WorkArchitect().apply(_msg("작업 만들어줘"), out)
    d = r["draft"]["items"][0]["description"]
    assert "아키텍처 결정 기록" not in d and "스프린트 회의록" not in d
    assert "DL-9072" not in d
    assert "검증하지 않은 참고" in r["draft"]["rationale"]


def test_unlinked_bullets_under_reference_variant_are_dropped_too():
    """모델이 '참고 사항'이라고 써도 같은 출처 계약이다."""
    out = {"questions": [], "mode": "task", "rationale": "",
           "items": [{"summary": "s", "type": "Task",
                      "description": '<h3>참고 사항</h3><ul>'
                                     '<li>운영팀 구두 요청</li></ul>'}]}
    r = WorkArchitect().apply(_msg("작업 만들어줘"), out)
    assert "운영팀 구두 요청" not in r["draft"]["items"][0]["description"]


def test_reference_cleanup_runs_again_after_body_repair(monkeypatch):
    """본문 보정 호출이 참고를 새로 만들면 생산자 뒤의 두 번째 가드가 걷어낸다."""
    from app.agent.workflow.agents import work_architect as R

    repaired = ("<h3>배경</h3><p>요청</p><h3>작업 범위</h3>"
                "<ul><li>포함: 화면 표시</li><li>제외: 편집</li></ul>"
                '<h3>완료 조건 (DoD)</h3><ul data-type="taskList">'
                '<li data-checked="false">화면에서 설명 표시 확인</li></ul>'
                "<h3>참고 자료</h3><ul><li>운영팀 구두 요청</li></ul>")
    monkeypatch.setattr(R, "_task_for_module",
                        lambda *_a, **_k: {"description": repaired})
    out = {"questions": [], "mode": "task", "rationale": "",
           "items": [{"summary": "[Catalog] 컬럼 설명 표시", "type": "Task",
                      "description": "", "components": ["Catalog"]}]}
    r = WorkArchitect().apply(_msg("컬럼 설명을 화면에 보여줘. 알아서"), out)
    assert "운영팀 구두 요청" not in r["draft"]["items"][0]["description"]
    assert "본문 보정 뒤" in r["draft"]["rationale"]


def test_a_scope_without_exclusions_is_flagged_but_never_invented():
    """knowledge/07: '하지 않는 것을 적는 게 절반이다'. 제외가 빠지면 리뷰 때마다 '이것도
    포함인가요?'가 반복된다(DRAFT-COMPARISON 갭 ③ — 체커만 있고 가드가 없었다).
    무엇을 빼는지는 사용자만 아는 것이라 **채워 넣지는 않는다** — 알리기만 한다."""
    out = {"questions": [], "mode": "task", "rationale": "",
           "items": [{"summary": "[ETL] 적재 배치 재시도 로직 추가", "type": "Task",
                      "description": "<h3>작업 범위</h3><ul><li>포함: 재시도 로직</li></ul>"}]}
    r = WorkArchitect().apply(_msg("재시도 로직 추가해줘"), out)
    assert "하지 않는 것" in r["draft"]["rationale"]
    assert "제외" not in r["draft"]["items"][0]["description"], "지어내지는 않는다"


def test_a_scope_that_states_exclusions_is_not_flagged():
    out = {"questions": [], "mode": "task", "rationale": "",
           "items": [{"summary": "[ETL] 적재 배치 재시도 로직 추가", "type": "Task",
                      "description": "<h3>작업 범위</h3><ul><li>포함: 재시도 로직</li>"
                                     "<li>제외: 알림 채널 개편</li></ul>"}]}
    r = WorkArchitect().apply(_msg("재시도 로직 추가해줘"), out)
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
    r = WorkArchitect().apply(_msg("통계 파이프라인 개발해야 해"), out)
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
    r = WorkArchitect().apply(_msg("실시간 수집 파이프라인을 개발해야 해"), out)
    assert r["questions"] and "Sub-Task" in str(r["questions"][0].get("options"))


def test_numbered_volume_split_tasks_are_collapsed_into_children():
    """번호만 다른 Task N개(분량 분할 오판)는 코드가 1 Task + Sub-Task 로 접는다 —
    실측 재발: '테이블 1~5' Task 5개. 번호가 제목 중간에 있어도 잡는다."""
    out = {"questions": [], "mode": "task", "rationale": "",
           "structure": "multiple_tasks", "structure_source": "inferred",
           "items": [{"summary": f"[Catalog] 메타데이터 미등록 테이블 {i} 등록",
                      "type": "Task", "assignee": f"skcc.x{i}", "description": ""}
                     for i in range(1, 6)]}
    r = WorkArchitect().apply(_msg("테이블 30개 등록, 사람 나눠서. 알아서"), out)
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
    r = WorkArchitect().apply(_msg("테이블 8개 등록하고 결과 보고도. 알아서"), out)
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
    r = WorkArchitect().apply(_msg("세 가지 해줘. 알아서"), out)
    assert len(r["draft"]["items"]) == 3


def test_structure_is_filled_from_the_shape_when_the_model_omits_it():
    """모델이 structure 를 빠뜨리면 **구조 가드 둘이 조용히 꺼진다** — 하향 편향은
    single_task 를, 산출 어긋남 보정은 task_with_subtasks 를 키로 보기 때문이다
    (실측 STR1 4회 중 2회가 구조 미지정이었고 그때 두 가드 다 안 돌았다).
    채우는 것은 의도 추측이 아니라 **산출물 모양의 기술**이다."""
    with_kids = {"questions": [], "mode": "task", "rationale": "",
                 "items": [{"summary": "[ETL] 재시도 로직 추가", "type": "Task",
                            "children": [{"summary": "a"}, {"summary": "b"}]}]}
    assert WorkArchitect().apply(_msg("재시도 로직 추가해줘"),
                           with_kids)["draft"]["structure"] == "task_with_subtasks"
    alone = {"questions": [], "mode": "task", "rationale": "",
             "items": [{"summary": "[ETL] 재시도 로직 추가", "type": "Task"}]}
    assert WorkArchitect().apply(_msg("재시도 로직 추가해줘"),
                           alone)["draft"]["structure"] == "single_task"


def test_stage_split_tasks_are_collapsed_too():
    """단계 낱말만 다른 Task 들(…설계/…구현/…검증)도 같은 산출물 — 접는다."""
    out = {"questions": [], "mode": "task", "rationale": "",
           "structure": "task_with_subtasks", "structure_source": "inferred",
           "items": [{"summary": f"[ETL] NDV 통계 파이프라인 {w}", "type": "Task",
                      "description": ""} for w in ("설계", "구현", "검증")]}
    r = WorkArchitect().apply(_msg("파이프라인 개발. 알아서"), out)
    d = r["draft"]
    assert len(d["items"]) == 1 and len(d["items"][0]["children"]) == 3


def test_functionally_different_tasks_are_not_collapsed():
    """기능 분화(모듈·산출물이 다른 Task)는 접지 않는다 — 접으면 STR2 가 망가진다."""
    out = {"questions": [], "mode": "task", "rationale": "",
           "structure": "multiple_tasks", "structure_source": "user_specified",
           "items": [{"summary": "[Workbench] 성능 측정 리포트 작성", "type": "Task", "description": "",
                      "children": [{"summary": "설계"}, {"summary": "구현"}]},
                     {"summary": "[Runtime] 쿼리 인덱스 조정", "type": "Task", "description": ""},
                     {"summary": "[Catalog] 사용 가이드 작성", "type": "Task", "description": ""}]}
    r = WorkArchitect().apply(
        _msg("성능 측정하고 인덱스도 손보고 가이드도. 알아서", situation="조사 완료"), out)
    assert len(r["draft"]["items"]) == 3
    assert not any(i.get("children") for i in r["draft"]["items"])
    assert not r["questions"]
    assert all(i.get("description") for i in r["draft"]["items"])


def test_semantic_dedupe_prefers_direct_action_over_unrequested_assessment():
    from app.agent.workflow.agents.work_architect import _dedupe_semantic_items

    state = _msg("쿼리 엔진 쪽 인덱스도 손봐야 해. 알아서")
    rows = [
        {"summary": "[Runtime] 쿼리 엔진 인덱스 개선 필요성 평가",
         "components": ["Runtime"]},
        {"summary": "[Runtime] 쿼리 엔진 인덱스 최적화",
         "components": ["Runtime"]},
    ]

    removed = _dedupe_semantic_items(state, rows)

    assert len(rows) == 1
    assert "최적화" in rows[0]["summary"]
    assert any("필요성 평가" in title for title in removed)


def test_explicit_stage_request_keeps_nested_work_in_multi_task_plan():
    from app.agent.workflow.agents.work_architect import _drop_unrequested_nested_work

    state = _msg("성능 측정과 가이드 작성을 각각 Task로 만들고 측정은 단계별 Sub-Task로 나눠줘")
    rows = [
        {"summary": "성능 측정", "children": [{"summary": "설계"}, {"summary": "검증"}]},
        {"summary": "가이드 작성"},
    ]

    assert _drop_unrequested_nested_work(state, rows) == []
    assert len(rows[0]["children"]) == 2


def test_self_exclusion_is_removed_without_consuming_the_previous_include_bullet():
    from app.agent.workflow.agents.work_architect import _drop_self_exclusions

    rows = [{
        "summary": "[ETL] StarRocks Puffin NDV 통계 파이프라인 개발",
        "description": ("<h3>작업 범위</h3><ul>"
                        "<li>포함: 데이터 수집 로직 구현</li>"
                        "<li>제외: [ETL] StarRocks Puffin NDV 통계 파이프라인 개발</li>"
                        "</ul>"),
    }]

    assert _drop_self_exclusions(rows)
    assert "포함: 데이터 수집 로직 구현" in rows[0]["description"]
    assert "제외: 요청에 명시되지 않은 연관 기능 변경" in rows[0]["description"]
    assert "제외: [ETL]" not in rows[0]["description"]


def test_self_exclusion_is_dropped_when_another_scope_boundary_exists():
    from app.agent.workflow.agents.work_architect import _drop_self_exclusions

    rows = [{
        "summary": "[Runtime] 쿼리 엔진 인덱스 조정",
        "description": ("<ul><li>포함: 인덱스 조정</li>"
                        "<li>제외: [Runtime] 쿼리 엔진 인덱스 조정</li>"
                        "<li>제외(별도 ticket): 사용 가이드 작성</li></ul>"),
    }]

    assert _drop_self_exclusions(rows)
    body = rows[0]["description"]
    assert "제외: [Runtime]" not in body
    assert "제외(별도 ticket): 사용 가이드 작성" in body
    assert "요청에 명시되지 않은" not in body


def test_removed_deployment_dod_uses_deliverable_specific_evidence():
    from app.agent.workflow.agents.work_architect import _drop_unrequested_deployment_dod

    rows = [{
        "summary": "[Catalog] 리니지 뷰어 사용 가이드 작성",
        "description": ('<h3>완료 조건 (DoD)</h3><ul data-type="taskList">'
                        '<li data-checked="false">운영 환경 배포 완료</li></ul>'),
    }]

    assert _drop_unrequested_deployment_dod(_msg("사용 가이드 작성해줘"), rows)
    body = rows[0]["description"]
    assert "산출물 링크" in body and "내부 리뷰" in body
    assert "테스트 리포트" not in body and "배포" not in body


def test_relative_due_is_computed_by_code_not_the_model():
    """상대 날짜("다음주 수요일")는 코드가 계산한다 — 모델 산술이 요일을 틀렸다(실측:
    같은 질문에 수요일과 일요일을 번갈아 냈다). 과거로 떨어지면 다가오는 그 요일로."""
    from datetime import date, timedelta
    from app.agent.workflow.agents.work_architect import _relative_due
    d = date.fromisoformat(_relative_due("마감 다음주 수요일로 미루고"))
    assert d.weekday() == 2 and d > date.today()
    f = date.fromisoformat(_relative_due("이번 주 금요일까지"))
    assert f.weekday() == 4 and f >= date.today()
    assert _relative_due("그냥 미뤄줘") == ""
    assert _relative_due("내일까지") == (date.today() + timedelta(days=1)).isoformat()


def test_this_week_due_is_the_current_or_next_workweek_friday():
    from datetime import date, timedelta
    from app.agent.workflow.agents.work_architect import _relative_due

    today = date.today()
    friday = today - timedelta(days=today.weekday()) + timedelta(days=4)
    if friday < today:
        friday += timedelta(days=7)
    assert _relative_due("이번 주까지. 알아서") == friday.isoformat()
    assert _relative_due("금주까지 처리") == friday.isoformat()


def test_question_turn_never_claims_a_parentless_subtask_can_be_created():
    from app.agent.workflow.agents.result_integrator import ResultIntegrator

    state = _msg("서브태스크 하나만 딱 만들어줘. 부모는 없어도 돼")
    state["questions"] = [{"question": "어떤 방식으로 바꿀까요?", "kind": "choice",
                           "options": ["Task로 만든다", "부모를 지정한다"]}]
    got = ResultIntegrator().apply(
        state,
        {"text": "사용자가 원했으므로 별도의 부모 없이 서브태스크를 생성합니다. 아래에서 선택해 주세요"},
    )["reply"]

    assert "부모가 필수" in got
    assert "부모 없이 생성할 수 없음" in got
    assert "부모 없이 서브태스크를 생성" not in got


def test_question_only_turn_skips_the_text_llm(monkeypatch):
    from app.agent.workflow.agents.base import TextAgent
    from app.agent.workflow.agents.result_integrator import ResultIntegrator

    def unexpected_llm(*_args, **_kwargs):
        raise AssertionError("질문 폼 전용 턴에서 텍스트 LLM을 호출하면 안 됨")

    monkeypatch.setattr(TextAgent, "_run", unexpected_llm)
    state = _msg("정확한 변경 대상을 알려줘")
    state["questions"] = [{
        "question": "어느 티켓을 변경할까요?", "kind": "text", "options": [],
        "required_input": True, "why_required": "변경할 티켓을 식별할 수 없음",
    }]

    reply = ResultIntegrator()._run(state)["reply"]

    assert "### 확인 필요" in reply
    assert "변경할 티켓을 식별할 수 없음" in reply


def test_parentless_subtask_asks_for_a_valid_hierarchy_before_content():
    out = {"questions": [
        {"question": "배경은 무엇인가요?", "kind": "text", "options": []},
        {"question": "완료 조건은 무엇인가요?", "kind": "text", "options": []},
    ], "mode": "subtask", "rationale": "", "items": []}

    got = WorkArchitect().apply(
        _msg("서브태스크 하나만 딱 만들어줘. 부모는 없어도 돼"), out)

    assert not got["draft"]["items"]
    assert len(got["questions"]) == 1  # 유효한 계층 선택만 — 선택형 자유 의견은 중복 질문
    first = got["questions"][0]
    assert first["field"] == "parent"
    assert "부모 없이 만들 수 없습니다" in first["question"]
    assert "최상위 Task" in str(first["options"])


def test_relative_due_overrides_model_date_on_a_single_creation_draft():
    """생성 경로도 상대 기한을 코드로 고정한다 — 형식상 유효한 오답은 schema가 못 잡는다."""
    from app.agent.workflow.agents.work_architect import _relative_due
    out = {"questions": [], "mode": "task", "rationale": "",
           "structure": "single_task", "structure_source": "user_specified",
           "items": [{"summary": "[Catalog] hotfix 배포", "type": "Task",
                      "description": "", "duedate": "2099-01-01"}]}
    r = WorkArchitect().apply(_msg("이번 주 금요일까지 hotfix 배포 Task 만들어줘. 알아서"), out)
    assert r["draft"]["items"][0]["duedate"] == _relative_due("이번 주 금요일까지")


def test_duration_timebox_becomes_a_deterministic_due_date():
    from datetime import date, timedelta
    from app.agent.workflow.agents.work_architect import _relative_due
    assert _relative_due("기간은 2주 정도") == (date.today() + timedelta(days=14)).isoformat()


def test_relative_due_does_not_guess_across_multiple_creation_items():
    """복수 항목의 기한 배분은 사용자 의미 판단 — 하나의 상대 표현을 전부 덮어쓰지 않는다."""
    from app.agent.workflow.agents.work_architect import _apply_relative_due_to_single_draft
    items = [{"summary": "[Catalog] A", "type": "Task", "duedate": "2099-01-01"},
             {"summary": "[Runtime] B", "type": "Task", "duedate": "2099-01-02"}]
    applied = _apply_relative_due_to_single_draft(
        _msg("이번 주 금요일까지 A와 B를 각각 만들어줘"), items)
    assert applied == ""
    assert [i["duedate"] for i in items] == ["2099-01-01", "2099-01-02"]


def test_unrequested_quality_claims_are_removed_from_ticket_body():
    """그럴듯한 효익도 사용자가 말하지 않았으면 배경·범위·DoD의 날조다."""
    from app.agent.workflow.agents.work_architect import _remove_unrequested_quality_claims
    items = [{"summary": "[ETL] CDC 재처리 배치 개선", "type": "Task",
              "description": (
                  "<h3>배경</h3><p>성능과 운영 효율성을 높이기 위한 요청</p>"
                  "<h3>작업 범위</h3><ul><li>포함: 성능 개선</li><li>제외: 다른 배치</li></ul>"
                  '<h3>완료 조건 (DoD)</h3><ul data-type="taskList">'
                  '<li data-checked="false">시스템 안정성 테스트 완료</li></ul>')}]
    assert _remove_unrequested_quality_claims(_msg("CDC 재처리 배치 개선 Task 만들어줘"), items)
    body = items[0]["description"]
    assert all(word not in body for word in ("성능", "운영 효율성", "안정성"))
    assert "CDC 재처리 배치 개선 요청됨" in body
    assert "결과와 검증 기록" in body


def test_user_requested_quality_dimension_is_preserved():
    """사용자가 직접 말한 품질 차원은 제거 대상이 아니다."""
    from app.agent.workflow.agents.work_architect import _remove_unrequested_quality_claims
    body = ("<h3>배경</h3><p>쿼리 성능 개선 요청</p>"
            "<h3>작업 범위</h3><ul><li>포함: 쿼리 성능 개선</li></ul>"
            '<h3>완료 조건 (DoD)</h3><ul data-type="taskList">'
            '<li data-checked="false">성능 측정 결과를 기록</li></ul>')
    items = [{"summary": "[Runtime] 쿼리 성능 개선", "type": "Task", "description": body}]
    assert not _remove_unrequested_quality_claims(_msg("쿼리 성능 개선 Task 만들어줘"), items)
    assert items[0]["description"] == body


def test_unrequested_user_testing_is_removed_but_explicit_request_is_preserved():
    from app.agent.workflow.agents.work_architect import _remove_unrequested_quality_claims
    body = ('<h3>완료 조건 (DoD)</h3><ul data-type="taskList">'
            '<li data-checked="false">사용자 테스트를 통해 결과를 검증한다.</li></ul>')
    items = [{"summary": "[Catalog] 정리", "type": "Task", "description": body}]
    assert _remove_unrequested_quality_claims(_msg("Catalog 정리 Task 만들어줘"), items)
    assert "사용자 테스트" not in items[0]["description"]

    explicit = [{"summary": "[Catalog] 정리", "type": "Task", "description": body}]
    assert not _remove_unrequested_quality_claims(
        _msg("Catalog 정리 후 사용자 테스트를 수행하는 Task 만들어줘"), explicit)
    assert explicit[0]["description"] == body


def test_epic_typed_items_promote_the_mode_to_epic(monkeypatch):
    """"새 Epic 만들어줘"에 모델이 type=Epic 항목을 내면서 mode 를 task 로 두면 —
    epic 경로를 못 타 validate_bulk 가 거부하고 승인 카드 없이 죽었다(실측 Round K)."""
    monkeypatch.setattr(
        "app.agent.workflow.agents.work_architect._existing_epic_like", lambda _summary: None)
    out = {"questions": [], "mode": "task", "rationale": "",
           "structure": "new_epic", "structure_source": "user_specified",
           "items": [{"summary": "[DataOps] 데이터 품질 모니터링", "type": "Epic",
                      "epic_name": "품질모니터링", "description": ""}]}
    r = WorkArchitect().apply(_msg(
        "6주 동안 ETL, Catalog, DataOps의 skcc.x1103, skcc.x1327, skcc.x1402가 "
        "맡을 3건의 Task를 독립 진척 단위인 "
        "데이터 품질 모니터링 Epic으로 만들어줘. 알아서"), out)
    assert r["draft"]["mode"] == "epic"
    assert len(r["draft"]["items"]) == 1


def test_epic_scale_recognizes_task_count_after_the_word_task():
    from app.agent.workflow.agents.work_architect import _new_epic_unmet_criteria

    state = _msg(
        "6주 동안 skcc.x1103, skcc.x1327, skcc.x1402가 맡을 Task 3건을 "
        "독립 진척 단위인 데이터 품질 Epic으로 만들어줘")
    assert _new_epic_unmet_criteria(state) == []


def test_adding_one_more_item_keeps_the_pending_draft_items():
    """승인 전 초안에 "하나 더 추가"를 요청했는데 모델이 새 항목만 내면 —
    기존 항목이 통째로 사라진다(실측 Round O). 코드가 병합해 유지한다."""
    prev = {"mode": "task", "items": [{"summary": "[Catalog] 카탈로그 검색 성능 개선",
                                       "type": "Task", "description": "본문"}]}
    out = {"questions": [], "mode": "task", "rationale": "",
           "items": [{"summary": "[Catalog] 성능 측정 대시보드 구축", "type": "Task",
                      "description": "본문"}]}
    r = WorkArchitect().apply(_msg("좋아. 하나 더 추가해줘 — 성능 측정 대시보드 구축",
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
    r = WorkArchitect().apply(_msg("B 는 빼줘", draft=prev, turns=1), out)
    assert len(r["draft"]["items"]) == 1


def test_the_module_prefix_is_added_to_titles_when_the_model_forgets():
    """제목의 `[모듈]` 접두는 검색이 걸리는 관행이다(knowledge/01) — 긴 재료를
    붙여넣으면 모델이 빠뜨린다(실측 Round P). 코드가 붙인다."""
    out = {"questions": [], "mode": "task", "rationale": "",
           "items": [{"summary": "검색 응답시간 지표 대시보드 노출", "type": "Task",
                      "components": ["Catalog"]},
                     {"summary": "[ETL] 이미 붙어 있으면 그대로", "type": "Task",
                      "components": ["ETL"]}]}
    r = WorkArchitect().apply(_msg("회의 메모에서 할 일 뽑아서 티켓 만들어줘. 알아서"), out)
    sums = [i["summary"] for i in r["draft"]["items"]]
    assert sums[0].startswith("[Catalog] "), sums
    assert sums[1] == "[ETL] 이미 붙어 있으면 그대로"


def test_only_the_fields_the_user_asked_for_are_changed():
    """마감만 미뤄 달라고 했는데 우선순위까지 카드에 얹히면 모르고 승인한다(실측 Round P)."""
    out = {"questions": [], "mode": "task", "items": [], "rationale": "",
           "change": {"key": "DL-9090", "duedate": "2026-08-14", "priority": "P3-Minor"}}
    r = WorkArchitect().apply(_msg("두 번째 거 마감을 다음 주 금요일로 미뤄줘", intent=Intent.MODIFY), out)
    ch = r["change_plan"].get("changes") or {}
    assert "duedate" in ch and "priority" not in ch, ch


def test_empty_body_sections_are_removed():
    """참고에 실을 것이 없으면 섹션째 지운다 — 헤딩만 남은 '참고'가 티켓에 박제됐다(실측 S4)."""
    from app.agent.workflow.agents.work_architect import _drop_empty_sections
    d = ("<h3>배경</h3><p>왜 하는가</p><h3>완료 조건 (DoD)</h3><ul><li>검증</li></ul>"
         "<h3>참고</h3><ul></ul>")
    out = _drop_empty_sections(d)
    assert "참고" not in out
    assert "배경" in out and "완료 조건" in out and "검증" in out


def test_duplicate_question_names_the_real_candidate_and_asks_only_the_decision():
    from app.agent.workflow.agents.work_architect import _normalize_duplicate_and_bug_questions
    state = _msg("프로듀서를 Avro 로 전환하는 작업을 새로 만들자",
                 already_exists=True,
                 evidence=[{"key": "DL-9072", "title": "[ETL] 프로듀서 Avro 직렬화 전환",
                            "why": "같은 전환 범위를 진행 중"}])
    questions = _normalize_duplicate_and_bug_questions(state, [{"question": "어떻게 할까요?"}])
    assert len(questions) == 1
    assert "DL-9072" in questions[0]["question"]
    assert "프로듀서 Avro 직렬화 전환" in questions[0]["question"]


def test_intended_parent_epic_is_not_treated_as_a_duplicate_child_task():
    from app.agent.workflow.agents.work_architect import _normalize_duplicate_and_bug_questions

    state = _msg("DL-9200 아래 PSR 증빙 추출 Task를 만들어줘", already_exists=True,
                 evidence=[{"key": "DL-9200", "title": "Iceberg Puffin NDV 도입",
                            "why": "같은 이니셔티브"}])
    items = [{"summary": "[Catalog] PSR 증빙 원본 추출", "type": "Task",
              "epic": "DL-9200"}]
    questions = _normalize_duplicate_and_bug_questions(
        state, [{"question": "같은 작업이 이미 있습니다"}], items=items)
    assert questions == []


def test_bug_interview_groups_only_missing_diagnostic_facts():
    from app.agent.workflow.agents.work_architect import _normalize_duplicate_and_bug_questions
    bug1 = _normalize_duplicate_and_bug_questions(
        _msg("리니지 뷰어가 가끔 안 뜬다. 버그로 올려줘"),
        [{"question": "실제 동작은?"}, {"question": "기대 동작은?"}])
    assert len(bug1) == 1 and "환경" in bug1[0]["question"]
    assert "가끔 표시되지 않음" in bug1[0]["question"]
    bug3 = _normalize_duplicate_and_bug_questions(
        _msg("야간 배치가 커넥션 타임아웃으로 실패한다. 버그로 등록해줘"),
        [{"question": "재현은?"}])
    assert len(bug3) == 1
    assert all(word in bug3[0]["question"] for word in ("DAG/Job", "실행 환경", "오류 로그"))


def test_data_fixture_labels_are_dropped():
    """배치 재료로 기존 라벨 목록을 주니 모델이 데이터 관리용 표식을 집었다(실측:
    카탈로그 검색 티켓에 `ui-fixture`). 그 필터로 조회하는 화면이 오염된다.
    일반 라벨은 건드리지 않는다 — 적절성은 사용자가 카드에서 판단한다."""
    out = {"questions": [], "mode": "task", "rationale": "",
           "items": [{"summary": "[Catalog] 검색 성능 개선", "type": "Task",
                      "components": ["Catalog"],
                      "labels": ["ui-fixture", "tbl-lineage_ui", "성능"]}]}
    r = WorkArchitect().apply(_msg("카탈로그 검색 성능 개선 티켓 만들어줘. 알아서"), out)
    labels = r["draft"]["items"][0].get("labels") or []
    assert "ui-fixture" not in labels and "tbl-lineage_ui" not in labels
    assert "성능" in labels, "일반 라벨은 남아야 한다"


# ── 경로별 프롬프트 조립 ────────────────────────────────────────────
def test_section_titles_used_for_pruning_really_exist():
    """제목이 하나라도 어긋나면 **조용히 아무것도 안 빠진다** — 그러면 최적화가
    사라진 줄도 모르고 토큰만 계속 나간다. 제목 존재를 테스트가 지킨다."""
    from app.agent.prompts.roles import SYSTEM_WORK_ARCHITECT, sections
    from app.agent.workflow.agents.work_architect import _CREATE_ONLY, _MODIFY_ONLY
    have = set(sections(SYSTEM_WORK_ARCHITECT))
    for t in _CREATE_ONLY + _MODIFY_ONLY:
        assert t in have, f"work_architect.md 에 '## {t}' 절이 없다"


def test_modify_turns_drop_the_creation_only_sections():
    """기존 티켓의 필드를 바꾸는 턴에 '어떻게 쪼갤 것인가'·'본문 4섹션' 지시는
    판단에 쓰이지 않으면서 매 호출 2천 토큰을 태운다."""
    from app.agent.workflow.agents.work_architect import _role_md
    md = _role_md({"intent": Intent.MODIFY})
    assert "분할 규칙" not in md and "구조 선택" not in md
    assert "Existing Ticket Changes" in md, "변경 경로 지시는 남아야 한다"
    # 초안을 고치는 modify 턴은 생성 지시가 필요하다 — 빼면 안 된다.
    md2 = _role_md({"intent": Intent.MODIFY, "draft": {"items": [{"summary": "s"}]}})
    assert "Decomposition Rules" in md2


def test_creation_turns_keep_every_creation_section():
    """초안을 만드는 턴에서는 품질이 먼저다 — 생성 지시를 빼지 않는다."""
    from app.agent.workflow.agents.work_architect import _role_md
    md = _role_md({"intent": Intent.PLAN_WORK})
    for t in ("Structure Selection", "Decomposition Rules", "Ticket Body Contract",
              "Epic Creation", "Title and Topic Preservation"):
        assert t in md


# ── 정성 판독(실 LLM)에서 잡힌 결함의 회귀 ──────────────────────────────────
def test_repeated_sentence_is_folded():
    """같은 문장을 두 번 쓰면 접는다 — 표·목록의 정당한 반복은 놔둔다.

    실측: "아래 카드에서 확인 후 승인해 주세요."가 문단 끝과 그다음 줄에 각각 나왔다.
    모델은 자기가 두 번 썼다는 걸 모르므로 프롬프트로 막을 종류가 아니다.
    """
    from app.agent.workflow.agents.result_integrator import _dedupe_sentences

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

    from app.agent.workflow.agents import work_architect as R

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

    실측: Auditor 반려로 재작성이 돌면 구조 이유가 바뀌는데, 앞선 왕복에서 붙은 옛
    줄이 남아 승인 카드에 서로 다른 두 이유가 떴다(헤더는 새 것, 근거 줄은 옛 것).
    """
    import re

    from app.agent.workflow.agents.work_architect import WorkArchitect

    state = {"intent": "plan_work", "messages": [], "draft": {}}
    out = {"mode": "task", "structure": "single_task",
           "structure_why": "단일 산출물이라 Task 하나면 된다",
           "rationale": "(구조: task_with_subtasks — 옛 왕복에서 남은 이유)",
           "items": [{"type": "Task", "summary": "[ETL] 적재 재시도 로직 추가",
                      "description": "<h3>배경</h3><p>x</p>", "components": ["ETL"]}]}
    got = WorkArchitect().apply(state, out)
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
    from app.agent.workflow.agents.work_architect import _children_from_dod
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


def test_an_explicit_stage_split_never_falls_back_to_zero_children(monkeypatch):
    """보정 LLM과 얇은 DoD가 모두 빈손이어도 사용자 지정 구조는 실제 카드가 되어야 한다."""
    from app.agent import config as C
    from app.agent.workflow.agents.work_architect import _split_into_children

    def fail_llm(*_args, **_kwargs):
        raise RuntimeError("provider unavailable")

    monkeypatch.setattr(C, "get_llm", fail_llm)
    item = {"summary": "[ETL] Iceberg Puffin NDV 통계 생성 Batch Job 구현",
            "description": '<h3>완료 조건 (DoD)</h3><ul data-type="taskList">'
                           '<li data-checked="false">Batch Job 실행 성공</li>'
                           '<li data-checked="false">통계 정확성 확인</li></ul>'}
    kids = _split_into_children(_msg("단계별 Sub-Task 로 나눠줘. 알아서"), item)
    assert len(kids) == 3
    assert all("Iceberg Puffin NDV" in c["summary"] for c in kids), kids
    assert [c["summary"].split()[-1] for c in kids] == ["설계", "구현", "검증"]


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
    r = WorkArchitect().apply(_msg("starrocks puffin ndv 통계정보 파이프라인을 개발해야해. 알아서"), out)
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
    r = WorkArchitect().apply(_msg("적재 파이프라인 정리해줘. 알아서"), out)
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
    r = WorkArchitect().apply(_msg("서브태스크 하나만 만들어줘. 부모는 없어도 돼"), out)
    if not r["draft"]["items"]:                      # 전부 걷힌 경로일 때만 단언한다
        assert r["questions"], "초안이 0건이면 최소한 다음 수를 물어야 한다"
        assert "제외" in str(r["draft"].get("rationale") or ""), \
            "무엇이 사라졌는지 기록이 남아야 사후에 추적할 수 있다"


def test_an_unrelated_capability_notice_is_stripped_from_the_reason_line():
    """승인 카드의 근거 줄은 사용자가 **판단하는 자리**다 — 묻지 않은 안내가 있으면 안 된다.

    실측(CMTB1): 일괄 코멘트 계획의 why 가 "삭제는 지원되지 않음. 상태를 닫음으로 전이…"
    였다. 삭제 요청이 아니었는데 프롬프트의 예외 안내를 모델이 옮겨 적은 것이다.
    """
    from app.agent.workflow.agents.work_architect import _change_plan
    out = {"rationale": "(삭제는 지원되지 않는다 — 상태 전이(닫음)나 보관 라벨을 대안으로 안내)",
           "change": {}}
    plan = {"keys": ["DL-9090"], "changes": {},
            "why": "삭제는 지원되지 않음. 상태를 닫음으로 전이하세요."}
    st = _msg("ETL 티켓 전부에 상태 점검 코멘트 남겨줘")     # 삭제 이야기가 없다
    got, _qs = _change_plan(st, out, [], [])
    assert "삭제는 지원되지" not in (out.get("rationale") or ""), out.get("rationale")

    # 진짜 삭제 요청이면 안내가 남아야 한다 — 지우는 가드가 필요한 안내까지 지우면 안 된다
    out2 = {"rationale": "(삭제는 지원되지 않는다 — 상태 전이(닫음)나 보관 라벨을 대안으로 안내)",
            "change": {}}
    _change_plan(_msg("DL-9090 삭제해줘"), out2, [], [])
    assert "삭제는 지원되지" in (out2.get("rationale") or "")


def test_a_bug_report_keeps_its_body_rules_even_if_intent_slips():
    """버그 초안은 **의도가 아니라 요청의 내용**으로 고른다(사용자 지적).

    `report_bug` 는 `plan_work` 와 지나는 노드도 도구도 같다 — 다른 것은 WorkArchitect 의 goal
    하나뿐이라, 결국 "Task 를 만드는데 type 이 Bug"다. 갈래로 두면 분류가 틀릴 때
    본문 템플릿이 통째로 바뀐다(재현·기대·실제 → 배경·범위·DoD). 바뀌면 안 되는 것이다.
    """
    from app.agent.workflow.agents.work_architect import WorkArchitect
    from app.agent.workflow.state import Intent

    r = WorkArchitect()
    # 의도가 plan_work 로 **미끄러져도** 버그 본문 규율이 적용된다
    task = r.task({"messages": _msg("적재 배치가 어젯밤부터 계속 실패한다. 버그로 올려줘"
                                    )["messages"],
                   "intent": Intent.PLAN_WORK, "situation": "조사 결과"})
    assert "재현 경로" in task and "기대 동작" in task, "버그 본문 규율이 빠졌다"

    # 버그 이야기가 아니면 평소 규율이다 — 아무 요청에나 버그 템플릿을 씌우면 안 된다
    plain = r.task({"messages": _msg("메타데이터 등록 작업 만들어줘")["messages"],
                    "intent": Intent.PLAN_WORK, "situation": "조사 결과"})
    assert "재현 경로" not in plain

def test_a_bug_body_is_never_overwritten_with_the_task_template(monkeypatch):
    """Bug 본문에 **재현 경로·기대·실제**가 남는가 — 코드가 보장한다.

    실측 사고(사용자 관점 리뷰 F5, blocker): 사용자가 "크롬에서 재현되고 기대는 그래프가
    그려지는 것"까지 줬는데 승인 카드의 본문은 **배경 · 작업 범위 · 완료 조건**이었다.
    재현 경로가 통째로 사라진 Bug 티켓은 아무도 못 잡는다.

    원인은 판단이 아니라 **배선**이었다. 지시문은 갈래를 나눠 옳게 시켰는데, 본문이 얇을 때
    다시 쓰는 `_fill_thin_bodies` 가 **Task 템플릿밖에 몰라서** 모델이 옳게 쓴 것을 덮었다.
    (이 저장소가 반복해 배운 것: 판단이 갈리면 **보장도 같이 갈려야 한다**.)

    LLM 은 막아 둔다 — 보정 호출이 빈손이어도 최소선은 서야 한다는 것까지 재는 테스트다.
    """
    from app.agent.workflow.agents import work_architect as R

    # 보정 LLM 을 끊는다 — 예외가 나도 본문은 나가야 한다(코드가 조립하는 부분만 남는다)
    monkeypatch.setattr("app.agent.config.get_llm",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no llm")))

    st = _msg("리니지 뷰어에서 2홉 이상 펼치면 화면이 빈다. 크롬에서 재현되고 "
              "기대는 그래프가 그려지는 것. 버그로 올려줘")
    items = [{"summary": "[Workbench] 리니지 뷰어에서 2홉 이상 펼치면 화면이 빈다",
              "type": "Bug", "description": "<h3>배경</h3><p>증상이 발생하고 있습니다.</p>",
              "components": ["Workbench"]}]
    R._fill_thin_bodies(st, items, repair=True)
    body = items[0]["description"]
    for sec in ("재현", "기대", "실제"):
        assert sec in body, (sec, body)
    # Task 템플릿이 섞여 들어오지 않는다 — 버그에 작업 범위·DoD 는 잡는 데 안 쓰인다
    assert "작업 범위" not in body and "완료 조건" not in body, body


def test_pasted_voc_uses_reported_screen_symptom_instead_of_wrapper_or_placeholder(monkeypatch):
    """PASTE1: 붙여넣기 명령문은 actual이 아니며, 원문에 있는 재현 정보는 버리지 않는다."""
    from app.agent.workflow.agents import work_architect as R
    monkeypatch.setattr("app.agent.config.get_llm",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no llm")))
    st = _msg("아래 VoC 그대로 티켓으로 만들어줘. 알아서\n\n---\n"
              "데이터 조회할 때 컬럼 설명이 안 보여서 담당자에게 묻고 있습니다. "
              "조회 화면에서 바로 봤으면 좋겠습니다.")
    body = R._bug_body_for(st, {"summary": "[Catalog] 조회 화면 컬럼 설명 미노출",
                                "type": "Bug"})
    assert R._ASK_REPORTER not in body, body
    assert "조회 화면" in body and "컬럼 설명" in body
    assert "컬럼 설명을 바로 확인할 수 있음" in body
    assert "담당자에게 별도로 확인 중" in body
    assert "봤으면 좋겠습니다" not in body and "묻고 있습니다" not in body
    assert "티켓으로 만들어줘" not in body and "---" not in body


def test_complete_pasted_voc_recovers_bug_draft_instead_of_reasking_reproduction(monkeypatch):
    """A complete report must survive a model response containing only a generic question."""
    from app.agent.workflow.agents import work_architect as R

    monkeypatch.setattr("app.agent.config.get_llm",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no llm")))
    state = _msg(
        "아래 VoC 그대로 티켓으로 만들어줘. 알아서\n\n---\n"
        "데이터 조회할 때 컬럼 설명이 안 보여서 담당자에게 묻고 있습니다. "
        "카탈로그에 설명이 있다는데 화면에서는 안 보입니다. "
        "조회 화면에서 바로 봤으면 좋겠습니다.",
        related_docs=[{"title": "LTM 사용 가이드", "url": "#/home"}],
    )
    out = {"questions": [{"question": "재현 경로를 알려 주세요", "kind": "text",
                           "required_input": True, "why_required": "재현 정보 필요"}],
           "mode": "task", "rationale": "재현 경로가 필요하여 추가 정보를 요청합니다.",
           "items": []}
    result = WorkArchitect().apply(state, out)
    rows = result["draft"]["items"]
    assert len(rows) == 1 and rows[0]["type"] == "Bug"
    assert not result["questions"]
    assert "컬럼 설명" in rows[0]["summary"]
    assert all(value in rows[0]["description"] for value in ("재현 경로", "기대 동작", "실제 동작"))
    assert "LTM 사용 가이드" not in rows[0]["description"] and "#/home" not in rows[0]["description"]
    assert "추가 정보를 요청" not in result["draft"]["rationale"]


def test_existing_bug_sections_replace_placeholder_actual_with_reported_symptom(monkeypatch):
    """PASTE2/BUG2: headings alone are not a pass when the user already supplied the actual result."""
    from app.agent.workflow.agents import work_architect as R
    monkeypatch.setattr("app.agent.config.get_llm",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no llm")))
    state = _msg("운영에서 배치를 실행하면 connection timeout으로 실패한다. 버그로 등록해줘")
    items = [{"summary": "[ETL] 배치 connection timeout", "type": "Bug",
              "description": ("<h3>재현 경로</h3><p>운영 배치 실행</p>"
                              "<h3>기대 동작</h3><p>배치 완료</p>"
                              "<h3>실제 동작</h3><p>확인 필요</p>")}]
    R._fill_thin_bodies(state, items, repair=True)
    R._repair_bug_facts_from_report(state, items)
    body = items[0]["description"]
    assert "connection timeout으로 실패" in body
    assert "<h3>실제 동작</h3><p>확인 필요" not in body


def test_pasted_runtime_incident_preserves_environment_target_and_retry_facts(monkeypatch):
    """PASTE2: a complete chat incident is executable evidence, not an empty Bug form."""
    from app.agent.workflow.agents import work_architect as R
    monkeypatch.setattr("app.agent.config.get_llm",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no llm")))
    state = _msg(
        "이거 버그로 등록해줘. 알아서\n\n"
        "[10:12] 김운영: prod의 dag_etl_nightly 야간 배치 또 실패했어요\n"
        "[10:13] 이개발: 로그 보니 커넥션 타임아웃이네요. 어제도 같은 시간대\n"
        "[10:15] 김운영: 재실행하면 되긴 하는데 매일 이러면 곤란해요"
    )
    body = R._bug_body_for(state, {"summary": "[ETL] 야간 배치 실패", "type": "Bug"})
    for section in ("재현 경로", "기대 동작", "실제 동작"):
        assert section in body
    for literal in ("prod", "dag_etl_nightly", "커넥션 타임아웃", "재실행", "매일"):
        assert literal in body, (literal, body)
    assert R._ASK_REPORTER not in body


def test_adjacent_repeated_title_phrase_is_collapsed_only_once():
    from app.agent.workflow.agents.work_architect import _collapse_repeated_summary
    assert _collapse_repeated_summary(
        "[Workbench] 데이터 리니지 뷰어 리니지 뷰어 성능 회귀 테스트") == \
        "[Workbench] 데이터 리니지 뷰어 성능 회귀 테스트"
    assert _collapse_repeated_summary("[ETL] 설계 구현 검증") == "[ETL] 설계 구현 검증"


def test_explicit_implementation_child_covers_the_generic_implementation_stage():
    from app.agent.workflow.agents.work_architect import _execution_stage

    assert _execution_stage("Puffin NDV 통계 생성 Batch Job 구현") == "implementation"
    assert _execution_stage("Puffin NDV PoC — 구현") == "implementation"
    assert _execution_stage("Puffin NDV PoC — 검증") == "validation"

    from app.agent.workflow.agents.work_architect import WorkArchitect
    state = _msg("Puffin NDV Batch Job 구현을 단계별 Sub-Task로 나눠줘. 알아서")
    out = {"mode": "task", "structure": "task_with_subtasks", "items": [{
        "summary": "[ETL] Puffin NDV PoC",
        "type": "Task",
        "components": ["ETL"],
        "description": ("<h3>배경</h3><p>Puffin NDV PoC 요청</p>"
                        "<h3>작업 범위</h3><ul><li>포함: 배치 Job 구현</li>"
                        "<li>제외: 운영 배포</li></ul>"
                        "<h3>완료 조건 (DoD)</h3><ul data-type=\"taskList\">"
                        "<li data-checked=\"false\">PoC 결과 기록</li></ul>"),
        "children": [{"summary": "Puffin NDV 통계 생성 Batch Job 구현"}],
    }]}
    result = WorkArchitect().apply(state, out)
    summaries = [row["summary"] for row in result["draft"]["items"][0]["children"]]
    assert sum(_execution_stage(value) == "implementation" for value in summaries) == 1
    assert {_execution_stage(value) for value in summaries} >= {"design", "validation"}


def test_unmentioned_logged_in_user_is_not_invented_as_the_ticket_requester():
    from app.agent.workflow.agents import work_architect as R

    state = _msg("Iceberg Puffin NDV 배치 Job을 구현해줘")
    items = [{
        "summary": "[ETL] Puffin NDV 배치 Job 구현",
        "description": ("<h3>배경</h3><p>배치 Job 구현 요청. "
                        "{{mention:UI픽스처01}}의 요청에 따라 진행됩니다.</p>"),
    }]

    assert R._drop_unrequested_requester_attribution(state, items) is True
    assert "UI픽스처01" not in items[0]["description"]
    assert "배치 Job 구현 요청" in items[0]["description"]


def test_self_exclusion_and_unverified_performance_cause_are_removed():
    from app.agent.workflow.agents import work_architect as R
    item = {"summary": "[ETL] 쿼리 성능 대대적 개선", "type": "Task",
            "components": ["ETL"],
            "description": ("<h3>배경</h3><p>쿼리 성능 저하로 데이터 처리 속도가 느립니다.</p>"
                            "<h3>작업 범위</h3><ul><li>포함: 인덱스 최적화</li>"
                            "<li>제외: [ETL] 쿼리 성능 대대적 개선</li></ul>"
                            "<h3>완료 조건 (DoD)</h3><ul data-type=\"taskList\">"
                            "<li data-checked=\"false\">인덱스 적용 후 측정 결과 기록</li></ul>")}
    state = _msg("쿼리 성능 개선을 대대적으로 해보자. 기간은 2주고 ETL 쪽만 손볼 거야")
    assert R._remove_unrequested_quality_claims(state, [item])
    R._drop_self_exclusions([item])
    body = item["description"]
    assert "속도가 느" not in body and "인덱스" not in body
    assert "제외: [ETL] 쿼리 성능 대대적 개선" not in body
    assert "제외: ETL 외 모듈 변경" in body


def test_long_subject_does_not_hide_a_vague_completion_condition():
    from app.agent.workflow.agents.work_architect import _sharpen_dod, _vague_dod
    assert _vague_dod(["StarRocks Puffin NDV 통계정보 생성 파이프라인이 정상적으로 작동함"])
    assert _vague_dod(["측정 결과에 대한 검토 완료"])
    assert _vague_dod(["도움말 팝업이 정상적으로 표시됨"])
    assert _vague_dod(["신규 테이블의 널 비율이 성공적으로 체크됨"])
    assert _vague_dod(["체크 결과가 문서화되어 관련 팀에 공유됨"])
    assert _vague_dod(["회귀 테스트 결과가 기록되고 검토됨"])
    assert _vague_dod(["테스트 결과가 관련 문서에 첨부됨"])
    assert _vague_dod(["신규 테이블의 널 비율이 계산되어 보고됨"])
    assert not _vague_dod(["NDV 생성 결과와 테스트 로그를 티켓에 기록함"])
    item = {"summary": "[ETL] StarRocks Puffin NDV 통계정보 생성 파이프라인 개발",
            "type": "Task", "description": (
                "<h3>완료 조건 (DoD)</h3><ul data-type=\"taskList\">"
                "<li data-checked=\"false\">NDV 통계정보 생성 파이프라인이 정상적으로 작동해야 할 것</li>"
                "<li data-checked=\"false\">관련 문서화 완료</li></ul>")}
    assert _sharpen_dod(_msg("NDV 파이프라인 개발"), [item])
    assert "작동해야" not in item["description"] and "문서화 완료" not in item["description"]
    assert "함 실행" not in item["description"] and "할 것 실행" not in item["description"]


def test_vague_subtask_dod_is_replaced_instead_of_repeated_before_evidence():
    from app.agent.workflow.agents.work_architect import _sharpen_dod

    item = {"summary": "[Workbench] 데이터 리니지 뷰어 성능 측정", "type": "Sub-Task",
            "description": ('<h3>완료 조건 (DoD)</h3><ul data-type="taskList">'
                            '<li data-checked="false">측정 결과에 대한 검토 완료</li></ul>')}

    assert _sharpen_dod(_msg("성능 측정 서브태스크"), [item])
    body = item["description"]
    assert "측정 결과에 대한 검토 완료" not in body
    assert "검증 기준·측정값·판정 결과" in body


def test_specific_artifact_dod_supersedes_generic_review_row():
    from app.agent.workflow.agents.work_architect import _dedupe_dod_rows

    item = {"summary": "[Catalog] 사용 가이드 작성", "description": (
        '<ul data-type="taskList">'
        '<li data-checked="false">사용 가이드 작성 결과가 반영되었음을 담당 리뷰로 확인한다</li>'
        '<li data-checked="false">가이드 링크와 내부 리뷰 결과를 parent ticket에 기록한다</li>'
        '</ul>')}

    assert _dedupe_dod_rows([item])
    body = item["description"]
    assert "결과가 반영되었음을" not in body
    assert "가이드 링크와 내부 리뷰 결과" in body


def test_late_normalizers_cannot_reintroduce_an_unrequested_quality_dimension():
    from app.agent.workflow.agents import work_architect as R

    item = {"summary": "[ETL] NDV 통계 파이프라인 구축", "type": "Task",
            "description": ('<h3>완료 조건 (DoD)</h3><ul data-type="taskList">'
                            '<li data-checked="false">성능 측정 지표와 목표값은 담당팀 확인 필요 — '
                            '확정 후 측정값과 판정 결과를 티켓에 기록한다</li></ul>')}

    assert R._remove_unrequested_quality_claims(_msg("NDV 통계 파이프라인 구축"), [item])
    assert "성능 측정" not in item["description"]
    assert "검증 기록" in item["description"] and "담당 리뷰" in item["description"]


def test_a_plain_task_still_gets_the_task_template(monkeypatch):
    """반대편 — 버그가 아니면 배경이 채워지는 기존 규율은 그대로다."""
    from app.agent.workflow.agents import work_architect as R
    monkeypatch.setattr("app.agent.config.get_llm",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no llm")))
    st = _msg("메타데이터 등록 작업이 필요해")
    items = [{"summary": "[Catalog] 메타데이터 등록", "type": "Task",
              "description": "", "components": ["Catalog"]}]
    R._fill_thin_bodies(st, items, repair=True)
    assert "배경" in items[0]["description"]
    assert "재현" not in items[0]["description"]


def test_assignee_ids_never_become_technical_subjects_in_ticket_body():
    from app.agent.workflow.agents import work_architect as R

    state = _msg(
        "DL-9090 아래 성능 측정은 skcc.x1402, 가이드는 skcc.x1450에게 Sub-Task로 맡겨줘"
    )
    items = [{
        "summary": "[Workbench] 데이터 리니지 뷰어 성능 측정",
        "type": "Sub-Task", "parent": "DL-9090", "assignee": "skcc.x1402",
        "description": (
            "<h3>작업 범위</h3><ul><li>포함: X1402 성능 측정 수행</li>"
            "<li>제외: 다른 모델의 성능 측정</li></ul>"
            "<h3>완료 조건 (DoD)</h3><ul data-type=\"taskList\">"
            "<li data-checked=\"false\">X1402 측정 결과 검토</li></ul>"
        ),
    }]

    assert R._remove_assignee_semantic_drift(state, items)
    body = items[0]["description"]
    assert "X1402" not in body and "skcc.x1402" not in body
    assert "다른 모델" not in body
    assert "성능 측정" in body


def test_unrequested_collection_and_storage_do_not_replace_statistics_generation_semantics():
    from app.agent.workflow.agents import work_architect as R

    state = _msg("starrocks puffin ndv 통계정보를 생성하는 파이프라인을 개발해야해")
    items = [{
        "summary": "[ETL] StarRocks Puffin NDV 통계정보 생성 파이프라인 개발",
        "type": "Task", "components": ["ETL"],
        "description": (
            "<h3>배경</h3><p>데이터 수집 및 저장 기능이 필요합니다.</p>"
            "<h3>작업 범위</h3><ul><li>포함: 데이터 수집 로직 개발</li>"
            "<li>포함: 데이터베이스 저장 구조 설계</li></ul>"
            "<h3>완료 조건 (DoD)</h3><ul data-type=\"taskList\">"
            "<li data-checked=\"false\">수집 데이터가 데이터베이스에 저장됨</li></ul>"
        ),
    }]

    assert R._remove_unrequested_quality_claims(state, items)
    body = items[0]["description"]
    assert "데이터 수집" not in body and "데이터베이스" not in body
    assert "통계정보 생성 파이프라인" in body


def test_statistics_generation_is_not_expanded_to_collection_transform_deploy_and_reporting():
    from app.agent.workflow.agents import work_architect as R

    state = _msg("StarRocks Puffin NDV 통계정보를 생성하는 파이프라인을 개발해야해")
    item = {
        "summary": "[ETL] StarRocks Puffin NDV 통계정보 생성 파이프라인 개발",
        "type": "Task", "components": ["ETL"],
        "description": (
            "<h3>배경</h3><p>StarRocks Puffin의 NDV 통계정보를 수집하고 처리한다.</p>"
            "<h3>작업 범위</h3><ul><li>데이터 소스 연결</li><li>데이터 변환</li>"
            "<li>배포 및 운영 환경 구성</li></ul>"
            "<h3>완료 조건 (DoD)</h3><ul data-type=\"taskList\">"
            "<li data-checked=\"false\">데이터 분석 및 보고서 작성</li></ul>"),
    }

    assert R._repair_statistics_generation_semantics(state, [item])
    body = item["description"]
    assert "통계정보를 수집" not in body
    assert "데이터 소스 연결" not in body and "데이터 변환" not in body
    assert "운영 환경 구성" not in body
    assert '<li data-checked="false">데이터 분석 및 보고서 작성' not in body
    assert "제외: 요청에 명시되지 않은 데이터 수집·변환·배포 및 보고서 작성" in body
    assert "생성된 NDV 통계정보" in body and "실행 성공·실패 로그" in body


def test_statistics_generation_drops_unrequested_monitoring_dashboard():
    from app.agent.workflow.agents import work_architect as R

    state = _msg("StarRocks Puffin NDV 통계정보 생성 파이프라인을 개발해줘")
    item = {"summary": "[ETL] StarRocks Puffin NDV 통계 파이프라인 개발", "type": "Task",
            "description": ('<h3>완료 조건 (DoD)</h3><ul data-type="taskList">'
                            '<li data-checked="false">모니터링 대시보드에서 실시간 데이터 흐름 확인</li>'
                            '</ul>')}
    assert R._remove_unrequested_quality_claims(state, [item])
    R._sharpen_dod(state, [item])
    assert "모니터링" not in item["description"] and "대시보드" not in item["description"]
    assert "실행 로그와 회귀 테스트 결과" in item["description"]


def test_generated_stage_children_receive_executable_scope_and_evidence():
    from app.agent.workflow.agents import work_architect as R

    items = [{
        "summary": "[ETL] NDV 통계정보 생성 파이프라인", "type": "Task",
        "children": [
            {"summary": "NDV 생성 구조 설계"},
            {"summary": "NDV 생성 로직 구현"},
            {"summary": "NDV 생성 결과 검증"},
        ],
    }]

    assert R._ensure_child_descriptions(items)
    bodies = [child["description"] for child in items[0]["children"]]
    assert all("작업 범위" in body and "완료 조건" in body for body in bodies)
    assert "산출물 링크와 리뷰 결과" in bodies[0]
    assert "성공·실패 로그와 테스트 결과" in bodies[1]
    assert "기준·측정값·판정 결과" in bodies[2]


def test_ticket_keys_and_assignee_ids_do_not_trigger_topic_drift_warning():
    from app.agent.workflow.agents.work_architect import _topic_drift

    state = _msg("DL-9090 아래 팝업 작업은 x1402에게 만들어줘")
    items = [{"summary": "[Workbench] 도움말 팝업 추가", "type": "Sub-Task",
              "parent": "DL-9090", "assignee": "skcc.x1402",
              "description": "<h3>작업 범위</h3><p>도움말 팝업 추가</p>"}]

    assert _topic_drift(state, items) == ""


def test_action_family_dod_uses_observable_evidence_and_drops_unrequested_report():
    from app.agent.workflow.agents.work_architect import _sharpen_dod

    items = [{"summary": "[Runtime] 쿼리 인덱스 개선", "type": "Task",
              "description": ('<h3>완료 조건 (DoD)</h3><ul data-type="taskList">'
                              '<li data-checked="false">성능 테스트 완료</li>'
                              '<li data-checked="false">결과 보고서 공유</li></ul>')}]

    assert _sharpen_dod(_msg("쿼리 인덱스 개선 Task 만들어줘"), items)
    body = items[0]["description"]
    assert "적용 전·후 실행 계획" in body and "측정값" in body
    assert "결과 보고서 공유" not in body


def test_subtask_dod_uses_artifact_matching_the_action_family():
    from app.agent.workflow.agents.work_architect import _sharpen_dod

    parent = {"summary": "[Workbench] 리니지 뷰어", "type": "Task", "children": [
        {"summary": "[Workbench] 리니지 뷰어 가이드 작성", "type": "Sub-Task",
         "description": ('<ul data-type="taskList">'
                         '<li data-checked="false">내부 리뷰 및 피드백 반영 실행 로그와 테스트 결과를 기록</li>'
                         '</ul>')},
        {"summary": "[Workbench] 리니지 뷰어 회귀 테스트", "type": "Sub-Task",
         "description": ('<ul data-type="taskList">'
                         '<li data-checked="false">회귀 테스트 결과가 기록되고 검토됨</li>'
                         '<li data-checked="false">테스트 결과가 관련 문서에 첨부됨</li>'
                         '</ul>')},
    ]}

    assert _sharpen_dod(_msg("가이드 작성과 회귀 테스트 Sub-Task 만들어줘"), [parent])
    guide, regression = [child["description"] for child in parent["children"]]
    assert "산출물 링크와 리뷰 결과" in guide and "실행 로그" not in guide
    assert "실행 케이스와 실패 로그, 판정 결과" in regression
    assert "관련 문서에 첨부" not in regression


def test_template_dod_uses_link_and_review_instead_of_test_or_measurement_evidence():
    from app.agent.workflow.agents.work_architect import _dedupe_dod_rows, _sharpen_dod

    item = {"summary": "[Catalog] RGP 검증 기준 및 결과 템플릿", "type": "Task",
            "description": ('<ul data-type="taskList">'
                            '<li data-checked="false">내부·외부 근거 분리 실행 로그와 테스트 결과를 기록</li>'
                            '<li data-checked="false">절차와 호환성 기록 검증 기준·측정값·판정 결과를 기록</li>'
                            '</ul>')}
    assert _sharpen_dod(_msg("RGP 검증 기준 및 결과 템플릿을 작성해줘"), [item])
    _dedupe_dod_rows([item])
    body = item["description"]
    assert "산출물 링크와 리뷰 결과" in body
    assert "실행 로그" not in body and "측정값" not in body


def test_null_ratio_dod_records_each_requested_target_instead_of_generic_pipeline_success():
    from app.agent.workflow.agents.work_architect import _sharpen_dod

    item = {"summary": "[ETL] Lake 배치 적재 테이블 널 비율 체크", "type": "Task",
            "description": ('<h3>완료 조건 (DoD)</h3><ul data-type="taskList">'
                            '<li data-checked="false">결과가 반영되었음을 담당 리뷰로 확인한다</li>'
                            '<li data-checked="false">성공·실패 경로를 회귀 테스트로 확인한다</li>'
                            '</ul>')}

    assert _sharpen_dod(_msg("신규 30개 테이블 널 비율 체크 Task"), [item])
    body = item["description"]
    assert "요청한 30개 대상별 null ratio 측정값" in body
    assert "성공·실패 경로" not in body


def test_null_ratio_dod_is_deterministic_across_calculate_review_and_report_wording():
    from app.agent.workflow.agents.work_architect import _dod_rows, _sharpen_dod

    item = {"summary": "[ETL] 신규 등록 테이블 널 비율 체크", "type": "Task",
            "description": ('<h3>완료 조건 (DoD)</h3><ul data-type="taskList">'
                            '<li data-checked="false">신규 테이블의 널 비율을 산출</li>'
                            '<li data-checked="false">산출된 널 비율을 검토 및 보고</li>'
                            '</ul>')}
    assert _sharpen_dod(_msg("신규 등록 30개 테이블의 널 비율만 체크해줘"), [item])
    assert _dod_rows(item["description"]) == [
        "요청한 30개 대상별 null ratio 측정값과 실패·제외 목록을 티켓에 기록해 확인한다"
    ]


def test_literal_numeric_mutation_does_not_gain_an_unverified_business_benefit():
    from app.agent.workflow.agents import work_architect as R

    item = {"summary": "[ETL] 적재 지연 알림 임계값 조정", "type": "Task",
            "description": ("<h3>배경</h3><p>30분에서 45분으로 조정 요청. 이 변경은 "
                            "유연성을 높이고 불필요한 알림을 줄이기 위해 필요합니다.</p>"
                            "<h3>작업 범위</h3><ul><li>포함: 45분 변경</li>"
                            "<li>제외: 다른 알림</li></ul>"
                            "<h3>완료 조건 (DoD)</h3><ul data-type=\"taskList\">"
                            "<li data-checked=\"false\">45분 설정 확인</li></ul>")}

    assert R._remove_unrequested_quality_claims(
        _msg("적재 지연 알림 임계값을 30분에서 45분으로 조정"), [item])
    assert "유연성" not in item["description"] and "불필요한 알림" not in item["description"]


def test_generic_review_record_dod_is_replaced_with_action_specific_evidence():
    from app.agent.workflow.agents import work_architect as R

    state = _msg("카탈로그 화면에 '내 모듈만' 필터를 추가해줘")
    items = [{
        "summary": "[Catalog] 내 모듈만 필터 추가", "type": "Task",
        "description": (
            "<h3>완료 조건 (DoD)</h3><ul data-type=\"taskList\">"
            "<li data-checked=\"false\">필터 추가 결과와 검증 기록이 티켓에 남고 담당 리뷰로 확인됨</li>"
            "</ul>"
        ),
    }]

    assert R._sharpen_dod(state, items)
    body = items[0]["description"]
    assert "결과와 검증 기록" not in body and "담당 리뷰" not in body
    assert "필터" in body and ("화면" in body or "테스트" in body)

def test_boilerplate_closers_are_stripped_by_code_not_asked_for():
    """맺음말 상투구는 **버릇**이라 프롬프트로 안 잡힌다 — 코드가 지운다.

    common.md 가 "맺음말·상투구 금지"를 이미 적어 뒀는데, 사용자 관점 리뷰의 다섯 흐름 중
    넷에 그대로 나왔다. 이 한 줄은 어떤 문맥에서도 '틀리지' 않아서, 모델은 규칙을 읽고도
    예의상 계속 붙인다. **판단이 아니면 코드가 지운다**(이 저장소의 규율).

    ★ 지우는 것은 아무것도 제안하지 않는 되물음뿐이다 — 구체적인 다음 행동을 제안하는 줄은
      정보라서 남는다. 둘을 못 가르면 이 가드는 답을 깎아 먹는다.
    """
    from app.agent.workflow.agents.result_integrator import _drop_boilerplate_closers as f

    for junk in ("변경 경위나 관련 티켓 내용이 더 궁금하면 말씀 주세요.",
                 "추가적인 정보가 필요하면 말씀 주세요.",
                 "남은 일과 리스크에 대한 추가 정보가 필요하면 말씀해 주세요."):
        out = f("적재주기: 30분 1회\n\n" + junk)
        assert junk not in out, junk
        assert "적재주기: 30분 1회" in out          # 내용은 그대로

    # 줄 끝에 붙어 오는 꼴도 문장 단위로 떼어 낸다
    out = f("즉시 DL-9029 부터. 추가적인 세부사항이 더 궁금하면 말씀 주세요.")
    assert "궁금" not in out and "DL-9029" in out

    # 남겨야 하는 것 — 구체적 제안 · 티켓을 가리키는 요청 · 참조 목록
    for keep in ("다음은 성능 측정 티켓을 잡을까요?",
                 "DL-9044 를 확인해 주세요.",
                 "**참조**\n[1] DL-9044 — 적재주기 변경"):
        assert keep.splitlines()[-1] in f("결론 한 줄\n\n" + keep), keep


def test_delegated_creation_retries_questionless_empty_result_once(monkeypatch):
    from langchain_core.messages import HumanMessage
    from app.agent.workflow.agents.base import StructuredAgent

    calls = []
    outputs = [
        {"questions": [], "draft": {"items": []}, "change_plan": {}},
        {"questions": [], "draft": {"items": [
            {"summary": "NDV 배치 Job 구현", "type": "Task"},
        ]}, "change_plan": {}},
    ]

    def fake_node(_self):
        def run(_state):
            calls.append(WorkArchitect._force_draft)
            return outputs.pop(0)
        return run

    monkeypatch.setattr(StructuredAgent, "node", fake_node)
    state = {
        "intent": Intent.PLAN_WORK,
        "messages": [HumanMessage(content="NDV 배치 Job Task 만들어줘. 알아서")],
    }
    result = WorkArchitect().node()(state)

    assert calls == [False, True]
    assert result["draft"]["items"][0]["summary"] == "NDV 배치 Job 구현"


def test_creation_contract_uses_semantic_body_parts_and_runtime_renders_html():
    from app.agent.workflow.agents.work_architect import CREATE_SCHEMA

    item_schema = CREATE_SCHEMA["properties"]["items"]["items"]
    assert "description" not in item_schema["properties"]
    state = _msg("NDV 배치 Job Task와 단계별 Sub-Task 만들어줘. 알아서")
    output = {
        "questions": [], "mode": "task", "structure": "task_with_subtasks",
        "items": [{
            "summary": "[ETL] Puffin NDV 배치 Job 구현", "type": "Task",
            "background": "PoC 구현 요청됨",
            "scope_in": ["Iceberg 배치 테이블에 NDV 통계 생성"],
            "scope_out": ["실시간 적재"],
            "dod": ["Batch Job 실행 성공", "Puffin 통계 파일 확인"],
            "children": [{
                "summary": "NDV writer 구현",
                "scope_in": ["Puffin writer 로직 구현"],
                "dod": ["writer 단위 테스트 통과"],
            }],
        }],
    }
    result = WorkArchitect().apply(state, output)
    item = result["draft"]["items"][0]

    assert "<h3>배경</h3>" in item["description"]
    assert 'data-checked="false"' in item["description"]
    assert "실시간 적재" in item["description"]
    assert "<h3>작업 범위</h3>" in item["children"][0]["description"]
    assert "background" not in item and "scope_in" not in item and "dod" not in item


def test_creation_renderer_drops_scope_contradiction_and_links_ticket_references():
    state = _msg("Puffin NDV Batch Job PoC를 1차 목표로 만들어줘. 알아서")
    state["evidence"] = [{"key": "DL-7001", "title": "Iceberg 통계 조사",
                          "why": "PoC의 선행 조사", "fitness": "direct"}]
    output = {
        "questions": [], "mode": "task", "structure": "single_task",
        "items": [{
            "summary": "[ETL] Puffin NDV Batch Job PoC", "type": "Task",
            "background": "DL-7001 조사 결과를 반영한 PoC 요청",
            "scope_in": ["Puffin NDV Batch Job 구현"],
            "scope_out": ["Puffin NDV Batch Job 구현"],
            "dod": ["Job 실행 성공", "Puffin 통계 파일 확인"],
            "references": ["DL-7001 Iceberg 통계 조사"],
        }],
    }

    result = WorkArchitect().apply(state, output)
    body = result["draft"]["items"][0]["description"]

    assert body.count("Puffin NDV Batch Job 구현") == 1
    assert "운영 배포 및 전체 대상 확대" in body
    assert '<a href="/browse/DL-7001" data-ticket-key="DL-7001">DL-7001</a>' in body
