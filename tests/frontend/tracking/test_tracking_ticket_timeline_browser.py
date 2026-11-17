"""The real Tracking panels use a compact, actual-time axis and saved evidence."""
import re
from datetime import datetime, timezone, timedelta
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
    expected = [event for event in RUN['events'] if event['issueKey'] in {'TASK-1', 'SUB-1'}]
    assert_original_partition(axis, expected)
    expect(page.locator('.tp-event[data-event-id="child-done"]')).to_have_count(1)
    # Compressed markers retain actual time (the midpoint of their original range).
    geometry = axis.locator('.tt-marker').evaluate_all('els=>els.map(el=>{const r=el.getBoundingClientRect();return {x:r.x+r.width/2,at:Number(el.dataset.at)}})')
    geometry.sort(key=lambda item: item['at'])
    first, last = geometry[0], geometry[-1]
    scale = (last['x']-first['x'])/(last['at']-first['at'])
    assert scale > 0
    assert all(abs(item['x']-first['x']-(item['at']-first['at'])*scale) < 1 for item in geometry)
    assert_axis_geometry(axis)
    assert_compact_callouts(axis)
    page.emulate_media(reduced_motion='reduce')
    assert axis.locator('.tt-callout').first.evaluate('el=>getComputedStyle(el).transitionDuration') == '0s'
    page.emulate_media(reduced_motion='no-preference')
    assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
    summary_before = timeline.locator('.tt-summary').text_content()
    timeline.get_by_role('button', name='하위 작업별 펼치기', exact=True).click()
    expect(timeline.locator('.tt-child-lanes .tt-axis')).to_have_count(1)
    assert_original_partition(axis, expected)
    expect(timeline.locator('.tt-summary')).to_have_text(summary_before)
    timeline.get_by_role('button', name='하위 작업별 접기', exact=True).click()
    expect(timeline.locator('.tt-axis')).to_have_count(1)
    timeline.get_by_role('button', name='댓글 표시', exact=True).click()
    assert_original_partition(axis, [event for event in expected if event['kind'] != 'comment'])
    expect(timeline.locator('.tt-summary')).to_have_text(summary_before)
    timeline.get_by_role('button', name='댓글 표시', exact=True).click()
    assert_original_partition(axis, expected)
    comment_group = marker_for_original(axis, 'report-7')
    expect(comment_group).to_have_attribute('data-grouped', 'true')
    comment_group.click()
    dialog = timeline.locator('.tt-event-dialog')
    expect(dialog).to_be_visible()
    expect(dialog.locator('button[data-event-id="report-7"]')).to_contain_text('업무 보고 7')
    page.keyboard.press('Escape')
    expect(dialog).to_have_count(0)
    expect(comment_group).to_be_focused()
    comment_group.press('Enter')
    comment = dialog.locator('button[data-event-id="report-7"]')
    comment.focus()
    page.keyboard.press('Enter')
    expect(page.locator('.tp-event[data-event-id="report-7"]')).to_have_class(re.compile(r'\bis-selected\b'))
    expect(page.locator('.tp-event[data-event-id="report-7"]')).to_be_in_viewport()
    expect(marker_for_original(axis, 'report-7')).to_have_class(re.compile(r'\bis-selected\b'))
    activate_original(page, axis, 'done')
    expect(page.locator('.tp-event[data-event-id="done"]')).to_have_class(re.compile(r'\bis-selected\b'))
    assert reads == before
    assert not unexpected
    assert not errors


def marker_for_original(axis, event_id):
    """A compressed marker can represent any original, including hidden callouts."""
    settle_layout(axis)
    indices = axis.locator('.tt-marker').evaluate_all(
        '(els,id)=>els.flatMap((el,i)=>JSON.parse(el.dataset.memberIds).includes(id)?[i]:[])', event_id)
    assert len(indices) == 1, (event_id, indices)
    return axis.locator('.tt-marker').nth(indices[0])


