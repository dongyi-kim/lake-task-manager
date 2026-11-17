"""Selecting a real Epic/parent Task exposes its timeline without a second action."""
from copy import deepcopy
import re

import pytest

from test_tracking_comment_browser import tracking_browser
from test_tracking_ticket_timeline_browser import (
    COVERAGE, RUN, activate_original, assert_axis_geometry, assert_compact_callouts,
    assert_original_dialog, assert_original_partition, marker_for_original, replace_saved_run,
)


HIERARCHY = deepcopy(RUN)
HIERARCHY['issues'].append({'key': 'EPIC-1', 'summary': '수집 신뢰성 Epic', 'type': 'Epic',
                            'status': 'In Progress', 'statusCategory': 'indeterminate', 'coverage': COVERAGE})
for issue in HIERARCHY['issues']:
    if issue['key'] in {'TASK-1', 'LEAF-1'}:
        issue['epicKey'] = 'EPIC-1'
    if issue['key'] in {'SUB-1', 'LEAF-1'}:
        issue.update(status='Closed', statusCategory='done')
HIERARCHY['tree'] = [{'id': 'module:etl', 'kind': 'module', 'name': 'ETL', 'children': [
    {'id': 'wbs:etl', 'kind': 'wbs', 'name': '데이터 수집 WBS', 'children': [
        {'id': 'epic:EPIC-1', 'key': 'EPIC-1', 'kind': 'Epic', 'children': [
            {'id': 'task:TASK-1', 'key': 'TASK-1', 'kind': 'Task', 'children': [
                {'id': 'task:SUB-1', 'key': 'SUB-1', 'kind': 'Sub-Task', 'children': []}]},
            {'id': 'task:LEAF-1', 'key': 'LEAF-1', 'kind': 'Bug', 'children': []},
        ]},
    ]},
]}]

pytestmark = pytest.mark.parametrize('tracking_browser', [HIERARCHY], indirect=True)


def test_parent_scope_titles_open_timeline_and_child_evidence_without_extra_requests(tracking_browser):
    from playwright.sync_api import expect

    page, reads, unexpected, errors = tracking_browser
    before = list(reads)
    nav = page.get_by_role('navigation', name='업무 계층 탐색', exact=True)
    nav.get_by_role('button', name='데이터 수집 WBS 범위 선택', exact=True).click()
    expect(page.locator('.tt-timeline')).to_have_count(0)
    page.locator('.tb-row[data-key="EPIC-1"] .tb-title-button').click()
    timeline = page.locator('.rf-main .tt-timeline')
    expect(timeline).to_be_visible()
    expect(timeline).to_have_attribute('data-issue-key', 'EPIC-1')
    expect(timeline.get_by_role('heading')).to_have_text('이 Epic의 상태 변화')
    expect(timeline.locator('.tt-empty')).to_contain_text('자체')
    expect(page.locator('.tp-evidence')).to_have_count(0)
    expect(timeline.get_by_role('progressbar')).to_have_attribute('aria-valuenow', '1')
    expect(timeline.get_by_role('progressbar')).to_have_attribute('aria-valuemax', '2')
    expect(timeline.locator('.tt-axis')).to_have_count(1)
    expect(timeline.locator('.tt-child-event[data-event-id="start"]')).to_have_attribute('aria-label', re.compile('상태 변화와 보고를 확인하는 업무'))
    expect(timeline.locator('.tt-child-event[data-event-id="child-done"]')).to_have_count(0)
    # Briefing resets its buttons; the real main canvas must retain approved bubbles.
    callout = timeline.locator('.tt-callout[data-event-id="start"]')
    assert callout.evaluate('el=>getComputedStyle(el).borderTopWidth') == '1px'
    assert callout.evaluate('el=>getComputedStyle(el).backgroundColor') not in ('transparent', 'rgba(0, 0, 0, 0)')
    assert timeline.locator('.tt-marker[data-event-id="start"]').evaluate('el=>getComputedStyle(el).color') != timeline.evaluate('el=>getComputedStyle(el).color')
    activate_original(page, timeline.locator('.tt-axis[data-source-key="combined"]'), 'start')
    expect(page.locator('.tp-evidence .tt-timeline')).to_have_attribute('data-issue-key', 'TASK-1')
    expect(page.locator('.tp-event[data-event-id="start"]')).to_be_in_viewport()
    page.get_by_role('button', name='상세 근거 닫기', exact=True).click()

    page.locator('.rf-main').evaluate('el=>el.scrollTop=el.scrollHeight')
    page.locator('.tb-row[data-key="TASK-1"] .tb-title-button').click()
    expect(timeline).to_have_attribute('data-issue-key', 'TASK-1')
    expect(timeline).to_be_in_viewport()
    assert page.locator('.rf-main').evaluate('el=>el.scrollTop') == 0
    assert_original_partition(timeline.locator('.tt-axis[data-source-key="combined"]'), [event for event in HIERARCHY['events'] if event['issueKey'] in {'TASK-1', 'SUB-1'}])
    expect(timeline.get_by_role('progressbar')).to_have_attribute('aria-valuenow', '1')
    expect(timeline.get_by_role('progressbar')).to_have_attribute('aria-valuemax', '1')
    expect(page.locator('.tb-row[data-key="SUB-1"]')).to_have_count(1)

    activate_original(page, timeline.locator('.tt-axis[data-source-key="combined"]'), 'done')
    expect(page.locator('.tp-evidence .tt-timeline')).to_be_visible()
    expect(page.locator('.rf-main .tt-timeline')).to_have_count(0)
    expect(page.locator('.tp-event[data-event-id="done"]')).to_have_class(re.compile(r'\bis-selected\b'))
    expect(page.locator('.tp-event[data-event-id="done"]')).to_be_in_viewport()
    page.get_by_role('button', name='상세 근거 닫기', exact=True).click()
    expect(timeline).to_be_visible()

    activate_original(page, timeline.locator('.tt-axis[data-source-key="combined"]'), 'child-done')
    expect(page.locator('.tp-evidence-leaf .tt-timeline')).to_have_attribute('data-issue-key', 'SUB-1')
    expect(page.locator('.tt-timeline')).to_have_count(1)
    assert reads == before
    assert not unexpected
    assert not errors


