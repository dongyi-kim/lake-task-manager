"""Known-key batch reads use real Jira search, never a loop of single-ticket GETs."""
import anyio
from jsonschema import Draft202012Validator
from mcp import Client
import pytest


def seed(env):
    keys = []
    for index in range(3):
        response = env.provider._client.post('/rest/api/2/issue', json={'fields': {
            'project': {'key': 'DL'}, 'issuetype': {'name': 'Task'},
            'summary': f'batch {index}', 'description': f'raw body {index}'}})
        assert response.status_code == 201
        keys.append(response.json()['key'])
    env.calls.clear()
    return keys


def test_batch_schema_and_three_bodies_in_one_actual_request(build_env):
    env = build_env()
    keys = seed(env)
    async def scenario():
        async with Client(env.runtime.server) as client:
            catalog = {tool.name: tool for tool in (await client.list_tools()).tools}
            assert 'get_tickets' in catalog
            schema = catalog['get_tickets'].input_schema
            assert set(schema['properties']) == {'keys', 'include_description'}
            result = await client.call_tool('get_tickets', {'keys': [keys[2], keys[0], keys[2], keys[1]],
                                                          'include_description': True})
            assert not result.is_error, result.structured_content
            Draft202012Validator(catalog['get_tickets'].output_schema).validate(result.structured_content)
            data = result.structured_content['data']
            assert data['requestedKeys'] == [keys[2], keys[0], keys[1]]
            assert [row['key'] for row in data['issues']] == data['requestedKeys']
            assert data['returned'] == 3 and data['complete'] and data['missingKeys'] == []
            assert {row['fields']['description'] for row in data['issues']} == {f'raw body {i}' for i in range(3)}
            assert len([call for call in env.calls if call[1] == '/rest/api/2/search']) == 1
            assert not [call for call in env.calls if call[1].startswith('/rest/api/2/issue/')]
            assert env.cache.get('issue:mock:' + keys[0]) is None
    anyio.run(scenario)


def test_smaller_actual_jira_search_pages_are_followed_without_issue_fanout(build_env):
    env = build_env()
    keys = seed(env)
    original = env.provider.get_json
    def capped(path, *args, **kwargs):
        if path == '/rest/api/2/search':
            kwargs['params'] = {**kwargs['params'], 'maxResults': 1}
        return original(path, *args, **kwargs)
    env.provider.get_json = capped
    async def scenario():
        async with Client(env.runtime.server) as client:
            result = await client.call_tool('get_tickets', {'keys': keys, 'include_description': True})
            assert not result.is_error, result.structured_content
            assert result.structured_content['data']['complete']
            assert result.structured_content['data']['returned'] == 3
            assert len([call for call in env.calls if call[1] == '/rest/api/2/search']) == 3
            assert not [call for call in env.calls if call[1].startswith('/rest/api/2/issue/')]
    anyio.run(scenario)


def test_search_failure_is_tool_error_not_a_zero_count_batch(build_env):
    env = build_env()
    def failure(*args, **kwargs):
        raise TimeoutError('source unavailable')
    env.client.search_issues_page = failure
    async def scenario():
        async with Client(env.runtime.server) as client:
            result = await client.call_tool('get_tickets', {'keys': ['DL-1']})
            assert result.is_error
            assert result.structured_content['ok'] is False
            assert result.structured_content.get('data') is None
    anyio.run(scenario)


@pytest.mark.parametrize('keys', [[], ['DL-' + str(i) for i in range(101)], ['DL-1 OR project = XX'],
                                 ['XX-1'], ['DL-1', 'XX-2']])
def test_invalid_or_out_of_scope_batch_rejected_before_upstream(build_env, keys):
    env = build_env()
    async def scenario():
        async with Client(env.runtime.server) as client:
            result = await client.call_tool('get_tickets', {'keys': keys})
            assert result.is_error
            assert env.calls == []
    anyio.run(scenario)


def test_defaults_off_and_missing_ticket_is_explicit_partial_not_empty_success(build_env):
    env = build_env()
    keys = seed(env)
    async def scenario():
        async with Client(env.runtime.server) as client:
            args = {'keys': [keys[0].lower(), 'DL-99999999']}
            first = await client.call_tool('get_tickets', args)
            assert not first.is_error, first.structured_content
            data = first.structured_content['data']
            assert data['requestedKeys'] == [keys[0], 'DL-99999999']
            assert data['missingKeys'] == ['DL-99999999'] and not data['complete']
            assert data['returned'] == 1 and 'description' not in data['issues'][0]['fields']
            on = await client.call_tool('get_tickets', {**args, 'include_description': True})
            assert on.structured_content['data']['issues'][0]['fields']['description'] == 'raw body 0'
            again = await client.call_tool('get_tickets', args)
            assert 'description' not in again.structured_content['data']['issues'][0]['fields']
    anyio.run(scenario)
