"""Saved-run hierarchy groups remain independent scopes on the actual Vue UI."""
import re
from collections import Counter

import pytest

from test_tracking_comment_browser import open_surface, tracking_browser


UNASSIGNED = '소속 Epic 없음'
COVERAGE = {"complete": True, "historyComplete": True, "commentsComplete": True}
TASK_A = '인증 없는 업무의 긴 제목과 하위 작업을 확인하는 첫 번째 업무'
SUB_A = '첫 번째 업무의 하위 확인'


def ticket(key, *children, kind='Task', name=None):
    return {"id": 'task:' + key, "key": key, "kind": kind, "children": list(children), **({"name": name} if name else {})}


HIERARCHY_RUN = {
    "issues": [
        {"key": key, "summary": summary, "type": kind, "coverage": COVERAGE, **extra}
        for key, summary, kind, extra in [
            ('EPIC-1', '실제 Epic', 'Epic', {"contextOnly": True}),
            ('REAL-1', '실제 Epic에 속한 업무', 'Task', {"epicKey": 'EPIC-1'}),
            ('TASK-1', TASK_A, 'Task', {}),
            ('SUB-1', SUB_A, 'Sub-Task', {"parentKey": 'TASK-1', "isSubtask": True}),
            ('TASK-2', '두 번째 WBS의 독립 업무', 'Task', {}),
            ('MODULE-1', '모듈 바로 아래 업무', 'Task', {}),
            ('EPIC-2', '미연결 WBS의 Epic', 'Epic', {"contextOnly": True}),
            ('UNMAPPED-1', '미연결 WBS의 업무', 'Task', {"epicKey": 'EPIC-2'}),
            ('ORPHAN-1', '최상위 독립 업무', 'Task', {}),
            ('VOC-1', '[화면 - 사용성] VOC의 독립 업무', 'Task', {"voc": True}),
        ]
    ],
    "tree": [
        {"id": 'module:alpha', "kind": 'module', "name": '제품 모듈', "children": [
            {"id": 'wbs:a', "kind": 'wbs', "name": 'WBS A', "children": [
                ticket('EPIC-1', ticket('REAL-1'), kind='Epic'),
                ticket('TASK-1', ticket('SUB-1', kind='Sub-Task'), name=TASK_A),
            ]},
            {"id": 'wbs:b', "kind": 'wbs', "name": 'WBS B', "children": [ticket('TASK-2')]},
            ticket('MODULE-1'),
        ]},
        {"id": 'unmapped', "kind": 'module', "name": 'WBS 미연결', "children": [
            ticket('EPIC-2', ticket('UNMAPPED-1'), kind='Epic'),
        ]},
        ticket('ORPHAN-1'), ticket('VOC-1'),
    ],
    "events": [
        {"id": event, "issueKey": key, "kind": kind, "date": date,
         "actorName": '회귀 검증', "body": text}
        for event, key, kind, date, text in [
            ('a-comment', 'TASK-1', 'comment', '2026-11-13T09:00:00Z', '첫 번째 업무의 보고'),
            ('a-created', 'TASK-1', 'created', '2026-11-12T09:00:00Z', ''),
            ('a-sub-comment', 'SUB-1', 'comment', '2026-11-13T10:00:00Z', '하위 업무의 보고'),
            ('a-sub-completed', 'SUB-1', 'completed', '2026-11-12T10:00:00Z', ''),
            ('b-comment', 'TASK-2', 'comment', '2026-11-13T08:00:00Z', '두 번째 업무의 보고'),
            ('real-comment', 'REAL-1', 'comment', '2026-11-13T07:00:00Z', '실제 Epic 업무의 보고'),
            ('module-comment', 'MODULE-1', 'comment', '2026-11-13T06:00:00Z', '모듈 업무의 보고'),
            ('unmapped-comment', 'UNMAPPED-1', 'comment', '2026-11-13T05:30:00Z', '미연결 WBS 업무의 보고'),
            ('orphan-comment', 'ORPHAN-1', 'comment', '2026-11-13T05:00:00Z', '최상위 업무의 보고'),
            ('voc-comment', 'VOC-1', 'comment', '2026-11-13T04:00:00Z', 'VOC 업무의 보고'),
        ]
    ],
}

pytestmark = pytest.mark.parametrize('tracking_browser', [HIERARCHY_RUN], indirect=True)
A_EVENTS = {'a-comment', 'a-created', 'a-sub-comment', 'a-sub-completed'}


def navigation(page):
    return page.get_by_role('navigation', name='업무 계층 탐색', exact=True)


def unassigned_buttons(page):
    return navigation(page).get_by_role('button', name=f'{UNASSIGNED} 범위 선택', exact=True)


def select_group(page, index=0):
    unassigned_buttons(page).nth(index).click()


def brief_titles(page):
    return page.locator('.tb-title-button').all_text_contents()


