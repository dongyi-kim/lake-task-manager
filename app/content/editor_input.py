"""Submit normalization shared by Jira UI, MCP and bulk HTML commands."""
import re
from html import escape
from html.parser import HTMLParser
from urllib.parse import urlsplit

from app.content.htmlsafe import lift_mentions_html, unproxy_media


def normalize_editor_html(value, jira_base=""):
    class Links(HTMLParser):
        def __init__(self):
            super().__init__(convert_charrefs=False)
            self.output = []

        def handle_starttag(self, tag, attrs):
            raw = self.get_starttag_text()
            values = dict(attrs)
            attr = 'href' if tag == 'a' else 'src' if tag == 'img' else None
            target = values.get(attr) or ''
            if attr and target.lower().startswith(('blob:', 'draft:')):
                raise ValueError('첨부를 먼저 업로드한 뒤 반환된 경로로 본문을 작성하세요.')
            if attr == 'href' and jira_base:
                parsed = urlsplit(target)
                if (not parsed.netloc or parsed.hostname in {'localhost', '127.0.0.1', '::1'}) and re.fullmatch(r'/browse/[A-Za-z][A-Za-z0-9]*-\d+', parsed.path):
                    pattern = r'''\bhref\s*=\s*(?:"[^"]*"|'[^']*'|[^\s>]+)'''
                    raw = re.sub(pattern, lambda _: 'href="' + escape(jira_base.rstrip('/') + parsed.path, quote=True) + '"', raw, flags=re.I)
            self.output.append(raw)

        handle_startendtag = handle_starttag
        def handle_endtag(self, tag): self.output.append('</' + tag + '>')
        def handle_data(self, data): self.output.append(data)
        def handle_entityref(self, name): self.output.append('&' + name + ';')
        def handle_charref(self, name): self.output.append('&#' + name + ';')
        def handle_comment(self, data): self.output.append('<!--' + data + '-->')

    parser = Links()
    parser.feed(lift_mentions_html(unproxy_media(value or '')))
    parser.close()
    return ''.join(parser.output)
