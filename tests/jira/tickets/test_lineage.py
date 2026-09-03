"""계보 스파인 API — 조상(ticket_ancestors) / 형제(ticket_siblings).

핵심: 조상·형제 조회는 티켓단위 캐시(issue:{env}:{key}, epic_children)를 재사용하고
조립 결과도 개별 캐시된다. 좌측 스파인 패널이 이 두 API 로 그려진다.
"""
import os
import sys

import pytest

from app.auth.base import SessionExpired
from app.infra.cache import Cache            # noqa: E402
from app.jira.jira_client import JiraClient  # noqa: E402
from app.infra.settings import get_settings   # noqa: E402
from app.mock.world import get_world         # noqa: E402


def _client():
    return JiraClient(get_settings(), Cache(":memory:"))


def _subtask_chain():
    """parent 가 있고 그 parent 가 Epic Link 를 가진 Sub-Task → (sub, parent, epic)."""
    w = get_world()
    for k, it in w.issues.items():
        p = it.get("parentKey")
        if p and w.issues.get(p, {}).get("epicKey"):
            return k, p, w.issues[p]["epicKey"]
    raise AssertionError("no subtask→parent→epic chain in world")


def _key_of_type(t):
    for k, it in get_world().issues.items():
        if it["type"] == t:
            return k
    raise AssertionError("no issue of type " + t)


# ── 조상 ──
def test_ancestors_subtask_is_epic_then_parent():
    sub, parent, epic = _subtask_chain()
    anc = _client().ticket_ancestors(sub)
    assert [a["key"] for a in anc] == [epic, parent]      # 위→아래
    for a in anc:
        assert a["summary"] and a["type"]
        assert a["statusCategory"] in ("todo", "inprogress", "done")


def test_ancestors_carry_progress_pct():
    """스파인 레일에 조상별 진척 바를 그리므로 pct 가 실려야 한다."""
    sub, _parent, _epic = _subtask_chain()
    anc = _client().ticket_ancestors(sub)
    assert anc and all(isinstance(a["pct"], int) for a in anc)
    assert all(0 <= a["pct"] <= 100 for a in anc)


def test_ancestors_epic_has_none():
    assert _client().ticket_ancestors(_key_of_type("Epic")) == []


# ── 형제 ──
def test_siblings_of_subtask_are_parent_subtasks_including_self():
    sub, parent, _epic = _subtask_chain()
    sibs = _client().ticket_siblings(sub)
    assert {s["key"] for s in sibs} == set(get_world().issues[parent]["subtasks"])
    cur = [s for s in sibs if s["current"]]
    assert len(cur) == 1 and cur[0]["key"] == sub        # 현재 티켓이 정확히 하나 표시


def test_siblings_of_task_are_epic_children():
    w = get_world()
    task = next(k for k, it in w.issues.items()
                if it.get("epicKey") and not it.get("parentKey") and it.get("type") != "Epic")
    sibs = _client().ticket_siblings(task)
    assert task in {s["key"] for s in sibs}
    assert all(s["summary"] and s["statusCategory"] in ("todo", "inprogress", "done") for s in sibs)
    assert sum(1 for s in sibs if s["current"]) == 1


def test_siblings_of_epic_empty():
    assert _client().ticket_siblings(_key_of_type("Epic")) == []


# ── 캐시 ──
def test_lineage_reuses_per_ticket_cache():
    """조상 각각이 티켓단위 캐시(전체 issue: 또는 경량 issueL:)로 개별 캐시되고, 조립 결과도 캐시된다.
    (뱃지는 경량 조회라 issueL: 에 담긴다 — 어느 쪽이든 '개별 캐시됨' 이면 된다.)"""
    sub, parent, epic = _subtask_chain()
    c = _client()
    env = c.env

    def cached(key):
        return (c.cache.get(f"issue:{env}:{key}") is not None
                or c.cache.get(f"issueL:{env}:{key}") is not None)

    assert not cached(epic)
    c.ticket_ancestors(sub)
    assert cached(epic)                                       # 조상 개별 캐시(전체 또는 경량)
    assert cached(parent)
    assert c.cache.get(f"ancestors:{env}:{sub}") is not None   # 조립 결과 캐시
    c.ticket_siblings(sub)
    # 형제 목록은 **그룹(부모)별 공유 캐시** — 각 티켓별이 아니라 부모 키로 캐시된다(형제 전원 공유).
    assert c.cache.get(f"siblings:{env}:sub:{parent}") is not None


