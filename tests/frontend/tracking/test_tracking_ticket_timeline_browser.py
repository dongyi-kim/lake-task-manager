"""The real Tracking panels use a compact, actual-time axis and saved evidence."""
import re
from copy import deepcopy

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
           'bodyText': '업무 보고 ' + str(i), 'date': f'2026-11-12T04:{i*2:02}:00Z'} for i in range(8)],
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
def test_combined_time_axis_preserves_spacing_density_and_evidence_navigation(tracking_browser, width, theme):
    from playwright.sync_api import expect

    page, reads, unexpected, errors = tracking_browser
    page.set_viewport_size({'width': width, 'height': 1000})
    page.locator('html').evaluate('(el,theme)=>el.dataset.theme=theme', theme)
    before = list(reads)
    timeline = open_task_detail(page)
    expect(timeline).to_be_visible()
    expect(timeline).to_have_attribute('data-issue-key', 'TASK-1')
    expect(timeline.get_by_role('heading', name='이 Task의 상태 변화', exact=True)).to_be_visible()
    expect(timeline.locator('.tt-axis')).to_have_count(1)
    axis = timeline.locator('.tt-axis[data-source-key="combined"]')
    expect(axis).to_be_visible()
    expect(timeline.locator('.tt-child-lanes')).to_have_count(0)
    expect(timeline.locator('.tt-transition')).to_have_count(4)
    expect(timeline.locator('.tt-transition[data-event-id="child-done"]')).to_have_count(0)
    expect(timeline.locator('.tt-child-event[data-event-id="child-done"]')).to_have_count(1)
    expect(page.locator('.tp-event[data-event-id="child-done"]')).to_have_count(1)
    expect(axis.locator('.tt-transition[data-event-id="done"]')).to_have_attribute('aria-label', re.compile(r'10:00.*Done'))
    settle_layout(axis)
    positions = axis.locator('.tt-transition').evaluate_all('els=>Object.fromEntries(els.map(el=>[el.dataset.eventId,{x:el.getBoundingClientRect().left+el.getBoundingClientRect().width/2,at:Number(el.dataset.at)}]))')
    hour_dx = positions['reopened']['x'] - positions['done']['x']
    assert hour_dx > 0
    assert abs(positions['review']['x'] - positions['reopened']['x'] - hour_dx) < 0.1
    assert abs((positions['done']['x'] - positions['start']['x']) - 48*hour_dx) < 1
    assert positions['review']['at'] - positions['done']['at'] == 2*3600000
    assert_compact_callouts(axis)
    page.emulate_media(reduced_motion='reduce')
    assert axis.locator('.tt-callout').first.evaluate('el=>getComputedStyle(el).transitionDuration') == '0s'
    page.emulate_media(reduced_motion='no-preference')
    assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
    summary_before = timeline.locator('.tt-summary').text_content()
    timeline.get_by_role('button', name='하위 작업별 펼치기', exact=True).click()
    expect(timeline.locator('.tt-child-lanes .tt-axis')).to_have_count(1)
    expect(axis.locator('.tt-transition')).to_have_count(4)
    expect(timeline.locator('.tt-summary')).to_have_text(summary_before)
    timeline.get_by_role('button', name='하위 작업별 접기', exact=True).click()
    expect(timeline.locator('.tt-axis')).to_have_count(1)
    comment_group = axis.locator('.tt-comment-group')
    expect(comment_group).to_have_count(1)
    comment_group.click()
    dialog = timeline.locator('.tt-comment-dialog')
    expect(dialog).to_be_visible()
    expect(dialog.locator('button[data-event-id]')).to_have_count(8)
    page.keyboard.press('Escape')
    expect(dialog).to_have_count(0)
    expect(comment_group).to_be_focused()
    comment_group.press('Enter')
    comment = dialog.locator('button[data-event-id="report-7"]')
    comment.focus()
    page.keyboard.press('Enter')
    expect(page.locator('.tp-event[data-event-id="report-7"]')).to_have_class(re.compile(r'\bis-selected\b'))
    expect(page.locator('.tp-event[data-event-id="report-7"]')).to_be_in_viewport()
    timeline.locator('.tt-transition[data-event-id="done"]').click()
    expect(page.locator('.tp-event[data-event-id="done"]')).to_have_class(re.compile(r'\bis-selected\b'))
    assert reads == before
    assert not unexpected
    assert not errors


