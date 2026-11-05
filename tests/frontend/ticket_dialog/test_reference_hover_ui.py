"""Shared reference previews must paint above real TicketDialog content and backdrop."""
import json

import pytest

from frontend.editor.test_editor_roundtrip_ui import editor_browser, _open_ticket  # noqa: F401


def _painted_above_dialog(tooltip):
    # pointer-events:none intentionally lets clicks through. Enable hit testing only while
    # sampling the painted stack; this changes neither geometry nor stacking order.
    return tooltip.evaluate("""tip => {
      const prior = tip.style.pointerEvents;
      try {
        tip.style.pointerEvents = 'auto';
        const r = tip.getBoundingClientRect();
        const points = [[r.x+r.width/2, r.y+8], [r.x+r.width/2, r.bottom-8]];
        return points.every(([x,y]) => document.elementFromPoint(x,y)?.closest('.reference-hover') === tip);
      } finally { tip.style.pointerEvents = prior; }
    }""")


@pytest.mark.parametrize('editor_browser', [{'format': 'html'}], indirect=True)
def test_ticket_and_person_preview_paint_above_modal_without_blocking_navigation(editor_browser):
    from playwright.sync_api import expect

    page, base, errors, upload = editor_browser
    console_issues = []
    page.on('console', lambda message: console_issues.append(message.text)
            if message.type in ('warning', 'error') else None)
    # The unrelated remote font is unavailable in offline test environments.
    page.route('https://cdn.jsdelivr.net/gh/orioncactus/pretendard/packages/pretendard/dist/web/variable/pretendardvariable.css',
               lambda route: route.fulfill(content_type='text/css', body=''))
    for key, target in [('DL-9007', 'DL-9001'), ('DL-9001', 'DL-9007')]:
        response = page.request.post(f'{base}/__editor_test__/seed/{key}', data={'description':
            '<h2>참조 미리보기 확인</h2><p>티켓 링크 위에 마우스를 올리면 상세 요약을 확인할 수 있습니다.</p>'
            f'<p><a href="/browse/{target}">{target}</a></p>'
            '<p>담당자: <a class="user-hover" href="/secure/ViewProfile.jspa?name=test.ui02">'
            'UI픽스처02</a></p>'})
        assert response.ok, response.text()

    _open_ticket(page, base, 'DL-9007')
    tooltip = page.locator('.reference-hover[role=tooltip]')
    outside_link = page.locator('.tkt-page .tkt-desc .jira-badge[data-key="DL-9001"]').first
    outside_link.hover()
    expect(tooltip.locator('.reference-hover-row').first).to_contain_text('DL-9001')
    assert _painted_above_dialog(tooltip)
    page.mouse.move(5, 5)
    expect(tooltip).to_be_hidden()

    # Open through the actual delegated ticket link, not a synthetic modal or z-index fixture.
    outside_link.click()
    modal = page.locator('.tkt-ov')
    modal.locator('.tkt-desc').get_by_text('참조 미리보기 확인', exact=True).wait_for()
    inside_link = modal.locator('.tkt-desc .jira-badge[data-key="DL-9007"]').first
    inside_link.hover()
    expect(tooltip.locator('.reference-hover-row').first).to_contain_text('DL-9007')
    assert _painted_above_dialog(tooltip)
    assert tooltip.evaluate('el => getComputedStyle(el).pointerEvents') == 'none'

    # The old CSS looked "visible" to Playwright despite being hidden behind the modal.
    legacy = page.add_style_tag(content='.reference-hover { z-index:1200 !important; }')
    assert tooltip.is_visible()
    assert not _painted_above_dialog(tooltip)
    page.screenshot(path=str(upload.parent / 'reference-hover-before.png'), animations='disabled')
    legacy.evaluate('el => el.remove()')
    assert _painted_above_dialog(tooltip)
    page.screenshot(path=str(upload.parent / 'reference-hover-ticket.png'), animations='disabled')

    page.mouse.move(5, 5)
    expect(tooltip).to_be_hidden()
    person = modal.locator('.tkt-desc .mention-badge[data-id="test.ui02"]').first
    person.hover()
    expect(tooltip).to_contain_text('Full Display Name')
    expect(tooltip).to_contain_text('test.ui02')
    assert _painted_above_dialog(tooltip)
    page.screenshot(path=str(upload.parent / 'reference-hover-person.png'), animations='disabled')
    page.mouse.move(5, 5)
    expect(tooltip).to_be_hidden()

    inside_link.click()
    modal.locator('.tkt-desc .jira-badge[data-key="DL-9001"]').first.wait_for()
    expect(modal.locator('.tb-key')).to_have_text('DL-9007')
    modal.locator('.tb-btn.close').click()
    expect(modal).to_have_count(0)
    outside_link.hover()
    expect(tooltip.locator('.reference-hover-row').first).to_contain_text('DL-9001')
    assert _painted_above_dialog(tooltip)
    assert not errors, errors
    assert not console_issues, console_issues
    (upload.parent / 'reference-hover-proof.json').write_text(json.dumps({
        'url': page.url, 'title': page.title(), 'viewport': page.viewport_size,
        'pageAndHttpErrors': errors, 'consoleWarningsAndErrors': console_issues,
        'legacyOccluded': True, 'fixedPaintedAbove': True,
    }, ensure_ascii=False), encoding='utf-8')