def detail_event_ids(page):
    return set(page.locator('.tp-evidence .tp-event').evaluate_all(
        'events => events.map(event => event.dataset.eventId)'
    ))


def assert_clean(reads, before, unexpected, errors):
    assert reads == before, 'Display grouping and hierarchy actions must not fetch more evidence'
    assert not unexpected
    assert not errors


def test_unassigned_scopes_keep_wbs_module_and_root_members_separate(tracking_browser):
    from playwright.sync_api import expect

    page, reads, unexpected, errors = tracking_browser
    before = list(reads)
    expect(unassigned_buttons(page)).to_have_count(4)
    for index, context, titles in [
        (0, ['WBS A', UNASSIGNED], [TASK_A]),
        (1, ['WBS B', UNASSIGNED], ['두 번째 WBS의 독립 업무']),
        (2, ['WBS Task 미연결 · 제품 모듈', UNASSIGNED], ['모듈 바로 아래 업무']),
        (3, ['WBS Task 미연결', UNASSIGNED], ['최상위 독립 업무']),
    ]:
        select_group(page, index)
        expect(page.locator('.tb-briefing')).to_be_visible()
        expect(page.locator('.tp-evidence')).to_have_count(0)
        assert brief_titles(page) == titles
        breadcrumb = page.get_by_role('navigation', name='선택 범위 경로')
        expect(breadcrumb.locator('button')).to_have_text(['전체 업무', *context])

    navigation(page).get_by_role('button', name='실제 Epic (EPIC-1) 범위 선택', exact=True).click()
    assert brief_titles(page) == ['실제 Epic에 속한 업무']
    navigation(page).get_by_role('button', name='화면 - 사용성 범위 선택', exact=True).click()
    assert brief_titles(page) == ['VOC의 독립 업무']
    expect(unassigned_buttons(page)).to_have_count(4)
    assert_clean(reads, before, unexpected, errors)


def test_nonleaf_keeps_briefing_beside_evidence_and_leaf_uses_full_main(tracking_browser):
    from playwright.sync_api import expect

    page, reads, unexpected, errors = tracking_browser
    before = list(reads)
    select_group(page)
    page.get_by_role('button', name='범위 활동', exact=True).click()
    expect(page.locator('.tp-evidence')).to_be_visible()
    expect(page.locator('.tp-evidence')).not_to_have_class(re.compile(r'\btp-evidence-leaf\b'))
    expect(page.locator('.tb-briefing')).to_be_visible()
    assert detail_event_ids(page) == A_EVENTS
    expect(page.get_by_role('navigation', name='근거 상위 경로')).to_contain_text('WBS A')
    page.get_by_role('button', name='상세 근거 닫기', exact=True).click()
    assert brief_titles(page) == [TASK_A]

    page.locator('.tb-row[data-key="TASK-1"] .tb-evidence .tb-text').click()
    assert detail_event_ids(page) == A_EVENTS
    expect(page.locator('.tb-briefing')).to_be_visible()
    page.get_by_role('button', name='상세 근거 닫기', exact=True).click()
    navigation(page).get_by_role('button', name=UNASSIGNED + ' 펼치기', exact=True).first.click()
    navigation(page).get_by_role('button', name=f'{TASK_A} (TASK-1) 범위 선택', exact=True).click()
    assert brief_titles(page) == [SUB_A]
    navigation(page).get_by_role('button', name=f'{SUB_A} (SUB-1) 범위 선택', exact=True).click()
    expect(page.locator('.rf-workspace > .tp-evidence-leaf')).to_be_visible()
    expect(page.locator('.rf-main')).to_be_hidden()
    assert detail_event_ids(page) == {'a-sub-comment', 'a-sub-completed'}
    expect(page.get_by_role('navigation', name='근거 상위 경로')).to_contain_text(TASK_A)
    page.get_by_role('button', name='상위 범위로 돌아가기', exact=True).click()
    expect(page.locator('.tb-briefing')).to_be_visible()
    assert brief_titles(page) == [SUB_A]
    select_group(page, 1)
    assert brief_titles(page) == ['두 번째 WBS의 독립 업무']
    assert_clean(reads, before, unexpected, errors)


