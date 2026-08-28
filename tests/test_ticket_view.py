"""티켓 상세 뷰(ticket_view) — 순수 빌더 + mock 통합.

핵심: description(HTML)이 **정화**된 채로 나오고, 리치 요소(table/code/blockquote/callout/image)가
mock(jira820 renderedFields) 경유로 실제 렌더된다.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.infra.cache import Cache            # noqa: E402
from app.jira.jira_client import JiraClient, _build_ticket_view   # noqa: E402
from app.jira.media_service import JiraMediaMixin                  # noqa: E402
from app.infra.settings import get_settings   # noqa: E402
from app.mock.world import get_world         # noqa: E402


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


def test_ticket_view_hydrates_partial_assignee_and_reporter_names(monkeypatch):
    """상세 응답에 username만 있어도 담당자·보고자 FieldEdit 트리거에는 본명을 표시한다."""
    client = _client()
    raw = {"key": "DL-1", "fields": {
        "summary": "s", "description": "",
        "issuetype": {"name": "Task"},
        "status": {"name": "Open", "statusCategory": {"key": "new"}},
        "assignee": {"name": "jira.assignee"},
        "reporter": {"name": "jira.reporter"},
    }}
    names = {"jira.assignee": "김담당 SKCC", "jira.reporter": "이보고 SKCC"}
    monkeypatch.setattr(client, "_get_issue_view", lambda key, fresh=False: raw)
    monkeypatch.setattr(client, "_display_name", lambda uid: names[uid])

    view = client.ticket_view("DL-1")
    assert view["assignee"] == "김담당"
    assert view["reporter"] == "이보고"
    assert view["assigneeDisplay"] == "김담당 SKCC"
    assert view["reporterDisplay"] == "이보고 SKCC"
    assert view["assigneeId"] == "jira.assignee"
    assert view["reporterId"] == "jira.reporter"


def test_builder_reuses_loaded_epic_name_without_another_request():
    raw = {"key": "DL-9000", "fields": {
        "summary": "UI 회귀 테스트 Epic", "customfield_10003": "UI Fixture",
        "issuetype": {"name": "Epic"},
        "status": {"name": "Open", "statusCategory": {"key": "new"}},
    }}
    v = _build_ticket_view(raw, "customfield_10002", epic_name_field="customfield_10003")
    assert v["epicName"] == "UI Fixture"


def test_builder_distinguishes_plain_ticket_key_autolink_from_explicit_jira_link():
    """renderer 결과가 같은 key 라벨이어도 raw URL이 있던 링크만 Detailed 표식을 받는다."""
    raw = {"key": "DL-1", "fields": {
        "summary": "s",
        "description": "plain DL-5002 and [DL-5003|https://jira.example/browse/DL-5003]",
        "issuetype": {"name": "Task"},
        "status": {"name": "Open", "statusCategory": {"key": "new"}},
    }, "renderedFields": {"description": (
        '<p>plain <a href="https://jira.example/browse/DL-5002">DL-5002</a> and '
        '<a href="https://jira.example/browse/DL-5003">DL-5003</a></p>')}}
    html = _build_ticket_view(raw, "customfield_10002")["descriptionHtml"]
    assert 'href="https://jira.example/browse/DL-5002"' in html
    assert 'href="https://jira.example/browse/DL-5002" class="jira-link-explicit"' not in html
    explicit = html[html.index('href="https://jira.example/browse/DL-5003"'):]
    assert "jira-link-explicit" in explicit.split(">", 1)[0]


# ── mock 통합: jira820 renderedFields 로 리치 요소가 실제 렌더 + 정화 ──
def _client():
    return JiraClient(get_settings(), Cache(":memory:"))


def test_jira_client_preserves_media_service_facade_contract():
    """분리 뒤에도 기존 JiraClient 호출부가 같은 공개 메서드를 사용한다."""
    assert issubclass(JiraClient, JiraMediaMixin)
    for name in ("user_avatar", "conf_title_by_id", "link_title", "favicon", "fetch_media"):
        assert getattr(JiraClient, name) is getattr(JiraMediaMixin, name)


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
    assert "<table " in html and "<th " in html           # 표
    assert '<pre class="jecodeblock">' in html and "<code" in html  # 코드블록
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
    assert '<pre class="jecodeblock">' in html               # 코드
    assert '<img src="/ticket-sample.svg"' in html
    assert "<script" not in html.lower()


def test_mock_epic_has_panel_and_table_and_safe_link():
    v = _client().ticket_view(_key_of_type("Epic"))
    html = v["descriptionHtml"]
    assert '<div class="panel">' in html and "panel-title" in html
    assert "<table " in html
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
    # dev jira_base 는 127.0.0.1:8080 (localhost 는 Windows 에서 ::1 폴백으로 요청마다 ~2초)
    c = _client()
    from urllib.parse import urlparse
    assert c._media_allowed_host(urlparse(c.s.jira_base).hostname)
    assert not c._media_allowed_host("evil.example")
    # fetch_media 는 허용 안 된 호스트를 즉시 차단(SSRF 방지)
    assert c.fetch_media("https://evil.example/x.png") == (None, None)
    assert c.fetch_media("") == (None, None)
    assert c.fetch_media("ftp://x/y") == (None, None)


def test_mock_does_not_proxy_app_static_images():
    # 앱이 직접 서빙하는 static 이미지는 dev 에서 프록시로 바꾸지 않는다(그대로 로드된다)
    v = _client().ticket_view(_key_of_type("Bug"))
    assert "/api/img?u=" not in v["descriptionHtml"]
    assert '<img src="/ticket-sample.svg"' in v["descriptionHtml"]


def test_mock_proxies_jira_attachment_images():
    # 첨부(/secure/…)는 jira820 에 있어 앱 오리진으로는 못 받는다 → dev 도 프록시를 태워야 한다
    out = _client()._proxy_media(
        '<p><img src="/secure/attachment/30001/shot.png" alt="" />'
        '<img src="/ticket-sample.svg" alt="" /></p>')
    assert '/api/img?u=%2Fsecure%2Fattachment%2F30001%2Fshot.png' in out
    assert '<img src="/ticket-sample.svg"' in out


def test_prod_proxy_media_rewrites_images():
    # prod 분기: description/코멘트의 <img> 가 /api/img 프록시로 재작성돼야 함
    c = _client()
    c.env = "prod"
    try:
        out = c._proxy_media('<p><img src="/secure/attachment/9/x.png" alt="" /></p>')
        assert "/api/img?u=" in out and "secure%2Fattachment" in out
    finally:
        c.env = "mock"


def test_prod_comment_source_proxies_attachment_image_for_edit_and_unproxies_on_save():
    """게시 후 보이던 붙여넣기 이미지가 수정 에디터에서 localhost/secure 로 깨지지 않아야 한다."""
    c = _client()
    class Provider:
        @staticmethod
        def get_json(*args, **kwargs):
            return {"comments": [{
                "id": "42",
                "body": '<p>before<img src="/secure/attachment/9/paste.png" alt="paste.png"></p>',
            }]}

    c._provider = Provider()
    c._provider_built = True
    c.env = "prod"
    old_format = c.s.comment_format
    try:
        c.s.comment_format = "html"
        source = c.comment_source("DL-9007", "42")

        assert source and source["id"] == "42"
        assert "/api/img?u=" in source["html"]
        assert "secure%2Fattachment%2F9%2Fpaste.png" in source["html"]
        assert 'src="/secure/attachment/' not in source["html"]
        stored = c.comment_field_value(source["html"])
        assert "/api/img?u=" not in stored
        assert "/secure/attachment/9/paste.png" in stored
    finally:
        c.s.comment_format = old_format


def test_prod_comment_source_preserves_mention_through_edit_and_republish():
    """prod 댓글 수정이 user-hover를 일반 링크로 굳히지 않고 실제 멘션으로 왕복해야 한다."""
    c = _client()
    class Provider:
        @staticmethod
        def get_json(*args, **kwargs):
            return {"comments": [{
                "id": "43",
                "body": ('<p>담당 <a class="user-hover" '
                         'href="/secure/ViewProfile.jspa?name=skcc.x1103">이준서</a> 님</p>'),
            }]}

    c._provider = Provider()
    c._provider_built = True
    c.env = "prod"
    old_format = c.s.comment_format
    try:
        c.s.comment_format = "html"
        source = c.comment_source("DL-9008", "43")

        assert source and 'data-type="mention"' in source["html"]
        assert 'data-id="skcc.x1103"' in source["html"]
        stored = c.comment_field_value(source["html"])
        assert 'class="user-hover"' in stored
        assert 'href="/secure/ViewProfile.jspa?name=skcc.x1103"' in stored
        assert 'data-type="mention"' not in stored
    finally:
        c.s.comment_format = old_format


def test_wiki_comment_source_revives_task_checkboxes_for_editing():
    c = _client()
    class Provider:
        @staticmethod
        def get_json(*args, **kwargs):
            return {"comments": [{
                "id": "44",
                "body": ('<p dir="auto"><input id="task-1" type="checkbox" '
                         'checked="checked" />완료 항목</p>'),
            }]}

    c._provider = Provider()
    c._provider_built = True
    old_format = c.s.comment_format
    try:
        c.s.comment_format = "wiki"
        source = c.comment_source("DL-9008", "44")

        assert source and '<input id="task-1" type="checkbox" checked="checked"' in source["html"]
        assert "&lt;input" not in source["html"]
    finally:
        c.s.comment_format = old_format


def test_wiki_comment_source_resolves_attached_image_for_editing():
    c = _client()
    original_get_issue = c.get_issue
    class Provider:
        @staticmethod
        def get_json(*args, **kwargs):
            return {"comments": [{"id": "45", "body": "이미지 !paste-roundtrip.png!"}]}

    c._provider = Provider()
    c._provider_built = True
    c.get_issue = lambda key: {"fields": {"attachment": [{
        "filename": "paste-roundtrip.png",
        "content": "/secure/attachment/45/paste-roundtrip.png",
    }]}}
    old_format = c.s.comment_format
    try:
        c.s.comment_format = "wiki"
        source = c.comment_source("DL-9008", "45")

        assert source and "/api/img?u=" in source["html"]
        assert "secure%2Fattachment%2F45%2Fpaste-roundtrip.png" in source["html"]
        assert 'src="paste-roundtrip.png"' not in source["html"]
    finally:
        c.s.comment_format = old_format
        c.get_issue = original_get_issue


def test_mock_uploaded_comment_image_roundtrips_into_edit_source():
    """실제 mock REST의 첨부 목록과 댓글 원본을 함께 써 수정 이미지 URL을 복원한다."""
    c = _client()
    old_format = c.s.comment_format
    try:
        c.s.comment_format = "wiki"
        uploaded = c.upload_attachment("DL-9007", "roundtrip-source.png", b"png", "image/png")[0]
        html = f'<p>이미지<img src="{uploaded["content"]}" alt="roundtrip-source.png"></p>'
        created = c.add_comment("DL-9007", c.comment_field_value(html))
        source = c.comment_source("DL-9007", str(created["id"]))

        assert source and "/api/img?u=" in source["html"], source
        assert "roundtrip-source.png" in source["html"]
    finally:
        c.s.comment_format = old_format


def test_prod_description_and_comment_tables_store_jira_visible_borders():
    """prod HTML 저장값 자체에 표 선이 있어야 하며 재게시해도 중복되지 않아야 한다."""
    c = _client()
    old_description = c.s.description_format
    old_comment = c.s.comment_format
    source = "<table><tbody><tr><th>제목</th><td>값</td></tr></tbody></table>"
    try:
        c.s.description_format = "html"
        c.s.comment_format = "html"
        for serializer in (c.desc_field_value, c.comment_field_value):
            stored = serializer(source)
            assert stored.startswith('<table border="1" cellpadding="0" cellspacing="0"')
            assert stored.count("border:1px solid #dfe1e6") == 3
            assert "border-collapse:collapse" in stored
            assert serializer(stored) == stored
    finally:
        c.s.description_format = old_description
        c.s.comment_format = old_comment


def test_ticket_view_keeps_rendered_mention_and_supplies_canonical_edit_html():
    """본문은 Jira 앵커로 표시하되 수정 에디터에는 별도 canonical mention HTML을 준다."""
    c = _client()
    original = c._get_issue_view
    old_format = c.s.description_format
    try:
        c.s.description_format = "html"
        c._get_issue_view = lambda *args, **kwargs: {
            "key": "DL-9009",
            "fields": {"description": ('<p>담당 <a class="user-hover" '
                       'href="/secure/ViewProfile.jspa?name=skcc.x1103">이준서</a></p>')},
            "renderedFields": {},
        }
        view = c.ticket_view("DL-9009", fresh=True)

        assert 'class="user-hover"' in view["descriptionHtml"]
        assert 'data-type="mention"' in view["descriptionEditHtml"]
        assert 'data-id="skcc.x1103"' in view["descriptionEditHtml"]
    finally:
        c.s.description_format = old_format
        c._get_issue_view = original


def test_wiki_description_edit_html_repairs_renderer_color_macro_leak():
    """Jira renderedFields가 color 매크로를 덜 풀어도 수정 진입 서식과 멘션은 살아 있어야 한다."""
    c = _client()
    original = c._get_issue_view
    old_format = c.s.description_format
    try:
        c.s.description_format = "wiki"
        c._get_issue_view = lambda *args, **kwargs: {
            "key": "DL-9009",
            "fields": {"description": "{color:#dc2626}빨강{color} [~skcc.x1103]"},
            "renderedFields": {"description": ('<p>{color:#dc2626}빨강{color} '
                               '<a class="user-hover" '
                               'href="/secure/ViewProfile.jspa?name=skcc.x1103">이준서</a></p>')},
        }
        view = c.ticket_view("DL-9009", fresh=True)

        assert '{color:' not in view["descriptionEditHtml"]
        assert '<span style="color:#dc2626">빨강</span>' in view["descriptionEditHtml"]
        assert 'data-type="mention"' in view["descriptionEditHtml"]
        assert 'data-id="skcc.x1103"' in view["descriptionEditHtml"]
    finally:
        c.s.description_format = old_format
        c._get_issue_view = original


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
    assert "epicKey" in b and "epicSummary" in b
    assert "due" in b and "updated" in b
    assert c.ticket_badge("DL-999999") is None


def test_subtask_badge_resolves_epic_through_cached_parent():
    c = _client()
    sub = next(it for it in c.search_issues("ORDER BY updated DESC", max_results=300)
               if ((it.get("fields") or {}).get("issuetype") or {}).get("subtask"))
    sf = sub.get("fields") or {}
    parent_key = (sf.get("parent") or {}).get("key")
    parent = c.get_issue_light(parent_key)
    epic_key = ((parent.get("fields") or {}).get(c.s.epic_link_field_id))
    badge = c.ticket_badge(sub["key"])
    assert badge["epicKey"] == epic_key
    if epic_key:
        assert badge["epicSummary"]


def test_ticket_view_none_for_missing():
    assert _client().ticket_view("DL-999999") is None


def test_ticket_view_fields_populated():
    v = _client().ticket_view(_key_of_type("Bug"))
    assert v["key"] and v["summary"] and v["status"]
    assert v["statusCategory"] in ("todo", "inprogress", "done")
    assert isinstance(v["labels"], list) and isinstance(v["components"], list)


def test_html_source_description_without_renderedfields():
    """사내 WYSIWYG 에디터 인스턴스는 fields.description 원문이 HTML 이다.

    renderedFields 가 비었을 때 평문 취급하면 태그가 글자로 보인다(<p>안녕</p>).
    HTML 로 보이면 sanitize 경로를 타야 한다.
    """
    raw = {"fields": {"description": '<p>안녕하세요</p><div class="jePanel_info"><p>정보</p></div>',
                      "status": {}, "issuetype": {}}}
    v = _build_ticket_view(raw, "customfield_10001")
    assert v["descriptionFormat"] == "html"
    assert "<p>안녕하세요</p>" in v["descriptionHtml"]
    assert "&lt;p&gt;" not in v["descriptionHtml"]          # 이스케이프되면 안 된다
    assert 'class="callout callout-info"' in v["descriptionHtml"]   # 벤더 class 정규화도 탄다


def test_plain_text_with_angle_bracket_stays_text():
    """'a < b' 같은 평문을 HTML 로 오인하면 내용이 잘린다."""
    raw = {"fields": {"description": "조건은 a < b 이고 c > d 이다", "status": {}, "issuetype": {}}}
    v = _build_ticket_view(raw, "customfield_10001")
    assert v["descriptionFormat"] == "text"
    assert "a &lt; b" in v["descriptionHtml"]


def test_inline_tag_in_plaintext_is_not_html():
    """평문에 <b>x</b> 를 적어 둔 경우 — 블록 태그가 없으면 평문으로 본다(그대로 보여야 한다)."""
    raw = {"fields": {"description": "줄1\n줄2 <b>x</b>", "status": {}, "issuetype": {}}}
    assert _build_ticket_view(raw, "cf")["descriptionFormat"] == "text"