def test_missing_epic_source_keeps_saved_tree_type_and_unknown_coverage(tracking_browser):
    from playwright.sync_api import expect

    page, reads, unexpected, errors = tracking_browser
    missing = deepcopy(HIERARCHY)
    missing.update(id='saved-comments', status='partial')
    missing['issues'] = [issue for issue in missing['issues'] if issue['key'] != 'EPIC-1']
    missing['coverage'] = {**COVERAGE, 'complete': False,
                           'missing': [{'issueKey': 'EPIC-1', 'source': 'issue'}]}
    missing['tree'][0]['children'][0]['children'][0]['name'] = '원본 수집 중인 Epic'
    before = list(reads)
    page.evaluate('run=>window.dispatchEvent(new CustomEvent("api-cache-updated", '
                  '{detail:{path:"/api/tracking/runs/saved-comments",value:run}}))', missing)
    page.get_by_role('navigation', name='업무 계층 탐색', exact=True).get_by_role(
        'button', name='원본 수집 중인 Epic (EPIC-1) 범위 선택', exact=True).click()

    timeline = page.locator('.rf-main .tt-timeline')
    expect(timeline).to_be_visible()
    expect(timeline.get_by_role('heading')).to_have_text('이 Epic의 상태 변화')
    expect(page.locator('.tb-scope-title h1')).to_have_text('원본 수집 중인 Epic')
    expect(timeline.locator('.tt-child-event[data-event-id="start"]')).to_have_attribute('aria-label', re.compile('TASK-1'))
    expect(timeline.locator('.tt-child-progress')).to_contain_text('잠정')
    expect(timeline.get_by_role('progressbar')).to_have_count(0)
    expect(timeline.locator('.tt-partial')).to_contain_text('이력·댓글 수집이 완료되지 않았습니다.')

    page.get_by_role('button', name='범위 활동', exact=True).click()
    detail_timeline = page.locator('.tp-evidence .tt-timeline')
    expect(detail_timeline.get_by_role('heading')).to_have_text('이 Epic의 상태 변화')
    expect(page.locator('.tp-evidence h2')).to_have_text('원본 수집 중인 Epic')
    expect(detail_timeline.locator('.tt-child-event[data-event-id="start"]')).to_have_attribute('aria-label', re.compile('TASK-1'))
    expect(detail_timeline.get_by_role('progressbar')).to_have_count(0)
    expect(detail_timeline.locator('.tt-partial')).to_contain_text('이력·댓글 수집이 완료되지 않았습니다.')
    assert reads == before
    assert not unexpected
    assert not errors


