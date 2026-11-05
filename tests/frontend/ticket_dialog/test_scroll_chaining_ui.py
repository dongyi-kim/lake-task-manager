"""Wheel input over clipped rich content must reach the real dialog scroll owner."""
import pytest

from frontend.editor.test_editor_roundtrip_ui import editor_browser  # noqa: F401


def _open_scroll_fixture(page, base):
    page.route('https://cdn.jsdelivr.net/**', lambda route: route.fulfill(content_type='text/css', body=''))
    filler = ''.join(f'<p>본문 내용 {i}</p>' for i in range(12))
    content = [
        '<p>WHEEL-PLAIN 일반 본문</p>',
        '<pre><code>WHEEL-CODE ' + 'wide_code_content ' * 40 + '</code></pre>',
        '<div class="panel"><div class="panel-body"><p>WHEEL-PANEL 기존 Jira 패널</p></div></div>',
        '<div class="panel"><div class="panel-body"><table><tr><td>WHEEL-NESTED-TABLE 패널 안 표</td></tr></table></div></div>',
        '<div class="callout callout-info"><p>WHEEL-CALLOUT 안내</p></div>',
    ]
    html = '<h2>휠 스크롤 확인</h2>' + ''.join(filler + item for item in content) + filler + '<p>WHEEL-END</p>'
    response = page.request.post(base + '/__editor_test__/seed/DL-9001', data={'description': html})
    assert response.ok, response.text()
    page.goto(base + '/#/home')
    page.wait_for_function('() => !!window.__lakeUp')
    page.evaluate('window.scrollTo(0, 400)')
    page.evaluate("window.dispatchEvent(new CustomEvent('lake-open-ticket', {detail:{key:'DL-9001'}}))")
    overlay = page.locator('.tkt-ov')
    overlay.locator('.tkt-desc-box').get_by_text('휠 스크롤 확인', exact=True).wait_for()
    return overlay


def _wheel_over(page, target, dy=300, dx=0):
    box = target.bounding_box()
    assert box is not None
    page.mouse.move(box['x'] + min(45, box['width'] / 2), box['y'] + min(12, box['height'] / 2))
    page.mouse.wheel(dx, dy)


def _assert_boundary_does_not_scroll_background(page, body, end):
    body.evaluate(' (el,end) => { el.scrollTop = end ? el.scrollHeight : 0; }', end)
    background = page.evaluate('scrollY')
    before = body.evaluate('el => el.scrollTop')
    # The body padding avoids unrelated inner scroll owners at both boundaries.
    box = body.bounding_box()
    page.mouse.move(box['x'] + 3, box['y'] + box['height'] / 2)
    page.mouse.wheel(0, 420 if end else -420)
    # Wheel is delivered asynchronously by Chromium; observe a settled input frame.
    page.wait_for_timeout(150)
    assert body.evaluate('el => el.scrollTop') == before
    assert page.evaluate('scrollY') == background


@pytest.mark.parametrize('editor_browser', [{'format': 'html'}], indirect=True)
def test_rendered_code_panels_and_clipped_sidebar_chain_to_dialog_only(editor_browser):
    page, base, errors, _ = editor_browser
    overlay = _open_scroll_fixture(page, base)
    body = overlay.locator('.tkt-body')
    for text in ['WHEEL-PLAIN', 'WHEEL-CODE', 'WHEEL-PANEL', 'WHEEL-NESTED-TABLE', 'WHEEL-CALLOUT']:
        target = overlay.locator('.tkt-desc-box').get_by_text(text, exact=False).first
        target.evaluate("el => el.scrollIntoView({block:'center'})")
        before = body.evaluate('el => el.scrollTop')
        _wheel_over(page, target)
        page.wait_for_function('before => document.querySelector(".tkt-ov .tkt-body").scrollTop > before + 20', arg=before)
        target.evaluate("el => el.scrollIntoView({block:'center'})")
        before = body.evaluate('el => el.scrollTop')
        _wheel_over(page, target, dy=-300)
        page.wait_for_function('before => document.querySelector(".tkt-ov .tkt-body").scrollTop < before - 20', arg=before)

    # Code still owns horizontal movement; allowing vertical chaining must not flatten it.
    code = overlay.locator('.tkt-desc-box pre code').first
    code.evaluate("el => { el.scrollIntoView({block:'center'}); el.scrollLeft=0; }")
    body_before = body.evaluate('el => el.scrollTop')
    _wheel_over(page, code, dy=0, dx=220)
    page.wait_for_function('document.querySelector(".tkt-ov .tkt-desc-box pre code").scrollLeft > 20')
    assert abs(body.evaluate('el => el.scrollTop') - body_before) < 1

    # The sidebar uses overflow:hidden for title clipping, not an independent scroll owner.
    title = overlay.locator('.spn-title').first
    title.evaluate("el => el.scrollIntoView({block:'center'})")
    body.evaluate('el => {el.scrollTop=0}')
    _wheel_over(page, title)
    page.wait_for_function('document.querySelector(".tkt-ov .tkt-body").scrollTop > 20')
    for end in [False, True]:
        _assert_boundary_does_not_scroll_background(page, body, end)
    assert not errors, errors


@pytest.mark.parametrize('editor_browser', [{'format': 'html'}], indirect=True)
def test_editor_code_scrolls_host_and_host_end_chains_to_dialog(editor_browser):
    page, base, errors, _ = editor_browser
    overlay = _open_scroll_fixture(page, base)
    overlay.locator('button.sec-edit').first.click()
    host = overlay.locator('.tkt-desc-edit .cmt-ed-host')
    code = host.locator('pre code').first
    code.wait_for(state='visible')
    for dy in [300, -300]:
        code.evaluate("el => el.scrollIntoView({block:'center'})")
        before = host.evaluate('el => el.scrollTop')
        _wheel_over(page, code, dy=dy)
        page.wait_for_function("""({before,sign}) =>
          (document.querySelector('.tkt-desc-edit .cmt-ed-host').scrollTop-before)*sign > 20
        """, arg={'before': before, 'sign': 1 if dy > 0 else -1})

    body = overlay.locator('.tkt-body')
    end = host.get_by_text('WHEEL-END', exact=True)
    end.evaluate("el => el.scrollIntoView({block:'center'})")
    host.evaluate('el => {el.scrollTop=el.scrollHeight}')
    # scrollIntoView can place the outer body at its end when this editor fits the viewport.
    # Keep the host at its end, but give the outer body real room to receive the next wheel.
    body.evaluate('el => {el.scrollTop=0}')
    assert body.evaluate('el => el.scrollHeight-el.clientHeight-el.scrollTop') > 20
    before = body.evaluate('el => el.scrollTop')
    _wheel_over(page, end)
    page.wait_for_function('before => document.querySelector(".tkt-ov .tkt-body").scrollTop > before + 20', arg=before)
    for boundary in [False, True]:
        _assert_boundary_does_not_scroll_background(page, body, boundary)
    assert not errors, errors
