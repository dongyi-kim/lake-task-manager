"""A complete parent search can still hide a known child from a progress denominator."""
from copy import deepcopy

import anyio
import httpx2
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

from support.evaluation_tmp import external_tmp_path as tmp_path
from tools.codex_mcp_eval.work_evidence import optional_count_progress
from tools.codex_mcp_eval.work_fixture import WorkFixture


def test_exhausted_visible_parent_query_is_not_whole_task_membership(tmp_path):
    with WorkFixture(tmp_path / 'case', today='2026-11-09', port=0,
                     fault='partial', skill_condition='candidate') as fixture:
        parent = fixture.provider._client.get('/rest/api/2/issue/DL-94010',
                                               params={'fields': 'subtasks'}).json()
        known = {row['key'] for row in parent['fields']['subtasks']}
        assert known == {'DL-94011', 'DL-94012'}

        async def exercise():
            async with httpx2.AsyncClient(headers={'Authorization': 'Bearer ' + fixture.token}) as http:
                async with Client(streamable_http_client(fixture.url, http_client=http)) as client:
                    root = await client.call_tool('get_ticket', {'key': 'DL-94010', 'detail': 'read', 'fresh': True})
                    assert root.structured_content['data']['type'] == 'Task'
                    result = await client.call_tool('search_issues', {
                        'jql': 'parent = DL-94010 ORDER BY key ASC', 'max_results': 100, 'start_at': 0})
                    envelope, page = result.structured_content, result.structured_content['data']
                    assert envelope['ok'] and not envelope['partial'] and envelope['missing'] == 0
                    assert envelope['cache']['state'] == 'fresh'
                    assert page['startAt'] == 0 and page['returned'] == page['total'] == 1
                    assert page['hasMore'] is False and page['nextStartAt'] is None
                    assert {row['key'] for row in page['issues']} == known - {'DL-94012'}
                    assert page['issues'][0]['fields']['issuetype']['subtask'] is True
                    assert page['issues'][0]['fields']['parent']['key'] == 'DL-94010'
                    query_only = deepcopy(fixture.tool_calls)
                    assert not optional_count_progress(query_only, {'done': 1, 'total': 1, 'percent': 100},
                                                       '하위 전체 1건 중 1건 완료, 100%')
                    children = await client.call_tool('get_children', {'key': 'DL-94010'})
                    status = children.structured_content
                    assert not status.get('ok') or status.get('partial') or status.get('missing')
                    assert not optional_count_progress(fixture.tool_calls,
                                                       {'done': 1, 'total': 1, 'percent': 100}, '하위 전체 100%')
        anyio.run(exercise)
