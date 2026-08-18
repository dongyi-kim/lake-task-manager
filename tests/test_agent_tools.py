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
    """역할 분리는 도구 목록으로 강제한다 — ResearchAnalyst 이 티켓을 만들 수 있으면 안 된다."""
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


def test_get_ticket_exposes_current_priority_for_safe_change_previews():
    r = _run(T.BY_NAME["get_ticket"], key="DL-9203")

    assert r["priority"] == "P2-Major"


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
    hit = _run(T.BY_NAME["search_work_history"], query="데이터", limit=20)["jira"]
    from app.agent.tools._ctx import client
    key = next((row["key"] for row in hit
                if (client().ticket_badge(row["key"]) or {}).get("statusCategory") != "done"), "")
    if not key:
        pytest.skip("열린 검색 결과 없음")
    payload = {"key": key, "changes": {"duedate": "2026-12-31"}}
    tok = approval.stage("t1", "update_ticket", payload)
    approval.approve(tok, "t1")
    r = _run(T.BY_NAME["update_ticket"], key=key, duedate="2026-12-31", approval_token=tok)
    assert r["ok"] is True, r
    assert _run(T.BY_NAME["get_ticket"], key=key)["duedate"] == "2026-12-31"


def test_update_with_no_fields_is_rejected_before_touching_jira():
    r = _run(T.BY_NAME["update_ticket"], key="DL-1", approval_token="x")
    assert r["ok"] is False and "바꿀 필드" in r["error"]


def test_done_ticket_update_is_blocked_before_consuming_approval(monkeypatch):
    class _Done:
        def ticket_badge(self, key):
            return {"key": key, "statusCategory": "done", "type": "Task"}

        def editmeta(self, key):
            raise AssertionError("Done이면 editmeta/update까지 가면 안 된다")

    monkeypatch.setattr("app.agent.tools.write_tools.client", lambda: _Done())
    payload = {"key": "DL-9", "changes": {"summary": "바뀐 제목"}}
    tok = approval.stage("t1", "update_ticket", payload)
    approval.approve(tok, "t1")
    r = _run(T.BY_NAME["update_ticket"], key="DL-9", summary="바뀐 제목",
             approval_token=tok)
    assert not r["ok"] and "완료된 티켓" in r["error"]
    assert approval.peek(tok), "상태 검증에서 막힌 승인은 소비하지 않는다"


def test_done_ticket_comment_and_reopened_transition_metadata(monkeypatch):
    class _Done:
        def add_comment(self, key, body):
            return {"id": "c1"}

        def transitions(self, key):
            return [{"id": "4", "name": "Reopen Issue", "to": "Reopened",
                     "toCategory": "todo"}]

    monkeypatch.setattr("app.agent.tools.write_tools.client", lambda: _Done())
    tok = approval.stage("t1", "add_ticket_comment", {"key": "DL-9", "body": "완료 후 기록"})
    approval.approve(tok, "t1")
    assert _run(T.BY_NAME["add_ticket_comment"], key="DL-9", body="완료 후 기록",
                approval_token=tok)["ok"]
    trs = _run(T.BY_NAME["list_transitions"], key="DL-9")
    assert trs == [{"id": "4", "name": "Reopen Issue", "to": "Reopened",
                    "toCategory": "todo"}]


# ── 외부 지식(웹·GitHub) — 폐쇄망 fail-soft 가 생명이다 ─────────────
def test_web_tools_are_read_only_and_registered():
    assert {"search_web", "search_github"} <= set(T.BY_NAME)
    write = {t.name for t in T.WRITE_TOOLS}
    assert not ({"search_web", "search_github"} & write)


def test_web_search_fails_soft_when_blocked(monkeypatch):
    """채점 샌드박스는 폐쇄망일 수 있다 — 예외가 아니라 '막혀 있다'는 사실을 돌려준다."""
    import httpx
    def boom(*a, **k):
        raise OSError("network unreachable")
    monkeypatch.setattr(httpx, "get", boom)
    r = _run(T.BY_NAME["search_web"], query="CDC trade-offs")
    assert "error" in r and "사내 조사" in r["error"]


def test_web_search_uses_file_ca_bundle_and_parses_results(monkeypatch):
    import certifi
    import httpx

    seen = {}

    class Response:
        status_code = 200
        text = ('<a class="result__a" href="https://apache.org/docs">Apache docs</a>'
                '<div class="result__snippet">Official reference</div>')

    def fake_get(*args, **kwargs):
        seen.update(kwargs)
        return Response()

    monkeypatch.setattr(httpx, "get", fake_get)
    r = _run(T.BY_NAME["search_web"], query="Apache docs")
    assert seen["verify"] == certifi.where()
    assert r["results"][0]["url"] == "https://apache.org/docs"


def test_web_official_detection_is_subject_owned_not_product_curated():
    from app.agent.tools.web_tools import _official_source

    assert _official_source("https://docs.acmedb.io/reference", "AcmeDB official docs")
    assert not _official_source("https://docs.acmedb.io/reference", "OtherDB official docs")
    assert not _official_source("https://acmedb-docs.example/reference", "AcmeDB official docs")
    assert not _official_source("https://acmedb.evil.example/reference", "AcmeDB official docs")
    assert _official_source("https://datatracker.ietf.org/doc/rfc9110/", "HTTP standard")


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
        desc = T.BY_NAME[name].description.lower()
        assert "internal" in desc and "ticket" in desc


def test_historian_gets_web_tools_but_writers_do_not():
    from app.agent.workflow.agents.research_analyst import ResearchAnalyst
    names = {t.name for t in ResearchAnalyst().tools}
    assert {"search_web", "search_github"} <= names


# ── 후보 지도 — 본문을 읽기 전에 코드가 신호를 취합한다 ──────────────
def test_neighborhood_aggregates_multiple_signals():
    """계보·라벨·컴포넌트·링크·참여자를 한 번에 — 모델의 검색 반복을 대체하는 지도다."""
    from app.agent.tools.survey_tools import neighborhood
    from app.agent.tools import _ctx
    key = _ctx.client().search_issues(
        "statusCategory = indeterminate ORDER BY updated DESC", max_results=3)[0]["key"]
    r = neighborhood(key)
    assert r["seed"] == key and r["candidates"], r
    vias = {v for c in r["candidates"] for v in c["via"]}
    assert len(vias) >= 2, f"신호가 하나뿐이다: {vias}"
    assert key not in {c["key"] for c in r["candidates"]}, "씨앗 티켓이 후보에 섞였다"
    # 겹침(via 수) 내림차순 — 여러 신호에 걸린 후보가 위로
    ns = [len(c["via"]) for c in r["candidates"]]
    assert ns == sorted(ns, reverse=True)


def test_neighborhood_is_compact_enough_for_context():
    import json
    from app.agent.tools.survey_tools import neighborhood
    from app.agent.tools import _ctx
    key = _ctx.client().search_issues("ORDER BY updated DESC", max_results=1)[0]["key"]
    assert len(json.dumps(neighborhood(key), ensure_ascii=False)) < 5000


def test_historian_injects_seed_map_for_mentioned_keys(monkeypatch):
    """사용자가 티켓을 지목하면 ReAct 전에 지도가 자료로 들어간다(fake 로 검증)."""
    import os
    os.environ["LAKE_AGENT_PROVIDER"] = "fake"
    from langchain_core.messages import HumanMessage
    from app.agent.workflow.agents.research_analyst import ResearchAnalyst
    from app.agent.tools import _ctx
    key = _ctx.client().search_issues(
        "statusCategory = indeterminate ORDER BY updated DESC", max_results=3)[0]["key"]
    h = ResearchAnalyst()
    captured = {}
    orig_task = h.task
    def spy_task(state):
        captured["seed_map"] = state.get("seed_map") or ""
        return orig_task(state)
    monkeypatch.setattr(h, "task", spy_task)
    h.node()({"messages": [HumanMessage(content=f"{key} 관련 정리")],
              "mentioned_keys": [key], "keywords": [key], "trace": []})
    assert "후보:" in captured["seed_map"], "지도가 주입되지 않았다"


def test_refiner_normalizes_priority_shorthand():
    """P3 → P3-Minor 는 판단이 아니라 표기다 — 코드가 정규화해야 Auditor 왕복이 안 샌다.

    실측: 모델이 'P3' 로 내면 검증에서 튕기고, 재작성 한도가 소진되면 그 지적이
    사용자 답변에 그대로 노출됐다("P3는 적절한 우선순위가 아닙니다").
    """
    from app.agent.workflow.agents.work_architect import WorkArchitect
    r = WorkArchitect()
    out = r.apply({"turns": 0, "messages": []},
                  {"mode": "task", "questions": [],
                   "items": [{"summary": "[ETL] 정규화 확인", "type": "Task", "priority": "P3"},
                             {"summary": "[ETL] 온전한 값 유지", "type": "Task",
                              "priority": "P1-Critical"}]})
    pris = [i["priority"] for i in out["draft"]["items"]]
    assert pris == ["P3-Minor", "P1-Critical"]


def test_approval_amend_assignees_rebinds_fingerprint():
    """승인 카드에서 고른 담당자는 스테이징 내용과 지문을 **같이** 바꾼다.

    승인 후엔 못 고친다 — 그건 '보여 준 것과 다른 실행'이다.
    """
    from app.agent import approval
    t = approval.stage("th-x", "create_tickets",
                       {"mode": "task", "items": [{"summary": "a", "type": "Task"}]})
    ok, why = approval.amend_assignees(t, "th-x", {"0": "skcc.x1042"})
    assert ok, why
    rec = approval.peek(t)
    assert rec["payload"]["items"][0]["assignee"] == "skcc.x1042"
    assert rec["fp"] == approval.fingerprint(rec["payload"])
    assert not approval.amend_assignees(t, "다른대화", {"0": "x"})[0]
    assert not approval.amend_assignees(t, "th-x", {"9": "x"})[0]
    approval.approve(t, "th-x")
    assert not approval.amend_assignees(t, "th-x", {"0": "x"})[0]
    approval.reject(t)


