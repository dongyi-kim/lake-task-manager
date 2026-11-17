"""Saved comment HTML renders and folds on every actual TrackingView surface."""
import mimetypes
import re
from html import escape
from types import SimpleNamespace
from urllib.parse import urlsplit

import pytest

from support.browser import launch_chromium
from support.paths import REPO_ROOT


RICH_HTML = (
    '<p>첫 번째 보고 문장</p><p>두 번째 보고 문장</p><p>세 번째 보고 문장</p>'
    '<table><tbody><tr><th>항목</th><th>결과</th></tr>'
    '<tr><td>적재</td><td>검증 완료</td></tr></tbody></table>'
    '<ul><li>후속 확인 항목</li></ul>'
    '<p><a href="#comment-reference">댓글 참고 링크</a></p>'
)
PLAIN_TEXT = '일반 댓글 첫째 줄\n일반 댓글 둘째 줄\n일반 댓글 셋째 줄\n일반 댓글 마지막 줄'


@pytest.fixture
def tracking_browser(request):
    """Mount production Vue and CSS; only the saved-run HTTP boundary is fake."""
    playwright = pytest.importorskip("playwright.sync_api")
    coverage = {"complete": True, "historyComplete": True, "commentsComplete": True}
    run = {
        "id": "saved-comments", "status": "complete", "coverage": coverage,
        "request": {"startDate": "2026-11-01", "endDate": "2026-11-15", "people": [], "modules": []},
        "asOf": "2026-11-16T00:00:00+09:00",
        "issues": [
            {"key": "TASK-1", "summary": "댓글 렌더링 업무", "type": "Task", "coverage": coverage},
            {"key": "SUB-1", "summary": "하위 확인 업무", "type": "Sub-Task", "parentKey": "TASK-1", "isSubtask": True, "coverage": coverage},
        ],
        "tree": [{"id": "task:TASK-1", "key": "TASK-1", "kind": "task", "children": [
            {"id": "task:SUB-1", "key": "SUB-1", "kind": "subtask", "children": []}]}],
        "events": [
            {"id": "rich-comment", "issueKey": "TASK-1", "kind": "comment", "date": "2026-11-13T09:00:00Z",
             "body": RICH_HTML, "bodyHtml": RICH_HTML, "bodyText": "첫 번째 보고 문장\n두 번째 보고 문장\n세 번째 보고 문장\n항목 결과\n적재 검증 완료\n후속 확인 항목\n댓글 참고 링크", "actorName": "보고 작성자"},
            {"id": "plain-comment", "issueKey": "TASK-1", "kind": "comment", "date": "2026-11-12T09:00:00Z",
             "body": PLAIN_TEXT, "actorName": "일반 작성자"},
        ],
    }
    if getattr(request, 'param', None) == 'grouping':
        run['issues'].append({"key": "TASK-2", "summary": "별도 확인 업무", "type": "Task", "coverage": coverage})
        run['tree'].append({"id": "task:TASK-2", "key": "TASK-2", "kind": "task", "children": []})
        run['events'].extend([
            {"id": "edited-comment", "issueKey": "SUB-1", "kind": "commentEdited", "date": "2026-11-13T16:00:00Z",
             "body": "Edited first line\nEdited second line\nEdited third line",
             "bodyHtml": "<p>Edited first line</p><p>Edited second line</p><p>Edited third line</p>",
             "bodyText": "Edited first line\nEdited second line\nEdited third line", "actorName": "수정 작성자"},
            {"id": "sub-completed", "issueKey": "SUB-1", "kind": "completed", "date": "2026-11-13T10:00:00Z",
             "actorName": "하위 담당자"},
            {"id": "sub-created", "issueKey": "SUB-1", "kind": "created", "date": "2026-11-12T08:00:00Z",
             "actorName": "하위 담당자"},
            {"id": "other-change", "issueKey": "TASK-2", "kind": "fieldChanged", "date": "2026-11-13T08:00:00Z",
             "field": "priority", "from": "Low", "to": "High", "actorName": "별도 담당자"},
        ])
    if getattr(request, 'param', None) == 'legacy-comments':
        from app.tracking.presentation import present_comments

        # Exercise the real saved-run projection rather than injecting bodyHtml.
        body = (RICH_HTML + '<pre><code>&lt;div&gt; &amp; x &lt; 3</code></pre>'
                '<script>window.commentUnsafe = true</script>'
                '<a href="javascript:window.commentUnsafe=true" onclick="window.commentUnsafe=true">Unsafe link</a>')
        sources, run['events'] = [], []
        for index, variant in enumerate(['raw', 'encoded', 'quoted', 'mismatch']):
            stored_body = escape(body, quote=False) if variant == 'encoded' else body
            run['events'].append({
                'id': variant, 'issueKey': 'TASK-1', 'kind': 'commentEdited' if index % 2 else 'comment',
                'commentId': str(index), 'date': f'2026-11-{13-index:02}T09:00:00Z',
                'body': stored_body, 'excerpt': stored_body, 'actorName': '저장된 작성자',
            })
            if variant in ('quoted', 'mismatch'):
                sources.append({'id': str(index), 'body': body if variant == 'quoted' else '<p>Newer</p>',
                                'renderedBody': '<p>' + escape(body, quote=False) + '</p>' if variant == 'quoted' else '<p>Newer</p>'})
        run = present_comments(run, SimpleNamespace(env='test', s=SimpleNamespace(comment_format='wiki')),
                               {'sources': {'TASK-1': {'comments': sources}}})
    if getattr(request, 'param', None) == 'plain-html-config':
        from app.tracking.presentation import present_comments

        run['events'] = [run['events'][1]]
        run = present_comments(run, SimpleNamespace(env='prod', s=SimpleNamespace(comment_format='html')))
    # Other saved-run regressions can opt into their own hierarchy without
    # changing the compact comment/grouping fixtures above.
    if isinstance(getattr(request, 'param', None), dict):
        run.update(request.param)
    styles = ["tokens", "base", "ticket", "tracking", "tracking-report", "tracking-overview", "tracking-scope", "tracking-comment", "tracking-reference", "tracking-reference-panels", "tracking-briefing"]
    html = '<!doctype html><html lang="ko"><head><meta charset="utf-8"><title>Tracking comment regression</title>'
    html += ''.join(f'<link rel="stylesheet" href="/styles/{name}.css">' for name in styles)
    html += '''</head><body><div id="app" class="wrap"></div><script type="module">
      import {createApp} from '/vendor/vue.esm-browser.prod.js';
      import TrackingView from '/components/tracking-view.js';
      createApp(TrackingView).mount('#app');
    </script></body></html>'''
    api_requests, unexpected, errors = [], [], []
    static = REPO_ROOT / "app/static"

    def respond(route):
        path = urlsplit(route.request.url).path
        if path.startswith('/api/'):
            api_requests.append((route.request.method, path))
            payloads = {
                '/api/tracking/options': {"people": [], "modules": [], "aiEnabled": False},
                '/api/tracking/runs': {"runs": [{"id": run['id'], "status": "complete"}]},
                '/api/tracking/runs/saved-comments': run,
            }
            if route.request.method == 'GET' and path in payloads:
                route.fulfill(json=payloads[path])
            else:
                unexpected.append((route.request.method, path))
                route.fulfill(status=500, json={"error": "Unexpected collection or Jira request"})
        elif path == '/':
            route.fulfill(content_type='text/html; charset=utf-8', body=html)
        else:
            asset = (static / path.lstrip('/')).resolve()
            if asset.is_relative_to(static.resolve()) and asset.is_file():
                content_type = 'text/javascript' if asset.suffix == '.js' else mimetypes.guess_type(asset.name)[0] or 'application/octet-stream'
                route.fulfill(content_type=content_type, body=asset.read_bytes())
            else:
                unexpected.append(('asset', path))
                route.fulfill(status=404)

    with playwright.sync_playwright() as driver:
        browser = launch_chromium(driver)
        try:
            page = browser.new_page(viewport={"width": 1440, "height": 1000})
            page.set_default_timeout(5000)
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.on('console', lambda message: errors.append(message.text) if message.type == 'error' else None)
            page.route('**/*', respond)
            page.goto('http://tracking.test/')
            try:
                playwright.expect(page.locator('.tracking-reference .rf-workspace').first).to_be_visible()
            except AssertionError as error:
                raise AssertionError(f'{error}\nConsole: {errors}\nUnexpected requests: {unexpected}') from error
            if not isinstance(getattr(request, 'param', None), dict):
                page.get_by_role('navigation', name='업무 계층 탐색').get_by_role(
                    'button', name='소속 Epic 없음 범위 선택', exact=True,
                ).click()
            yield page, api_requests, unexpected, errors
        finally:
            browser.close()