def test_scoped_grouping_search_and_highlight_keep_ancestor_context(tracking_browser):
    from playwright.sync_api import expect

    page, reads, unexpected, errors = tracking_browser
    before = list(reads)
    select_group(page)
    report_grouping = page.get_by_role('combobox', name='업무 그룹핑', exact=True)
    for mode in ['status', 'hierarchy']:
        report_grouping.select_option(mode)
        assert brief_titles(page) == [TASK_A]
    area = open_surface(page, 'feed')
    grouping = page.get_by_role('combobox', name='날짜별 활동 그룹핑', exact=True)
    search = page.get_by_role('textbox', name='업무 검색')
    for mode in ['none', 'ticket', 'date-ticket']:
        grouping.select_option(mode)
        assert set(area.locator('.tr-feed-event').evaluate_all('els => els.map(el => el.dataset.eventId)')) == A_EVENTS
        search.fill(SUB_A)
        page.get_by_role('combobox', name='검색 표시 방식').select_option('matches')
        expect(navigation(page).get_by_role('button', name=f'{TASK_A} (TASK-1) 범위 선택', exact=True)).to_be_visible()
        expect(unassigned_buttons(page)).to_have_count(1)
        expect(navigation(page).get_by_role('button', name='WBS A 범위 선택', exact=True)).to_be_visible()
        assert set(area.locator('.tr-feed-event').evaluate_all('els => els.map(el => el.dataset.eventId)')) == {'a-sub-comment', 'a-sub-completed'}
        search.fill('')
    page.get_by_role('combobox', name='사람 또는 업무 강조').select_option('task:SUB-1')
    assert set(area.locator('.tr-feed-event').evaluate_all('els => els.map(el => el.dataset.eventId)')) == {'a-sub-comment', 'a-sub-completed'}
    page.get_by_role('button', name='강조 지우기', exact=True).click()
    assert set(area.locator('.tr-feed-event').evaluate_all('els => els.map(el => el.dataset.eventId)')) == A_EVENTS
    assert_clean(reads, before, unexpected, errors)


def test_tree_folding_resizing_and_collapse_remain_local_and_keyboard_operable(tracking_browser):
    from playwright.sync_api import expect

    page, reads, unexpected, errors = tracking_browser
    before = list(reads)
    nav = navigation(page)
    fold = nav.get_by_role('button', name=UNASSIGNED + ' 펼치기', exact=True).first
    expect(fold).to_have_attribute('aria-expanded', 'false')
    fold.focus()
    fold.press('Enter')
    fold = nav.get_by_role('button', name=UNASSIGNED + ' 접기', exact=True).first
    expect(fold).to_have_attribute('aria-expanded', 'true')
    target = nav.get_by_role('button', name=f'{TASK_A} (TASK-1) 범위 선택', exact=True)
    expect(target).to_be_visible()
    box = fold.bounding_box()
    assert box['width'] >= 24 and box['height'] >= 24
    separator = page.get_by_role('separator', name='계층 패널 너비 조절', exact=True)
    separator.focus()
    for _ in range(4):
        separator.press('ArrowLeft')
    expect(separator).to_have_attribute('aria-valuenow', '220')
    expect(target).to_have_attribute('title', TASK_A + ' (TASK-1)')
    assert target.evaluate('el => el.scrollHeight <= el.clientHeight + 1 && el.scrollWidth <= el.clientWidth + 1'), 'Long titles must remain readable within the narrow navigation'
    target.press('Enter')
    assert brief_titles(page) == [SUB_A]
    separator.focus()
    separator.press('ArrowRight')
    expect(separator).to_have_attribute('aria-valuenow', '236')
    separator.press('Home')
    expect(separator).to_have_attribute('aria-valuenow', '252')
    page.get_by_role('button', name='업무 탐색 패널 접기', exact=True).click()
    expect(page.locator('.tp-nav-rail')).to_be_visible()
    page.get_by_role('button', name='업무 탐색 패널 펼치기', exact=True).click()
    expect(navigation(page)).to_be_visible()
    for width in [1440, 1280]:
        page.set_viewport_size({"width": width, "height": 1000})
        overflow = page.evaluate('Array.from(document.querySelectorAll("body *")).filter(el => el.getBoundingClientRect().right > innerWidth + 1).slice(0,8).map(el => ({tag:el.tagName,class:el.className,width:el.getBoundingClientRect().width,right:el.getBoundingClientRect().right}))')
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), f'Horizontal overflow at {width}px: {overflow}'
    assert_clean(reads, before, unexpected, errors)


def test_leaf_report_copy_contains_only_selected_leaf_evidence(tracking_browser):
    from playwright.sync_api import expect

    page, reads, unexpected, errors = tracking_browser
    before = list(reads)
    select_group(page)
    page.locator('.tb-row[data-key="TASK-1"] .tb-title-button').click()
    page.locator('.tb-row[data-key="SUB-1"] .tb-title-button').click()
    expect(page.locator('.rf-workspace > .tp-evidence-leaf')).to_be_visible()
    assert detail_event_ids(page) == {'a-sub-comment', 'a-sub-completed'}
    page.get_by_role('button', name='보고서 복사', exact=True).click()
    dialog = page.get_by_role('dialog')
    expect(dialog).to_be_visible()
    text = dialog.get_by_role('textbox', name='보고서 내용').input_value()
    assert SUB_A in text
    assert '하위 업무의 보고' in text and '완료' in text and 'SUB-1' in text
    assert all(fact not in text for fact in ['첫 번째 업무의 보고', '두 번째 업무의 보고', 'REAL-1', 'VOC-1'])
    assert_clean(reads, before, unexpected, errors)