@pytest.mark.parametrize("message", [
    "HTTP 401 session expired",
    "HTTP 403 on /rest/api/2/issue/DL-1 — 세션 만료 가능. login 재실행.",
])
def test_transient_ancestor_failure_is_not_cached_as_empty(monkeypatch, message):
    """A cold auth/transport failure must stay retryable, not become a 15-minute empty lineage."""
    c = _client()
    key = "DL-AUTH-FAIL"
    calls = {"n": 0}

    def fail(_key, **_kwargs):
        calls["n"] += 1
        raise SessionExpired(message)

    monkeypatch.setattr(c, "get_issue", fail)
    for expected_calls in (1, 2):
        with pytest.raises(SessionExpired):
            c.ticket_ancestors(key)
        assert calls["n"] == expected_calls
        assert c.cache.get_stale(f"ancestors:{c.env}:{key}") is None


def test_expired_issue_fallback_cannot_be_promoted_into_fresh_ancestry(monkeypatch):
    """Derived ancestry uses strict refresh even though ordinary issue views may serve stale."""
    c = _client()
    key = _key_of_type("Epic")                  # legitimate result would be an empty list
    raw = c.get_issue(key)
    c.cache.set(f"issue:{c.env}:{key}", raw, -1)

    def expired(*_args, **_kwargs):
        raise SessionExpired("HTTP 401 session expired")

    monkeypatch.setattr(c.provider, "get_json", expired)
    with pytest.raises(SessionExpired):
        c.ticket_ancestors(key)
    assert c.cache.get_stale(f"ancestors:{c.env}:{key}") is None


def test_permission_denied_ancestor_is_best_effort_but_not_cached(monkeypatch):
    """Per-ticket visibility failures stay silent and retryable instead of poisoning ancestry."""
    c = _client()
    key = "DL-HIDDEN"
    calls = {"n": 0}

    def denied(_key, **_kwargs):
        calls["n"] += 1
        raise PermissionError("403 forbidden")

    monkeypatch.setattr(c, "get_issue", denied)
    assert c.ticket_ancestors(key) == []
    assert c.ticket_ancestors(key) == []
    assert calls["n"] == 2
    assert c.cache.get_stale(f"ancestors:{c.env}:{key}") is None


def test_warm_ancestor_structure_projects_latest_targeted_epic_metadata():
    """An Epic rename need not evict every child's cached ancestry to show the new label."""
    sub, _parent, epic = _subtask_chain()
    c = _client()
    first = c.ticket_ancestors(sub)
    assert c.cache.get(f"ancestors:{c.env}:{sub}") is not None
    epic_node = next(node for node in first if node.get("key") == epic)

    cache_key = f"epicmeta:{c.env}:{epic}"
    changed = dict(c.cache.get(cache_key))
    changed["title"] = "새 Epic 이름"
    changed["epicName"] = "새 Epic 이름"
    c.cache.set(cache_key, changed, c.EPIC_META_TTL)

    second = c.ticket_ancestors(sub)
    updated = next(node for node in second if node.get("key") == epic)
    assert updated["epicName"] == "새 Epic 이름"
    assert updated["epicName"] != epic_node["epicName"]


