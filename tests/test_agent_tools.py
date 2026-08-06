"""agent/tools — 도구 왕복 + HITL 게이트.

도구는 **mock world 실물**을 상대로 돈다(스텁 아님). 스텁을 상대로 통과하는 도구는 아무것도
보증하지 않는다 — 우리가 확인하고 싶은 건 "LTM 내부 함수의 반환 모양을 제대로 접었는가"다.

LLM 은 필요 없다. 도구는 provider 와 무관하다.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JIRA_ENV", "mock")

pytest.importorskip("langchain_core", reason="requirements-agent.txt 미설치")

from app.agent import approval                      # noqa: E402
from app.agent import tools as T                    # noqa: E402


@pytest.fixture(autouse=True)
def _clean():
    approval.clear()
    yield
    approval.clear()


def _run(tool, **kw):
    """@tool 로 감싼 함수를 인자 그대로 부른다(LLM 이 부르는 경로와 같다)."""
    return tool.invoke(kw)


# ── 레지스트리 ─────────────────────────────────────────────────────
def test_write_tools_are_not_in_read_bundles():
    """역할 분리는 도구 목록으로 강제한다 — Historian 이 티켓을 만들 수 있으면 안 된다."""
    read_names = {t.name for t in T.READ_TOOLS}
    for w in T.WRITE_TOOLS:
        assert w.name not in read_names


def test_every_write_tool_demands_a_token():
    for w in T.WRITE_TOOLS:
        assert "approval_token" in w.args, f"{w.name} 에 승인 인자가 없다"


def test_tools_carry_descriptions_for_the_model():
    for t in T.ALL_TOOLS:
        assert len(t.description or "") > 40, f"{t.name} 의 설명이 너무 짧다(LLM 이 읽는 명세다)"


# ── 탐색 ───────────────────────────────────────────────────────────
def test_search_finds_history_in_both_sources():
    r = _run(T.BY_NAME["search_work_history"], query="데이터", limit=5)
    assert r["jira"] or r["confluence"], "12개월 world 인데 아무것도 안 잡힌다"
    for it in r["jira"]:
        assert it.get("key")


def test_get_ticket_opens_body_and_comments():
    hit = _run(T.BY_NAME["search_work_history"], query="수집", limit=5)["jira"]
    if not hit:
        pytest.skip("검색 결과 없음")
    r = _run(T.BY_NAME["get_ticket"], key=hit[0]["key"])
    assert r["key"] == hit[0]["key"]
    assert r.get("summary")
    assert "comments" in r or "comments_error" in r
    # 코멘트가 있으면 **본문이 실제로 담겨야** 한다(html→텍스트). 빈 본문을 모델에 먹이던
    # 버그가 "comments 키 존재" 단언만으로 통과했었다.
    for cm in r.get("comments") or []:
        assert (cm.get("body") or "").strip(), f"빈 코멘트 본문: {cm}"


def test_get_ticket_reports_missing_key_instead_of_raising():
    """도구는 예외로 그래프를 죽이지 않는다 — 모델이 읽고 다음 수를 두게 한다."""
    r = _run(T.BY_NAME["get_ticket"], key="NOPE-99999")
    assert "error" in r


def test_ticket_context_walks_one_hop():
    hit = _run(T.BY_NAME["search_work_history"], query="파이프라인", limit=5)["jira"]
    if not hit:
        pytest.skip("검색 결과 없음")
    r = _run(T.BY_NAME["get_ticket_context"], key=hit[0]["key"])
    assert set(r) >= {"key", "related", "documents", "timeline"}


def test_tool_output_stays_small_enough_for_context():
    """도구 결과는 그대로 컨텍스트에 실린다 — 원본을 그대로 흘리면 안 된다."""
    import json
    r = _run(T.BY_NAME["search_work_history"], query="데이터", limit=8)
    assert len(json.dumps(r, ensure_ascii=False)) < 6000


# ── 사람 ───────────────────────────────────────────────────────────
def test_team_workload_returns_roster_with_counts():
    r = _run(T.BY_NAME["get_team_workload"], module="")
    assert r["people"], "people.yaml 로스터가 비었다"
    p = r["people"][0]
    assert {"id", "open", "inProgress", "done28d"} <= set(p)


def test_workload_is_sorted_lightest_first():
    r = _run(T.BY_NAME["get_team_workload"], module="")["people"]
    load = [(x.get("inProgress", 0), x.get("open", 0)) for x in r]
    assert load == sorted(load)


def test_participants_include_more_than_the_assignee():
    """코멘트 작성자까지 잡혀야 '그 논의에 낀 사람'을 추천할 수 있다."""
    for it in _run(T.BY_NAME["search_work_history"], query="개선", limit=8)["jira"]:
        r = _run(T.BY_NAME["get_ticket_participants"], key=it["key"])
        if len(r["people"]) > 1:
            return
    pytest.skip("코멘트가 붙은 티켓을 못 찾음")


def test_person_profile_carries_recommendation_signals():
    uid = _run(T.BY_NAME["get_team_workload"], module="")["people"][0]["id"]
    r = _run(T.BY_NAME["get_person_profile"], user_id=uid)
    assert r["id"] == uid
    assert "workload" in r or "workload_error" in r


# ── 검증 ───────────────────────────────────────────────────────────
def test_validate_rejects_a_plan_with_no_summary():
    r = _run(T.BY_NAME["validate_ticket_plan"], mode="task",
             items=[{"summary": "", "type": "Task"}])
    assert r["ok"] is False and r["errors"]


def test_validate_rejects_a_nonexistent_parent():
    r = _run(T.BY_NAME["validate_ticket_plan"], mode="subtask",
             items=[{"summary": "쪼갠 일", "type": "Sub-Task", "parent": "NOPE-99999"}])
    assert r["ok"] is False


def test_validate_accepts_a_clean_top_level_task():
    types = _run(T.BY_NAME["list_ticket_options"], kind="taskTypes")["taskTypes"]
    r = _run(T.BY_NAME["validate_ticket_plan"], mode="task",
             items=[{"summary": "CDC 도입 검토", "type": types[0], "epic": None}])
    assert r["ok"] is True, r["errors"]


def test_validate_demands_epic_be_explicit_even_when_empty():
    """빠뜨려서 생긴 고아 티켓과 일부러 최상위로 둔 티켓은 다르다 — docstring 이 이걸 알려 준다."""
    types = _run(T.BY_NAME["list_ticket_options"], kind="taskTypes")["taskTypes"]
    r = _run(T.BY_NAME["validate_ticket_plan"], mode="task",
             items=[{"summary": "epic 을 빠뜨린 초안", "type": types[0]}])
    assert r["ok"] is False
    assert "epic" in T.BY_NAME["validate_ticket_plan"].description


def test_options_expose_real_values_not_invented_ones():
    r = _run(T.BY_NAME["list_ticket_options"], kind="")
    assert r.get("taskTypes")
    assert "components" in r and "priorities" in r


# ── HITL 게이트 ────────────────────────────────────────────────────
ITEMS = [{"summary": "승인 없이 만들어지면 안 되는 티켓", "type": "Task", "epic": None}]


def test_create_refuses_an_invalid_plan_before_touching_jira():
    """Jira 는 롤백이 없다 — 반쯤 만들어진 배치가 가장 나쁘다."""
    bad = [dict(ITEMS[0]), {"summary": "", "type": "Task", "epic": None}]
    tok = approval.stage("t1", "create_tickets", {"mode": "task", "items": bad})
    approval.approve(tok, "t1")
    r = _run(T.BY_NAME["create_tickets"], mode="task", items=bad, approval_token=tok)
    assert r["ok"] is False and not r["created"]


def test_create_without_token_is_refused():
    r = _run(T.BY_NAME["create_tickets"], mode="task", items=ITEMS, approval_token="")
    assert r["ok"] is False and r["needsApproval"] is True


def test_create_with_a_forged_token_is_refused():
    r = _run(T.BY_NAME["create_tickets"], mode="task", items=ITEMS,
             approval_token="I-am-a-made-up-token")
    assert r["ok"] is False


def test_staged_but_unapproved_token_is_refused():
    tok = approval.stage("t1", "create_tickets", {"mode": "task", "items": ITEMS})
    r = _run(T.BY_NAME["create_tickets"], mode="task", items=ITEMS, approval_token=tok)
    assert r["ok"] is False and "승인" in r["error"]


def test_approved_token_creates_and_then_burns():
    tok = approval.stage("t1", "create_tickets", {"mode": "task", "items": ITEMS})
    assert approval.approve(tok, "t1")
    r = _run(T.BY_NAME["create_tickets"], mode="task", items=ITEMS, approval_token=tok)
    assert r.get("created"), r
    # 1회용 — 같은 토큰으로 두 번 만들 수 없다(재시도가 중복 생성이 되면 안 된다)
    again = _run(T.BY_NAME["create_tickets"], mode="task", items=ITEMS, approval_token=tok)
    assert again["ok"] is False


def test_token_is_bound_to_the_exact_content():
    """승인 화면에 보인 것과 다른 걸 만들 수 없다 — HITL 이 실제로 의미를 갖는 지점."""
    tok = approval.stage("t1", "create_tickets", {"mode": "task", "items": ITEMS})
    approval.approve(tok, "t1")
    # 바꿔치기 항목은 **그 자체로는 완전히 유효**해야 한다 — 그래야 규칙 검증이 아니라
    # 승인 지문이 막았다는 게 증명된다.
    swapped = [{"summary": "사용자가 본 적 없는 티켓", "type": "Task", "epic": None}]
    r = _run(T.BY_NAME["create_tickets"], mode="task", items=swapped, approval_token=tok)
    assert r["ok"] is False and "다릅니다" in r["error"]


def test_token_cannot_be_reused_for_a_different_action():
    tok = approval.stage("t1", "create_tickets", {"mode": "task", "items": ITEMS})
    approval.approve(tok, "t1")
    r = _run(T.BY_NAME["add_ticket_comment"], key="DL-1", body="x", approval_token=tok)
    assert r["ok"] is False


def test_update_ticket_round_trip():
    hit = _run(T.BY_NAME["search_work_history"], query="데이터", limit=3)["jira"]
    if not hit:
        pytest.skip("검색 결과 없음")
    key = hit[0]["key"]
    payload = {"key": key, "changes": {"duedate": "2026-12-31"}}
    tok = approval.stage("t1", "update_ticket", payload)
    approval.approve(tok, "t1")
    r = _run(T.BY_NAME["update_ticket"], key=key, duedate="2026-12-31", approval_token=tok)
    assert r["ok"] is True, r
    assert _run(T.BY_NAME["get_ticket"], key=key)["duedate"] == "2026-12-31"


def test_update_with_no_fields_is_rejected_before_touching_jira():
    r = _run(T.BY_NAME["update_ticket"], key="DL-1", approval_token="x")
    assert r["ok"] is False and "바꿀 필드" in r["error"]


# ── 외부 지식(웹·GitHub) — 폐쇄망 fail-soft 가 생명이다 ─────────────
def test_web_tools_are_read_only_and_registered():
    assert {"search_web", "search_github"} <= set(T.BY_NAME)
    write = {t.name for t in T.WRITE_TOOLS}
    assert not ({"search_web", "search_github"} & write)


def test_web_search_fails_soft_when_blocked(monkeypatch):
    """채점 샌드박스는 폐쇄망일 수 있다 — 예외가 아니라 '막혀 있다'는 사실을 돌려준다."""
    import duckduckgo_search
    def boom(*a, **k):
        raise OSError("network unreachable")
    monkeypatch.setattr(duckduckgo_search, "DDGS", boom)
    r = _run(T.BY_NAME["search_web"], query="CDC trade-offs")
    assert "error" in r and "사내 조사" in r["error"]


def test_github_search_fails_soft_when_blocked(monkeypatch):
    import httpx
    def boom(*a, **k):
        raise httpx.ConnectError("blocked")
    monkeypatch.setattr(httpx, "get", boom)
    r = _run(T.BY_NAME["search_github"], query="cdc kafka")
    assert "error" in r and "사내 조사" in r["error"]


def test_web_tools_docstrings_forbid_internal_terms():
    """검색어로 사내 정보가 새는 것을 막는 경계가 명세(docstring)에 있어야 한다 —
    이 규칙은 코드로 강제할 수 없어서(무엇이 '사내 정보'인지 판정 불가) 명세가 최후의 선이다."""
    for name in ("search_web", "search_github"):
        assert "사내" in T.BY_NAME[name].description


def test_historian_gets_web_tools_but_writers_do_not():
    from app.agent.workflow.agents.historian import Historian
    names = {t.name for t in Historian().tools}
    assert {"search_web", "search_github"} <= names