def open_surface(page, surface):
    if surface == 'summary':
        page.get_by_role('button', name='내용 펼치기', exact=True).first.click()
        return page.locator('.tb-row-expanded')
    if surface == 'feed':
        page.get_by_role('button', name='날짜별 활동', exact=True).click()
        return page.locator('.tr-feed')
    page.locator('.tb-row[data-key="TASK-1"] .tb-evidence .tb-text').click()
    return page.locator('.tp-evidence')


def assert_folded_comment(comment):
    from playwright.sync_api import expect

    toggle = comment.get_by_role('button', name='댓글 펼치기', exact=True)
    expect(toggle).to_have_attribute('aria-expanded', 'false')
    expect(comment).to_have_class(re.compile(r'\bis-collapsed\b'))
    assert comment.locator('.tr-comment-clip').evaluate(
        'el => el.getBoundingClientRect().height < el.firstElementChild.getBoundingClientRect().height'
    ), 'A collapsed comment must actually clip its rendered body with production CSS'
    return toggle


@pytest.mark.parametrize('surface', ['summary', 'feed', 'detail'])
def test_rich_comment_renders_and_folds_without_opening_evidence(tracking_browser, surface):
    from playwright.sync_api import expect

    page, reads, unexpected, errors = tracking_browser
    area = open_surface(page, surface)
    comment = area.locator('.tr-comment').filter(has=page.locator('table'))
    expect(comment).to_have_count(1)
    expect(comment.locator('table')).to_have_count(1)
    expect(comment.locator('table th')).to_have_text(['항목', '결과'])
    expect(comment.locator('ul > li')).to_have_text(['후속 확인 항목'])
    assert not re.search(r'<(?:p|table)\b', area.inner_text())
    assert page.locator('button button, button a, a button').count() == 0
    requests_before = list(reads)
    evidence_count = page.locator('.tp-evidence').count()
    toggle = assert_folded_comment(comment)
    toggle.click()
    expect(comment.get_by_role('button', name='댓글 접기', exact=True)).to_have_attribute('aria-expanded', 'true')
    expect(comment).not_to_have_class(re.compile(r'\bis-collapsed\b'))
    assert comment.locator('.tr-comment-clip').evaluate('el => el.clientHeight >= el.scrollHeight - 1')
    comment.get_by_role('link', name='댓글 참고 링크', exact=True).click()
    expect(page).to_have_url('http://tracking.test/#comment-reference')
    expect(page.locator('.tp-evidence')).to_have_count(evidence_count)
    if surface != 'detail':
        expect(page.locator('.tp-evidence')).to_have_count(0)
    comment.get_by_role('button', name='댓글 접기', exact=True).click()
    assert_folded_comment(comment)
    if surface == 'summary':
        page.locator('.tb-row[data-key="TASK-1"] .tb-evidence .tb-text').click()
    elif surface == 'feed':
        area.locator('.tr-feed-event').filter(has=page.locator('table')).locator('.tr-feed-ticket').click()
    if surface != 'detail':
        expect(page.locator('.tp-evidence')).to_be_visible()
        expect(page.get_by_role('complementary', name='상세 근거')).to_be_visible()
    assert reads == requests_before
    assert not unexpected
    assert not errors


