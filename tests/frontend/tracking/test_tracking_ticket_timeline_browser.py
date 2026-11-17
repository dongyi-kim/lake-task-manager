"""The real detail panel renders and navigates own-issue horizontal timelines."""
import re

import pytest

from test_tracking_comment_browser import tracking_browser


COVERAGE = {'complete': True, 'historyComplete': True, 'commentsComplete': True}
RUN = {
    'request': {'startDate': '2026-11-10', 'endDate': '2026-11-16', 'people': [], 'modules': []},
    'coverage': COVERAGE,
    'issues': [
        {'key': 'TASK-1', 'summary': '상태 변화와 보고를 확인하는 업무', 'type': 'Task', 'coverage': COVERAGE,
         'status': 'Review', 'statusCategory': 'indeterminate'},
        {'key': 'SUB-1', 'summary': '하위 재현 확인', 'type': 'Sub-Task', 'isSubtask': True,
         'parentKey': 'TASK-1', 'coverage': COVERAGE},
        {'key': 'LEAF-1', 'summary': '댓글만 있는 독립 업무', 'type': 'Bug', 'coverage': COVERAGE},
        {'key': 'EMPTY-1', 'summary': '확인된 활동이 없는 업무', 'type': 'Task', 'coverage': COVERAGE, 'status': 'Done'},
        {'key': 'PARTIAL-1', 'summary': '이력 확인 중인 업무', 'type': 'Task', 'coverage': {'commentsComplete': True}},
    ],
    'tree': [{'id': 'timeline-wbs', 'kind': 'wbs', 'name': '타임라인 검증', 'children': [
        {'id': 'task:TASK-1', 'key': 'TASK-1', 'kind': 'Task', 'children': [
            {'id': 'task:SUB-1', 'key': 'SUB-1', 'kind': 'Sub-Task', 'children': []}]},
        *[{'id': 'task:' + key, 'key': key, 'kind': 'Task', 'children': []}
          for key in ['LEAF-1', 'EMPTY-1', 'PARTIAL-1']],
    ]}],
    'events': [
        {'id': 'start', 'issueKey': 'TASK-1', 'kind': 'fieldChanged', 'field': 'status',
         'from': 'Open', 'to': 'In Progress', 'date': '2026-11-10T01:00:00Z'},
        {'id': 'done', 'issueKey': 'TASK-1', 'kind': 'completed', 'field': 'status',
         'from': 'In Progress', 'to': 'Done', 'date': '2026-11-12T01:00:00Z'},
        {'id': 'reopened', 'issueKey': 'TASK-1', 'kind': 'reopened', 'field': 'status',
         'from': 'Done', 'to': 'In Progress', 'date': '2026-11-12T02:00:00Z'},
        {'id': 'review', 'issueKey': 'TASK-1', 'kind': 'fieldChanged', 'field': 'status',
         'from': 'In Progress', 'to': 'Review', 'date': '2026-11-12T03:00:00Z'},
        *[{'id': 'report-' + str(i), 'issueKey': 'TASK-1', 'kind': 'comment', 'commentId': str(i),
           'bodyText': '업무 보고 ' + str(i), 'date': f'2026-11-12T{4+i:02}:00:00Z'} for i in range(8)],
        {'id': 'child-done', 'issueKey': 'SUB-1', 'kind': 'completed', 'from': 'Doing', 'to': 'Done',
         'date': '2026-11-14T01:00:00Z'},
        {'id': 'leaf-comment', 'issueKey': 'LEAF-1', 'kind': 'comment', 'bodyText': '원인 확인 보고',
         'date': '2026-11-13T15:00:00Z'},
    ],
}

pytestmark = pytest.mark.parametrize('tracking_browser', [RUN], indirect=True)


def open_task_detail(page):
    page.get_by_role('navigation', name='업무 계층 탐색').get_by_role(
        'button', name='소속 Epic 없음 범위 선택', exact=True).click()
    page.locator('.tb-row[data-key="TASK-1"] .tb-evidence .tb-text').click()
    return page.locator('.tt-timeline')


@pytest.mark.parametrize('width,theme', [(1440, 'light'), (1280, 'light'), (1440, 'dark'), (1280, 'dark')])
def test_own_timeline_keeps_date_spacing_dense_events_and_evidence_navigation(tracking_browser, width, theme):
    from playwright.sync_api import expect

    page, reads, unexpected, errors = tracking_browser
    page.set_viewport_size({'width': width, 'height': 1000})
    page.locator('html').evaluate('(el,theme)=>el.dataset.theme=theme', theme)
    before = list(reads)
    timeline = open_task_detail(page)
    expect(timeline).to_be_visible()
    expect(timeline).to_have_attribute('data-issue-key', 'TASK-1')
    expect(timeline.get_by_role('heading', name='이 Task의 상태 변화', exact=True)).to_be_visible()
    expect(timeline.locator('.tt-day')).to_have_count(7)
    expect(timeline.locator('.tt-transition')).to_have_count(4)
    expect(timeline.locator('.tt-transition[data-event-id="child-done"]')).to_have_count(0)
    expect(timeline.locator('.tt-child-event[data-event-id="child-done"]')).to_have_count(1)
    expect(page.locator('.tp-event[data-event-id="child-done"]')).to_have_count(1)
    expect(timeline.locator('.tt-transition')).to_have_text([
        '10:00상태Open → In Progress', '10:00완료In Progress → Done',
        '11:00재오픈Done → In Progress', '12:00상태In Progress → Review',
    ], use_inner_text=False)
    spacing = timeline.locator('.tt-day').evaluate_all('days=>days.map(day=>day.getBoundingClientRect().left)')
    assert abs((spacing[2] - spacing[0]) - 2 * (spacing[1] - spacing[0])) < 1
    markers = timeline.locator('.tt-day').evaluate_all("days=>days.map(day=>({active:day.classList.contains('has-status'),fill:getComputedStyle(day.querySelector('.tt-date'),'::after').backgroundColor}))")
    assert markers[0]['active'] and not markers[1]['active'] and markers[2]['active']
    assert markers[0]['fill'] == markers[2]['fill'] != markers[1]['fill'], 'Empty dates use hollow markers, not filled status markers'
    assert timeline.locator('.tt-scroll').evaluate('el=>el.scrollWidth>el.clientWidth')
    assert timeline.evaluate('el=>el.getBoundingClientRect().height') < 550
    assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
    comment_group = timeline.get_by_role('button', name='11.12 댓글·수정 8건 펼치기', exact=True)
    comment_group.click()
    expect(timeline.locator('.tt-comment')).to_have_count(8)
    comment = timeline.locator('.tt-comment[data-event-id="report-7"]')
    comment.focus()
    page.keyboard.press('Enter')
    expect(page.locator('.tp-event[data-event-id="report-7"]')).to_have_class(re.compile(r'\bis-selected\b'))
    expect(page.locator('.tp-event[data-event-id="report-7"]')).to_be_in_viewport()
    timeline.locator('.tt-transition[data-event-id="done"]').click()
    expect(page.locator('.tp-event[data-event-id="done"]')).to_have_class(re.compile(r'\bis-selected\b'))
    assert reads == before
    assert not unexpected
    assert not errors


