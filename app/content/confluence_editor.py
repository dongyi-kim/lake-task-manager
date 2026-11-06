"""Pure LTM editor HTML -> Confluence storage, shared by UI and MCP writes.

Native storage is a separate input contract: never feed ac:/ri: source through an
HTML sanitizer. Preserved macro fragments are parsed with the bounded XML parser.
"""
import json
import copy
import re
from html import escape
from urllib.parse import urlsplit
import xml.etree.ElementTree as ET
from pydantic import BaseModel, ConfigDict, Field

from app.content.wikihtml import _Tree, _txt
from app.content.htmlsafe import _safe_style
from app.content.confluence_xml import AC, RI, parse_storage

ALLOWED = set("p strong b em i s u code span div h1 h2 h3 h4 h5 h6 ul ol li blockquote table tbody thead tfoot tr th td br hr colgroup col".split())
RICH = {"expand", "panel", "info", "note", "tip", "warning", "excerpt", "toc-zone"}
PLAIN = {"code", "noformat"}
PAGES = {"include": "", "excerpt-include": "", "pagetree": "root", "children": "page", "attachments": "page"}
KNOWN = RICH | PLAIN | PAGES.keys() | {"toc", "status", "anchor"}


class EditorAsset(BaseModel):
    model_config = ConfigDict(extra="forbid")
    token: str = Field(min_length=1, max_length=2048)
    filename: str = Field(min_length=1, max_length=255)


class EditorOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")
    assets: list[EditorAsset] = Field(default_factory=list, max_length=100)
    numbered: bool = False


def esc(value):
    return escape(str(value or ""), quote=True)


def cdata(text):
    return "<![CDATA[" + text.replace("]]>", "]]]]><![CDATA[>") + "]]>"


def xml(node):
    """Serialize storage with stable namespace prefixes and literal code bodies."""
    def name(value):
        return value.replace(AC, "ac:").replace(RI, "ri:")
    tag = name(node.tag)
    if tag.startswith("{"):
        detached = copy.deepcopy(node)
        detached.tail = None
        return ET.tostring(detached, encoding="unicode")
    attrs = "".join(f' {name(key)}="{esc(value)}"' for key, value in node.attrib.items())
    if node.tag == AC + "plain-text-body":
        return f"<{tag}{attrs}>" + cdata("".join(node.itertext())) + f"</{tag}>"
    body = esc(node.text) + "".join(xml(child) + esc(child.tail) for child in node)
    return f"<{tag}{attrs}>" + body + f"</{tag}>"


def editor_usernames(html):
    """Extract identifiers only; the command layer owns all user resolution I/O."""
    if len(html) > 2_000_000:
        raise ValueError("문서가 너무 큽니다.")
    tree = _Tree(); tree.feed(html); tree.close()
    nodes, names = [tree.root], []
    while nodes:
        node = nodes.pop()
        nodes.extend(reversed(node.children))
        if node.attrs.get('data-type') == 'mention':
            name = node.attrs.get('data-id') or ''
            if name not in names:
                names.append(name)
            if len(names) > 100:
                raise ValueError("한 번에 멘션할 수 있는 사용자는 100명까지입니다.")
    return names


