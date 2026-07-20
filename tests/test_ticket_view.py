"""티켓 상세 뷰(ticket_view) — 순수 빌더 + mock 통합.

핵심: description(HTML)이 **정화**된 채로 나오고, 리치 요소(table/code/blockquote/callout/image)가
mock(jira820 renderedFields) 경유로 실제 렌더된다.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.cache import Cache            # noqa: E402
from app.jira_client import JiraClient, _build_ticket_view   # noqa: E402
from app.settings import get_settings   # noqa: E402
from app.world import get_world         # noqa: E402


# ── 순수 빌더: renderedFields(HTML) 우선 + 정화 ──
def test_builder_prefers_rendered_html_and_sanitizes():
    raw = {
        "key": "DL-777",
        "fields": {
            "summary": "제목", "description": "*wiki* 원본",
            "issuetype": {"name": "Bug", "subtask": False},
            "status": {"name": "Open", "statusCategory": {"key": "new"}},
            "assignee": {"displayName": "김핀 SK"}, "reporter": {"displayName": "이보고 SK"},
            "components": [{"name": "ETL"}], "labels": ["x"],
        },
        "renderedFields": {
            "description": '<p>본문</p><script>alert(1)</script>'
                           '<a href="javascript:alert(1)">bad</a>'
                           '<div class="callout callout-warning evil">경고</div>',
        },
    }
    v = _build_ticket_view(raw, "customfield_10002", jira_base="https://jira.example")
    assert v["descriptionFormat"] == "html"
    assert "<script" not in v["descriptionHtml"].lower()
    assert "alert(1)" not in v["descriptionHtml"] or "&lt;" in v["descriptionHtml"]
    assert "javascript:" not in v["descriptionHtml"].lower()
    assert 'class="callout callout-warning"' in v["descriptionHtml"]   # 허용 클래스만
    assert "evil" not in v["descriptionHtml"]
    assert v["key"] == "DL-777" and v["type"] == "Bug"
    assert v["url"] == "https://jira.example/browse/DL-777"


def test_builder_falls_back_to_plaintext_when_no_rendered():
    raw = {"key": "DL-1", "fields": {
        "summary": "s", "description": "줄1\n줄2 <b>x</b>",
        "issuetype": {"name": "Task"}, "status": {"name": "Open", "statusCategory": {"key": "new"}}}}
    v = _build_ticket_view(raw, "customfield_10002")
    assert v["descriptionFormat"] == "text"
    assert "<br>" in v["descriptionHtml"]           # 줄바꿈 보존
    assert "&lt;b&gt;" in v["descriptionHtml"]      # 원시 태그 escape
    assert "<b>" not in v["descriptionHtml"]


# ── mock 통합: jira820 renderedFields 로 리치 요소가 실제 렌더 + 정화 ──
def _client():
    return JiraClient(get_settings(), Cache(":memory:"))


def _key_of_type(t):
    for k, it in get_world().issues.items():
        if it["type"] == t:
            return k
    raise AssertionError("no issue of type " + t)


def test_mock_bug_description_has_rich_elements():
    v = _client().ticket_view(_key_of_type("Bug"))
    assert v is not None
    html = v["descriptionHtml"]
    assert v["descriptionFormat"] == "html"
    assert "<table>" in html and "<th>" in html           # 표
    assert '<pre class="code">' in html and "<code" in html  # 코드블록
    assert "callout callout-warning" in html              # 콜아웃
    assert '<img src="/ticket-sample.svg"' in html        # 이미지(오프라인)
    assert "<ol>" in html                                  # 번호 목록(재현 절차)
    assert 'class="conf-link"' in html                     # Confluence 링크 뱃지(런북, URL 판별)
    assert 'class="user-hover"' in html                    # 맨션(실 Jira 프로필 앵커)
    assert "<script" not in html.lower()                  # 정화됨


def test_mock_story_description_has_quote_and_code():
    v = _client().ticket_view(_key_of_type("Story"))
    html = v["descriptionHtml"]
    assert "<blockquote>" in html                          # 인용
    assert '<pre class="code">' in html                    # 코드
    assert '<img src="/ticket-sample.svg"' in html
    assert "<script" not in html.lower()


def test_mock_epic_has_panel_and_table_and_safe_link():
    v = _client().ticket_view(_key_of_type("Epic"))
    html = v["descriptionHtml"]
    assert '<div class="panel">' in html and "panel-title" in html
    assert "<table>" in html
    # 링크는 정화기가 rel/target 강제
    assert 'rel="noopener noreferrer nofollow"' in html and 'target="_blank"' in html


def test_mock_comments_render_sanitized_html_with_badges():
    """코멘트도 renderedBody→정화 HTML. 맨션([~user])·Confluence 링크가 뱃지로 나온다."""
    c = _client()
    seen_html = seen_mention = seen_conf = False
    for k in list(get_world().issues.keys())[:80]:
        for cm in c.issue_comments(k, limit=10):
            assert set(cm.keys()) >= {"date", "author", "html"}
            assert "<script" not in cm["html"].lower()
            if "<" in cm["html"]:
                seen_html = True
            if 'class="user-hover"' in cm["html"]:
                seen_mention = True
            if 'class="conf-link"' in cm["html"]:
                seen_conf = True
        if seen_html and seen_mention and seen_conf:
            break
    assert seen_html, "코멘트가 HTML 로 렌더되지 않음"
    assert seen_mention, "맨션 뱃지 미검출"
    assert seen_conf, "Confluence 링크 뱃지 미검출"


def test_media_host_allow_and_ssrf_block():
    c = _client()   # mock: jira_base=http://localhost:8080 → host 'localhost'
    assert c._media_allowed_host("localhost")
    assert not c._media_allowed_host("evil.example")
    # fetch_media 는 허용 안 된 호스트를 즉시 차단(SSRF 방지)
    assert c.fetch_media("https://evil.example/x.png") == (None, None)
    assert c.fetch_media("") == (None, None)
    assert c.fetch_media("ftp://x/y") == (None, None)


def test_mock_does_not_proxy_images():
    # mock/local 은 same-origin static → 프록시 재작성 안 함(_proxy_media no-op)
    v = _client().ticket_view(_key_of_type("Bug"))
    assert "/api/img?u=" not in v["descriptionHtml"]
    assert '<img src="/ticket-sample.svg"' in v["descriptionHtml"]


def test_prod_proxy_media_rewrites_images():
    # prod 분기: description/코멘트의 <img> 가 /api/img 프록시로 재작성돼야 함
    c = _client()
    c.env = "prod"
    try:
        out = c._proxy_media('<p><img src="/secure/attachment/9/x.png" alt="" /></p>')
        assert "/api/img?u=" in out and "secure%2Fattachment" in out
    finally:
        c.env = "mock"


def test_api_img_rejects_disallowed_host():
    from fastapi.testclient import TestClient

    from app.main import app
    r = TestClient(app).get("/api/img", params={"u": "https://evil.example/x.png"})
    assert r.status_code == 404


def test_ticket_badge_light():
    c = _client()
    b = c.ticket_badge(_key_of_type("Bug"))
    assert b and b["key"] and b["summary"] and b["type"]
    assert b["statusCategory"] in ("todo", "inprogress", "done")
    assert "assignee" in b
    assert c.ticket_badge("DL-999999") is None


def test_ticket_view_none_for_missing():
    assert _client().ticket_view("DL-999999") is None


def test_ticket_view_fields_populated():
    v = _client().ticket_view(_key_of_type("Bug"))
    assert v["key"] and v["summary"] and v["status"]
    assert v["statusCategory"] in ("todo", "inprogress", "done")
    assert isinstance(v["labels"], list) and isinstance(v["components"], list)


# ── 조상(ancestors) / 자손(descendants) + 개별 캐시 ──
def _subtask_with_epic():
    """parent 가 있고 그 parent 가 Epic Link 를 가진 Sub-Task 키 → (sub, parent, epic)."""
    w = get_world()
    for k, it in w.issues.items():
        p = it.get("parentKey")
        if p and w.issues.get(p, {}).get("epicKey"):
            return k, p, w.issues[p]["epicKey"]
    raise AssertionError("no subtask→parent→epic chain in world")


def test_ancestors_subtask_chain_is_epic_then_parent():
    sub, parent, epic = _subtask_with_epic()
    anc = _client().ticket_ancestors(sub)
    assert [a["key"] for a in anc] == [epic, parent]        # 위→아래: [epic(조부모), parent]
    for a in anc:
        assert a["key"] and a["summary"] and a["type"]
        assert a["statusCategory"] in ("todo", "inprogress", "done")


def test_ancestors_story_is_its_epic_only():
    w = get_world()
    key = next(k for k, it in w.issues.items()
               if it.get("epicKey") and not it.get("parentKey") and it.get("type") != "Epic")
    assert [a["key"] for a in _client().ticket_ancestors(key)] == [w.issues[key]["epicKey"]]


def test_ancestors_epic_is_empty():
    assert _client().ticket_ancestors(_key_of_type("Epic")) == []


def test_descendants_epic_has_two_levels():
    d = _client().ticket_descendants(_key_of_type("Epic"))
    assert d, "epic 자손 없음"
    n0 = d[0]
    assert n0["key"] and n0["summary"] and n0["type"]
    assert n0["statusCat"] in ("todo", "inprogress", "done")
    subs = [s for n in d for s in (n.get("children") or [])]
    assert subs and all(s["key"] and s["summary"] for s in subs)   # 2단계(자식→Sub-Task)


def test_descendants_task_returns_its_subtasks():
    w = get_world()
    task = next(k for k, it in w.issues.items() if it.get("subtasks"))
    d = _client().ticket_descendants(task)
    assert {n["key"] for n in d} == set(w.issues[task]["subtasks"])
    assert all(not n.get("children") for n in d)                   # Sub-Task 는 leaf


def test_descendants_subtask_is_empty():
    sub, _p, _e = _subtask_with_epic()
    assert _client().ticket_descendants(sub) == []


def test_ancestors_each_ancestor_individually_cached():
    """조상 조회 시 각 조상 티켓이 개별 티켓 캐시(issue:{env}:{key})로 저장돼야 한다."""
    sub, parent, epic = _subtask_with_epic()
    c = _client()
    env = c.env
    assert c.cache.get(f"issue:{env}:{epic}") is None          # 조회 전엔 개별 캐시 없음
    assert c.cache.get(f"issue:{env}:{parent}") is None
    c.ticket_ancestors(sub)
    assert c.cache.get(f"issue:{env}:{epic}") is not None       # 조상 각각이 개별 캐시에 존재
    assert c.cache.get(f"issue:{env}:{parent}") is not None
    assert c.cache.get(f"ancestors:{env}:{sub}") is not None    # 결과 블롭도 캐시


def test_descendants_each_child_individually_cached():
    """Epic 자손 조회 시 각 자식 티켓이 개별 캐시로 write-through 되어야 한다."""
    c = _client()
    env = c.env
    epic = _key_of_type("Epic")
    d = c.ticket_descendants(epic)
    child = d[0]["key"]
    assert c.cache.get(f"issue:{env}:{child}") is not None      # 자식 개별 write-through
    assert c.cache.get(f"descendants:{env}:{epic}") is not None
