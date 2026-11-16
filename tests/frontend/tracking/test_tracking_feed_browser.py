"""Grouping reorganizes saved Tracking activity without changing its evidence."""
import re

import pytest

from test_tracking_comment_browser import (
    assert_folded_comment,
    open_surface,
    tracking_browser,
)


pytestmark = pytest.mark.parametrize('tracking_browser', ['grouping'], indirect=True)


EVENT_ORDER = ['edited-comment', 'sub-completed', 'rich-comment', 'other-change', 'plain-comment', 'sub-created']
TICKET_ORDER = ['edited-comment', 'sub-completed', 'sub-created', 'rich-comment', 'plain-comment', 'other-change']


def event_ids(area):
    return area.locator('.tr-feed-event').evaluate_all(
        'events => events.map(event => event.dataset.eventId)'
    )


def test_grouping_keeps_every_event_sorted_and_persists_without_refetching(tracking_browser):
    """Catch missing events, per-day-only ticket grouping, UTC buckets and lost preferences."""
    from playwright.sync_api import expect

    page, reads, unexpected, errors = tracking_browser
    area = open_surface(page, 'feed')
    grouping = page.get_by_role('combobox', name='날짜별 활동 그룹핑', exact=True)
    expect(grouping).to_have_value('none')
    expect(grouping.locator('option')).to_have_text(['그룹핑 안함', '티켓별로 보기', '날짜 내 티켓 묶기'])
    assert event_ids(area) == EVENT_ORDER
    expect(area.locator('.tr-feed-ticket-heading')).to_have_count(0)
    before = list(reads)

    grouping.select_option('ticket')
    expect(area.locator('.tr-feed-ticket-group')).to_have_count(3)
    headings = area.locator('.tr-feed-ticket-heading')
    expect(headings).to_have_count(3)
    expect(headings.nth(0)).to_contain_text('하위 확인 업무')
    expect(headings.nth(0)).to_contain_text('SUB-1')
    expect(headings.nth(1)).to_contain_text('댓글 렌더링 업무')
    expect(headings.nth(1)).to_contain_text('TASK-1')
    expect(headings.nth(2)).to_contain_text('별도 확인 업무')
    expect(headings.nth(2)).to_contain_text('TASK-2')
    assert event_ids(area) == TICKET_ORDER
    assert event_ids(area.locator('.tr-feed-ticket-group').nth(0)) == ['edited-comment', 'sub-completed', 'sub-created']

    grouping.select_option('date-ticket')
    sections = area.locator('.tr-feed-section')
    expect(sections).to_have_count(3)
    for section, date in zip(sections.all(), ['2026.11.14', '2026.11.13', '2026.11.12']):
        expect(section).to_contain_text(date)
    expect(area).to_contain_text('한국 시간')
    expect(sections.nth(0).locator('time')).to_have_text('01:00')
    assert event_ids(sections.nth(0)) == ['edited-comment']
    assert event_ids(sections.nth(1)) == ['sub-completed', 'rich-comment', 'other-change']
    assert event_ids(sections.nth(2)) == ['plain-comment', 'sub-created']
    expect(area.locator('.tr-feed-ticket-group')).to_have_count(6)
    expect(area.locator('.tr-feed-ticket-heading')).to_have_count(6)
    assert sorted(event_ids(area)) == sorted(EVENT_ORDER)
    assert reads == before
    assert page.evaluate("localStorage.getItem('ltm.tracking.feed.grouping')") == 'date-ticket'

    page.reload()
    area = open_surface(page, 'feed')
    expect(grouping).to_have_value('date-ticket')
    assert event_ids(area) == EVENT_ORDER
    assert not unexpected
    assert not errors


@pytest.mark.parametrize('mode', ['none', 'ticket', 'date-ticket'])
def test_each_grouping_applies_search_highlight_and_renders_foldable_comments(tracking_browser, mode):
    """Catch grouping bypassing match-only filters or replacing the shared rich renderer."""
    from playwright.sync_api import expect

    page, reads, unexpected, errors = tracking_browser
    area = open_surface(page, 'feed')
    grouping = page.get_by_role('combobox', name='날짜별 활동 그룹핑', exact=True)
    grouping.select_option(mode)
    before = list(reads)
    rich = area.locator('.tr-feed-event[data-event-id="rich-comment"] .tr-comment')
    expect(rich.locator('table th')).to_have_text(['항목', '결과'])
    expect(rich.locator('ul > li')).to_have_text(['후속 확인 항목'])
    assert_folded_comment(rich).click()
    expect(rich.get_by_role('button', name='댓글 접기', exact=True)).to_have_attribute('aria-expanded', 'true')
    assert rich.locator('.tr-comment-clip').evaluate('el => el.clientHeight >= el.scrollHeight - 1')
    rich.get_by_role('button', name='댓글 접기', exact=True).click()
    assert_folded_comment(rich)
    edited = area.locator('.tr-feed-event[data-event-id="edited-comment"]')
    expect(edited.locator('.tr-event-kind')).to_have_text('댓글 수정')
    expect(edited.locator('.tr-comment-body > p')).to_have_text(['Edited first line', 'Edited second line', 'Edited third line'])
    assert_folded_comment(edited.locator('.tr-comment')).click()
    expect(edited.get_by_role('button', name='댓글 접기', exact=True)).to_have_attribute('aria-expanded', 'true')
    assert page.locator('button button, button a, a button').count() == 0

    search = page.get_by_role('textbox', name='업무 검색')
    search.fill('하위 확인')
    page.get_by_role('combobox', name='검색 표시 방식').select_option('matches')
    assert event_ids(area) == ['edited-comment', 'sub-completed', 'sub-created']
    expect(area.locator('.tr-feed-ticket-heading').filter(has_text='TASK-1')).to_have_count(0)
    search.fill('')
    page.get_by_role('combobox', name='사람 또는 업무 강조', exact=True).select_option('task:SUB-1')
    assert event_ids(area) == ['edited-comment', 'sub-completed', 'sub-created']
    page.get_by_role('button', name='강조 지우기', exact=True).click()
    assert sorted(event_ids(area)) == sorted(EVENT_ORDER)
    page.get_by_role('navigation', name='업무 계층 탐색', exact=True).get_by_role(
        'button', name='소속 Epic 없음 펼치기', exact=True,
    ).click()
    page.get_by_role('button', name='댓글 렌더링 업무 (TASK-1) 범위 선택', exact=True).click()
    area = open_surface(page, 'feed')
    assert sorted(event_ids(area)) == sorted(['edited-comment', 'sub-completed', 'rich-comment', 'plain-comment', 'sub-created'])
    expect(area.locator('.tr-feed-ticket-heading').filter(has_text='TASK-2')).to_have_count(0)
    for next_mode in ['none', 'ticket', 'date-ticket']:
        grouping.select_option(next_mode)
        assert sorted(event_ids(area)) == sorted(['edited-comment', 'sub-completed', 'rich-comment', 'plain-comment', 'sub-created'])
    assert reads == before

    area.locator('.tr-feed-event[data-event-id="rich-comment"] .tr-feed-ticket').click()
    expect(page.locator('.tp-evidence')).to_be_visible()
    expect(page.get_by_role('complementary', name='상세 근거')).to_be_visible()
    assert reads == before
    assert not unexpected
    assert not errors