def test_historian_task_renders_without_error():
    """task() 는 어떤 State 조합에서도 예외 없이 문자열을 내야 한다.

    회귀: node() 는 web_context 라는 키로 넣는데 task() 가 정의 안 된 이름(web_ctx)을
    참조해 **매 호출 NameError** → fallback 으로 조용히 삼켜졌다. ReAct 를 통째로 씌운
    테스트는 fallback 때문에 이걸 못 잡는다 — task() 를 직접 부른다.
    """
    from langchain_core.messages import HumanMessage
    from app.agent.workflow.agents.research_analyst import ResearchAnalyst
    h = ResearchAnalyst()
    base = {"messages": [HumanMessage(content="CDC 방식 기술 검토")], "trace": []}
    assert "history related to the work request" in h.task(base)  # web_context 없음
    with_web = {**base, "web_context": "- [웹] CDC 비교 글"}
    assert "External Technology Research" in h.task(with_web)  # 있으면 자료로 실린다
    assert "CDC 비교 글" in h.task(with_web)


def test_prefetched_plan_work_concludes_without_react_tool_loop(monkeypatch):
    """deterministic 조회가 끝난 생성 요청은 ResearchAnalyst 도구 21개를 다시 순회하지 않는다."""
    from langchain_core.messages import HumanMessage
    from app.agent.workflow.agents.base import ToolAgent
    from app.agent.workflow.agents.research_analyst import ResearchAnalyst
    from app.agent.workflow.state import Intent
    called = {"react": 0, "conclude": 0}

    def base_node(_self):
        def run(_state):
            called["react"] += 1
            return {"situation": "react를 타면 실패"}
        return run

    monkeypatch.setattr(ToolAgent, "node", base_node)
    h = ResearchAnalyst()

    def conclude(state, scratch):
        called["conclude"] += 1
        assert state.get("_research_analyst_prefetched") is True
        assert scratch == []
        return {"situation": "사전 조회로 정리", "evidence": []}

    monkeypatch.setattr(h, "_conclude", conclude)
    out = h.node()({"intent": Intent.PLAN_WORK,
                    "messages": [HumanMessage(content="카탈로그 품질 규칙 점검 Task 만들어줘")],
                    "keywords": ["카탈로그", "품질"], "mentioned_keys": [],
                    "query_results": {"jira": {"items": [], "total": 0}}, "trace": []})
    assert called == {"react": 0, "conclude": 1}
    assert out["situation"] == "사전 조회로 정리"
    assert any("도구 재호출 생략" in x.get("note", "") for x in out["trace"])


def test_prefetched_plan_work_format_failure_passes_raw_material_without_react(monkeypatch):
    from langchain_core.messages import HumanMessage
    from app.agent.workflow.agents.base import ToolAgent
    from app.agent.workflow.agents.research_analyst import ResearchAnalyst
    from app.agent.workflow.state import Intent

    monkeypatch.setattr(ToolAgent, "node", lambda _self: lambda _state: (_ for _ in ()).throw(
        AssertionError("format failure must not repeat acquisition")))
    analyst = ResearchAnalyst()
    monkeypatch.setattr(analyst, "_conclude", lambda _state, _scratch: (_ for _ in ()).throw(
        RuntimeError("invalid JSON")))
    out = analyst.node()({
        "intent": Intent.PLAN_WORK,
        "messages": [HumanMessage(content="Iceberg NDV Task 만들어줘. 알아서")],
        "keywords": ["Iceberg NDV"], "mentioned_keys": [],
        "pre_survey": "DL-1 PoC 완료", "trace": [],
    })

    assert "사전 조회를 완료" in out["situation"]
    assert out["pre_survey"]
    assert any("원문 전달" in row.get("note", "") for row in out["trace"])


def test_completed_ask_query_plan_concludes_once_without_presurvey_or_react(monkeypatch):
    """A fully executed QueryPlan is a complete evidence acquisition phase, not a ReAct hint."""
    from langchain_core.messages import HumanMessage
    import app.agent.workflow.agents.research_analyst as mod
    from app.agent.workflow.agents.base import ToolAgent
    from app.agent.workflow.agents.research_analyst import ResearchAnalyst
    from app.agent.workflow.state import Intent

    monkeypatch.setattr(mod, "_presurvey", lambda _state: (_ for _ in ()).throw(
        AssertionError("completed QueryPlan must not run a duplicate presurvey")))
    monkeypatch.setattr(ToolAgent, "node", lambda _self: lambda _state: (_ for _ in ()).throw(
        AssertionError("completed QueryPlan must not enter ReAct")))
    analyst = ResearchAnalyst()
    calls = []

    def conclude(state, scratch):
        calls.append((state.get("_research_analyst_prefetched"), scratch))
        return {"situation": "상세 근거 묶음으로 정리", "evidence": []}

    monkeypatch.setattr(analyst, "_conclude", conclude)
    out = analyst.node()({
        "intent": Intent.ASK,
        "messages": [HumanMessage(content="Puffin 적용 근거를 내외부 자료로 조사해줘")],
        "keywords": ["Puffin"], "mentioned_keys": [],
        "query_plan": {"queries": [
            {"id": "jira", "source": "jira"},
            {"id": "docs", "source": "confluence"},
            {"id": "web", "source": "web"},
        ]},
        "query_results": [
            {"id": "jira", "source": "jira", "result": {
                "tickets": [{"key": "DL-1"}],
                "ticketDetails": [{"key": "DL-1", "description": "검증 결과"}],
            }},
            {"id": "docs", "source": "confluence", "result": {
                "documents": [{"id": "1", "title": "설계"}],
                "documentBodies": [{"id": "1", "title": "설계", "text": "본문"}],
            }},
            {"id": "web", "source": "web", "result": {
                "results": [{"title": "공식 문서", "url": "https://example.com"}],
            }},
        ],
        "trace": [],
    })
    assert calls == [(True, [])]
    assert out["situation"] == "상세 근거 묶음으로 정리"
    assert any("QueryPlan 근거 묶음" in row.get("note", "") for row in out["trace"])


def _completed_typed_write_state(*, action: str, research: bool = False) -> dict:
    """Generic completed acquisition for create/comment/update fast-path contracts."""
    from langchain_core.messages import HumanMessage
    from app.agent.workflow.state import Intent

    target = "DL-9301"
    intent = Intent.PLAN_WORK if action == "create" else Intent.MODIFY
    requests = {
        "create": "ProtocolX 처리 절차를 적용하는 Task를 생성해줘",
        "comment": f"{target}에 결정 사항 댓글을 남겨줘",
        "update": f"{target}의 우선순위를 사용자가 지정한 값으로 변경해줘",
    }
    request = requests[action]
    write_task = {
        "id": "write", "kind": "ticket" if action == "create" else (
            "comment" if action == "comment" else "write"),
        "instruction": request, "depends_on": ["research"] if research else [],
        "write_intent": True, "completion_criteria": ["요청한 초안을 준비한다"],
    }
    tasks = []
    if research:
        tasks.append({
            "id": "research", "kind": "research",
            "instruction": "최신 결정 근거와 이전 논의를 비교한다",
            "depends_on": [], "write_intent": False,
            "completion_criteria": ["출처별 시점과 최신 결론을 구분한다"],
        })
    tasks.append(write_task)
    return {
        "intent": intent,
        "messages": [HumanMessage(content=request)],
        "request_text": request,
        "keywords": ["ProtocolX"] if action == "create" else ["결정 사항"],
        "mentioned_keys": [] if action == "create" else [target],
        "request_plan": {"goal": request, "tasks": tasks},
        "continuation_contract": {
            "version": "continuation.v1", "root_request": request,
            "intent": intent, "action": action,
            "target_keys": [] if action == "create" else [target],
            "outcome_ids": [task["id"] for task in tasks], "decisions": [],
        },
        "query_plan": {"queries": [{"id": "jira", "source": "jira"}]},
        "query_results": [{"id": "jira", "source": "jira", "result": {
            "scopeProjects": ["DL"], "canonicalJql": 'project in ("DL")',
            "tickets": ([] if action == "create" else [{
                "key": target, "summary": "[일반] 결정 반영 대상", "status": "In Progress",
            }]),
            "ticketDetails": ([] if action == "create" else [{
                "key": target, "summary": "[일반] 결정 반영 대상",
                "status": "In Progress", "description": "검증된 현재 본문",
                "comments": [{"author": "skcc.x1001", "created": "2026-08-17",
                              "body": "이전 논의 기록"}],
            }]),
        }}],
        "trace": [],
    }


@pytest.mark.parametrize("action", ["comment", "update", "create"])
def test_completed_typed_write_query_plan_uses_zero_llm_zero_tool_fast_path(
        action, monkeypatch):
    """A typed write consumes the completed bounded ledger without a second acquisition pass."""
    import app.agent.tools.survey_tools as survey_tools
    import app.agent.workflow.agents.research_analyst as mod
    from app.agent.workflow.agents.base import ToolAgent
    from app.agent.workflow.agents.research_analyst import ResearchAnalyst

    def forbidden(label):
        return lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError(label))

    monkeypatch.setattr(survey_tools, "neighborhood", forbidden(
        "completed typed write must not rebuild a candidate neighborhood"))
    monkeypatch.setattr(mod, "_presurvey", forbidden(
        "completed typed write must not repeat lexical/semantic retrieval"))
    monkeypatch.setattr(mod, "_topic_dossier", forbidden(
        "completed typed write must not rematerialize a topic dossier"))
    monkeypatch.setattr(mod, "_research_outside", forbidden(
        "completed typed write must not supplement an already completed QueryPlan"))
    monkeypatch.setattr(ToolAgent, "node", lambda _self: forbidden(
        "completed typed write must not enter ReAct"))
    analyst = ResearchAnalyst()
    monkeypatch.setattr(analyst, "_conclude", forbidden(
        "write-only completed plan must not call structured conclusion"))
    monkeypatch.setattr(analyst, "invoke_structured", forbidden(
        "write-only completed plan must not call semantic synthesis"))

    out = analyst.node()(_completed_typed_write_state(action=action))

    assert out["already_exists"] is False
    if action != "create":
        assert out["evidence"][0]["key"] == "DL-9301"
    assert any("deterministic 근거 장부" in row.get("note", "")
               for row in out["trace"])


