"""DC storage macros rendered without loading upstream scripts in the local app.

Source revisions use the shared long-lived cache. Referenced heads/tree membership use
the normal short freshness policy; assembled include results are never revision-cached.
"""
from html import escape
import json
import re
import xml.etree.ElementTree as ET
from urllib.parse import quote

from app.jira import confluence_service as reads
from app.auth.base import PermissionDenied

from app.content.confluence_xml import AC, RI, parse_storage


def has_macros(value):
    return "structured-macro" in value


def macro_parameters(node):
    return {item.get(AC + "name", ""): "".join(item.itertext()) for item in node.findall(AC + "parameter")}


def reference(client, node, parameter, current):
    item = next((item for item in node.findall(AC + "parameter") if item.get(AC + "name", "") == parameter), None)
    if item is None:
        if node.get(AC + 'name') != 'pagetree':
            return current
        item = ET.Element(AC + 'parameter'); item.text = '@home'
    page = item.find(".//" + RI + "page")
    space = page.get(RI + "space-key") if page is not None else None
    title = page.get(RI + "content-title") if page is not None else "".join(item.itertext()).strip()
    space = space or current["space"]["key"]
    if page is None and ":" in title:
        space, title = title.split(":", 1)
    allowed = {str(value).casefold() for value in client.s.search_confluence_spaces or []}
    if space.casefold() not in allowed:
        raise PermissionDenied("참조 문서가 설정된 Space 범위 밖입니다.")
    def head(cid):
        result = reads.head(client, str(cid))
        if result.get('space', {}).get('key', '').casefold() not in allowed:
            raise PermissionDenied("참조 문서가 설정된 Space 범위 밖입니다.")
        return result
    if title == "/" or (not title and space != current['space']['key']):
        return {"id": "", "space": {"key": space}}
    if title in ("", "@self"):
        return current
    if title == "@parent":
        parents = current.get("ancestors") or []
        return head(parents[-1]["id"]) if parents else current
    if title in ("@home", "@none"):
        if title == "@none":
            return {"id": "", "space": {"key": space}}
        result = reads.cached_read(client, "conftree", "/rest/api/space/" + quote(space, safe=""), {"expand": "homepage"}, lambda data: bool(data.get("homepage")))
        return head(result["homepage"]["id"])
    data = reads.cached_read(client, "confsearch", "/rest/api/content", {"spaceKey": space, "title": title,
        "type": "page", "status": "current", "expand": "space,version,ancestors", "limit": 2},
        lambda data: isinstance(data.get("results"), list))
    matches = [item for item in data["results"] if item.get("title") == title and (item.get("space") or {}).get("key") == space]
    if len(matches) != 1:
        raise ValueError("참조 문서를 찾을 수 없습니다.")
    return head(matches[0]["id"])