def test_dates_stay_visible_while_scrolling_inside_the_detail_axis(tracking_browser):
    page, reads, unexpected, errors = tracking_browser
    timeline = open_task_detail(page)
    axis = timeline.locator('.tt-axis[data-source-key="combined"]')
    settle_layout(axis)
    scroll = page.locator('.tp-evidence-scroll')
    scroll.evaluate('el=>{const ticks=el.querySelector(".tt-axis-ticks");el.scrollTop+=ticks.getBoundingClientRect().top-el.getBoundingClientRect().top+50}')
    settle_layout(axis)
    # The detail scroll container retains its top padding when sticky content pins.
    content_top = scroll.evaluate('el=>el.getBoundingClientRect().top+el.clientTop+parseFloat(getComputedStyle(el).paddingTop)')
    tick_box = axis.locator('.tt-axis-ticks').bounding_box()
    plot_box = axis.locator('.tt-axis-plot').bounding_box()
    assert plot_box['y'] < content_top + 32
    assert abs(tick_box['y'] - content_top) <= 2
    assert tick_box['y'] + tick_box['height'] < plot_box['y'] + plot_box['height']
    # A further scroll moves the plot while the date ruler stays at that inset.
    scroll.evaluate('el=>el.scrollTop+=20')
    settle_layout(axis)
    scrolled_ticks = axis.locator('.tt-axis-ticks').bounding_box()
    scrolled_plot = axis.locator('.tt-axis-plot').bounding_box()
    assert abs(scrolled_ticks['y'] - content_top) <= 2
    assert abs(scrolled_plot['y'] - (plot_box['y'] - 20)) <= 2
    assert scrolled_ticks['y'] + scrolled_ticks['height'] < scrolled_plot['y'] + scrolled_plot['height']
    assert not unexpected
    assert not errors


def activate_original(page, axis, event_id):
    marker = marker_for_original(axis, event_id)
    grouped = marker.get_attribute('data-grouped') == 'true'
    marker.click()
    if grouped:
        axis.locator(f'.tt-event-dialog button[data-event-id="{event_id}"]').click()


def assert_original_partition(axis, events):
    settle_layout(axis)
    markers = axis.locator('.tt-marker').evaluate_all('els=>els.map(el=>({ids:JSON.parse(el.dataset.memberIds),count:Number(el.dataset.count),sources:Number(el.dataset.sourceCount),label:el.getAttribute("aria-label"),grouped:el.dataset.grouped,id:el.dataset.eventId,at:Number(el.dataset.at)}))')
    originals = {event['id']: event for event in events}
    represented = [event_id for marker in markers for event_id in marker['ids']]
    assert len(represented) == len(set(represented))
    assert set(represented) == set(originals)
    assert sum(marker['count'] for marker in markers) == len(events)
    for marker in markers:
        members = [originals[event_id] for event_id in marker['ids']]
        assert marker['count'] == len(members)
        assert marker['id'] == marker['ids'][0]
        assert marker['sources'] == len({event['issueKey'] for event in members})
        if marker['sources'] > 1:
            assert f"{marker['sources']}개 작업" in marker['label']
        assert marker['grouped'] == ('true' if len(members) > 1 else 'false')
        times = [datetime.fromisoformat(event['date'].replace('Z', '+00:00')).timestamp()*1000 for event in members]
        assert abs(marker['at'] - (min(times)+max(times))/2) < 1
    return markers


def assert_axis_geometry(axis):
    settle_layout(axis)
    assert axis.evaluate('el=>el.getBoundingClientRect().height') <= 260
    assert axis.locator('.tt-axis-plot').evaluate('el=>el.getBoundingClientRect().height') == 192
    rail = axis.locator('.tt-axis-rail')
    assert rail.evaluate('el=>getComputedStyle(el).borderTopWidth') == '2px'
    assert rail.evaluate('el=>getComputedStyle(el).borderTopStyle') == 'solid'
    # WCAG graphical-object contrast: the time rail must remain visible in both themes.
    contrast = rail.evaluate(r"""el=>{
      const rgb=s=>s.match(/[\d.]+/g).slice(0,3).map(Number).map(v=>{v/=255;return v<=.04045?v/12.92:((v+.055)/1.055)**2.4});
      const luminance=s=>{const c=rgb(s);return .2126*c[0]+.7152*c[1]+.0722*c[2]};
      const a=luminance(getComputedStyle(el).borderTopColor),b=luminance(getComputedStyle(el.parentElement).backgroundColor);
      return (Math.max(a,b)+.05)/(Math.min(a,b)+.05);
    }""")
    assert contrast >= 3, contrast
    ticks = axis.locator('.tt-axis-ticks time')
    assert ticks.count() >= 3
    tick_geometry = ticks.evaluate_all('els=>els.map(el=>{const r=el.getBoundingClientRect();return {left:r.left,right:r.right}})')
    axis_rect = axis.bounding_box()
    assert all(item['left'] >= axis_rect['x']-1 and item['right'] <= axis_rect['x']+axis_rect['width']+1 for item in tick_geometry)
    for previous, current in zip(tick_geometry, tick_geometry[1:]):
        assert previous['right']+3 <= current['left'], tick_geometry
    for tick in ticks.all():
        assert tick.is_visible()
        assert tick.evaluate('el=>parseFloat(getComputedStyle(el).fontSize)') >= 12
        assert tick.evaluate('el=>Number(getComputedStyle(el).fontWeight)') >= 600
    levels = axis.locator('.tt-marker').evaluate_all('els=>els.map(el=>Math.round(parseFloat(el.style.top)))')
    assert len(set(levels)) <= 3