def test_switching_structural_details_resets_scroll_but_same_scope_refresh_preserves_it(tracking_browser):
    from playwright.sync_api import expect

    page, reads, unexpected, errors = tracking_browser
    page.set_viewport_size({'width': 1440, 'height': 650})
    scopes = deepcopy(HIERARCHY)
    scopes.update(id='saved-comments', status='complete')
    second = deepcopy(scopes['tree'][0]['children'][0])
    second.update(id='wbs:second', name='두 번째 WBS')
    scopes['tree'][0]['children'].append(second)
    before = list(reads)
    page.evaluate('run=>window.dispatchEvent(new CustomEvent("api-cache-updated", '
                  '{detail:{path:"/api/tracking/runs/saved-comments",value:run}}))', scopes)
    page.locator('.tb-row').filter(has_text='데이터 수집 WBS').get_by_role(
        'button', name='근거 보기', exact=True).click()
    scroll = page.locator('.tp-evidence-scroll')
    scroll.evaluate('el=>el.scrollTop=el.scrollHeight')
    assert scroll.evaluate('el=>el.scrollTop') > 500

    page.locator('.tb-row').filter(has_text='두 번째 WBS').get_by_role(
        'button', name='근거 보기', exact=True).click()
    expect(page.locator('.tp-evidence h2')).to_have_text('두 번째 WBS')
    assert scroll.evaluate('el=>el.scrollTop') == 0

    scroll.evaluate('el=>el.scrollTop=el.scrollHeight')
    position = scroll.evaluate('el=>el.scrollTop')
    assert position > 500
    scopes['asOf'] = '2026-11-17T01:00:00+09:00'
    page.evaluate('run=>window.dispatchEvent(new CustomEvent("api-cache-updated", '
                  '{detail:{path:"/api/tracking/runs/saved-comments",value:run}}))', scopes)
    expect(page.locator('.tp-evidence h2')).to_have_text('두 번째 WBS')
    assert abs(scroll.evaluate('el=>el.scrollTop') - position) < 1
    assert reads == before
    assert not unexpected
    assert not errors


@pytest.mark.parametrize('surface', ['summary', 'detail'])
def test_epic_timeline_respects_voc_projected_scope_without_losing_own_or_child_focus(tracking_browser, surface):
    from playwright.sync_api import expect

    page, reads, unexpected, errors = tracking_browser
    scoped = deepcopy(HIERARCHY)
    scoped.update(id='saved-comments', status='complete')
    scoped['issues'].append({'key': 'VOC-1', 'type': 'Task', 'epicKey': 'EPIC-1',
                             'summary': '[사용자 - 오류] 별도 분류한 요청', 'modules': ['사용자 VoC'],
                             'status': 'Closed', 'statusCategory': 'done', 'coverage': COVERAGE})
    scoped['tree'][0]['children'][0]['children'][0]['children'].append(
        {'id': 'task:VOC-1', 'key': 'VOC-1', 'kind': 'Task', 'children': []})
    scoped['events'].extend([
        {'id': 'voc-done', 'issueKey': 'VOC-1', 'kind': 'completed',
         'date': '2026-11-10T01:00:00Z', 'from': 'Doing', 'to': 'Closed'},
        {'id': 'epic-report', 'issueKey': 'EPIC-1', 'kind': 'comment',
         'date': '2026-11-10T02:00:00Z', 'bodyText': 'Epic 자체 진행 보고'},
    ])
    before = list(reads)
    page.evaluate('run=>window.dispatchEvent(new CustomEvent("api-cache-updated", '
                  '{detail:{path:"/api/tracking/runs/saved-comments",value:run}}))', scoped)
    page.get_by_role('navigation', name='업무 계층 탐색', exact=True).get_by_role(
        'button', name='수집 신뢰성 Epic (EPIC-1) 범위 선택', exact=True).click()
    expect(page.locator('.tb-inventory-top')).to_contain_text('Task 2개')
    if surface == 'detail':
        page.get_by_role('button', name='범위 활동', exact=True).click()

    timeline = page.locator('.tt-timeline')
    expect(timeline).to_have_count(1)
    expect(timeline.get_by_role('progressbar')).to_have_attribute('aria-valuenow', '1')
    expect(timeline.get_by_role('progressbar')).to_have_attribute('aria-valuemax', '2')
    expect(timeline.locator('.tt-child-event[data-event-id="voc-done"]')).to_have_count(0)
    expect(timeline.locator('.tt-child-event[data-event-id="child-done"]')).to_have_count(0)
    axis = timeline.locator('.tt-axis[data-source-key="combined"]')
    expected = [event for event in scoped['events'] if event['issueKey'] in {'EPIC-1', 'TASK-1', 'LEAF-1'}]
    assert_original_partition(axis, expected)
    dialog = assert_original_dialog(axis, scoped, expected)
    expect(dialog.locator('button[data-event-id="epic-report"]')).to_contain_text('Epic 자체 진행 보고')
    page.keyboard.press('Escape')
    activate_original(page, timeline.locator('.tt-axis[data-source-key="combined"]'), 'start')
    expect(page.locator('.tp-evidence .tt-timeline')).to_have_attribute('data-issue-key', 'TASK-1')
    expect(page.locator('.tp-event[data-event-id="start"]')).to_be_in_viewport()
    assert reads == before
    assert not unexpected
    assert not errors


