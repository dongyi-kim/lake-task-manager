"""One rendered heavy-ticket flow: paging, bounded enrichment, failures, and no false ack."""
from urllib.parse import parse_qs, urlsplit

from frontend.ticket_dialog.test_ticket_updates_ui import updates_dialog, static_server  # noqa: F401


def test_large_thread_pages_without_bulk_collection_or_oversized_render(updates_dialog, tmp_path):
    page, state, failed, expect = updates_dialog
    state['comments_total'] = 2000
    state['comments_complete'] = False
    state['comments'] = [dict(id=str(i), date=state['iso'](state['latest']),
        author='작성자', html=f'<p>현재 댓글 {i}</p>') for i in range(20)]
    state['detail']['descriptionHtml'] = '<p>' + 'x' * (600 * 1024) + '</p>'
    state['detail']['descriptionSections'] = [{'html': state['detail']['descriptionHtml']}]
    calls = []
    mode, held = ['ok'], []

    def comments_page(route):
        start = int(parse_qs(urlsplit(route.request.url).query)['startAt'][0])
        calls.append(start)
        if mode[0] == 'hold':
            held.append(route)
            return
        if mode[0] == 'failed':
            route.fulfill(status=503, json={'error': '조회 지연'})
            return
        if mode[0] == 'invalid':
            route.fulfill(json={'comments': [], 'total': 2000, 'partial': True,
                'pageComplete': False, 'incompleteReason': 'changed_during_read', 'nextStartAt': None})
            return
        rows = [dict(id=str(i), date=state['iso'](state['latest']), author='작성자',
                     html=f'<p>이전 댓글 {i}</p>') for i in range(start, start + 5)]
        rows[0].update(html='', bodyTooLarge=True, originalUrl='https://jira.local/browse/DL-1?focusedCommentId=20')
        route.fulfill(json={'comments': rows, 'total': 2000, 'startAt': start,
            'nextStartAt': start + 5, 'complete': False, 'fetchedAt': None})

    page.route('**/api/issue/DL-1/comments/page?*', comments_page)
    page.get_by_role('button', name='티켓 열기', exact=True).click()
    expect(page.get_by_role('link', name='Jira에서 설명 보기 · 수정')).to_be_visible()
    expect(page.locator('.tkt-comments .tkt-cmt')).to_have_count(20)
    assert page.locator('.tkt-desc').evaluate_all('(nodes)=>nodes.reduce((n,x)=>n+x.textContent.length,0)') < 10000
    page.get_by_role('button', name='다음 댓글 페이지', exact=True).click()
    expect(page.get_by_text('이전 댓글 21', exact=True)).to_be_visible()
    expect(page.get_by_role('link', name='Jira에서 댓글 보기 · 수정')).to_be_visible()
    expect(page.get_by_text('현재 댓글 1', exact=True)).to_have_count(0)
    expect(page.locator('.tkt-comments .tkt-cmt')).to_have_count(5)
    page.get_by_role('button', name='다음 댓글 페이지', exact=True).click()
    expect(page.get_by_text('이전 댓글 26', exact=True)).to_be_visible()
    page.get_by_role('button', name='이전 댓글 페이지', exact=True).click()
    expect(page.get_by_text('이전 댓글 21', exact=True)).to_be_visible()
    mode[0] = 'failed'
    page.get_by_role('button', name='이전 댓글 페이지', exact=True).click()
    expect(page.get_by_text('조회 지연', exact=True)).to_be_visible()
    expect(page.get_by_text('이전 댓글 21', exact=True)).to_be_visible()
    mode[0] = 'invalid'
    page.get_by_role('button', name='이전 댓글 페이지', exact=True).click()
    expect(page.get_by_text('댓글 응답이 불완전하거나 조회 중 변경되었습니다. 다시 시도해 주세요.', exact=True)).to_be_visible()
    expect(page.get_by_text('이전 댓글 21', exact=True)).to_be_visible()
    assert state['complete_calls'] == 0 and state['acks'] == [] and calls == [20, 25, 20, 0, 0]
    mode[0] = 'hold'
    page.get_by_role('button', name='다음 댓글 페이지', exact=True).click()
    page.wait_for_function('() => window.harness.$refs.ticket.commentPageBusy')
    page.wait_for_timeout(20)
    assert held
    # A mutation refresh supersedes a page fetch even when the ticket did not change.
    state['comments'] = [{'id': 'new', 'author': '작성자', 'html': '<p>방금 수정된 댓글</p>'}]
    state['comments_total'] = 1
    state['comments_complete'] = True
    page.evaluate("async()=> { const {api}=await import('/lib/api.js'); api.refresh(); await window.harness.$refs.ticket.reloadComments(); }")
    expect(page.get_by_text('방금 수정된 댓글', exact=True)).to_be_visible()
    held.pop().fulfill(json={'comments': [{'id': 'old', 'html': '<p>오래된 페이지</p>'}],
        'total': 2000, 'nextStartAt': 26, 'complete': False, 'fetchedAt': None})
    page.wait_for_function('() => !window.harness.$refs.ticket.commentPageBusy')
    expect(page.get_by_text('방금 수정된 댓글', exact=True)).to_be_visible()
    expect(page.get_by_text('오래된 페이지', exact=True)).to_have_count(0)
    # One oversized placeholder is incomplete content, not another nonexistent page.
    page.evaluate("() => window.harness.$refs.ticket.applyUpdateComments([{id:'large',html:'',bodyTooLarge:true}], '', {total:1,complete:false})")
    expect(page.get_by_role('button', name='다음 댓글 페이지', exact=True)).to_have_count(0)
    page.screenshot(path=str(tmp_path / 'large-ticket.png'))
    page.keyboard.press('Escape')
    expect(page.locator('.tkt-comments')).to_have_count(0)
    assert failed == [(503, '/api/issue/DL-1/comments/page')]