def test_sibling_cache_is_shared_by_parent_and_invalidated_together():
    """형제 목록은 부모별 **공유 캐시**(각 티켓이 제 형제목록을 따로 들지 않는다). 그래서 형제 하나가
    바뀌면 부모 그룹 캐시 하나만 비워도 **형제 전원의 뷰가 갱신**된다(LCA 부모의 child 정보로 공유)."""
    sub, parent, _epic = _subtask_chain()
    c = _client()
    env = c.env
    c.ticket_siblings(sub)
    gkey = f"siblings:{env}:sub:{parent}"
    assert c.cache.get(gkey) is not None
    # '현재' 표시만 티켓별로 다르고, 다른 형제를 조회해도 **같은 그룹 캐시**를 재사용한다
    other = next(k for k in get_world().issues[parent]["subtasks"] if k != sub)
    sibs_other = c.ticket_siblings(other)
    assert sum(1 for s in sibs_other if s["current"]) == 1 and next(s for s in sibs_other if s["current"])["key"] == other
    # 형제 하나(other)를 무효화하면 부모 그룹 캐시가 비워진다 → 형제 전원 갱신
    c._invalidate_ticket(other)
    assert c.cache.get(gkey) is None
    # 부모의 진척·하위목록 캐시도 함께 비워진다(하위 상태변경이 부모 진척을 바꾸므로)
    assert c.cache.get(f"issueview:{env}:{parent}") is None


# ── 타임라인 ──
def test_timeline_has_created_and_status_and_comment():
    """중요 이벤트(생성/상태/댓글)가 최신순으로 나온다."""
    c = _client()
    w = get_world()
    key = next(k for k, it in w.issues.items() if it.get("resolved") and it.get("comments"))
    tl = c.ticket_timeline(key)
    kinds = {e["kind"] for e in tl}
    assert "created" in kinds and "status" in kinds and "comment" in kinds
    dates = [e["date"] for e in tl if e.get("date")]
    assert dates == sorted(dates, reverse=True)          # 최신순
    for e in tl:
        assert "author" in e and "date" in e


def test_timeline_excludes_description_edits():
    """단순 설명 수정은 제외 — world 에 일부러 description 변경 이력을 심어두었다."""
    c = _client()
    w = get_world()
    # description 변경 이력이 실제로 있는 티켓을 고른다(필터가 무의미해지지 않도록)
    key = next(k for k, it in w.issues.items()
               if any(i.get("field") == "description"
                      for ch in it.get("changelog", []) for i in ch["items"]))
    tl = c.ticket_timeline(key)
    assert tl, "타임라인이 비어 필터 검증 불가"
    assert all((e.get("field") or "").lower() != "description" for e in tl)
    assert "description" not in {e["kind"] for e in tl}


def test_timeline_cached():
    c = _client()
    key = _key_of_type("Bug")
    c.ticket_timeline(key)
    assert c.cache.get(f"timeline:{c.env}:{key}:self:v2") is not None
    assert c.cache.get(f"timeline:{c.env}:{key}:children:v2") is not None


def test_subtaskless_timeline_can_defer_cold_changelog_without_blocking(monkeypatch):
    """SubTask가 없어도 본인 changelog가 멈출 수 있으므로 cold 타임라인은 background로 넘긴다."""
    c = _client()
    key = next(k for k, it in get_world().issues.items() if not it.get("subtasks"))
    scheduled = []

    def schedule(cache_key, ttl, producer):
        scheduled.append((cache_key, ttl, producer))

    status_calls = []
    monkeypatch.setattr(c, "_status_cats", lambda: status_calls.append(True) or {})
    monkeypatch.setattr(c, "_refresh_bg", schedule)
    assert c.ticket_timeline(key, defer=True) is None
    assert status_calls == []                 # 202를 돌려주기 전에 Jira status 조회도 하지 않는다
    assert len(scheduled) == 1
    assert scheduled[0][0] == f"timeline:{c.env}:{key}:self:v2"
    assert callable(scheduled[0][2])


