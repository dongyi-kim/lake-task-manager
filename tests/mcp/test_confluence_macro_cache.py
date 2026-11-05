"""Include revisions reuse the same cache as MCP; failures never become empty bodies."""
from tests.mcp.test_confluence_commands import commands  # noqa: F401
from app.services.confluence_macros import MacroRenderer
from app.jira import confluence_service as reads


def test_include_cache_reuse_refresh_and_mutation_invalidation(commands):
    command,upstream=commands
    source=command.create_page(space='DL',title='Source',body='<p>Version one</p>',mutation_id='macro-source-001')
    host=upstream.items['10']
    storage='<ac:structured-macro ac:name="include"><ac:parameter ac:name="">Source</ac:parameter></ac:structured-macro>'
    def render(force=False): return MacroRenderer(command.client,force=force).render(host,storage)
    assert 'Version one' in render()
    before=len(upstream.gets)
    assert 'Version one' in render() and len(upstream.gets)==before
    # External edit: cached body v1 remains immutable; a forced head selects v2.
    upstream.put_json('https://docs.test/wiki/rest/api/content/'+source['id'],json_body={'title':'Source','version':{'number':2},'body':{'storage':{'value':'<p>Version two</p>'}}})
    assert 'Version two' in render(force=True)
    assert 'Version one' in reads.body(command.client,source['id'],version=1)['body']['storage']['value']
    command.update_page(page_id=source['id'],title='Source',body='<p>Version three</p>',expected_version=2,mutation_id='macro-source-update')
    assert 'Version three' in render()
    before=len(upstream.gets)
    assert 'Version three' in render() and len(upstream.gets)==before
    upstream.fail_read=True
    assert '불러오지 못했습니다' in render(force=True)
    assert 'Version three' in render(force=True)