class MacroRenderer:
    def __init__(self, client, *, force=False):
        self.client, self.force = client, force
        self.owners, self.warnings = set(), []
        self.count = 0

    def render(self, page, storage, trail=()):
        cid = str(page["id"])
        if cid in trail or len(trail) >= 6:
            return self.unavailable("문서 참조가 순환하거나 중첩 한도를 초과했습니다.")
        if (page.get("space") or {}).get("key", "").casefold() not in {value.casefold() for value in self.client.s.search_confluence_spaces or []}:
            raise PermissionDenied("설정된 Space 범위 밖입니다.")
        self.owners.add(cid)
        root = parse_storage(storage)
        return self.children(root, page, (*trail, cid))

    def unavailable(self, message):
        self.warnings.append(message)
        return '<div class="conf-macro-unavailable">' + escape(message) + '</div>'

    def children(self, node, page, trail):
        return escape(node.text or "") + "".join(self.safe_element(item, page, trail) + escape(item.tail or "") for item in node)

    def safe_element(self, node, page, trail):
        try:
            return self.element(node, page, trail)
        except Exception:
            return self.unavailable("참조 항목을 불러오지 못했습니다. 원문에서 확인하거나 다시 불러와 주세요.")

    def element(self, node, page, trail):
        tag = node.tag
        if tag == AC + "structured-macro":
            self.count += 1
            if self.count > 100:
                return self.unavailable("매크로 표시 한도에 도달했습니다. Confluence 원문에서 확인하세요.")
            try:
                return self.macro(node, page, trail)
            except Exception as exc:
                # Best effort for sibling blocks, but never cache an error as a successful body.
                if isinstance(exc, PermissionDenied) or getattr(exc, "status", None) in (403, 404):
                    return self.unavailable("참조 문서를 볼 수 없습니다.")
                return self.unavailable("매크로를 불러오지 못했습니다. 최신 여부 확인으로 다시 시도하세요.")
        if tag == AC + "image":
            attachment = node.find(RI + "attachment")
            if attachment is None:
                external = node.find(RI + "url")
                if external is None:
                    return self.unavailable("이미지 주소를 확인하지 못했습니다.")
                # The document sanitizer validates the URL before the browser receives it.
                src = external.get(RI + "value", "")
                width = node.get(AC + "width", "")
                return '<img src="' + escape(src, quote=True) + '" alt="' + escape(node.get(AC + "alt", ""), quote=True) + '"' + (' width="' + width + '"' if width.isdigit() else '') + ' />'
            owner = str(page["id"])
            if attachment.find(RI + "page") is not None:
                # Resolve the attachment's own page under the same configured-space policy.
                proxy = ET.Element(AC + "structured-macro")
                parameter = ET.SubElement(proxy, AC + "parameter", {AC + "name": ""})
                parameter.append(attachment.find(RI + "page"))
                target = reference(self.client, proxy, "", page); owner = str(target["id"]); self.owners.add(owner)
            src = self.client.s.confluence_base.rstrip('/') + '/download/attachments/' + owner + '/' + quote(attachment.get(RI + "filename", ""), safe="")
            width = node.get(AC + "width", "")
            return '<img src="' + escape(src, quote=True) + '" alt="' + escape(node.get(AC + "alt", ""), quote=True) + '"' + (' width="' + width + '"' if width.isdigit() else '') + ' />'
        if tag == AC + "link":
            attachment, user, target = node.find(RI + "attachment"), node.find(RI + "user"), node.find(RI + "page")
            label_node = node.find(AC + "plain-text-link-body")
            label = "".join(label_node.itertext()) if label_node is not None else ""
            rich_label = node.find(AC + 'link-body')
            label_html = self.children(rich_label, page, trail) if rich_label is not None else escape(label)
            if attachment is not None:
                name = attachment.get(RI + "filename", "")
                owner = str(page['id'])
                if attachment.find(RI + 'page') is not None:
                    proxy = ET.Element(AC + 'structured-macro')
                    ET.SubElement(proxy, AC + 'parameter', {AC + 'name': ''}).append(attachment.find(RI + 'page'))
                    owner = str(reference(self.client, proxy, '', page)['id']); self.owners.add(owner)
                href = self.client.s.confluence_base.rstrip('/') + '/download/attachments/' + owner + '/' + quote(name, safe='')
                return '<a href="' + escape(href, quote=True) + '">' + (label_html or escape(name)) + '</a>'
            if user is not None:
                return '<span>' + (label_html or escape('@' + user.get(RI + 'userkey', ''))) + '</span>'
            if target is not None:
                proxy = ET.Element(AC + "structured-macro"); ET.SubElement(proxy, AC + "parameter", {AC + "name": ""}).append(target)
                resolved = reference(self.client, proxy, "", page)
                anchor = '#' + quote(node.get(AC + 'anchor'), safe='') if node.get(AC + 'anchor') else ''
                return '<a href="' + escape(self.client.s.confluence_base.rstrip('/'), quote=True) + '/pages/viewpage.action?pageId=' + str(resolved["id"]) + anchor + '">' + (label_html or escape(resolved['title'])) + '</a>'
            anchor = node.get(AC + "anchor")
            if anchor:
                return '<a href="#conf-anchor-' + escape(anchor, quote=True) + '">' + (label_html or escape(anchor)) + '</a>'
        if tag == AC + "task-list":
            return '<ul>' + self.children(node, page, trail) + '</ul>'
        if tag == AC + "task":
            body = node.find(AC + "task-body")
            checked = node.findtext(AC + "task-status") == "complete"
            return '<li><input type="checkbox"' + (' checked="checked"' if checked else '') + ' />' + (self.children(body, page, trail) if body is not None else '') + '</li>'
        if tag.startswith((AC, RI)):
            return self.children(node, page, trail)
        tag = tag.removeprefix('{http://www.w3.org/1999/xhtml}')
        if '{' in tag:
            return ''
        # All ordinary XHTML goes through the existing restrictive sanitizer afterwards.
        attrs = ''.join(' ' + key + '="' + escape(value, quote=True) + '"' for key, value in node.attrib.items() if ":" not in key and "{" not in key)
        return '<' + tag + attrs + '>' + self.children(node, page, trail) + ('</' + tag + '>' if tag not in ('hr', 'br', 'img') else '')

    def macro(self, node, page, trail):
        name = node.get(AC + "name", "")
        params = macro_parameters(node)
        supported = {
            'expand': {'title'}, 'panel': {'title'}, 'info': {'title','icon'}, 'note': {'title','icon'},
            'tip': {'title','icon'}, 'warning': {'title','icon'}, 'excerpt': {'hidden'}, 'include': {''},
            'excerpt-include': {'','nopanel'}, 'code': {'language','title','collapse','linenumbers','firstline'},
            'noformat': {'nopanel'}, 'status': {'title','colour','subtle'}, 'anchor': {''},
            'toc': {'type','minLevel','maxLevel','outline'}, 'toc-zone': {'location','minLevel','maxLevel','outline'},
            'pagetree': {'root','sort','startDepth','reverse','searchBox','expandCollapseAll'},
            'children': {'page','depth','sort','reverse','all'}, 'attachments': {'page','sortBy','sortOrder'},
        }
        if name in supported and set(params) - supported[name]:
            self.warnings.append(name + ' 매크로의 고급 옵션 일부는 Confluence 원문에서 확인하세요. 저장 원문은 보존됩니다.')
        body = node.find(AC + "rich-text-body")
        content = lambda: self.children(body, page, trail) if body is not None else ''
        title = escape(params.get("title", ""))
        if name in ("info", "note", "tip", "warning", "panel"):
            icon = '' if name == 'panel' or params.get('icon') == 'false' else {'info':'ⓘ ', 'note':'✎ ', 'tip':'✓ ', 'warning':'⚠ '}[name]
            return '<div class="conf-native-panel' + (' callout callout-' + name if name != 'panel' else '') + '">' + icon + ('<strong>' + title + '</strong>' if title else '') + content() + '</div>'
        if name == "expand":
            return '<details class="conf-expand"><summary>' + (title or '자세히 보기') + '</summary>' + content() + '</details>'
        if name == "excerpt":
            return '' if params.get('hidden') == 'true' else content()
        if name in ("include", "excerpt-include"):
            target = reference(self.client, node, "", page)
            if self.force:
                target = reads.head(self.client, str(target["id"]), force=True)
            data = reads.body(self.client, str(target["id"]), representation="storage")
            storage = data['body']['storage']['value']
            if name == 'excerpt-include':
                excerpt = next((item for item in parse_storage(storage).iter(AC + 'structured-macro') if item.get(AC + 'name') == 'excerpt'), None)
                if excerpt is None:
                    return self.unavailable('참조 문서에 발췌 영역이 없습니다.')
                fragment = excerpt.find(AC + 'rich-text-body')
                storage = (escape(fragment.text or '') + ''.join(ET.tostring(item, encoding='unicode') for item in fragment)) if fragment is not None else ''
            result = self.render(target, storage, trail)
            if name == 'excerpt-include' and params.get('nopanel') != 'true':
                result = '<div class="conf-native-panel"><strong>' + escape(target['title']) + '</strong>' + result + '</div>'
            return result
        if name in ('code', 'noformat'):
            source = node.findtext(AC + 'plain-text-body', '')
            language = re.sub(r'[^a-zA-Z0-9_+-]', '', params.get('language', 'none'))
            numbered = params.get('linenumbers') == 'true'
            plain = ' conf-no-panel' if name == 'noformat' and params.get('nopanel') == 'true' else ''
            result = ('<strong>' + title + '</strong>' if title else '') + '<pre class="conf-code' + plain + '" data-conf-code="' + escape(json.dumps({'numbered': numbered, 'firstline': params.get('firstline', '1')}), quote=True) + '"><code class="language-' + language + '">' + escape(source) + '</code></pre>'
            return '<details class="conf-expand"><summary>' + (title or '코드 보기') + '</summary>' + result + '</details>' if params.get('collapse') == 'true' else result
        if name == 'status':
            colour = params.get('colour', 'Grey').lower()
            colour = colour if colour in ('grey','red','yellow','green','blue') else 'grey'
            return '<span class="conf-status conf-status-' + colour + (' conf-status-subtle' if params.get('subtle') == 'true' else '') + '">' + (title or escape(colour)) + '</span>'
        if name == 'anchor':
            return '<span id="conf-anchor-' + escape(params.get('', ''), quote=True) + '"></span>'
        if name in ('toc', 'toc-zone'):
            placeholder = '<div class="conf-inline-toc" data-conf-toc="' + escape(json.dumps(params), quote=True) + '"></div>'
            if name == 'toc':
                return placeholder
            return '<section class="conf-toc-zone">' + (placeholder if params.get('location') != 'bottom' else '') + content() + (placeholder if params.get('location') in ('bottom','both') else '') + '</section>'
        if name in ('pagetree', 'children', 'attachments'):
            target = reference(self.client, node, 'root' if name == 'pagetree' else 'page', page)
            descriptor = {'name': name, 'pageId': str(target['id']), 'space': target['space']['key'], 'params': params}
            return '<div class="conf-dynamic-macro" data-conf-dynamic="' + escape(json.dumps(descriptor), quote=True) + '"></div>'
        return self.unavailable('지원하지 않는 매크로: ' + name + ' · 원문은 편집 시 보존됩니다.')