def test_timeline_route_marks_deferred_work_as_background(monkeypatch):
    """다이얼로그용 202 응답은 즉시 반환되고 모든 Jira 후속조회는 background priority다."""
    from fastapi.testclient import TestClient
    from app import main
    from app.auth.base import PRIO_BACKGROUND, upstream_priority

    seen = []

    class Client:
        def ticket_timeline(self, key, defer=False, include_children=True):
            seen.append((key, defer, include_children, upstream_priority()))
            return None

    monkeypatch.setattr(main, "_client", Client())
    response = TestClient(main.app).get("/api/ticket/DL-1/timeline?deferred=1&children=0")
    assert response.status_code == 202
    assert response.json() == {"pending": True}
    assert seen == [("DL-1", True, False, PRIO_BACKGROUND)]


def test_timeline_first_tier_never_collects_child_history(monkeypatch):
    """다이얼로그 최초 타임라인은 하위 이력 조회를 예약조차 하지 않는다."""
    c = _client()
    key = "DL-9100"                        # 직계 Sub-Task 14개 fixture
    monkeypatch.setattr(c, "_child_keys", lambda *_a, **_k: (_ for _ in ()).throw(
        AssertionError("1티어에서 child key를 읽으면 안 된다")))

    timeline = c.ticket_timeline(key, include_children=False)

    assert timeline
    assert all(e.get("srcKey") is None for e in timeline)
    assert c.cache.get(f"timeline:{c.env}:{key}:children:v2") is None


def test_second_tier_is_separately_deferred_and_merged(monkeypatch):
    """버튼 요청 뒤에만 2티어를 예약하고 완료 후 본인 이력과 최신순으로 합친다."""
    c = _client()
    key = "DL-9100"
    own = c.ticket_timeline(key, include_children=False)
    scheduled = []
    monkeypatch.setattr(c, "_refresh_bg", lambda cache_key, ttl, producer:
                        scheduled.append((cache_key, ttl, producer)))

    assert c.ticket_timeline(key, defer=True, include_children=True) is None
    assert [row[0] for row in scheduled] == [f"timeline:{c.env}:{key}:children:v2"]

    cache_key, ttl, producer = scheduled[0]
    c.cache.set(cache_key, producer(), ttl)
    merged = c.ticket_timeline(key, defer=True, include_children=True)
    assert any(e.get("srcKey") for e in merged)
    assert {e.get("srcKey") for e in merged if e.get("srcKey")} <= set(
        get_world().issues[key]["subtasks"])
    assert any(e.get("srcKey") is None for e in merged)
    assert [e.get("date") for e in merged] == sorted(
        [e.get("date") for e in merged], reverse=True)
    assert len(own) <= len(merged)


def test_delayed_second_tier_yields_to_foreground_requests(monkeypatch):
    """고의 지연 + SubTask 14개에서도 2티어가 일반 조회를 여러 왕복 동안 막지 않는다."""
    import time
    from app.auth import inprocess

    c = _client()
    key = "DL-9100"
    c.ticket_timeline(key, include_children=False)       # 최초 1티어는 먼저 완료된 상태
    monkeypatch.setattr(inprocess, "_LAT_MS", 60)       # prod 직렬·원격 체감 재현 옵션

    assert c.ticket_timeline(key, defer=True, include_children=True) is None
    time.sleep(0.02)                                    # 첫 background 왕복이 실행되게 둔다
    started = time.perf_counter()
    c.get_issue_light("DL-9092")                        # 타임라인과 무관한 사용자 조회
    elapsed = time.perf_counter() - started

    # 실행 중이던 한 왕복 + 내 한 왕복은 기다릴 수 있지만, 14개 전체 뒤로 밀리면 실패다.
    assert elapsed < 0.30, f"foreground request starved for {elapsed:.3f}s"
    child_key = f"timeline:{c.env}:{key}:children:v2"
    deadline = time.time() + 4
    while c.cache.get(child_key) is None and time.time() < deadline:
        time.sleep(0.03)
    assert c.cache.get(child_key) is not None
    merged = c.ticket_timeline(key, defer=True, include_children=True)
    assert len({e.get("srcKey") for e in merged if e.get("srcKey")}) == 14