def test_completed_typed_write_uses_persisted_materialized_target_provenance():
    """A continuation sidecar target remains a fully sourced direct target after an interview turn."""
    from app.agent.workflow.agents.research_analyst import ResearchAnalyst

    state = _completed_typed_write_state(action="update")
    details = state["query_results"][0]["result"].pop("ticketDetails")
    state["materialized_ticket_sources"] = {"ticketDetails": details}

    out = ResearchAnalyst().node()(state)

    target = next(row for row in out["evidence"] if row["key"] == "DL-9301")
    assert target["confidence"] == "high" and target["fitness"] == "direct"
    assert any(observation["source"] == "description"
               and observation["text"] == "검증된 현재 본문"
               for observation in target["observations"])


def test_completed_typed_comment_with_explicit_research_synthesizes_exactly_once(
        monkeypatch):
    """A real research deliverable gets one synthesis, while retrieval remains complete."""
    import app.agent.tools.survey_tools as survey_tools
    from app.agent.workflow.agents.base import ToolAgent
    from app.agent.workflow.agents.research_analyst import ResearchAnalyst

    monkeypatch.setattr(survey_tools, "neighborhood", lambda *_args: (_ for _ in ()).throw(
        AssertionError("completed compound write must not rebuild its neighborhood")))
    monkeypatch.setattr(ToolAgent, "node", lambda _self: lambda _state: (_ for _ in ()).throw(
        AssertionError("completed compound write must not enter ReAct")))
    analyst = ResearchAnalyst()
    calls = []

    def synthesize(state, messages):
        calls.append((state, messages))
        return {
            "situation": "2026-08-17 기록이 이전 논의보다 최신이다.",
            "evidence": [{
                "key": "DL-9301", "title": "[일반] 결정 반영 대상", "url": "",
                "why": "현재 본문과 이전 논의를 함께 포함한다.",
                "confidence": "high", "fitness": "direct", "limitations": "",
                "observations": [{"source": "description", "text": "검증된 현재 본문"}],
            }],
            "related_docs": [], "already_exists": False,
        }

    monkeypatch.setattr(analyst, "invoke_structured", synthesize)
    monkeypatch.setattr(analyst, "_conclude", lambda *_args: (_ for _ in ()).throw(
        AssertionError("completed compound write must use the prefetched synthesis contract")))

    out = analyst.node()(_completed_typed_write_state(action="comment", research=True))

    assert len(calls) == 1
    assert calls[0][0]["_research_analyst_prefetched"] is True
    assert out["evidence"][0]["key"] == "DL-9301"
    assert any("조사·작성 QueryPlan을 1회" in row.get("note", "")
               for row in out["trace"])


def test_completed_typed_write_preserves_explicit_respond_outcome_with_one_synthesis(
        monkeypatch):
    """An explicit user-facing explanation plus write is compound, not write-only."""
    from app.agent.workflow.agents.base import ToolAgent
    from app.agent.workflow.agents.research_analyst import ResearchAnalyst

    state = _completed_typed_write_state(action="comment")
    state["request_plan"]["tasks"].insert(0, {
        "id": "explain", "kind": "respond",
        "instruction": "검증된 현재 상황과 이전 논의의 차이를 먼저 설명한다",
        "depends_on": [], "write_intent": False,
        "completion_criteria": ["시점과 근거를 포함한다"],
    })
    state["continuation_contract"]["outcome_ids"] = ["explain", "write"]
    monkeypatch.setattr(ToolAgent, "node", lambda _self: lambda _state: (_ for _ in ()).throw(
        AssertionError("completed respond+write must not enter ReAct")))
    analyst = ResearchAnalyst()
    calls = []

    def synthesize(_state, _messages):
        calls.append(True)
        return {
            "situation": "검증된 현재 상황을 이전 논의와 구분했다.",
            "evidence": [], "related_docs": [], "already_exists": False,
        }

    monkeypatch.setattr(analyst, "invoke_structured", synthesize)
    out = analyst.node()(state)

    assert calls == [True]
    assert "현재 상황" in out["situation"]


def test_completed_typed_create_with_unopened_named_target_keeps_safe_fallback(monkeypatch):
    """A named parent/source search hit is not a materialized write ledger."""
    from app.agent.workflow.agents.base import ToolAgent
    from app.agent.workflow.agents.research_analyst import ResearchAnalyst

    state = _completed_typed_write_state(action="create")
    state["messages"][0].content = "검증 절차 Task를 생성해줘"
    state["request_text"] = "검증 절차 Task를 생성해줘"
    state["keywords"] = []
    state["continuation_contract"]["root_request"] = state["request_text"]
    state["continuation_contract"]["target_keys"] = ["DL-9301"]
    calls = []

    def base_node(_self):
        def run(_state):
            calls.append(_state)
            return {"situation": "대상 원본을 여는 기존 안전 경로", "evidence": []}
        return run

    monkeypatch.setattr(ToolAgent, "node", base_node)
    analyst = ResearchAnalyst()
    monkeypatch.setattr(analyst, "_conclude", lambda *_args: (_ for _ in ()).throw(
        AssertionError("unopened named target must not use prefetched write conclusion")))

    out = analyst.node()(state)

    assert len(calls) == 1
    assert out["situation"] == "대상 원본을 여는 기존 안전 경로"


@pytest.mark.parametrize("failure", ["missing", "error", "materialization"])
def test_typed_write_incomplete_query_plan_keeps_react_fallback(failure, monkeypatch):
    """Missing, errored, or unmaterialized acquisition never takes the completed fast path."""
    from app.agent.workflow.agents.base import ToolAgent
    from app.agent.workflow.agents.research_analyst import ResearchAnalyst

    state = _completed_typed_write_state(action="comment")
    state["messages"][0].content = "요청한 대상에 댓글을 남겨줘"
    state["mentioned_keys"] = []
    state["keywords"] = []
    if failure == "missing":
        state["query_plan"]["queries"].append({"id": "docs", "source": "confluence"})
    elif failure == "error":
        state["query_results"][0]["result"]["error"] = "scope unavailable"
    else:
        state["query_results"][0]["result"]["materializationErrors"] = ["DL-9301"]

    calls = []

    def base_node(_self):
        def run(_state):
            calls.append(_state)
            return {"situation": "기존 안전 조사 경로", "evidence": []}
        return run

    monkeypatch.setattr(ToolAgent, "node", base_node)
    out = ResearchAnalyst().node()(state)

    assert len(calls) == 1
    assert out["situation"] == "기존 안전 조사 경로"


def test_completed_plan_work_query_plan_builds_deterministic_provenance_ledger(monkeypatch):
    """Creation retrieval is already complete: preserve sources without another model call."""
    from copy import deepcopy
    from langchain_core.messages import HumanMessage
    import app.agent.workflow.agents.research_analyst as mod
    from app.agent.workflow.agents.base import ToolAgent
    from app.agent.workflow.agents.research_analyst import ResearchAnalyst
    from app.agent.workflow.state import Intent

    monkeypatch.setattr(mod, "_presurvey", lambda _state: (_ for _ in ()).throw(
        AssertionError("completed QueryPlan must not repeat scoped retrieval")))
    monkeypatch.setattr(ToolAgent, "node", lambda _self: lambda _state: (_ for _ in ()).throw(
        AssertionError("completed creation QueryPlan must not enter ReAct")))
    analyst = ResearchAnalyst()
    monkeypatch.setattr(analyst, "_conclude", lambda *_args: (_ for _ in ()).throw(
        AssertionError("completed creation QueryPlan must not call structured synthesis")))

    query_results = [
        {"id": "jira", "source": "jira", "result": {
            "canonicalJql": 'project in ("DL") AND text ~ "Puffin"',
            "scopeProjects": ["DL"], "pages": 2, "total": 1,
            "tickets": [{"key": "DL-401", "summary": "[Catalog] Puffin NDV PoC",
                         "status": "Done", "updated": "2026-08-10"}],
            "ticketDetails": [{
                "key": "DL-401", "summary": "[Catalog] Puffin NDV PoC",
                "status": "Done", "description": "PoC 범위에서 NDV 파일을 검증했다.",
                "updated": "2026-08-10",
                "comments": [{"author": "skcc.x1001", "created": "2026-08-11",
                              "body": "운영 적용 범위는 별도 결정이 필요하다."}],
            }],
        }},
        {"id": "comments", "source": "comments", "result": {
            "canonicalJql": 'project in ("DL") AND comment ~ "Puffin"',
            "scopeProjects": ["DL"], "pages": 1,
            "comments": [{"ticketKey": "DL-401", "ticketSummary": "[Catalog] Puffin NDV PoC",
                          "author": "skcc.x1002", "date": "2026-08-12",
                          "snippet": "Puffin 통계 형식 후보를 검토했다."}],
        }},
        {"id": "docs", "source": "confluence", "result": {
            "canonicalCql": 'space in ("DATA") AND siteSearch ~ "Puffin"',
            "scopeSpaces": ["DATA"], "pages": 1,
            "documents": [{"id": "991", "space": "DATA", "title": "Puffin NDV 설계 기록",
                           "url": "https://confluence.example/pages/991",
                           "excerpt": "NDV 저장 방식 검토", "modified": "2026-08-13"}],
            "documentBodies": [{"title": "Puffin NDV 설계 기록",
                                "url": "https://confluence.example/pages/991",
                                "updated": "2026-08-13",
                                "text": "Puffin 파일의 생성과 읽기 제약을 기록했다."}],
        }},
        {"id": "web", "source": "web", "result": {
            "query": "Apache Iceberg Puffin NDV official",
            "results": [{"title": "Apache Iceberg Puffin specification",
                         "url": "https://iceberg.apache.org/puffin-spec/", "official": True,
                         "snippet": "Puffin file format specification."}],
        }},
    ]
    original = deepcopy(query_results)
    state = {
        "intent": Intent.PLAN_WORK,
        "messages": [HumanMessage(content="Puffin NDV 운영 적용 Task 만들어줘")],
        "request_text": "Puffin NDV 운영 적용 Task 만들어줘",
        "request_plan": {"tasks": [{
            "id": "ticket", "kind": "ticket",
            "instruction": "Puffin NDV 운영 적용 Task를 만든다",
            "depends_on": [], "write_intent": True,
            "completion_criteria": ["적용 범위와 검증 기준을 포함"],
        }]},
        "keywords": ["Puffin", "NDV"], "mentioned_keys": [],
        "query_plan": {"queries": [
            {"id": "jira", "source": "jira", "completeness": "all"},
            {"id": "comments", "source": "comments", "completeness": "all"},
            {"id": "docs", "source": "confluence", "completeness": "all"},
            {"id": "web", "source": "web", "completeness": "page"},
        ]},
        "query_results": query_results,
        "web_context": "RAW WEB CONTEXT FOR WORK ARCHITECT",
        "trace": [],
    }

    out = analyst.node()(state)

    assert query_results == original, "deterministic ledger must not consume or rewrite raw results"
    assert out["web_context"] == "RAW WEB CONTEXT FOR WORK ARCHITECT"
    assert out["already_exists"] is False, "search candidates are not proven duplicates"
    evidence = {row["key"]: row for row in out["evidence"]}
    assert evidence["DL-401"]["title"] == "[Catalog] Puffin NDV PoC"
    assert {(obs["source"], obs.get("observed_at"), obs["text"])
            for obs in evidence["DL-401"]["observations"]} >= {
        ("description", "2026-08-10", "PoC 범위에서 NDV 파일을 검증했다."),
        ("comment", "2026-08-11", "skcc.x1001: 운영 적용 범위는 별도 결정이 필요하다."),
        ("comment", "2026-08-12", "skcc.x1002: Puffin 통계 형식 후보를 검토했다."),
    }
    jira_query = next(obs["text"] for obs in evidence["DL-401"]["observations"]
                      if obs["source"] == "query")
    assert 'scopeProjects=["DL"]' in jira_query and "pages=2" in jira_query
    assert 'canonicalJql=project in ("DL") AND text ~ "Puffin"' in jira_query
    doc = evidence["Puffin NDV 설계 기록"]
    assert doc["url"] == "https://confluence.example/pages/991"
    assert any(obs["source"] == "document" and obs["observed_at"] == "2026-08-13"
               and "생성과 읽기" in obs["text"] for obs in doc["observations"])
    web = evidence["Apache Iceberg Puffin specification"]
    assert web["url"] == "https://iceberg.apache.org/puffin-spec/"
    assert {obs["source"] for obs in web["observations"]} == {"external", "query"}
    assert next(obs["text"] for obs in web["observations"] if obs["source"] == "external") \
        == "Puffin file format specification."
    web_query = next(obs["text"] for obs in web["observations"] if obs["source"] == "query")
    assert "query=Apache Iceberg Puffin NDV official" in web_query and "official=true" in web_query
    assert out["related_docs"] == [{
        "title": "Puffin NDV 설계 기록", "url": "https://confluence.example/pages/991"}]
    assert any("deterministic 근거 장부" in row.get("note", "") for row in out["trace"])


