"""MCP exposes independent hints and records only explicit successful identity reads."""
import anyio
from jsonschema import Draft202012Validator
from mcp import Client


def test_public_person_search_contract_and_explicit_lookup_history(build_env, monkeypatch):
    from app.domain import person_context
    env = build_env()
    monkeypatch.setattr(person_context, 'module_dir', lambda: {'byUser': {'u01':['A'], 'u02':['A']}})

    async def scenario():
        async with Client(env.runtime.server) as client:
            tools = {tool.name: tool for tool in (await client.list_tools()).tools}
            limit_schema = tools['search_users'].input_schema['properties']['limit']
            assert (limit_schema['default'], limit_schema['minimum'], limit_schema['maximum']) == (8, 1, 50)
            first = await client.call_tool('search_users', {'query':'u02'})
            assert not first.is_error
            data = first.structured_content['data']
            candidate = next(u for u in data['users'] if u['id'] == 'u02')
            assert candidate['matchContext']['sameModule'] is True
            assert candidate['matchContext']['recentLookupAt'] is None
            Draft202012Validator(tools['search_users'].output_schema).validate(first.structured_content)
            again = await client.call_tool('search_users', {'query':'u02'})
            assert again.structured_content['data']['users'][0]['matchContext']['recentLookupAt'] is None
            lookup = await client.call_tool('get_user', {'user_id':'u02'})
            assert not lookup.is_error
            after = await client.call_tool('search_users', {'query':'u02', 'limit':20})
            assert after.structured_content['data']['users'][0]['matchContext']['recentLookupAt'] > 0
            Draft202012Validator(tools['search_users'].output_schema).validate(after.structured_content)
            assert not any(method != 'get' for method, _path, _body in env.calls)
            before = len(env.calls)
            for invalid_limit in (51, True, '8', 1.0):
                invalid = await client.call_tool('search_users', {'query':'u02', 'limit':invalid_limit})
                assert invalid.is_error
            assert len(env.calls) == before

    anyio.run(scenario)