def assert_original_dialog(axis, run, events):
    from playwright.sync_api import expect

    axis.locator('.tt-axis-footer').get_by_role('button', name=f'원본 기록 {len(events)}건', exact=True).click()
    dialog = axis.locator('.tt-event-dialog')
    rows = dialog.locator('button[data-event-id]')
    expect(rows).to_have_count(len(events))
    assert set(rows.evaluate_all('els=>els.map(el=>el.dataset.eventId)')) == {event['id'] for event in events}
    issues = {issue['key']: issue for issue in run['issues']}
    for event in events:
        row = dialog.locator(f'button[data-event-id="{event["id"]}"]')
        expect(row).to_have_attribute('data-issue-key', event['issueKey'])
        expect(row.locator('strong')).to_contain_text(issues[event['issueKey']]['summary'])
        expect(row.locator('strong small')).to_have_text(event['issueKey'])
        local = datetime.fromisoformat(event['date'].replace('Z', '+00:00')).astimezone(timezone(timedelta(hours=9)))
        expect(row.locator('time')).to_contain_text(local.strftime('%Y-%m-%d %H:%M:%S')+' KST')
        if 'bodyText' in event:
            expect(row.locator('span')).to_have_text(event['bodyText'])
        elif event.get('from') and event.get('to'):
            expect(row.locator('span')).to_contain_text(event['from'])
            expect(row.locator('span')).to_contain_text(event['to'])
    return dialog


def assert_compact_callouts(axis):
    """Visible guides stay one line and do not collide after measured layout."""
    settle_layout(axis)
    geometry = axis.locator('.tt-callout').evaluate_all('els=>els.map(el=>{const r=el.getBoundingClientRect();return {id:el.dataset.eventId,x:r.x,y:r.y,w:r.width,h:r.height,text:el.innerText,whiteSpace:getComputedStyle(el).whiteSpace}})')
    assert geometry
    plot_width = axis.locator('.tt-axis-plot').evaluate('el=>el.clientWidth')
    assert len(geometry) <= (2 if plot_width < 600 else 6)
    assert len({round(item['y'], 1) for item in geometry}) <= 2
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
    run['events'].append({'id': 'early-report', 'issueKey': 'TASK-1', 'kind': 'comment',
                          'bodyText': '자정 이후 처리 확인', 'date': '2026-11-11T15:10:00Z'})
    replace_saved_run(page, run)
    timeline = open_task_detail(page)
    before, summary = list(reads), timeline.locator('.tt-summary').text_content()
    timeline.locator('.tt-overview button[data-date="2026-11-12"]').click()
    axis = timeline.locator('.tt-axis[data-source-key="combined"]')
    daytime_ids = axis.locator('.tt-marker').evaluate_all('els=>els.flatMap(el=>JSON.parse(el.dataset.memberIds))')
    assert 'night-report' not in daytime_ids
    assert 'early-report' not in daytime_ids
    timeline.get_by_role('button', name=re.compile('24시간')).click()
    night = marker_for_original(axis, 'night-report')
    expect(night).to_be_visible()
    expect(night).to_have_attribute('aria-label', re.compile('23:10'))
    early = marker_for_original(axis, 'early-report')
    expect(early).to_have_attribute('aria-label', re.compile('2026-11-12 00:10'))
    day_events = [event for event in run['events'] if event['issueKey'] in {'TASK-1', 'SUB-1'} and datetime.fromisoformat(event['date'].replace('Z', '+00:00')).astimezone(timezone(timedelta(hours=9))).date().isoformat() == '2026-11-12']
    assert_original_partition(axis, day_events)
    dialog = assert_original_dialog(axis, run, day_events)
    page.keyboard.press('Escape')
    expect(dialog).to_have_count(0)
    expect(timeline.locator('.tt-summary')).to_have_text(summary)
    timeline.get_by_role('button', name='전체 기간', exact=True).click()
    assert_original_partition(axis, [event for event in run['events'] if event['issueKey'] in {'TASK-1', 'SUB-1'}])
    expect(timeline.locator('.tt-summary')).to_have_text(summary)
    assert reads == before
    assert not unexpected
    assert not errors