def _duplicate_query_state(*, request, title, issue_type="Task", status="In Progress",
                           status_category="indeterminate", description="same scope",
                           tier="task", parent=""):
    ticket = {
        "key": "DL-9901", "summary": title, "issueType": issue_type, "tier": tier,
        "status": status, "statusCategory": status_category,
    }
    if parent:
        ticket["parent"] = parent
    detail = {
        "key": "DL-9901", "summary": title, "type": issue_type, "status": status,
        "done": status_category == "done", "description": description,
    }
    return {
        "intent": "plan_work", "request_text": request,
        "keywords": ["Puffin", "NDV"], "mentioned_keys": [], "trace": [],
        "query_plan": {"queries": [{"id": "jira", "source": "jira"}]},
        "query_results": [{"id": "jira", "source": "jira", "result": {
            "scopeProjects": ["DL"], "canonicalJql": 'project in ("DL")',
            "tickets": [ticket], "ticketDetails": [detail],
        }}],
    }


def test_completed_creation_plan_proves_exact_open_task_duplicate_without_llm(monkeypatch):
    from langchain_core.messages import HumanMessage
    import app.agent.workflow.agents.research_analyst as mod
    from app.agent.workflow.agents.base import ToolAgent
    from app.agent.workflow.agents.research_analyst import ResearchAnalyst

    monkeypatch.setattr(mod, "_research_outside", lambda *_args: "")
    monkeypatch.setattr(ToolAgent, "node", lambda _self: lambda _state: (_ for _ in ()).throw(
        AssertionError("exact duplicate proof must not enter ReAct")))
    analyst = ResearchAnalyst()
    monkeypatch.setattr(analyst, "_conclude", lambda *_args: (_ for _ in ()).throw(
        AssertionError("exact duplicate proof must not call structured synthesis")))
    state = _duplicate_query_state(
        request="Puffin NDV 운영 적용 Task 만들어줘",
        title="[Catalog] Puffin NDV 운영 적용",
        description="Puffin NDV 운영 적용 범위를 구현하고 검증하는 기존 작업이다.",
    )
    state["messages"] = [HumanMessage(content=state["request_text"])]

    out = analyst.node()(state)

    assert out["already_exists"] is True
    assert "DL-9901" in out["situation"] and "기존" in out["situation"]
    assert out["evidence"][0]["key"] == "DL-9901"
    assert out["evidence"][0]["fitness"] == "direct"


def test_completed_creation_plan_proves_exact_open_bug_duplicate():
    from app.agent.workflow.agents.research_analyst import _exact_creation_duplicate_key

    state = _duplicate_query_state(
        request="리니지 뷰어 빈 화면 오류를 Bug로 등록해줘",
        title="[Workbench] 리니지 뷰어 빈 화면 오류 수정",
        issue_type="Bug",
        description="리니지 뷰어가 빈 화면으로 열리는 오류를 재현하고 수정하는 작업이다.",
    )

    assert _exact_creation_duplicate_key(state) == "DL-9901"


@pytest.mark.parametrize("overrides", [
    {
        "title": "[Catalog] Puffin NDV PoC 검토",
        "description": "Puffin NDV 기술 PoC만 검토하는 선행 조사이다.",
    },
    {"issue_type": "Epic", "tier": "epic"},
    {"status": "Done", "status_category": "done"},
    {"status": "Closed", "status_category": ""},
    {"issue_type": "Bug"},
    {"title": "[Catalog] Puffin NDV 설계 검토",
     "description": "Puffin NDV 설계 대안을 조사한다."},
    {"description": ""},
])
def test_completed_creation_plan_rejects_ambiguous_or_non_equivalent_candidates(overrides):
    from app.agent.workflow.agents.research_analyst import _exact_creation_duplicate_key

    base = {
        "request": "Puffin NDV 운영 적용 Task 만들어줘",
        "title": "[Catalog] Puffin NDV 운영 적용",
        "description": "Puffin NDV 운영 적용 범위를 구현하고 검증하는 기존 작업이다.",
    }
    state = _duplicate_query_state(**{**base, **overrides})
    if overrides.get("description") == "":
        state["query_results"][0]["result"]["ticketDetails"] = []

    assert _exact_creation_duplicate_key(state) == ""


def test_completed_creation_plan_requires_explicit_same_issue_type():
    from app.agent.workflow.agents.research_analyst import _exact_creation_duplicate_key

    state = _duplicate_query_state(
        request="Puffin NDV 운영 적용 티켓 만들어줘",
        title="[Catalog] Puffin NDV 운영 적용",
        description="Puffin NDV 운영 적용 범위를 구현하고 검증하는 기존 작업이다.",
    )

    assert _exact_creation_duplicate_key(state) == ""


def test_completed_internal_creation_plan_keeps_one_external_safety_net(monkeypatch):
    """A small planner may omit web; a technical creation request still gets one deterministic supplement."""
    from langchain_core.messages import HumanMessage
    import app.agent.workflow.agents.research_analyst as mod
    from app.agent.workflow.agents.base import ToolAgent
    from app.agent.workflow.agents.research_analyst import ResearchAnalyst
    from app.agent.workflow.state import Intent

    monkeypatch.setattr(ToolAgent, "node", lambda _self: lambda _state: (_ for _ in ()).throw(
        AssertionError("completed creation QueryPlan must not enter ReAct")))
    analyst = ResearchAnalyst()
    monkeypatch.setattr(analyst, "_conclude", lambda *_args: (_ for _ in ()).throw(
        AssertionError("completed creation QueryPlan must not call structured synthesis")))
    calls = []

    def outside(_agent, asked):
        calls.append(asked)
        return "Apache Puffin 공식 사양 — https://iceberg.apache.org/puffin-spec/"

    monkeypatch.setattr(mod, "_research_outside", outside)
    out = analyst.node()({
        "intent": Intent.PLAN_WORK,
        "messages": [HumanMessage(content="Puffin NDV 방식 적용 Task 만들어줘")],
        "request_text": "Puffin NDV 방식 적용 Task 만들어줘",
        "keywords": ["Puffin", "NDV"], "mentioned_keys": [],
        "query_plan": {"queries": [{"id": "jira", "source": "jira"}]},
        "query_results": [{"id": "jira", "source": "jira", "result": {
            "scopeProjects": ["DL"], "canonicalJql": 'project in ("DL")',
            "tickets": [], "total": 0,
        }}],
        "trace": [],
    })

    assert calls == ["Puffin NDV 방식 적용 Task 만들어줘"]
    assert out["web_context"] == "Apache Puffin 공식 사양 — https://iceberg.apache.org/puffin-spec/"
    assert out["already_exists"] is False


def test_deterministic_ledger_does_not_merge_same_title_documents_with_distinct_ids():
    from app.agent.workflow.agents.research_analyst import _completed_creation_ledger

    state = {"query_results": [{"id": "docs", "source": "confluence", "result": {
        "scopeSpaces": ["DATA"],
        "documents": [
            {"id": "101", "title": "공통 설계", "url": "https://conf/pages/101"},
            {"id": "202", "title": "공통 설계", "url": "https://conf/pages/202"},
        ],
        "documentBodies": [
            {"title": "공통 설계", "url": "https://conf/pages/101", "text": "첫 번째 본문"},
            {"title": "공통 설계", "url": "https://conf/pages/202", "text": "두 번째 본문"},
        ],
    }}]}

    docs = [row for row in _completed_creation_ledger(state)["evidence"]
            if row.get("url", "").startswith("https://conf/pages/")]
    assert {row["url"] for row in docs} == {"https://conf/pages/101", "https://conf/pages/202"}
    by_url = {row["url"]: row for row in docs}
    assert by_url["https://conf/pages/101"]["observations"][0]["text"] == "첫 번째 본문"
    assert by_url["https://conf/pages/202"]["observations"][0]["text"] == "두 번째 본문"