@pytest.mark.parametrize('surface,width,theme', [('summary', 1440, 'light'), ('detail', 1540, 'dark')])
def test_dense_epic_main_axis_compresses_to_fixed_height_and_preserves_direct_child_sources(tracking_browser, surface, width, theme):
    from playwright.sync_api import expect

    page, reads, unexpected, errors = tracking_browser
    page.set_viewport_size({'width': width, 'height': 1000})
    page.locator('html').evaluate('(el,theme)=>el.dataset.theme=theme', theme)
    dense = deepcopy(HIERARCHY)
    dense['events'].extend([
        {'id': f'epic-dense-{i}', 'issueKey': 'TASK-1' if i % 2 else 'LEAF-1',
         'kind': 'fieldChanged', 'field': 'status', 'from': f'Review {i}', 'to': f'Review {i+1}',
         'date': '2026-11-12T02:00:00Z'} for i in range(32)
    ])
    replace_saved_run(page, dense)
    before = list(reads)
    page.get_by_role('navigation', name='업무 계층 탐색', exact=True).get_by_role(
        'button', name='수집 신뢰성 Epic (EPIC-1) 범위 선택', exact=True).click()
    if surface == 'detail':
        page.get_by_role('button', name='범위 활동', exact=True).click()
    timeline = page.locator('.tt-timeline')
    expect(timeline).to_be_visible()
    expect(timeline.locator('.tt-axis')).to_have_count(1)
    expect(timeline.locator('.tt-child-lanes')).to_have_count(0)
    axis = timeline.locator('.tt-axis[data-source-key="combined"]')
    if surface == 'detail':
        assert 390 <= axis.evaluate('el=>el.getBoundingClientRect().width') <= 400
    else:
        assert axis.evaluate('el=>el.getBoundingClientRect().width') > 600
    expected = [event for event in dense['events'] if event['issueKey'] in {'EPIC-1', 'TASK-1', 'LEAF-1'}]
    markers = assert_original_partition(axis, expected)
    assert len(markers) < 20
    assert any(marker['sources'] > 1 for marker in markers)
    assert_axis_geometry(axis)
    assert_compact_callouts(axis)
    summary = timeline.locator('.tt-summary').text_content()
    dialog = assert_original_dialog(axis, dense, expected)
    expect(dialog.locator('button[data-event-id="child-done"]')).to_have_count(0)
    page.keyboard.press('Escape')
    expect(axis.locator('.tt-axis-footer button')).to_be_focused()
    # A nonrepresentative original still routes to the right child's evidence.
    marker = marker_for_original(axis, 'epic-dense-31')
    assert marker.get_attribute('data-event-id') != 'epic-dense-31'
    activate_original(page, axis, 'epic-dense-31')
    expect(page.locator('.tp-evidence .tt-timeline')).to_have_attribute('data-issue-key', 'TASK-1')
    expect(page.locator('.tp-event[data-event-id="epic-dense-31"]')).to_have_class(re.compile(r'\bis-selected\b'))
    expect(page.locator('.tp-event[data-event-id="epic-dense-31"]')).to_be_in_viewport()
    page.get_by_role('button', name='상세 근거 닫기', exact=True).click()
    expect(timeline.locator('.tt-summary')).to_have_text(summary)
    assert not page.evaluate('document.documentElement.scrollWidth>innerWidth')
    assert reads == before
    assert not unexpected
    assert not errors
