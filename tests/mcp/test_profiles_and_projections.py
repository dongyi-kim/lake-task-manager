"""Copied profile connections retain real SDK contracts and shared application state."""
import json

import anyio
import httpx2
from jsonschema import Draft202012Validator
from mcp import Client
from mcp.client.streamable_http import streamable_http_client


def test_copied_profiles_share_writes_caches_and_keep_output_schemas(build_env):
    env = build_env()

    async def scenario():
        async with env.app.router.lifespan_context(env.app):
            async with httpx2.AsyncClient(transport=httpx2.ASGITransport(app=env.app),
                                         base_url=env.runtime.url.removesuffix('/mcp')) as http:
                created = None
                full_catalog = {tool.name: tool for tool in await env.runtime.server.list_tools()}
                assert not full_catalog['create_task'].annotations.destructive_hint
                assert not full_catalog['add_comment'].annotations.destructive_hint
                assert full_catalog['update_description'].annotations.destructive_hint
                assert full_catalog['delete_ticket'].annotations.destructive_hint
                for profile, required, excluded in [
                    ('jira', 'create_task', 'delete_llm_config'),
                    ('reporting', 'get_ticket', 'create_task'),
                    ('confluence', 'update_confluence_page', 'delete_ticket'),
                    ('settings', 'update_wbs_task', 'update_description'),
                ]:
                    export = await http.post('/api/mcp/credentials', json={'format': 'http', 'profile': profile},
                                             headers={'X-LTM-Local': '1'})
                    config = json.loads(export.json()['text'])['mcpServers']['lake-task-manager']
                    http.headers.update(config['headers'])
                    assert config['url'].endswith('/mcp/' + profile)
                    async with Client(streamable_http_client(config['url'], http_client=http)) as client:
                        catalog = {tool.name: tool for tool in (await client.list_tools()).tools}
                        assert required in catalog and excluded not in catalog
                        assert {'authenticate', 'get_auth_status', 'get_content_guide'} <= catalog.keys()
                        assert len(catalog) < len(full_catalog)
                        assert all(tool.output_schema == full_catalog[name].output_schema for name, tool in catalog.items())
                        if profile == 'jira':
                            reply = await client.call_tool('create_task', {'body': {
                                'type': 'Task', 'summary': 'Profile shared write', 'clientMutationId': 'profiles:create:0001'}})
                            assert not reply.is_error, reply.structured_content
                            created = reply.structured_content['data']['key']
                        if profile == 'reporting':
                            reply = await client.call_tool('get_ticket', {'key': created, 'detail': 'summary'})
                            assert not reply.is_error, reply.structured_content
                            assert reply.structured_content['data']['summary'] == 'Profile shared write'
                            Draft202012Validator(catalog['get_ticket'].output_schema).validate(reply.structured_content)
                    assert (await http.post('/mcp/' + profile, json={}, headers={'Authorization': 'Bearer wrong'})).status_code == 401
                assert env.client.ticket_view(created)['summary'] == 'Profile shared write'
    anyio.run(scenario)


def test_compact_responses_keep_complete_bodies_and_do_not_pollute_caches(build_env):
    env = build_env()

    async def scenario():
        async with Client(env.runtime.server) as client:
            schema = {tool.name: tool.output_schema for tool in (await client.list_tools()).tools}
            async def read(tool, args):
                reply = await client.call_tool(tool, args)
                assert not reply.is_error, reply.structured_content
                Draft202012Validator(schema[tool]).validate(reply.structured_content)
                return reply.structured_content['data']
            full = await read('get_ticket', {'key': 'DL-1'})
            for detail, field in [('summary', None), ('read', 'descriptionHtml'), ('edit', 'descriptionEditHtml')]:
                result = await read('get_ticket', {'key': 'DL-1', 'detail': detail})
                assert result['detail'] == detail and result['key'] == full['key']
                assert set(result) & {'descriptionHtml', 'descriptionEditHtml', 'descriptionSections'} == ({field} if field else set())
                if field:
                    assert result[field] == full[field]
                assert len(json.dumps(result)) < len(json.dumps(full))
            assert await read('get_ticket', {'key': 'DL-1'}) == full
            args = {'jql': 'project = DL ORDER BY key ASC', 'max_results': 2}
            original = await read('search_issues', args)
            before = len(env.calls)
            compact = await read('search_issues', {**args, 'detail': 'summary'})
            assert len(env.calls) == before
            assert [row['key'] for row in compact['issues']] == [row['key'] for row in original['issues']]
            assert compact['total'] == original['total'] and compact['snapshotId'] == original['snapshotId']
            assert original['execution']['physicalQueryCount'] == 1
            assert compact['execution']['physicalQueryCount'] == 0
            repeated = await read('search_issues', args)
            assert repeated['execution'] == {**original['execution'], 'physicalQueryCount': 0}
            # Response projection preserves the cached data and plan. Dispatch accounting
            # describes this call, so a cache hit must not repeat the cold request's cost.
            assert {key: value for key, value in repeated.items() if key != 'execution'} == {
                key: value for key, value in original.items() if key != 'execution'}
    anyio.run(scenario)