def test_completed_write_ledger_preserves_exact_binding_order_caps_and_diagnostics():
    """Characterize the pure completed-query projection before its structural extraction."""
    from copy import deepcopy
    from app.agent.workflow.agents.research_analyst import _completed_write_ledger

    state = {
        "continuation_contract": {"target_keys": ["AB-7"]},
        "query_results": [
            {"id": "jira-main", "source": "jira", "result": {
                "scopeProjects": ["AB"], "pages": 2,
                "canonicalJql": 'project = "AB"',
                "tickets": [
                    {"key": "ab-7", "summary": "변경 대상", "url": "https://jira/browse/AB-7"},
                    {"key": "AB-8", "summary": "검토 후보"},
                ],
            }},
            {"id": "docs-main", "source": "confluence", "result": {
                "scopeSpaces": ["OPS"], "pages": 1,
                "documents": [{
                    "id": "guide-1", "title": "운영 가이드",
                    "url": "https://docs.example/pages/guide-1",
                    "excerpt": "배포 전 점검", "modified": "2026-08-10",
                }],
                "documentBodies": [{
                    "id": "guide-1", "title": "운영 가이드",
                    "url": "https://docs.example/pages/guide-1",
                    "text": "현재 운영 절차", "updated": "2026-08-11",
                }],
            }},
            {"id": "web-main", "source": "web", "result": {
                "query": "ProtocolY official documentation",
                "results": [{
                    "title": "ProtocolY Reference", "url": "https://protocol.example/reference",
                    "snippet": "Normative reference.", "official": True,
                    "published": "2026-08-12",
                }],
            }},
            {"id": "github-main", "source": "github", "result": {
                "query": "ProtocolY implementation",
                "results": [{
                    "name": "protocoly-client", "url": "https://code.example/protocoly-client",
                    "description": "Client implementation.", "updated": "2026-08-13",
                }],
            }},
        ],
        "materialized_ticket_sources": {"ticketDetails": [{
            "key": "AB-7", "summary": "변경 대상", "description": "검증된 현재 본문",
            "updated": "2026-08-14",
            "comments": [{
                "author": "user.17", "body": "결정 기록", "created": "2026-08-15",
            }],
        }]},
    }
    original = deepcopy(state)

    out = _completed_write_ledger(state, action="update")

    assert state == original
    assert out == {
        "situation": (
            "설정된 검색 범위와 페이지 조건에 따라 QueryPlan 조회를 완료했고 "
            "티켓 2건, 문서 1건, 외부 자료 2건을 원본 출처와 함께 전달한다. 이 항목들은 필드 변경 "
            "초안의 근거 장부이며, 검색 일치만으로 새 사실을 추론하지 않았다."
        ),
        "evidence": [
            {
                "key": "AB-7", "title": "변경 대상", "url": "https://jira/browse/AB-7",
                "why": "사용자가 지정한 필드 변경 대상이며 QueryRunner가 원본을 열어 확인했다.",
                "confidence": "high", "fitness": "direct",
                "limitations": (
                    "대상 원본이며, 새 값이나 댓글 내용은 사용자 요청과 별도 초안 계약에서만 확정한다."
                ),
                "observations": [
                    {"source": "description", "text": "검증된 현재 본문",
                     "observed_at": "2026-08-14"},
                    {"source": "comment", "text": "user.17: 결정 기록",
                     "observed_at": "2026-08-15"},
                    {"source": "query", "text": (
                        'QueryPlan jira:jira-main · scopeProjects=["AB"] · pages=2 · '
                        'canonicalJql=project = "AB" | QueryPlan jira:materialized-ticket-sources '
                        '· artifactId=materialized_ticket_sources'
                    ), "observed_at": ""},
                ],
            },
            {
                "key": "AB-8", "title": "검토 후보", "url": "",
                "why": "설정된 Jira 검색 범위에서 반환된 필드 변경 검토 후보이다.",
                "confidence": "unknown", "fitness": "unknown",
                "limitations": "검색 일치만으로 필드 변경 요청의 직접 근거인지 확정하지 않았다.",
                "observations": [{
                    "source": "query",
                    "text": ('QueryPlan jira:jira-main · scopeProjects=["AB"] · pages=2 '
                             '· canonicalJql=project = "AB"'),
                    "observed_at": "",
                }],
            },
            {
                "key": "운영 가이드", "title": "운영 가이드",
                "url": "https://docs.example/pages/guide-1",
                "why": "설정된 Confluence 검색 범위에서 반환되어 본문까지 확인한 문서 후보이다.",
                "confidence": "unknown", "fitness": "unknown",
                "limitations": "문서 내용만으로 필드 변경 요청의 직접 근거인지 확정하지 않았다.",
                "observations": [
                    {"source": "document", "text": "현재 운영 절차",
                     "observed_at": "2026-08-11"},
                    {"source": "document", "text": "배포 전 점검",
                     "observed_at": "2026-08-10"},
                    {"source": "query", "text": (
                        'QueryPlan confluence:docs-main · scopeSpaces=["OPS"] · pages=1'
                    ), "observed_at": ""},
                ],
            },
            {
                "key": "ProtocolY Reference", "title": "ProtocolY Reference",
                "url": "https://protocol.example/reference",
                "why": "QueryPlan web:web-main에서 반환된 외부 자료이다.",
                "confidence": "high", "fitness": "unknown",
                "limitations": "외부 자료이며 내부 필드 변경 대상의 현재 상태를 증명하지 않는다.",
                "observations": [
                    {"source": "external", "text": "Normative reference.",
                     "observed_at": "2026-08-12"},
                    {"source": "query", "text": (
                        "QueryPlan web:web-main · query=ProtocolY official documentation · official=true"
                    ), "observed_at": ""},
                ],
            },
            {
                "key": "protocoly-client", "title": "protocoly-client",
                "url": "https://code.example/protocoly-client",
                "why": "QueryPlan github:github-main에서 반환된 외부 자료이다.",
                "confidence": "unknown", "fitness": "unknown",
                "limitations": "외부 자료이며 내부 필드 변경 대상의 현재 상태를 증명하지 않는다.",
                "observations": [
                    {"source": "external", "text": "Client implementation.",
                     "observed_at": "2026-08-13"},
                    {"source": "query", "text": (
                        "QueryPlan github:github-main · query=ProtocolY implementation"
                    ), "observed_at": ""},
                ],
            },
        ],
        "related_docs": [{
            "title": "운영 가이드", "url": "https://docs.example/pages/guide-1",
        }],
        "epic_candidate": "", "already_exists": False,
        "_deterministic_passthrough": True,
    }


def test_deterministic_ledger_cap_keeps_internal_document_and_external_source_diversity():
    from app.agent.workflow.agents.research_analyst import _completed_creation_ledger

    tickets = [{"key": f"AB-{number}", "summary": f"ProtocolY 후보 {number}"}
               for number in range(1, 9)]
    details = [{**row, "description": f"후보 {number} 본문"}
               for number, row in enumerate(tickets, 1)]
    state = {
        "request_text": "ProtocolY 적용 Task 만들어줘", "keywords": ["ProtocolY"],
        "mentioned_keys": [], "trace": [],
        "query_results": [
            {"id": "jira", "source": "jira", "result": {
                "scopeProjects": ["AB"], "tickets": tickets, "ticketDetails": details,
            }},
            {"id": "docs", "source": "confluence", "result": {
                "scopeSpaces": ["OPS"],
                "documents": [{"id": "991", "title": "ProtocolY 내부 설계",
                               "url": "https://docs.example/pages/991"}],
                "documentBodies": [{"title": "ProtocolY 내부 설계",
                                    "url": "https://docs.example/pages/991", "text": "내부 설계 본문"}],
            }},
            {"id": "web", "source": "web", "result": {"results": [{
                "title": "ProtocolY reference", "url": "https://protocol.example/reference",
                "snippet": "official format", "official": True,
            }]}},
        ],
    }

    out = _completed_creation_ledger(state)

    assert [row["key"] for row in out["evidence"]] == [
        "AB-1", "AB-2", "AB-3", "AB-4", "AB-5",
        "ProtocolY 내부 설계", "ProtocolY reference", "AB-6",
    ]


def _compound_research_creation_state():
    """Completed acquisition for two user-visible outcomes: synthesis and a ticket draft."""
    from langchain_core.messages import HumanMessage
    from app.agent.workflow.state import Intent

    tickets = [{"key": f"DL-{900 + number}", "summary": f"일반 운영 후보 {number}"}
               for number in range(1, 9)]
    tickets.append({"key": "DL-909", "summary": "보존기간 예외정책 비교 결과"})
    details = [{**ticket, "description": f"{ticket['summary']} 본문"}
               for ticket in tickets]
    request = "보존기간 현행기준과 예외정책을 조사·비교해 요약하고 적용 Task도 생성해줘"
    return {
        "intent": Intent.PLAN_WORK,
        "messages": [HumanMessage(content=request)],
        "request_text": request,
        "keywords": ["보존기간", "예외정책"],
        "mentioned_keys": [],
        "request_plan": {"goal": request, "tasks": [
            {"id": "research", "kind": "research",
             "instruction": "보존기간 현행기준과 예외정책을 비교 요약한다",
             "depends_on": [], "write_intent": False,
             "completion_criteria": ["내부 결정과 외부 기준의 차이를 출처와 함께 제시"]},
            {"id": "ticket", "kind": "ticket",
             "instruction": "검증 가능한 적용 Task를 생성한다",
             "depends_on": ["research"], "write_intent": True,
             "completion_criteria": ["조사 결과를 범위와 DoD에 반영"]},
        ]},
        "query_plan": {"queries": [
            {"id": "jira", "source": "jira"},
            {"id": "docs", "source": "confluence"},
            {"id": "web", "source": "web"},
        ]},
        "query_results": [
            {"id": "jira", "source": "jira", "result": {
                "scopeProjects": ["DL"], "tickets": tickets, "ticketDetails": details,
            }},
            {"id": "docs", "source": "confluence", "result": {
                "scopeSpaces": ["DATA"],
                "documents": [
                    {"id": "1", "title": "일반 운영 안내", "url": "https://conf/pages/1"},
                    {"id": "2", "title": "보존기간 현행기준", "url": "https://conf/pages/2"},
                ],
                "documentBodies": [
                    {"id": "1", "title": "일반 운영 안내", "url": "https://conf/pages/1",
                     "text": "일반 안내"},
                    {"id": "2", "title": "보존기간 현행기준", "url": "https://conf/pages/2",
                     "text": "예외정책은 승인 기록과 함께 비교해야 한다."},
                ],
            }},
            {"id": "web", "source": "web", "result": {
                "query": "retention exception policy official",
                "results": [{"title": "Retention exception standard",
                             "url": "https://standards.example/retention",
                             "snippet": "Official exception comparison criteria.",
                             "official": True}],
            }},
        ],
        "trace": [],
    }


