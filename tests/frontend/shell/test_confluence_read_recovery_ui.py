"""Finite document retries preserve rendered content and distinguish read failures."""
import os
from pathlib import Path

from frontend.editor.test_editor_roundtrip_ui import editor_browser  # noqa: F401
from frontend.shell.test_confluence_browser_ui import install_documents


def test_document_retry_is_bounded_preserves_body_and_recovers(editor_browser):
    from playwright.sync_api import expect
    page, base, errors, _ = editor_browser
    install_documents(page)
    page.goto(base + '/#/confluence/12')
    expect(page.locator('.conf-document-body h2').first).to_have_text('버전 2 본문')
    attempts, failed = [], [True]
    def document(route):
        attempts.append(route.request.url)
        if failed[0]: route.fulfill(status=503,json={'service':'confluence','reason':'upstream_unavailable','retryable':True,'error':'unavailable'})
        else: route.fulfill(json={'id':'12','title':'복구된 문서','version':{'number':3},'space':{'key':'ETL'},'html':'<h2>버전 3 본문</h2>'})
    page.route('**/api/confluence/pages/12?version=3', document)
    page.get_by_role('button', name='최신 여부 확인', exact=True).click()
    page.get_by_role('button', name='새 버전 보기', exact=True).click()
    expect(page.locator('.conf-read-notice')).to_contain_text('문서를 다시 불러오는 중')
    expect(page.locator('.conf-read-notice')).to_have_attribute('role','alert')
    assert len(attempts) == 2
    expect(page.locator('.conf-document-body h2').first).to_have_text('버전 2 본문')
    expect(page.locator('.conf-read-notice')).to_contain_text('이전에 불러온 내용')
    destination = os.environ.get('LTM_UI_EVIDENCE')
    if destination: page.screenshot(path=str(Path(destination) / 'confluence-document-error.png'))
    failed[0] = False
    page.get_by_role('button',name='문서 다시 불러오기',exact=True).click()
    expect(page.locator('.conf-document h1')).to_have_text('복구된 문서')
    expect(page.locator('.conf-read-notice')).to_have_count(0)
    assert len(attempts) == 3
    assert all('HTTP 503' in error for error in errors), errors


def test_document_browser_timeout_has_read_specific_error_and_one_retry(editor_browser):
    from playwright.sync_api import expect
    page, base, errors, _ = editor_browser
    install_documents(page)
    page.clock.install()
    held = []
    page.route('**/api/confluence/pages/12', lambda route: held.append(route))
    page.goto(base + '/#/confluence/12')
    expect(page.locator('.conf-reader')).to_have_attribute('aria-busy','true')
    page.clock.fast_forward(36000)
    expect(page.locator('.conf-read-notice')).to_contain_text('문서를 다시 불러오는 중')
    page.clock.fast_forward(1500)
    page.wait_for_function('document.querySelector(".conf-read-notice").textContent.includes("다시 불러오는 중")')
    assert len(held) == 2
    page.clock.fast_forward(36000)
    expect(page.locator('.conf-read-notice')).to_have_attribute('role','alert')
    expect(page.locator('.conf-read-notice')).to_contain_text('문서 응답 대기 시간이 초과')
    expect(page.locator('.conf-read-notice')).not_to_contain_text('입력은 보존')
    expect(page.locator('.conf-reader')).to_have_attribute('aria-busy','false')
    assert len(held) == 2 and not errors


def test_authentication_failure_does_not_repeat_or_open_jira_login(editor_browser):
    from playwright.sync_api import expect
    page, base, errors, _ = editor_browser
    install_documents(page)
    calls = []
    def expired(route):
        calls.append(route.request.url)
        route.fulfill(status=401,json={'service':'confluence','needLogin':True,'reason':'authentication','error':'expired'})
    page.route('**/api/confluence/pages/12',expired)
    page.goto(base + '/#/confluence/12')
    expect(page.locator('.conf-read-notice')).to_contain_text('Confluence 인증 연결')
    expect(page.locator('.conf-read-notice')).not_to_contain_text('시간이 초과')
    assert len(calls) == 1
    assert not page.locator('.login-overlay').count()
    assert all('HTTP 401' in error for error in errors), errors