def test_virtual_epic_highlights_keep_distinct_parent_context(tracking_browser):
    from playwright.sync_api import expect

    page, reads, unexpected, errors = tracking_browser
    before = list(reads)
    area = open_surface(page, 'feed')
    page.get_by_role('combobox', name='검색 표시 방식').select_option('matches')
    highlight = page.get_by_role('combobox', name='사람 또는 업무 강조')
    options = highlight.locator('option').filter(has_text=UNASSIGNED)
    expect(options).to_have_count(4)
    labels = options.all_text_contents()
    assert len(set(labels)) == 4, 'Same-named virtual Epic choices need their WBS scope path'
    for context, expected in [('WBS A', A_EVENTS), ('WBS B', {'b-comment'})]:
        option = options.filter(has_text=context)
        expect(option).to_have_count(1)
        highlight.select_option(option.get_attribute('value'))
        assert set(area.locator('.tr-feed-event').evaluate_all('els => els.map(el => el.dataset.eventId)')) == expected
    assert_clean(reads, before, unexpected, errors)


def test_mixed_legacy_roots_show_wbs_scopes_and_collapse_without_module_or_fetches(tracking_browser):
    from playwright.sync_api import expect

    page, reads, unexpected, errors = tracking_browser
    before = list(reads)
    nav = navigation(page)
    expected_roots = Counter(['WBS A', 'WBS B', 'WBS Task 미연결 · 제품 모듈', 'WBS Task 미연결', 'WBS Task 미연결', '사용자 VOC'])
    roots = nav.locator('.tp-tree-entry:not(.has-parent)')
    assert Counter(roots.locator('.tp-tree-label').all_text_contents()) == expected_roots
    assert roots.evaluate_all("els => els.every(el => el.style.getPropertyValue('--tp-depth') === '0')")
    expect(roots.locator('.tp-kind-layers')).to_have_count(sum(expected_roots.values()))
    expect(nav.locator('.tp-module-caption')).to_have_count(0)
    expect(nav.get_by_role('button', name='제품 모듈 범위 선택', exact=True)).to_have_count(0)
    expect(nav.get_by_text('현재 모듈', exact=True)).to_have_count(0)

    nav.get_by_role('button', name='WBS A 범위 선택', exact=True).click()
    expect(page.get_by_role('navigation', name='선택 범위 경로').locator('button')).to_have_text(['전체 업무', 'WBS A'])
    expect(page.locator('.tb-briefing')).not_to_contain_text('제품 모듈')
    page.get_by_role('button', name='범위 활동', exact=True).click()
    expect(page.get_by_role('navigation', name='근거 상위 경로')).not_to_contain_text('제품 모듈')
    page.get_by_role('button', name='상세 근거 닫기', exact=True).click()

    nav.get_by_role('button', name='미연결 WBS의 Epic (EPIC-2) 범위 선택', exact=True).click()
    assert brief_titles(page) == ['미연결 WBS의 업무']
    expect(page.get_by_role('navigation', name='선택 범위 경로').locator('button')).to_have_text(
        ['전체 업무', 'WBS Task 미연결', 'EPIC-2']
    )
    page.get_by_role('button', name='트리의 모든 항목 접기', exact=True).click()
    assert Counter(nav.locator('.tp-tree-label').all_text_contents()) == expected_roots
    expect(nav.locator('.tp-tree-entry.has-parent')).to_have_count(0)
    expect(roots.locator('.tp-tree-exp[aria-expanded="false"]')).to_have_count(sum(expected_roots.values()))
    assert_clean(reads, before, unexpected, errors)


def test_root_summary_and_selected_wbs_use_wbs_task_labels(tracking_browser):
    from playwright.sync_api import expect

    page, reads, unexpected, errors = tracking_browser
    before = list(reads)
    nav = navigation(page)
    expect(nav.locator('.tp-side-kicker').first).to_have_text('WBS Task · Epic · Task · Subtask')
    expect(page.locator('.tb-inventory-top strong')).to_contain_text('조회된 직속 하위 WBS Task')
    expect(page.locator('.tb-work-header h2')).to_contain_text('직속 WBS Task 목록')
    root_types = page.locator('.tb-row .tb-identity small > span').filter(has_text=re.compile(r'^(?:wbs|WBS Task)$'))
    expect(root_types).to_have_count(6)
    expect(root_types).to_have_text(['WBS Task'] * 6)

    nav.get_by_role('button', name='WBS A 범위 선택', exact=True).click()
    expect(page.locator('.tb-kicker')).to_have_text('현재 선택WBS Task')
    assert_clean(reads, before, unexpected, errors)