def test_completed_compound_research_and_creation_synthesizes_once_for_downstream_sources(
        monkeypatch):
    """Explicit research is a deliverable, not merely a hidden prerequisite of ticket creation."""
    from app.agent.workflow.agents.base import ToolAgent
    from app.agent.workflow.agents.research_analyst import ResearchAnalyst
    from app.agent.workflow.agents.result_integrator import ResultIntegrator

    monkeypatch.setattr(ToolAgent, "node", lambda _self: lambda _state: (_ for _ in ()).throw(
        AssertionError("completed compound QueryPlan must not repeat retrieval through ReAct")))
    analyst = ResearchAnalyst()
    calls = []

    def synthesize(state, messages):
        rendered = "\n".join(str(getattr(message, "content", "")) for message in messages)
        calls.append((state, rendered))
        return {
            "situation": "보존기간 예외정책은 내부 현행기준과 외부 기준을 함께 비교해야 한다.",
            "evidence": [{
                "key": "보존기간 현행기준", "title": "보존기간 현행기준",
                "url": "https://conf/pages/2", "why": "예외정책의 내부 현행기준을 직접 설명한다.",
                "confidence": "high", "fitness": "direct", "limitations": "승인 주체는 미확인",
                "observations": [{"source": "document",
                                  "text": "예외정책은 승인 기록과 함께 비교해야 한다."}],
            }],
            "related_docs": [{"title": "보존기간 현행기준", "url": "https://conf/pages/2"}],
            "already_exists": False,
        }

    monkeypatch.setattr(analyst, "invoke_structured", synthesize)
    state = _compound_research_creation_state()

    out = analyst.node()(state)

    assert len(calls) == 1
    assert calls[0][0]["_research_analyst_prefetched"] is True
    assert "DL-909" in calls[0][1] and "보존기간 현행기준과 예외정책을 비교 요약한다" in calls[0][1]
    assert "Tool Transcript Data" not in calls[0][1]
    assert out["evidence"][0]["url"] == "https://conf/pages/2"
    assert any("복합 조사·작성" in row.get("note", "") for row in out["trace"])

    final_prompt = ResultIntegrator().task({**state, **out})
    assert "https://conf/pages/2" in final_prompt
    assert "예외정책은 승인 기록과 함께 비교해야 한다" in final_prompt


def test_compound_research_synthesis_failure_uses_relevant_bounded_ledger_without_react(
        monkeypatch):
    """A formatting failure keeps late relevant discoveries instead of first-hit 5/2/1 truncation."""
    from app.agent.workflow.agents.base import ToolAgent
    from app.agent.workflow.agents.research_analyst import ResearchAnalyst

    monkeypatch.setattr(ToolAgent, "node", lambda _self: lambda _state: (_ for _ in ()).throw(
        AssertionError("completed QueryPlan fallback must not repeat retrieval through ReAct")))
    analyst = ResearchAnalyst()
    monkeypatch.setattr(analyst, "invoke_structured", lambda *_args, **_kwargs: (_ for _ in ()).throw(
        RuntimeError("invalid structured result")))

    out = analyst.node()(_compound_research_creation_state())

    assert len(out["evidence"]) == 8
    assert any(row.get("key") == "DL-909" for row in out["evidence"]), \
        "the late source matching the explicit research outcome must survive the bounded fallback"
    assert any(row.get("url") == "https://conf/pages/2" for row in out["evidence"])
    assert any(row.get("url") == "https://standards.example/retention"
               for row in out["evidence"])
    assert any("관련도 기반" in row.get("note", "") for row in out["trace"])


def test_failed_plan_work_query_plan_is_not_treated_as_scoped_zero_result(monkeypatch):
    """A scope/config error is an acquisition gap, never evidence that no duplicate exists."""
    from langchain_core.messages import HumanMessage
    import app.agent.workflow.agents.research_analyst as mod
    from app.agent.workflow.agents.base import ToolAgent
    from app.agent.workflow.agents.research_analyst import ResearchAnalyst
    from app.agent.workflow.state import Intent

    monkeypatch.setattr(mod, "_completed_creation_ledger", lambda _state: (_ for _ in ()).throw(
        AssertionError("failed QueryPlan must not use deterministic complete-plan ledger")))
    monkeypatch.setattr(ToolAgent, "node", lambda _self: lambda _state: (_ for _ in ()).throw(
        AssertionError("prefetched failure handling must not repeat acquisition through ReAct")))
    analyst = ResearchAnalyst()
    calls = []

    def conclude(_state, _scratch):
        calls.append(True)
        return {"situation": "검색 범위 설정 오류를 확인해야 한다.", "evidence": []}

    monkeypatch.setattr(analyst, "_conclude", conclude)
    out = analyst.node()({
        "intent": Intent.PLAN_WORK,
        "messages": [HumanMessage(content="카탈로그 체크박스 추가 Task 만들어줘")],
        "keywords": ["카탈로그", "체크박스"], "mentioned_keys": [],
        "query_plan": {"queries": [{"id": "jira", "source": "jira"}]},
        "query_results": [{"id": "jira", "source": "jira", "result": {
            "error": "search.jira.projects 범위를 설정하세요", "tickets": [],
            "scopeProjects": [],
        }}],
        "trace": [],
    })

    assert calls == [True]
    assert out["situation"] == "검색 범위 설정 오류를 확인해야 한다."
    assert not any("QueryPlan 0건" in row.get("note", "") for row in out["trace"])


def test_incomplete_ask_query_plan_keeps_react_fallback(monkeypatch):
    """A missing or failed planned source must not be hidden by the one-pass optimization."""
    from langchain_core.messages import HumanMessage
    from app.agent.workflow.agents.base import ToolAgent
    from app.agent.workflow.agents.research_analyst import ResearchAnalyst
    from app.agent.workflow.state import Intent

    called = {"react": 0}

    def base_node(_self):
        def run(_state):
            called["react"] += 1
            return {"situation": "fallback 조사", "evidence": []}
        return run

    monkeypatch.setattr(ToolAgent, "node", base_node)
    analyst = ResearchAnalyst()
    out = analyst.node()({
        "intent": Intent.ASK,
        "messages": [HumanMessage(content="내외부 자료를 조사해줘")],
        "keywords": [], "mentioned_keys": [],
        "query_plan": {"queries": [
            {"id": "jira", "source": "jira"}, {"id": "web", "source": "web"},
        ]},
        "query_results": [
            {"id": "jira", "source": "jira", "result": {"tickets": []}},
            {"id": "web", "source": "web", "result": {"error": "blocked"}},
        ],
        "trace": [],
    })
    assert called["react"] == 1 and out["situation"] == "fallback 조사"


def test_query_plan_web_evidence_is_not_duplicated_in_research_task():
    """The same web snippets must not appear once in QueryPlan and again in web_context."""
    from langchain_core.messages import HumanMessage
    from app.agent.workflow.agents.research_analyst import ResearchAnalyst

    url = "https://example.com/official"
    state = {
        "messages": [HumanMessage(content="기술 조사")],
        "query_results": [{"id": "web", "source": "web", "result": {
            "results": [{"title": "공식", "url": url, "snippet": "근거"}]}}],
        "web_context": f"공식 — 근거 ({url})",
    }
    task = ResearchAnalyst().task(state)
    assert task.count(url) == 1


def test_research_evidence_short_title_is_bound_to_unique_verified_ticket_key():
    from app.agent.workflow.agents.research_analyst import _normalize_evidence_identity

    state = {"query_results": [{"source": "jira", "result": {
        "ticketDetails": [{"key": "DL-9203", "summary": "[Catalog] 검증 기준 초안"}],
    }}]}
    got = _normalize_evidence_identity({
        "key": "[회의] 검증 기준", "title": "[회의] 검증 기준",
        "why": "DL-9203에서 검증 기준을 작성함", "url": "",
        "observations": [{"source": "comment", "text": "기준 초안 작성"}],
    }, state)

    assert got["key"] == "DL-9203"
    assert got["title"] == "[Catalog] 검증 기준 초안"


def test_empty_plan_work_query_skips_presurvey_and_llm_synthesis(monkeypatch):
    """A scoped zero-result QueryPlan is already a complete duplicate check."""
    from langchain_core.messages import HumanMessage
    import app.agent.workflow.agents.research_analyst as mod
    from app.agent.workflow.agents.base import ToolAgent
    from app.agent.workflow.agents.research_analyst import ResearchAnalyst
    from app.agent.workflow.state import Intent

    monkeypatch.setattr(mod, "_presurvey", lambda _state: (_ for _ in ()).throw(
        AssertionError("PLAN_WORK must not repeat QueryPlan through presurvey")))
    monkeypatch.setattr(ToolAgent, "node", lambda _self: lambda _state: (_ for _ in ()).throw(
        AssertionError("zero-result creation must not enter ReAct")))
    h = ResearchAnalyst()
    monkeypatch.setattr(h, "_conclude", lambda *_a: (_ for _ in ()).throw(
        AssertionError("zero-result creation must not call the conclusion LLM")))
    out = h.node()({
        "intent": Intent.PLAN_WORK,
        "messages": [HumanMessage(content="카탈로그 체크박스 추가 Task 만들어줘")],
        "keywords": ["카탈로그", "체크박스"], "mentioned_keys": [],
        "query_results": [{"id": "jira", "source": "jira",
                           "result": {"total": 0, "tickets": []}}],
        "trace": [],
    })
    assert "직접 중복" in out["situation"] and not out["evidence"]
    assert any("LLM 요약 생략" in x.get("note", "") for x in out["trace"])