def test_leaf_comments_subtask_single_transition_empty_and_partial_states(tracking_browser):
    from playwright.sync_api import expect

    page, reads, unexpected, errors = tracking_browser
    before = list(reads)
    timeline = open_task_detail(page)
    page.get_by_role('button', name='상세 근거 닫기', exact=True).click()
    page.locator('.tb-row[data-key="LEAF-1"] .tb-title-button').click()
    expect(page.locator('.tp-evidence-leaf .tt-timeline')).to_be_visible()
    expect(timeline.locator('.tt-transition')).to_have_count(0)
    expect(timeline).to_contain_text('확인된 상태 변화가 없습니다.')
    expect(timeline.locator('.tt-day[data-date="2026-11-14"] .tt-comment')).to_have_count(1)
    assert timeline.locator('.tt-day[data-date="2026-11-14"] .tt-date').evaluate("el=>getComputedStyle(el,'::after').transform!=='none'"), 'Comment-only dates have a diamond marker'
    timeline.locator('.tt-comment').click()
    expect(page.locator('.tp-event[data-event-id="leaf-comment"]')).to_be_in_viewport()
    page.get_by_role('button', name='상위 범위로 돌아가기', exact=True).click()
    page.locator('.tb-row[data-key="TASK-1"] .tb-title-button').click()
    page.locator('.tb-row[data-key="SUB-1"] .tb-title-button').click()
    expect(timeline).to_have_attribute('data-issue-key', 'SUB-1')
    expect(timeline.get_by_role('heading', name='이 Sub-task의 상태 변화', exact=True)).to_be_visible()
    expect(timeline.locator('.tt-transition')).to_have_count(1)
    expect(timeline.locator('.tt-comment')).to_have_count(0)
    page.get_by_role('button', name='상위 범위로 돌아가기', exact=True).click()
    page.get_by_role('navigation', name='업무 계층 탐색').get_by_role(
        'button', name='소속 Epic 없음 범위 선택', exact=True).click()
    page.locator('.tb-row[data-key="EMPTY-1"] .tb-title-button').click()
    expect(timeline).to_contain_text('확인된 상태 변화·댓글이 없습니다.')
    expect(timeline.locator('.tt-transition')).to_have_count(0)
    expect(timeline.locator('.tt-partial')).to_have_count(0)
    page.get_by_role('button', name='상위 범위로 돌아가기', exact=True).click()
    page.locator('.tb-row[data-key="PARTIAL-1"] .tb-title-button').click()
    expect(timeline.locator('.tt-partial')).to_contain_text('수집이 완료되지 않았습니다.')
    assert reads == before
    assert not unexpected
    assert not errors


def test_switching_detail_starts_at_timeline_without_resetting_same_ticket_updates(tracking_browser):
    from playwright.sync_api import expect

    page, reads, unexpected, errors = tracking_browser
    page.set_viewport_size({'width': 1440, 'height': 650})
    timeline = open_task_detail(page)
    scroll = page.locator('.tp-evidence-scroll')
    scroll.evaluate('el=>el.scrollTop=el.scrollHeight')
    assert scroll.evaluate('el=>el.scrollTop') > 500

    page.locator('.tp-tree-label[aria-label*="LEAF-1"]').click()
    expect(timeline).to_have_attribute('data-issue-key', 'LEAF-1')
    expect(timeline).to_be_in_viewport()
    assert scroll.evaluate('el=>el.scrollTop') == 0

    timeline.locator('.tt-comment').click()
    evidence = page.locator('.tp-event[data-event-id="leaf-comment"]')
    expect(evidence).to_be_in_viewport()
    expect(evidence).to_have_class(re.compile(r'\bis-selected\b'))
    before_update = scroll.evaluate('el=>el.scrollTop')
    assert before_update > 0
    page.evaluate('run=>window.dispatchEvent(new CustomEvent("api-cache-updated", {detail:{'
                  'path:"/api/tracking/runs/saved-comments",value:run}}))',
                  {**RUN, 'id': 'saved-comments', 'status': 'complete'})
    page.wait_for_function('before=>Math.abs(document.querySelector(".tp-evidence-scroll").scrollTop-before)<1',
                           arg=before_update)
    expect(evidence).to_be_in_viewport()
    assert not unexpected
    assert not errors
