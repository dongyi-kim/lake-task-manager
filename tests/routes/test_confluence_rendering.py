"""Document image sources and text formatting survive the real sanitizer boundary."""
from types import SimpleNamespace

from app.content.htmlsafe import sanitize_html, sanitize_confluence_html
from app.routes.confluence import _document_image, document_html
from routes.test_confluence_browser import browser_api  # noqa: F401
from routes.test_confluence_macros import renderer, macro  # noqa: F401


def test_external_url_macro_and_inline_images_render_without_rewriting_storage(renderer):
    render, pages, _, _ = renderer
    source = '<ac:image ac:width="240" ac:alt="원격 그림"><ri:url ri:value="https://cdn.example/diagram.png"/></ac:image>'
    source += '<ac:image><ri:url ri:value="javascript:alert(1)"/></ac:image>'
    result = document_html(render.render(pages['1'], macro('expand', source)), 'https://docs.example/wiki', '1')
    assert 'src="https://cdn.example/diagram.png"' in result and 'width="240"' in result
    assert 'referrerpolicy="no-referrer"' in result
    assert '외부 이미지' not in result and 'javascript:' not in result
    assert not render.warnings


def test_image_routing_preserves_authenticated_owners_and_limits_proxy_targets():
    base = 'https://docs.example/wiki'
    assert _document_image('/wiki/download/attachments/22/chart.png?version=3', base, '1').startswith('/api/confluence/pages/22/image?')
    assert _document_image('/wiki/images/icons/a.svg', base, '1').startswith('/api/confluence/pages/1/image?')
    assert _document_image('https://jira.example/secure/attachment/7/p.png', base, '1', 'https://jira.example').startswith('/api/img?u=')
    for unsafe in ['', 'javascript:alert(1)', 'file:///private.png', 'https://name:password@cdn.example/a.png']:
        assert _document_image(unsafe, base, '1') is None
    assert _document_image('//cdn.example/diagram.svg', base, '1') == 'https://cdn.example/diagram.svg'


def test_authenticated_image_proxy_recovers_session_and_sandboxes_svg(browser_api, monkeypatch):
    from app.auth.base import SessionExpired
    http, _, client = browser_api
    calls, renewals = [], []
    def read(url):
        calls.append(url)
        if len(calls) == 1:
            raise SessionExpired()
        return b'<svg xmlns="http://www.w3.org/2000/svg"><path d="M0 0h10v10z"/></svg>', 'image/svg+xml'
    client.provider = SimpleNamespace(get_bytes=read)
    client._renew_service = lambda service: renewals.append(service) or True
    response = http.get('/api/confluence/pages/12/image', params={'path': '/wiki/images/icon.svg'})
    assert response.status_code == 200 and response.headers['content-type'].startswith('image/svg+xml')
    assert 'sandbox' in response.headers['content-security-policy']
    assert calls == ['https://docs.example/wiki/images/icon.svg'] * 2 and renewals == ['Confluence']
    for path in ['https://elsewhere.example/a.png', '/wiki/images/%2e%2e/rest/api/content', '/rest/api/content']:
        assert http.get('/api/confluence/pages/12/image', params={'path': path}).status_code == 403
    assert len(calls) == 2


def test_confluence_fonts_and_token_color_fallback_survive_without_unsafe_css():
    source = '<font color="#de350b" face="Malgun Gothic" size="4">색상</font>'
    source += '<span style="color:var(--ds-text-accent-blue, rgb(0,82,204));font-size:18px;font-weight:700;font-style:italic">강조</span>'
    source += '<span style="color:var(--bad,url(https://bad));font-size:999999px;position:fixed;behavior:url(bad)">안전</span>'
    result = sanitize_confluence_html(source)
    assert '<font' not in result and 'font-family:Malgun Gothic' in result
    assert 'color:#de350b' in result and 'font-size:18px' in result and 'font-weight:700' in result
    assert 'color:rgb(0,82,204)' in result and 'font-style:italic' in result
    assert 'var(' not in result and 'url(' not in result and 'position' not in result and '999999' not in result
    assert sanitize_confluence_html(result) == result
    assert 'font-size' not in sanitize_html('<span style="font-size:18px">Jira behavior unchanged</span>')