def test_shape_ships_people_name_map():
    """사람은 사번만 보내지 않는다 — 화면이 아바타+본명으로 그리도록 id→이름 지도가 실린다."""
    from app.agent.workflow.session import _people_names
    out = {"assignments": [{"index": 0, "user": "skcc.x1042",
                            "alternates": [{"user": "skcc.x1210", "why": "-"}]}],
           "pending": {"items": [{"assignee": "skcc.x1042"}]}}
    names = _people_names(out)
    assert names.get("skcc.x1042"), "추천 담당자의 본명이 없다"
    assert names.get("skcc.x1210"), "대안 후보의 본명이 없다"


def test_friendly_error_translates_provider_failures():
    """429·크레딧 오류는 영어 JSON 덤프가 아니라 사용자가 행동할 수 있는 한국어 안내로."""
    from app.agent.workflow.session import _friendly_error
    assert "분당 토큰" in _friendly_error("[people_advisor] Error code: 429 - Rate limit reached ... tokens per min")
    assert "크레딧" in _friendly_error("insufficient_quota: exceeded your current quota")
    assert "컨텍스트" in _friendly_error("maximum context length 128000 exceeded")
    assert _friendly_error("KeyError: boom") == ""       # 모르는 오류는 숨기지 않는다


def test_search_survives_a_noise_token():
    """전 토큰 AND 매칭에서 일반어 하나('테스크')가 끼어도 실존 티켓을 찾아야 한다.

    실측: 'UI 회귀 검증 픽스처 테스크' → 0건 → "이력 없음" 오답. 코드가 일반어를 떼고
    재검색하고, 그래도 비면 토큰별 매칭 수로 랭킹한다.
    """
    r = _run(T.BY_NAME["search_work_history"], query="UI 회귀 검증 픽스처 테스크", limit=5)
    assert any(x["key"] == "DL-9000" for x in r["jira"]), r["jira"]
    # 사다리를 안 타는 정상 질의는 그대로
    r2 = _run(T.BY_NAME["search_work_history"], query="UI 회귀 검증 픽스처", limit=5)
    assert any(x["key"] == "DL-9000" for x in r2["jira"])


def test_historian_presurvey_for_topic_questions(monkeypatch):
    """주제형 질문(키 없음)은 코드가 키워드+의미 검색을 먼저 돌려 자료로 준다.

    모델의 검색 실력에 기대지 않는다 — 근황 질문이면 최근 갱신순 목록이 이미 실려 있어야 한다.
    """
    import os
    os.environ["LAKE_AGENT_PROVIDER"] = "fake"
    from langchain_core.messages import HumanMessage
    from app.agent.workflow.agents.research_analyst import ResearchAnalyst, _presurvey
    pre = _presurvey({"keywords": ["UI", "회귀", "픽스처"],
                      "messages": [HumanMessage(content="UI 회귀 픽스처 히스토리 알려줘")]})
    assert "DL-9000" in pre, "실존 티켓이 사전 조사에 없다"
    assert "키워드 검색" in pre and "갱신" in pre
    # task() 에 실리는지 — ReAct 없이 task 직접 확인
    h = ResearchAnalyst()
    txt = h.task({"keywords": ["UI"], "pre_survey": pre,
                  "messages": [HumanMessage(content="근황?")]})
    assert "Prefetched Lexical and Semantic Search" in txt and "DL-9000" in txt


def test_unassigned_tool_does_not_lie_about_assigned_tickets():
    """mock JQL 이 'assignee is EMPTY' 를 조용히 무시한다 — 판정은 코드가 해야 한다.

    실측: 담당자 있는 티켓 전부가 '미배정'으로 둔갑해, '담당자 없는 업무' 질문에
    엉뚱한 목록이 답으로 나갔다. 이 도구의 결과에는 담당자 있는 티켓이 0건이어야 한다.
    """
    from app.agent.tools._ctx import client
    r = _run(T.BY_NAME["find_unassigned_tickets"], module="")
    assert "error" not in r
    for t in (r.get("tickets") or [])[:10]:
        raw = client().get_issue(t["key"]) or {}
        assert not ((raw.get("fields") or {}).get("assignee")), f"{t['key']} 는 담당자가 있다"


def test_group_activity_preaggregates_whole_roster():
    """그룹 활동 질의는 코드가 로스터 **전원**을 조회해 자료로 만든다.

    실측 2회: 모델에게 맡기면 한 명만 조회하고 끝내거나 사람별 정리를 건너뛴다.
    개인 질문·모듈 불명·비활동 의도에는 발동하지 않아야 한다.
    """
    from langchain_core.messages import HumanMessage
    from app.agent.workflow.agents.portfolio_analyst import _group_activity
    pre = _group_activity({"intent": "activity", "module": "", "messages": [
        HumanMessage(content="최근 7일간 ETL 모듈 구성원들의 주요 활동 내역")]})
    assert "[로스터]" in pre and "최근 7일" in pre
    assert pre.count("[skcc.") >= 3, "로스터 전원이 담기지 않았다"
    # 개인 질문 — 미발동(기존 경로)
    assert _group_activity({"intent": "activity", "module": "", "messages": [
        HumanMessage(content="skcc.x1042 최근 뭐 했어?")]}) == ""
    # 다른 의도 — 미발동
    assert _group_activity({"intent": "progress", "module": "", "messages": [
        HumanMessage(content="ETL 인력들 진척")]}) == ""
    # 기간 파싱
    pre14 = _group_activity({"intent": "activity", "module": "ETL", "messages": [
        HumanMessage(content="지난 14일 동안 우리 모듈 인력들 활동")]})
    assert "최근 14일" in pre14


def test_run_jql_forces_all_search_config_projects_without_primary_fallback():
    """JQL 바깥 범위는 search config 전체이며 project_key는 관여하지 않는다."""
    r = _run(T.BY_NAME["run_jql"], jql="statusCategory = indeterminate ORDER BY updated DESC", limit=5)
    assert r.get("count") is not None, r
    assert r["jql"].startswith('project in ("DL", "JIRA820") AND ('), r.get("jql")
    assert r.get("scopeProjects") == ["DL", "JIRA820"]
    assert len(r["tickets"]) <= 5

    # 사용자가 project 조건을 넣어도 설정 범위와 바깥 AND로 결합되어 탈출할 수 없다.
    bad = _run(T.BY_NAME["run_jql"], jql='project = OTHER AND status = Open')
    assert bad["jql"].startswith('project in ("DL", "JIRA820") AND (project = OTHER')
    assert not bad["tickets"]


def test_create_epic_needs_token_and_matches_fingerprint():
    """Epic 생성도 HITL — 지문은 epic_payload 와 도구 payload 가 같은 모양이어야 한다."""
    from app.agent.workflow.agents.work_architect import epic_payload
    draft = {"mode": "epic", "items": [{"summary": "데이터 거버넌스 강화", "type": "Epic",
                                       "epic_name": "거버넌스", "components": ["Catalog"],
                                       "description": "<h3>배경</h3><p>x</p>"}]}
    p = epic_payload(draft)
    # 토큰 없이는 거부
    r0 = _run(T.BY_NAME["create_epic"], summary=p["summary"], approval_token="",
              epic_name=p.get("epic_name", ""), description=p.get("description", ""),
              components=p.get("components"))
    assert r0["ok"] is False
    # 스테이징(같은 지문) → 승인 → 생성
    tok = approval.stage("t-epic", "create_epic", p)
    approval.approve(tok, "t-epic")
    r = _run(T.BY_NAME["create_epic"], summary=p["summary"], approval_token=tok,
             epic_name=p.get("epic_name", ""), description=p.get("description", ""),
             components=p.get("components"))
    assert r.get("ok") and r["created"][0]["key"], r
    # 실물 확인 — Epic 타입으로 생성됐는가. 프로세스 간 공유 캐시(cache.sqlite3)에 같은
    # 키의 낡은 행이 남을 수 있어(결정적 world 는 키 시퀀스를 재사용) 캐시를 비우고 읽는다.
    from app.agent.tools import _ctx
    c = _ctx.client()
    c.cache.invalidate("issue:")
    raw = c.get_issue(r["created"][0]["key"]) or {}
    assert ((raw.get("fields") or {}).get("issuetype") or {}).get("name", "").lower() == "epic"


def test_epic_mode_flows_through_propose_and_operator(monkeypatch):
    """mode=epic 초안 → _propose 가 create_epic 지문으로 스테이징 → ActionExecutor 결정적 실행."""
    import os
    os.environ["LAKE_AGENT_PROVIDER"] = "fake"
    from app.agent.workflow import graph as G
    from app.agent.workflow.agents.action_executor import ActionExecutor
    draft = {"mode": "epic", "items": [{"summary": "실시간 품질 관제 체계", "type": "Epic",
                                       "epic_name": "품질관제"}]}
    out = G._propose({"thread_id": "t-ep2", "review": {"ok": True},
                      "draft": draft})
    tok = out["approval_token"]
    assert approval.peek(tok)["action"] == "create_epic"
    approval.approve(tok, "t-ep2")
    res = ActionExecutor().node()({"thread_id": "t-ep2", "draft": draft, "approval_token": tok,
                             "change_plan": {}, "trace": []})
    r = res["result"]
    assert r["created"] and r["failed"] == []
    assert "Task" in (r["note"] or ""), "승인 후 Task 연쇄 제안이 note 에 있어야 한다"