def assert_compact_callouts(axis):
    """Visible guides stay one line and do not collide after measured layout."""
    settle_layout(axis)
    geometry = axis.locator('.tt-callout').evaluate_all('els=>els.map(el=>{const r=el.getBoundingClientRect();return {id:el.dataset.eventId,x:r.x,y:r.y,w:r.width,h:r.height,text:el.innerText,whiteSpace:getComputedStyle(el).whiteSpace}})')
    assert geometry
    for item in geometry:
        assert abs(item['h'] - 24) < 1
        assert not re.search(r'\b\d{1,2}:\d{2}\b|2026-', item['text']), item
    short_changes = axis.locator('.tt-callout strong').evaluate_all('els=>els.filter(el=>el.textContent.length<=11).map(el=>({text:el.textContent,visible:el.clientWidth,full:el.scrollWidth}))')
    assert all(item['full'] <= item['visible'] + 1 for item in short_changes), short_changes
    for index, item in enumerate(geometry):
        for other in geometry[index+1:]:
            overlap_x = min(item['x']+item['w'], other['x']+other['w']) - max(item['x'], other['x'])
            overlap_y = min(item['y']+item['h'], other['y']+other['h']) - max(item['y'], other['y'])
            assert overlap_x < 1 or overlap_y < 1, (item, other)


def settle_layout(axis):
    # ResizeObserver and Vue's measured-label update both complete before geometry reads.
    axis.evaluate('async()=>{for(let i=0;i<3;i++)await new Promise(requestAnimationFrame)}')


def replace_saved_run(page, run):
    page.evaluate('run=>window.dispatchEvent(new CustomEvent("api-cache-updated", {detail:{'
                  'path:"/api/tracking/runs/saved-comments",value:run}}))',
                  {**run, 'id': 'saved-comments', 'status': 'complete'})


def test_time_zoom_exposes_night_events_without_changing_summary_or_querying(tracking_browser):
    from playwright.sync_api import expect

    page, reads, unexpected, errors = tracking_browser
    run = deepcopy(RUN)
    run['events'].append({'id': 'night-report', 'issueKey': 'TASK-1', 'kind': 'comment',
                          'bodyText': '야간 처리 확인', 'date': '2026-11-12T14:10:00Z'})
    replace_saved_run(page, run)
    timeline = open_task_detail(page)
    before, summary = list(reads), timeline.locator('.tt-summary').text_content()
    timeline.locator('.tt-overview button[data-date="2026-11-12"]').click()
    axis = timeline.locator('.tt-axis[data-source-key="combined"]')
    expect(axis.locator('.tt-marker[data-event-id="night-report"]')).to_have_count(0)
    timeline.get_by_role('button', name=re.compile('24시간')).click()
    night = axis.locator('.tt-marker[data-event-id="night-report"]')
    expect(night).to_be_visible()
    expect(night).to_have_attribute('aria-label', re.compile('23:10'))
    expect(timeline.locator('.tt-summary')).to_have_text(summary)
    timeline.get_by_role('button', name='전체 기간', exact=True).click()
    expect(axis.locator('.tt-transition')).to_have_count(4)
    expect(timeline.locator('.tt-summary')).to_have_text(summary)
    assert reads == before
    assert not unexpected
    assert not errors