def test_summary_preview_is_plain_text_and_plain_comment_keeps_line_folding(tracking_browser):
    from playwright.sync_api import expect

    page, reads, unexpected, errors = tracking_browser
    preview = page.locator('.tb-comment-preview')
    expect(preview).to_contain_text('첫 번째 보고 문장')
    assert not re.search(r'<(?:p|table)\b', preview.inner_text())
    requests_before = list(reads)
    area = open_surface(page, 'detail')
    plain = area.locator('.tr-comment').filter(has_text='일반 댓글 첫째 줄')
    expect(plain.locator('.tr-comment-body')).to_have_text(PLAIN_TEXT)
    assert_folded_comment(plain).click()
    expect(plain.get_by_role('button', name='댓글 접기', exact=True)).to_have_attribute('aria-expanded', 'true')
    expect(plain).not_to_have_class(re.compile(r'\bis-collapsed\b'))
    assert reads == requests_before
    assert not unexpected
    assert not errors


@pytest.mark.parametrize('tracking_browser', ['legacy-comments'], indirect=True)
@pytest.mark.parametrize('surface', ['summary', 'feed', 'detail'])
def test_legacy_stored_comments_reach_every_surface_as_safe_rich_content(tracking_browser, surface):
    from playwright.sync_api import expect

    page, reads, unexpected, errors = tracking_browser
    previews = page.locator('.tb-comment-preview')
    assert not re.search(r'<(?:p|table)\b|&lt;(?:p|table)', previews.inner_text())
    before = list(reads)
    area = open_surface(page, surface)
    comments = area.locator('.tr-comment')
    expect(comments).to_have_count(4)
    for comment in comments.all():
        expect(comment.locator('table')).to_have_count(1)
        expect(comment.locator('pre code')).to_have_text('<div> & x < 3')
        assert not re.search(r'<(?:p|table)\b|&lt;(?:p|table)', comment.inner_text())
        assert_folded_comment(comment).click()
        expect(comment.get_by_role('button', name='댓글 접기', exact=True)).to_have_attribute('aria-expanded', 'true')
        assert comment.locator('script, [onclick], [href^="javascript:"]').count() == 0
    assert page.evaluate('window.commentUnsafe') is None
    assert reads == before
    assert not unexpected
    assert not errors