def test_timeline_includes_child_status_changes():
    """자손(하위 티켓)의 상태 변경도 srcKey 로 출처를 달고 합류한다."""
    c = _client()
    w = get_world()
    # 자식이 있고 그 자식이 착수/완료된(=상태 이력이 있는) 부모를 고른다
    parent = next(k for k, it in w.issues.items()
                  if it.get("subtasks")
                  and any(w.issues[s]["statusCategory"] != "todo" for s in it["subtasks"]))
    tl = c.ticket_timeline(parent)
    child_ev = [e for e in tl if e.get("srcKey")]
    assert child_ev, "자손 상태 변경이 타임라인에 없음"
    assert all(e["kind"] == "child-status" for e in child_ev)      # 자손은 상태 변경만
    assert {e["srcKey"] for e in child_ev} <= set(w.issues[parent]["subtasks"])
    assert all(e.get("from") and e.get("to") for e in child_ev)
    # 본인 이벤트는 srcKey 가 없다
    assert any(e.get("srcKey") is None for e in tl)


def test_timeline_child_changelog_is_cached_per_ticket():
    """자손 스캔이 티켓단위 changelog 캐시를 남겨, 그 자식을 열 때 재조회하지 않는다."""
    c = _client()
    w = get_world()
    parent = next(k for k, it in w.issues.items() if it.get("subtasks"))
    child = w.issues[parent]["subtasks"][0]
    assert c.cache.get(f"changelog:{c.env}:{child}") is None
    c.ticket_timeline(parent)
    assert c.cache.get(f"changelog:{c.env}:{child}") is not None


# ── VIT 모듈 분할 (병렬 로딩용) ──
def test_vit_module_matches_full_build():
    """모듈별 조립이 전체 조립의 해당 모듈과 동일해야 한다."""
    from app.domain import vit
    from app.infra.settings import load_people, load_plan
    c = _client()
    plan, people = load_plan(), load_people()
    full = vit.build_vit(c, plan, people)
    shell = vit.build_vit_shell(c, plan, people)
    assert [m["module"] for m in shell["modules"]] == [m["module"] for m in full["modules"]]
    assert shell["summary"]["total"] == full["summary"]["total"]
    for fm in full["modules"]:
        part = vit.build_vit_module(c, plan, people, fm["module"])
        assert [i["key"] for i in part["issues"]] == [i["key"] for i in fm["issues"]], fm["module"]


# ── 관련문서 중복 제거 ──
def test_conf_key_dedups_same_document():
    """같은 Confluence 페이지를 가리키는 서로 다른 URL 형태가 한 키로 묶여야 한다."""
    from app.jira.jira_client import _conf_key
    same = [
        "https://conf/spaces/DL/pages/12345/설계-노트",
        "https://conf/spaces/DL/pages/12345/설계-노트/",           # 끝 슬래시
        "https://conf/spaces/DL/pages/12345/제목이-바뀜?src=nav",   # 제목 변경 + 쿼리
        "https://conf/pages/viewpage.action?pageId=12345#s1",      # 구형 URL + 앵커
    ]
    assert len({_conf_key(u) for u in same}) == 1
    # display 형태는 space+제목으로(대소문자·'+' 정규화)
    assert _conf_key("https://c/display/DL/설정+가이드") == _conf_key("https://c/display/dl/설정 가이드/")
    # 다른 문서는 달라야 한다
    assert _conf_key("https://conf/spaces/DL/pages/999/x") != _conf_key(same[0])


def test_documents_are_deduped():
    c = _client()
    w = get_world()
    for k in list(w.issues)[:40]:
        docs = c.ticket_documents(k)
        if len(docs) < 2:
            continue
        from app.jira.jira_client import _conf_key
        keys = [_conf_key(d["url"]) for d in docs]
        assert len(keys) == len(set(keys)), f"{k}: 중복 문서 {keys}"
        assert all(d["title"] and d["url"] for d in docs)
        return


