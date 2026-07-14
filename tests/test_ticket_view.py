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


def test_ticket_view_none_for_missing():
    assert _client().ticket_view("DL-999999") is None


def test_ticket_view_fields_populated():
    v = _client().ticket_view(_key_of_type("Bug"))
    assert v["key"] and v["summary"] and v["status"]
    assert v["statusCategory"] in ("todo", "inprogress", "done")
    assert isinstance(v["labels"], list) and isinstance(v["components"], list)