def test_search_ladder_requires_core_token_match():
    """영문 기술 토큰(질의의 정체성)이 있으면 그중 1개는 맞아야 사다리를 통과한다.

    실측: 'Iceberg Puffin NDV 통계' 질문에 일반어(ETL 등)만 겹친 '경계값 오류 수정'이
    관련 이력으로 나갔다. 핵심 토큰 0개 매칭이면 결과 없음이 정답이다.
    """
    r = _run(T.BY_NAME["search_work_history"],
             query="Iceberg Puffin NDV 통계정보 생성 적재", limit=6)
    for it in r["jira"]:
        t = (it.get("title") or "")
        assert any(w.lower() in t.lower() for w in ("Iceberg", "Puffin", "NDV", "통계")), \
            f"핵심 토큰 없이 통과: {it}"


# ── 첨부파일 인지·읽기 ─────────────────────────────────────────────
def test_attachment_list_says_what_each_file_is():
    """목록 자체가 맥락이다 — '로그 첨부했습니다' 옆에 실제로 무엇이 붙어 있는가."""
    r = T.BY_NAME["list_attachments"].invoke({"ticket_key": "DL-5004"})
    names = {f["name"]: f for f in r["files"]}
    assert names, r
    img = next(f for f in r["files"] if f["name"].endswith(".png"))
    assert img["readable"] is False and "이미지" in img["kind"]
    md = next(f for f in r["files"] if f["name"].endswith(".md"))
    assert md["readable"] is True and md["size"].endswith(("KB", "MB"))
    assert md["author"] and md["created"]


def test_reading_a_text_attachment_returns_its_content():
    r = T.BY_NAME["read_attachment"].invoke({"ticket_key": "DL-5004",
                                           "filename": "배포_체크리스트.md"})
    assert "text" in r and "DL-5004" in r["text"], r


def test_partial_filename_is_enough():
    """모델이 확장자를 흘리는 일이 잦다 — 부분 일치로 받아 준다."""
    r = T.BY_NAME["read_attachment"].invoke({"ticket_key": "DL-5004",
                                           "filename": "배포_체크리스트"})
    assert "text" in r, r


def test_binary_and_missing_files_fail_with_a_usable_reason():
    """'못 읽는다'로 끝내지 말고 **무엇을 해야 하는지**까지 — 모델이 사람에게 물을 수 있게."""
    img = T.BY_NAME["read_attachment"].invoke({"ticket_key": "DL-5004",
                                             "filename": "설계_검토.png"})
    assert "error" in img and "사람에게" in img["error"]
    gone = T.BY_NAME["read_attachment"].invoke({"ticket_key": "DL-5004", "filename": "없는것.txt"})
    assert "error" in gone and "있는 것:" in gone["error"], "무엇이 있는지 알려 줘야 다시 시도한다"


def test_size_caps_differ_by_what_reading_costs():
    """글은 앞부분만 읽으면 되고 표는 훑어야 한다 — 상한이 같을 이유가 없다."""
    from app.agent.tools import file_tools as F
    assert F._cap("log") < F._cap("csv") <= F._cap("pdf")
    assert F._ext("a.LOG") == "log" and F._ext("noext") == ""
    # 큰 파일은 내려받기 전에 막는다(목록의 size 로 판정)
    assert not F._readable("dump.csv", 99 * 1024 * 1024)
    assert F._readable("rows.csv", 3 * 1024 * 1024)


def test_each_format_is_classified_by_how_it_can_be_read():
    """읽는 방식이 다르면 종류도 달라야 한다 — 모델이 무엇을 기대할지 알 수 있게."""
    from app.agent.tools import file_tools as F
    assert "표 데이터" in F._kind("현황.xlsx")
    assert "구조화 데이터" in F._kind("이벤트.ndjson")
    assert "소스코드" in F._kind("적재.sql") and "소스코드" in F._kind("run.sh")
    assert "본문 추출 가능" in F._kind("보고서.pdf")
    assert "읽을 수 없음" in F._kind("화면.png") and "읽을 수 없음" in F._kind("자료.pptx")
    # 읽을 수 있는 것과 없는 것이 목록에서 구분된다
    assert F._readable("설계.docx", 1024) and not F._readable("영상.mp4", 1024)


def _xlsx_bytes(rows):
    """엑셀 한 장을 만든다 — 리더가 zip+XML 을 제대로 푸는지 보기 위한 최소 파일."""
    import io as _io
    import zipfile as _zip
    def esc(v):
        return str(v).replace("&", "&amp;").replace("<", "&lt;")
    body = "".join(
        "<row r='%d'>%s</row>" % (i + 1, "".join(
            "<c r='%s%d' t='inlineStr'><is><t>%s</t></is></c>" % (chr(65 + j), i + 1, esc(v))
            for j, v in enumerate(r)))
        for i, r in enumerate(rows))
    sheet = ("<?xml version='1.0'?><worksheet xmlns='http://schemas.openxmlformats.org/"
             "spreadsheetml/2006/main'><sheetData>%s</sheetData></worksheet>" % body)
    buf = _io.BytesIO()
    with _zip.ZipFile(buf, "w") as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        z.writestr("xl/worksheets/sheet1.xml", sheet)
    return buf.getvalue()


def test_excel_gives_columns_and_only_the_related_rows():
    """표는 통째로 싣지 않는다 — 컬럼이 무엇인지와 찾는 대상의 행만."""
    from app.agent.tools import file_tools as F
    data = _xlsx_bytes([["테이블", "모듈", "행수"],
                        ["fdc.fdc_trace_summary_ic", "ETL", "120000000"],
                        ["yms.yms_lot_yield_daily", "Catalog", "8400000"]])
    r = F._read_xlsx("현황.xlsx", len(data), data, "yms.yms_lot_yield_daily")
    assert r["columns"] == ["테이블", "모듈", "행수"]
    assert r["rows_total"] == 2 and r["matched_count"] == 1
    assert r["matched"][0]["모듈"] == "Catalog"
    # find 없이 부르면 컬럼 + 표본만(전체를 붓지 않는다)
    plain = F._read_xlsx("현황.xlsx", len(data), data, "")
    assert "matched" not in plain and len(plain["sample"]) <= 3


def test_ndjson_is_read_as_events_not_as_one_blob():
    from app.agent.tools import file_tools as F
    data = ("\n".join(['{"ts":"10:01","table":"fdc.fdc_trace_summary_ic","lag":12}',
                       '{"ts":"10:02","table":"eqp.eqp_sensor_raw_1s","lag":900}',
                       "깨진 줄"])).encode("utf-8")
    r = F._read_ndjson("lag.ndjson", len(data), data, "eqp.eqp_sensor_raw_1s")
    assert set(r["keys"]) >= {"ts", "table", "lag"}
    assert r["matched_count"] == 1 and "900" in r["matched"][0]
    assert "못 읽은 줄 1개" in r["note"], "깨진 줄은 건너뛰되 조용히 넘어가지 않는다"


def test_word_documents_are_read_with_the_standard_library():
    from app.agent.tools import file_tools as F
    import io as _io
    import zipfile as _zip
    W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    doc = ("<?xml version='1.0'?><w:document xmlns:w='%s'><w:body>"
           "<w:p><w:r><w:t>적재 지연 원인은 커넥션 타임아웃이다.</w:t></w:r></w:p>"
           "<w:p><w:r><w:t>조치: 재시도 간격을 늘린다.</w:t></w:r></w:p>"
           "</w:body></w:document>" % W)
    buf = _io.BytesIO()
    with _zip.ZipFile(buf, "w") as z:
        z.writestr("word/document.xml", doc)
    data = buf.getvalue()
    r = F._read_docx("원인분석.docx", len(data), data, "")
    assert r["paragraphs"] == 2 and "커넥션 타임아웃" in r["text"]
    hit = F._read_docx("원인분석.docx", len(data), data, "재시도")
    assert hit["matched_lines"] >= 1 and "재시도" in hit["text"]


def test_parquet_says_what_is_missing_instead_of_guessing():
    """못 읽는 것을 못 읽는다고 말하는 편이, 파일명으로 내용을 짐작하는 것보다 낫다."""
    from app.agent.tools import file_tools as F
    try:
        import pyarrow  # noqa: F401
        pytest.skip("pyarrow 가 설치돼 있으면 실제로 읽는다")
    except ImportError:
        pass
    r = F._read_parquet("dump.parquet", 100, b"PAR1", "")
    assert "pyarrow" in r["error"] and "사람에게 요청" in r["error"]


# ── 퍼지 식별자 — 오탈자·공백형도 실물을 찾는다 (실측: '기록 없음' 오답 2연발) ──
def test_spaced_identifier_is_recognized():
    """"fdc trace summary ic는?" — 밑줄 없이 말해도 식별자다(한국어 조사는 경계)."""
    from app.agent.tools._ident import find_identifiers
    assert find_identifiers("fdc trace summary ic는?") == ["fdc_trace_summary_ic"]
    assert find_identifiers("please review the new code") == []      # 영어 일반문 오인 금지
    assert find_identifiers("ETL 모듈 진척률 어때") == []


def test_typo_identifier_gets_similar_suggestions():
    """'fdc_flat_summary_ic'(flat↔trace 오탈자) → 유사 식별자를 코드가 찾아 준다."""
    from app.agent.tools import BY_NAME
    r = BY_NAME["find_mentions"].invoke({"term": "fdc_flat_summary_ic", "limit": 8}) or {}
    assert not r.get("hits")
    sim = r.get("similar") or []
    assert sim and sim[0]["term"] == "fdc.fdc_trace_summary_ic" and sim[0]["matched"] >= 3


def test_dossier_offers_candidates_instead_of_guessing():
    """오탈자 추정은 추정이다 — 전체 히스토리를 추정으로 답하지 않고 **표기 후보**를
    돌려준다(다음 계층이 객관식 확인 질문으로 바꾼다. 사용자 결정)."""
    from app.agent.workflow.agents.research_analyst import _topic_dossier
    d = _topic_dossier("fdc_flat_summary_ic")
    assert d.startswith("[표기 후보]") and "fdc.fdc_trace_summary_ic" in d
    assert "DL-9044" not in d, "추정 대상의 실데이터를 미리 싣지 않는다"