@pytest.mark.parametrize('width', [1280, 1540])
def test_dense_same_time_changes_and_74_comments_keep_sources_and_all_evidence(tracking_browser, width):
    from playwright.sync_api import expect

    page, reads, unexpected, errors = tracking_browser
    page.set_viewport_size({'width': width, 'height': 900})
    run = deepcopy(RUN)
    run['issues'][0]['summary'] = '같은 업무명으로 반복되지만 원본 키로 식별하는 매우 긴 검증 업무 제목'
    run['issues'][1]['summary'] = run['issues'][0]['summary']
    run['events'] = [event for event in run['events'] if not event['id'].startswith('report-')]
    run['events'].extend([
        *[{'id': f'dense-{i}', 'issueKey': 'TASK-1', 'kind': 'fieldChanged', 'field': 'status',
           'from': f'Review {i}', 'to': f'Review {i+1}', 'date': '2026-11-12T02:00:00Z'} for i in range(32)],
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
    expected = [event for event in run['events'] if event['issueKey'] in {'TASK-1', 'SUB-1'}]
    markers = assert_original_partition(axis, expected)
    assert len(markers) < 20
    assert any(marker['sources'] > 1 for marker in markers)
    # The production detail axis is narrow within a desktop workspace.
    assert timeline.evaluate('el=>el.getBoundingClientRect().width') < 440
    if width == 1540:
        assert 390 <= axis.evaluate('el=>el.getBoundingClientRect().width') <= 400
    assert_axis_geometry(axis)
    assert_compact_callouts(axis)
    dense_markers = [marker for marker in markers if any(event_id.startswith('dense-') for event_id in marker['ids'])]
    assert len(dense_markers) <= 3
    assert sum(event_id.startswith('dense-') for marker in dense_markers for event_id in marker['ids']) == 32
    for event_id in ['dense-0', 'dense-31', 'done']:
        activate_original(page, axis, event_id)
        expect(page.locator(f'.tp-event[data-event-id="{event_id}"]')).to_have_class(re.compile(r'\bis-selected\b'))
        expect(page.locator(f'.tp-event[data-event-id="{event_id}"]')).to_be_in_viewport()
        expect(marker_for_original(axis, event_id)).to_have_class(re.compile(r'\bis-selected\b'))
    dialog = assert_original_dialog(axis, run, expected)
    page.keyboard.press('Escape')
    expect(dialog).to_have_count(0)
    expect(axis.locator('.tt-axis-footer button')).to_be_focused()
    own_group = marker_for_original(axis, 'many-73')
    own_group.click()
    expect(dialog.locator('button[data-event-id="many-73"]')).to_contain_text('독립적으로 확인 가능한 업무 보고 73')
    page.keyboard.press('Escape')
    expect(own_group).to_be_focused()
    own_group.press('Enter')
    dialog.locator('button[data-event-id="many-73"]').click()
    expect(page.locator('.tp-event[data-event-id="many-73"]')).to_be_in_viewport()
    expect(page.locator('.tp-event[data-event-id="many-73"]')).to_have_class(re.compile(r'\bis-selected\b'))
    assert page.evaluate('document.documentElement.scrollWidth<=innerWidth'), page.evaluate('Array.from(document.querySelectorAll("body *")).map(el=>({tag:el.tagName,cls:el.className,left:el.getBoundingClientRect().left,right:el.getBoundingClientRect().right})).filter(r=>r.right>innerWidth+1&&r.left>=0).slice(0,20)')
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