@pytest.mark.parametrize('tracking_browser', ['plain-html-config'], indirect=True)
def test_plain_comment_projected_under_html_configuration_still_folds(tracking_browser):
    from playwright.sync_api import expect

    page, reads, unexpected, errors = tracking_browser
    before = list(reads)
    area = open_surface(page, 'detail')
    comment = area.locator('.tr-comment')
    assert comment.locator('.tr-comment-body').inner_text() == PLAIN_TEXT
    assert_folded_comment(comment).click()
    expect(comment.get_by_role('button', name='댓글 접기', exact=True)).to_have_attribute('aria-expanded', 'true')
    assert reads == before
    assert not unexpected
    assert not errors


@pytest.mark.parametrize('action', ['이전 조회', 'AI 요약', '보고서 복사', '수집 범위와 비용'])
def test_secondary_actions_open_native_dialogs_without_collecting(tracking_browser, action):
    from playwright.sync_api import expect

    page, reads, unexpected, errors = tracking_browser
    before = list(reads)
    trigger = page.get_by_role('button', name=re.compile('^' + re.escape(action)))
    trigger.click()
    dialog = page.get_by_role('dialog')
    expect(dialog).to_be_visible()
    assert dialog.evaluate('element => element.tagName === "DIALOG" && element.open')
    if action == '보고서 복사':
        expect(dialog.get_by_role('textbox', name='보고서 내용')).to_have_value(re.compile('댓글 렌더링 업무'))
    elif action == 'AI 요약':
        expect(dialog).to_contain_text('전체 조회 결과')
        expect(dialog.get_by_role('button', name='AI 요약 생성', exact=True)).to_be_disabled()
    elif action == '수집 범위와 비용':
        expect(dialog).to_contain_text('Jira REST 호출')
    else:
        expect(dialog).to_contain_text('다시 수집하지 않습니다.')
    dialog.get_by_role('button', name='닫기', exact=True).click()
    expect(dialog).not_to_be_visible()
    expect(trigger).to_be_focused()
    assert reads == before
    assert not unexpected
    assert not errors
