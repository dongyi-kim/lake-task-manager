"""Saved-run hierarchy groups remain independent scopes on the actual Vue UI."""
import re

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
            ('orphan-comment', 'ORPHAN-1', 'comment', '2026-11-13T05:00:00Z', '최상위 업무의 보고'),
            ('voc-comment', 'VOC-1', 'comment', '2026-11-13T04:00:00Z', 'VOC 업무의 보고'),
        ]
    ],
}

pytestmark = pytest.mark.parametrize('tracking_browser', [HIERARCHY_RUN], indirect=True)
A_EVENTS = {'a-comment', 'a-created', 'a-sub-comment', 'a-sub-completed'}


def navigation(page):
    return page.get_by_role('navigation', name='업무 계층과 보고 범위', exact=True)


def unassigned_buttons(page):
    return navigation(page).get_by_role('button', name=f'미소속 {UNASSIGNED} 업무 요약 보기', exact=True)


def select_group(page, index=0):
    unassigned_buttons(page).nth(index).click()


def brief_titles(page):
    return page.locator('.tr-brief-title h3').all_text_contents()


def detail_event_ids(page):
    return set(page.locator('#tracking-detail .tr-evidence-event').evaluate_all(
        "events => events.map(event => event.id.replace('tracking-event-', ''))"
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
    # Same visible group name in two WBS contexts must never merge their scopes.
    for index, context, titles in [
        (0, ['제품 모듈', 'WBS A', UNASSIGNED], [TASK_A]),
        (1, ['제품 모듈', 'WBS B', UNASSIGNED], ['두 번째 WBS의 독립 업무']),
        (2, ['제품 모듈', UNASSIGNED], ['모듈 바로 아래 업무']),
        (3, [UNASSIGNED], ['최상위 독립 업무']),
    ]:
        select_group(page, index)
        expect(page.get_by_role('tab', name='요약', exact=True)).to_have_attribute('aria-selected', 'true')
        expect(page.get_by_role('tab', name='요약', exact=True)).to_be_enabled()
        assert brief_titles(page) == titles
        breadcrumb = page.get_by_role('navigation', name='현재 보고 범위')
        expect(breadcrumb.locator('button')).to_have_text(['전체 업무', *context])

    navigation(page).get_by_role('button', name='Epic 실제 Epic 업무 요약 보기', exact=True).click()
    assert brief_titles(page) == ['실제 Epic에 속한 업무']
    navigation(page).get_by_role('button', name='Epic 화면 - 사용성 업무 요약 보기', exact=True).click()
    assert brief_titles(page) == ['VOC의 독립 업무']
    expect(unassigned_buttons(page)).to_have_count(4)
    assert_clean(reads, before, unexpected, errors)


def test_unassigned_group_detail_and_task_subtree_keep_all_member_evidence(tracking_browser):
    from playwright.sync_api import expect

    page, reads, unexpected, errors = tracking_browser
    before = list(reads)
    select_group(page)
    page.get_by_role('tab', name=re.compile('상세')).click()
    expect(page.locator('#tracking-detail')).to_be_visible()
    assert detail_event_ids(page) == A_EVENTS
    expect(page.get_by_role('navigation', name='현재 업무 위치')).to_contain_text('WBS A')
    page.get_by_role('tab', name='요약', exact=True).click()
    assert brief_titles(page) == [TASK_A]

    group_fold = navigation(page).get_by_role('button', name=UNASSIGNED + ' 펼치기', exact=True).first
    group_fold.click()
    navigation(page).get_by_role('button', name=f'업무 {TASK_A} 업무 요약 보기', exact=True).click()
    assert brief_titles(page) == [TASK_A]
    page.get_by_role('tab', name=re.compile('상세')).click()
    assert detail_event_ids(page) == A_EVENTS

    # A terminal Subtask opens detail directly while its parent scope stays intact.
    navigation(page).get_by_role('button', name=f'서브 {SUB_A} 상세 근거 보기', exact=True).click()
    expect(page.get_by_role('tab', name='요약', exact=True)).to_be_disabled()
    assert detail_event_ids(page) == {'a-sub-comment', 'a-sub-completed'}
    expect(page.get_by_role('navigation', name='현재 업무 위치')).to_contain_text(UNASSIGNED)
    expect(page.get_by_role('navigation', name='현재 업무 위치')).to_contain_text(TASK_A)
    select_group(page, 1)
    assert brief_titles(page) == ['두 번째 WBS의 독립 업무']
    assert_clean(reads, before, unexpected, errors)


def test_unassigned_feed_grouping_search_and_highlight_retain_parent_context(tracking_browser):
    from playwright.sync_api import expect

    page, reads, unexpected, errors = tracking_browser
    before = list(reads)
    select_group(page)
    report_grouping = page.get_by_role('combobox', name='업무 요약 그룹핑', exact=True)
    for mode in ['status', 'hierarchy']:
        report_grouping.select_option(mode)
        assert brief_titles(page) == [TASK_A]
    area = open_surface(page, 'feed')
    grouping = page.get_by_role('combobox', name='날짜별 활동 그룹핑', exact=True)
    search = page.get_by_role('searchbox', name='수집 결과에서 검색')
    for mode in ['none', 'ticket', 'date-ticket']:
        grouping.select_option(mode)
        assert set(area.locator('.tr-feed-event').evaluate_all('els => els.map(el => el.dataset.eventId)')) == A_EVENTS
        search.fill(SUB_A)
        page.get_by_role('checkbox', name='일치 항목만').check()
        expect(navigation(page).get_by_role('button', name=f'업무 {TASK_A} 업무 요약 보기', exact=True)).to_be_visible()
        expect(unassigned_buttons(page)).to_have_count(1)
        expect(navigation(page).get_by_role('button', name='WBS WBS A 업무 요약 보기', exact=True)).to_be_visible()
        assert set(area.locator('.tr-feed-event').evaluate_all('els => els.map(el => el.dataset.eventId)')) == {'a-sub-comment', 'a-sub-completed'}
        search.fill('')
    page.get_by_role('combobox', name='강조 대상 종류', exact=True).select_option('task')
    page.get_by_role('combobox', name='강조 대상', exact=True).select_option('SUB-1')
    assert set(area.locator('.tr-feed-event').evaluate_all('els => els.map(el => el.dataset.eventId)')) == {'a-sub-comment', 'a-sub-completed'}
    page.get_by_role('button', name='강조 해제', exact=True).click()
    assert set(area.locator('.tr-feed-event').evaluate_all('els => els.map(el => el.dataset.eventId)')) == A_EVENTS
    expect(navigation(page).get_by_role('button', name=UNASSIGNED + ' 펼치기', exact=True).first).to_have_attribute('aria-expanded', 'false')
    navigation(page).get_by_role('button', name=re.compile(r'^전체 업무')).click()
    page.get_by_role('combobox', name='강조 대상 종류', exact=True).select_option('epic')
    options = page.get_by_role('combobox', name='강조 대상', exact=True).locator('option').filter(has_text=UNASSIGNED)
    expect(options).to_have_count(4)
    labels = options.all_text_contents()
    assert len(set(labels)) == 4, 'Identical group names need their actual WBS/module context'
    assert all('VOC' not in label for label in labels)
    for context, expected in [('WBS A', A_EVENTS), ('WBS B', {'b-comment'})]:
        option = options.filter(has_text=context)
        expect(option).to_have_count(1)
        page.get_by_role('combobox', name='강조 대상', exact=True).select_option(option.get_attribute('value'))
        assert set(area.locator('.tr-feed-event').evaluate_all('els => els.map(el => el.dataset.eventId)')) == expected
    assert_clean(reads, before, unexpected, errors)


def test_wbs_tree_folds_and_narrow_titles_remain_keyboard_operable(tracking_browser):
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
    target = nav.get_by_role('button', name=f'업무 {TASK_A} 업무 요약 보기', exact=True)
    expect(target).to_be_visible()
    box = fold.bounding_box()
    assert box['width'] >= 24 and box['height'] >= 24
    assert fold.inner_text().strip() != '>'

    page.get_by_role('combobox', name='계층 패널 너비', exact=True).select_option('240')
    separator = page.get_by_role('separator', name='계층 패널 너비 조절', exact=True)
    expect(separator).to_have_attribute('aria-valuenow', '240')
    expect(target).to_have_attribute('title', TASK_A + ' (TASK-1)')
    assert target.locator('strong').evaluate('el => { const s = getComputedStyle(el); return s.whiteSpace === "nowrap" && el.getBoundingClientRect().height <= parseFloat(s.lineHeight) + 1; }')
    target.press('Enter')
    assert brief_titles(page) == [TASK_A]
    separator.focus()
    separator.press('ArrowRight')
    expect(separator).to_have_attribute('aria-valuenow', '256')
    separator.press('Home')
    expect(separator).to_have_attribute('aria-valuenow', '300')
    for width in [1440, 1280]:
        page.set_viewport_size({"width": width, "height": 1000})
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), f'Horizontal overflow at {width}px'
    assert_clean(reads, before, unexpected, errors)