# ── 링크 관계 라벨 / Confluence 편집(초안) URL ──
def test_rel_label_shortens_verbose_jira_text():
    """사내 Jira 의 서술형 문구를 짧은 표준어로 — prod 피드백."""
    from app.jira.jira_client import _rel_label
    verbose = {"name": "Blocks",
               "outward": "Linked issue cannot finish until this issue finishes",
               "inward": "This issue cannot finish until linked issue finishes"}
    assert _rel_label(verbose, True) == "blocks"
    assert _rel_label(verbose, False) == "is blocked by"
    # 매핑에 없는 타입인데 문구가 길면 타입 이름으로 대체
    unknown = {"name": "Escalates", "outward": "a" * 40, "inward": "b" * 40}
    assert _rel_label(unknown, True) == "Escalates"
    # 짧은 문구는 그대로
    ok = {"name": "Custom", "outward": "supersedes", "inward": "is superseded by"}
    assert _rel_label(ok, True) == "supersedes"


def test_dialog_related_tasks_only_uses_explicit_issue_links():
    """본문의 Jira URL은 관련 Task 관계가 아니다. 다이얼로그에서는 명시적 issuelink만 보인다."""
    c = _client()
    linked = c.ticket_related("DL-9004", include_mentions=False)
    discovered = c.ticket_related("DL-9004", include_mentions=True)

    assert {r["key"] for r in linked} == {"DL-9005", "DL-9006", "DL-9001"}
    assert all(r["via"] == "link" for r in linked)
    assert "DL-5005" not in {r["key"] for r in linked}
    assert any(r["key"] == "DL-5005" and r["via"] == "mention" for r in discovered)


def test_conf_draft_url_title_and_key():
    """편집(초안) 모드 URL 은 제목이 없어 링크 텍스트로 폴백하고, draftId 로 중복 판정."""
    from app.jira.jira_client import _conf_key, _conf_title
    u = "https://conf/pages/resumedraft.action?draftId=98765&draftShareId=abc-def"
    assert _conf_title(u, "<b>배포 계획서</b>") == "배포 계획서"
    assert _conf_title(u, None) == "Confluence 문서"          # 텍스트도 없으면 기본값
    assert _conf_key(u) == "draft:98765"
    assert _conf_key(u) == _conf_key(u + "&extra=1")          # 부가 쿼리는 무시
    # URL 에 슬러그가 있으면 슬러그 우선(링크 텍스트보다 정확)
    assert _conf_title("https://c/spaces/DL/pages/1/설계-노트", "다른 텍스트") == "설계-노트"


def test_voc_ticket_gets_virtual_lineage_node():
    """VoC 는 Epic/부모에 안 붙는 경우가 많아 계보가 통째로 비어 버린다.

    실 티켓은 아니지만 '어디 소속인지' 는 보여주는 게 낫다 → 가상 상위 노드.
    virtual=True + key=None 이라 프론트가 클릭 대상에서 뺀다.
    """
    from app.mock.world import get_world
    w = get_world()
    key = next(k for k, i in w.issues.items()
               if i.get("component") == "사용자 VoC"
               and not i.get("epicKey") and not i.get("parentKey"))
    c = _client()
    anc = c.ticket_ancestors(key)
    assert len(anc) == 1
    assert anc[0]["summary"] == "사용자 VoC"
    assert anc[0]["virtual"] is True and anc[0]["key"] is None


def test_epic_lineage_unaffected_by_voc_rule():
    """Epic 에 속한 티켓은 기존 계보 그대로(가상 노드가 끼면 안 된다)."""
    from app.mock.world import get_world
    w = get_world()
    key = next(k for k, i in w.issues.items()
               if i.get("epicKey") and i.get("component") != "사용자 VoC")
    anc = _client().ticket_ancestors(key)
    assert anc and all(not n.get("virtual") for n in anc)