def editor_storage(html, *, assets=(), numbered=False, users=None, jira_base=""):
    if len(html) > 2_000_000 or re.search(r"<\s*(?:ac:|ri:|!DOCTYPE|!ENTITY)", html, re.I):
        raise ValueError("Confluence 원문 매크로는 format=storage로 입력하세요.")
    tree = _Tree(); tree.feed(html); tree.close()
    root = tree.root
    files = {item["token"]: item["filename"] for item in assets}
    users = users or {}
    task_id, stack, roots = 0, [], 0
    nodes, count = [(root, 0)], 0
    while nodes:
        node, depth = nodes.pop(); count += 1
        nodes.extend((child, depth + 1) for child in node.children)
        if count > 50000 or depth > 80:
            raise ValueError("문서 구조가 너무 복잡합니다.")
        config = node.attrs.get("data-conf-config")
        if config:
            raw = json.loads(config).get("raw")
            if raw:
                for item in parse_storage(raw).iter(AC + "task-id"):
                    if (item.text or "").isdigit():
                        task_id = max(task_id, int(item.text))

    def children(node):
        return "".join(render(child) for child in node.children)

    def attachment(name):
        return '<ri:attachment ri:filename="' + esc(name) + '" />'

    def macro(node):
        name = node.attrs["data-conf-macro"]
        config = json.loads(node.attrs.get("data-conf-config") or "{}")
        raw = config.get("raw", "")
        parsed = parse_storage(raw) if raw else None
        if name not in KNOWN:
            if parsed is None:
                raise ValueError("보존할 매크로 원문이 없습니다.")
            return raw  # unknown macros/resources stay byte-for-byte intact
        source = parsed[0] if parsed is not None and len(parsed) == 1 else ET.Element(AC + "structured-macro", {AC + "name": name})
        if source.tag != AC + "structured-macro" or source.get(AC + "name") != name:
            raise ValueError("매크로 종류와 원문이 일치하지 않습니다.")
        params = dict(config.get("params") or {})
        if name in PAGES:
            ref = config.get("reference")
            if ref:
                params[PAGES[name]] = parse_storage('<ac:link><ri:page ri:content-title="' + esc(ref.get("title"))
                    + '"' + (' ri:space-key="' + esc(ref["space"]) + '"' if ref.get("space") else '') + ' /></ac:link>')[0]
            elif name in {"include", "excerpt-include"} and not str(params.get(PAGES[name]) or '').strip():
                raise ValueError("참조 문서를 선택하세요.")
            elif not params.get(PAGES[name]):
                params.pop(PAGES[name], None)
                for child in list(source):
                    if child.tag == AC + 'parameter' and child.get(AC + 'name') == PAGES[name]:
                        source.remove(child)
        for key, value in params.items():
            item = next((child for child in source if child.tag == AC + "parameter" and child.get(AC + "name") == key), None)
            if item is None:
                item = ET.SubElement(source, AC + "parameter", {AC + "name": key})
            item.text = None
            for child in list(item):
                item.remove(child)
            if isinstance(value, ET.Element):
                item.append(value)
            else:
                item.text = str(value)
        for child in list(source):
            if child.tag in {AC + "rich-text-body", AC + "plain-text-body"}:
                source.remove(child)
        if name in RICH:
            content = next((child for child in node.children if "conf-macro-content" in (child.attrs.get("class") or "").split()), node)
            body = ET.SubElement(source, AC + "rich-text-body")
            parsed_body = parse_storage(children(content)); body.text = parsed_body.text
            body.extend(list(parsed_body))
        elif name in PLAIN:
            ET.SubElement(source, AC + "plain-text-body").text = _txt(node)
        return xml(source)

    def render(node):
        nonlocal task_id, roots
        if node.text is not None:
            return esc(node.text)
        tag, attrs = node.tag, node.attrs
        if attrs.get("data-conf-macro"):
            return macro(node)
        if tag in {"script", "style", "iframe", "object", "svg"}:
            return ""
        if attrs.get("data-type") == "mention":
            user = users.get(attrs.get("data-id"))
            if not user:
                raise ValueError("Confluence 멘션 사용자를 확인하지 못했습니다: " + (attrs.get("data-id") or ""))
            return '<ac:link><ri:user ri:userkey="' + esc(user) + '" /></ac:link>'
        if tag in {"a", "img"}:
            target = attrs.get("href" if tag == "a" else "src") or ""
            filename = files.get(target)
            if tag == "img":
                if not filename:
                    raise ValueError("이미지를 먼저 첨부하고 assets에 token·filename을 지정하세요.")
                width = ' ac:width="' + attrs['width'] + '"' if (attrs.get('width') or '').isdigit() else ''
                return '<ac:image' + width + ' ac:alt="' + esc(attrs.get('alt')) + '">' + attachment(filename) + '</ac:image>'
            if filename:
                return '<ac:link>' + attachment(filename) + '<ac:plain-text-link-body>' + cdata(_txt(node) or filename) + '</ac:plain-text-link-body></ac:link>'
            if target.startswith(("blob:", "draft:")):
                raise ValueError("첨부 원본이 없는 초안입니다. 파일을 다시 첨부해 주세요.")
            if re.fullmatch(r"/browse/[A-Za-z][A-Za-z0-9]*-\d+", target) and jira_base:
                target = jira_base.rstrip('/') + target
            return ('<a href="' + esc(target) + '">' + children(node) + '</a>'
                    if target.startswith('#') or urlsplit(target).scheme.lower() in {'http', 'https', 'mailto'} else children(node))
        if tag == "pre":
            code = next((child for child in node.children if child.tag == 'code'), node)
            match = re.search(r'language-([\w+-]+)', code.attrs.get('class') or '')
            return '<ac:structured-macro ac:name="code"><ac:parameter ac:name="language">' + esc(match[1] if match else 'none') + '</ac:parameter><ac:plain-text-body>' + cdata(_txt(code)) + '</ac:plain-text-body></ac:structured-macro>'
        kind = re.search(r'(?:^|\s)callout-(info|note|tip|warning|success|error)(?:\s|$)', attrs.get('class') or '')
        if kind:
            kind = kind[1]; native = {'success': 'tip', 'error': 'warning'}.get(kind, kind)
            title = {'success': '성공', 'error': '오류'}.get(kind)
            return '<ac:structured-macro ac:name="' + native + '">' + ('<ac:parameter ac:name="title">' + title + '</ac:parameter>' if title else '') + '<ac:rich-text-body>' + children(node) + '</ac:rich-text-body></ac:structured-macro>'
        if tag in {'p', 'div', 'root'} and any(child.tag == 'input' and (child.attrs.get('type') or '').lower() == 'checkbox' for child in node.children):
            prefix, tasks, current, body = [], [], None, []
            def flush():
                nonlocal task_id
                if current is not None:
                    task_id += 1
                    tasks.append(f'<ac:task><ac:task-id>{task_id}</ac:task-id><ac:task-status>'
                        + ('complete' if 'checked' in current.attrs else 'incomplete')
                        + '</ac:task-status><ac:task-body><p>' + ''.join(body) + '</p></ac:task-body></ac:task>')
            for child in node.children:
                if child.tag == 'input' and (child.attrs.get('type') or '').lower() == 'checkbox':
                    flush(); current, body = child, []
                else:
                    (body if current is not None else prefix).append(render(child))
            flush()
            return (('<p>' + ''.join(prefix) + '</p>' if tag == 'p' else ''.join(prefix)) if prefix else '') + '<ac:task-list>' + ''.join(tasks) + '</ac:task-list>'
        if attrs.get('data-type') == 'taskList' or tag == 'ul' and any('data-checked' in child.attrs for child in node.children):
            return '<ac:task-list>' + children(node) + '</ac:task-list>'
        if attrs.get('data-type') == 'taskItem' or tag == 'li' and 'data-checked' in attrs:
            task_id += 1
            return f'<ac:task><ac:task-id>{task_id}</ac:task-id><ac:task-status>' + ('complete' if attrs.get('data-checked') == 'true' else 'incomplete') + '</ac:task-status><ac:task-body>' + children(node) + '</ac:task-body></ac:task>'
        if tag in {'label', 'input'}:
            return ''
        if tag not in ALLOWED:
            return children(node)
        extra = ' id="' + esc(attrs['id']) + '"' if attrs.get('id') else ''
        css = _safe_style(attrs.get('style') or '')
        # Preserve layout properties supported by the previous browser adapter.
        for decl in (attrs.get('style') or '').split(';'):
            prop, _, value = decl.partition(':')
            if prop.strip().lower() in {'width', 'text-decoration', 'border-collapse'} and re.fullmatch(r'[\w\s.%#,-]+', value):
                css += ';' + prop.strip().lower() + ':' + value.strip()
        if tag in {'table', 'th', 'td'}:
            css += ';border:1px solid #b6bec9'
        if css:
            extra += ' style="' + esc(css) + '"'
        for key in ['colspan', 'rowspan', 'span', 'start']:
            if (attrs.get(key) or '').isdigit():
                extra += ' ' + key + '="' + attrs[key] + '"'
        if tag in {'br', 'hr', 'col'}:
            return '<' + tag + extra + ' />'
        number = ''
        if numbered and re.fullmatch('h[1-6]', tag):
            level, removed = int(tag[1]), None
            while stack and stack[-1][0] > level:
                removed = stack.pop()
            if stack and stack[-1][0] == level:
                stack[-1][1] += 1
            else:
                if not stack:
                    roots += 1
                stack.append([level, (removed[1] + 1 if removed else 1) if stack else roots])
            if len(stack) == 1:
                roots = stack[0][1]
            number = '.'.join(str(item[1]) for item in stack) + ' '
        return '<' + tag + extra + '>' + number + children(node) + '</' + tag + '>'
    return render(root)