def test_dense_same_time_changes_and_74_comments_keep_sources_and_all_evidence(tracking_browser):
    from playwright.sync_api import expect

    page, reads, unexpected, errors = tracking_browser
    page.set_viewport_size({'width': 1280, 'height': 900})
    run = deepcopy(RUN)
    run['issues'][0]['summary'] = '같은 업무명으로 반복되지만 원본 키로 식별하는 매우 긴 검증 업무 제목'
    run['issues'][1]['summary'] = run['issues'][0]['summary']
    run['events'] = [event for event in run['events'] if not event['id'].startswith('report-')]
    run['events'].extend([
        *[{'id': f'dense-{i}', 'issueKey': 'TASK-1', 'kind': 'fieldChanged', 'field': 'status',
           'from': f'Review {i}', 'to': f'Review {i+1}', 'date': '2026-11-12T02:00:00Z'} for i in range(12)],
        *[{'id': f'many-{i}', 'issueKey': 'TASK-1', 'kind': 'comment', 'commentId': str(i),
           'bodyText': f'독립적으로 확인 가능한 업무 보고 {i}',
           'date': f'2026-11-12T04:{i//60:02}:{i%60:02}Z'} for i in range(74)],
        *[{'id': f'child-comment-{i}', 'issueKey': 'SUB-1', 'kind': 'comment',
           'bodyText': f'하위 원본 보고 {i}', 'date': f'2026-11-12T04:00:0{i}Z'} for i in range(2)],
    ])
    replace_saved_run(page, run)
    before = list(reads)
    timeline = open_task_detail(page)
    axis = timeline.locator('.tt-axis[data-source-key="combined"]')
    expect(axis.locator('.tt-transition')).to_have_count(16)
    # Verify the production side panel, not a full-width standalone preview.
    assert timeline.evaluate('el=>el.getBoundingClientRect().width') < 440
    assert_compact_callouts(axis)
    markers = axis.locator('.tt-marker[data-event-id^="dense-"]').evaluate_all('els=>els.map(el=>{const r=el.getBoundingClientRect();return {x:r.x+r.width/2,y:r.y+r.height/2}})')
    assert len(markers) == 12
    assert max(item['x'] for item in markers) - min(item['x'] for item in markers) < 0.1
    assert len({round(item['y'], 1) for item in markers}) == 12
    for event_id in ['dense-0', 'dense-11', 'done']:
        marker = axis.locator(f'.tt-marker[data-event-id="{event_id}"]')
        leader = axis.locator(f'.tt-leader[data-event-id="{event_id}"]')
        expect(leader).to_have_count(1)
        start = leader.evaluate('el=>{const p=el.getPointAtLength(0);const q=new DOMPoint(p.x,p.y).matrixTransform(el.getScreenCTM());return {x:q.x,y:q.y}}')
        rect = marker.bounding_box()
        assert abs(start['x'] - rect['x'] - rect['width']/2) < 1
        assert abs(start['y'] - rect['y'] - rect['height']/2) <= 12
    groups = axis.locator('.tt-comment-group')
    expect(groups).to_have_count(2)
    own_group = groups.filter(has_text='74')
    expect(own_group).to_have_attribute('aria-label', re.compile('TASK-1'))
    own_group.click()
    dialog = timeline.locator('.tt-comment-dialog')
    expect(dialog.locator('button[data-event-id]')).to_have_count(74)
    expect(dialog).not_to_contain_text('하위 원본 보고')
    page.keyboard.press('Escape')
    expect(own_group).to_be_focused()
    own_group.press('Enter')
    dialog.locator('button[data-event-id="many-73"]').click()
    expect(page.locator('.tp-event[data-event-id="many-73"]')).to_be_in_viewport()
    expect(page.locator('.tp-event[data-event-id="many-73"]')).to_have_class(re.compile(r'\bis-selected\b'))
    assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
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
    expect(timeline.locator('.tt-comment')).to_have_count(1)
    expect(timeline.locator('.tt-comment')).to_have_attribute('aria-label', re.compile(r'11[./-]14.*00:00'))
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