def test_unlinked_task_gets_virtual_epic_none_lineage_node():
    """Epic 미연결 Task도 계보 영역을 숨기지 않고 누락 상태를 명시한다."""
    w = get_world()
    key = next(k for k, i in w.issues.items()
               if i.get("type") != "Epic" and not i.get("parentKey")
               and not i.get("epicKey") and i.get("component") != "사용자 VoC")
    anc = _client().ticket_ancestors(key)
    assert anc[0]["summary"] == "Epic 없음"
    assert anc[0]["type"] == "Epic"
    assert anc[0]["virtual"] is True and anc[0]["key"] is None


def test_relative_confluence_url_is_absolutized():
    """사내 본문에는 Confluence 링크가 '/display/DL/문서' 처럼 상대경로로 들어오기도 한다.

    그대로 두면 브라우저가 우리 앱(localhost) 기준으로 해석해 404 로 가고,
    같은 호스트로 보이니 run.py 외부링크 훅도 안 타서 시스템 브라우저가 아예 안 뜬다.
    """
    from app.jira.jira_client import _abs_url

    B = "https://confluence.corp.example"
    assert _abs_url("/display/DL/문서", B) == B + "/display/DL/문서"
    assert _abs_url("/pages/viewpage.action?pageId=1", B) == B + "/pages/viewpage.action?pageId=1"
    # 이미 절대 URL·프로토콜상대·앵커는 손대지 않는다
    assert _abs_url("https://x.example/a", B) == "https://x.example/a"
    assert _abs_url("//cdn/x", B) == "//cdn/x"
    assert _abs_url("#a", B) == "#a"
    # base 미설정이면 그대로(설정 누락을 조용히 감추지 않는다)
    assert _abs_url("/display/DL/문서", "") == "/display/DL/문서"


def test_documents_merge_remote_links_and_dedupe():
    """관련 문서 = 본문 언급 + Jira remote link, URL 기준 중복 제거.

    remote link 는 티켓뿐 아니라 Confluence 문서·Web link 도 가리킨다.
    본문에 이미 언급된 Confluence 문서가 remote link 로도 걸려 있으면 한 번만 나와야 하고,
    remote link 로만 있는 Web link 는 새로 추가돼야 한다.
    """
    from app.mock.world import get_world
    w = get_world()
    key = next(k for k, i in w.issues.items() if i.get("remotelinks"))
    docs = _client().ticket_documents(key)
    urls = [d["url"] for d in docs]
    # 같은 URL 이 두 번 나오지 않는다
    assert len(urls) == len(set(urls))
    # Web link(비 Confluence)도 포함된다
    assert any("wiki.corp.example" in u for u in urls)


def test_get_issue_light_prefers_fresher_of_full_and_light():
    """get_issue_light 는 전체(issue:)·경량(issueL:) 중 **TTL 이내이면서 더 최근에 받아온** 쪽을 쓴다.
    전체는 경량의 상위집합이라 내용은 늘 충분 — 관건은 최신성이다."""
    import time
    c = _client()
    env, key, ttl = c.env, "DL-9001", c.s.cache_ttl_seconds
    fk, lk = f"issue:{env}:{key}", f"issueL:{env}:{key}"
    # 전체를 먼저(오래), 경량을 나중(최신) → 경량이 선택돼야
    c.cache.set(fk, {"key": key, "fields": {"summary": "OLD-FULL"}}, ttl)
    time.sleep(0.02)
    c.cache.set(lk, {"key": key, "fields": {"summary": "NEW-LIGHT"}}, ttl)
    assert c.get_issue_light(key)["fields"]["summary"] == "NEW-LIGHT"
    # 반대로 전체가 더 최신이면 전체
    c.cache.set(lk, {"key": key, "fields": {"summary": "OLD-LIGHT"}}, ttl)
    time.sleep(0.02)
    c.cache.set(fk, {"key": key, "fields": {"summary": "NEW-FULL"}}, ttl)
    assert c.get_issue_light(key)["fields"]["summary"] == "NEW-FULL"
