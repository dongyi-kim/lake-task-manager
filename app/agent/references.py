"""Agent 산출물의 ticket/person/document/external 참조를 결정적으로 해석·렌더링한다."""

from __future__ import annotations

import html
import re
from html.parser import HTMLParser
from urllib.parse import urlsplit

from app.agent.tools._ctx import (client, jira_key_allowed, search_spaces,
                                  settings)


_TOKEN = re.compile(r"\{\{(ref|mention):([A-Za-z0-9_.:-]+)\}\}")


def _error(ref: dict, message: str) -> dict:
    return {"id": str(ref.get("id") or ""), "kind": str(ref.get("kind") or ""),
            "resolved": False, "error": message}


def _ticket(ref: dict) -> dict:
    key = str(ref.get("key") or ref.get("value") or "").strip().upper()
    if not jira_key_allowed(key):
        return _error(ref, "ticket이 search.jira.projects 범위 밖이거나 검색 범위가 비어 있습니다.")
    badge = client().ticket_badge(key) or {}
    if not badge.get("key") and not badge.get("summary"):
        return _error(ref, f"ticket {key}을 확인하지 못했습니다.")
    base = (getattr(settings(), "jira_base", "") or "").rstrip("/")
    return {"id": str(ref.get("id") or key), "kind": "ticket", "resolved": True,
            "key": key, "label": badge.get("summary") or key,
            "url": f"{base}/browse/{key}" if base else f"/browse/{key}",
            "metadata": {"issueType": badge.get("type"), "status": badge.get("status"),
                         "assignee": badge.get("assignee")}}


def _person(ref: dict) -> dict:
    uid = str(ref.get("user_id") or ref.get("userId") or ref.get("value") or "").strip()
    if not uid:
        return _error(ref, "user_id가 비었습니다.")
    try:
        data = client().provider.get_json("/rest/api/2/user", params={"username": uid}) or {}
    except Exception as exc:
        return _error(ref, f"사용자를 확인하지 못했습니다: {str(exc)[:120]}")
    actual = data.get("name") or data.get("key")
    if not actual:
        return _error(ref, f"사용자 {uid}를 확인하지 못했습니다.")
    return {"id": str(ref.get("id") or uid), "kind": "person", "resolved": True,
            "userId": actual, "label": data.get("displayName") or actual,
            "mention": f"[~{actual}]", "avatarUrl": f"/api/avatar/{actual}"}


def _document(ref: dict) -> dict:
    from app.agent.retrieval.harvest import _conf_id
    raw = str(ref.get("url") or ref.get("page_id") or ref.get("pageId")
              or ref.get("value") or "").strip()
    cid = _conf_id(raw) or (raw if raw.isdigit() else "")
    if not cid:
        return _error(ref, "Confluence page id를 찾지 못했습니다.")
    spaces = search_spaces()
    if not spaces:
        return _error(ref, "검색 범위 미설정 — search.confluence.spaces를 지정하세요")
    data = client().confluence_page(cid, expand="space,version") or {}
    space = ((data.get("space") or {}).get("key") or "").upper()
    if space not in {x.upper() for x in spaces}:
        return _error(ref, "문서가 search.confluence.spaces 범위 밖입니다.")
    links = data.get("_links") or {}
    web = links.get("webui") or ""
    base = (getattr(settings(), "confluence_base", "") or "").rstrip("/")
    url = raw if raw.startswith(("http://", "https://")) else \
        ((base + web) if base and web.startswith("/") else web)
    return {"id": str(ref.get("id") or cid), "kind": "document", "resolved": True,
            "pageId": cid, "space": space, "label": data.get("title") or cid,
            "url": url, "metadata": {"updated": (data.get("version") or {}).get("when")}}


def _external(ref: dict) -> dict:
    url = str(ref.get("url") or ref.get("value") or "").strip()
    try:
        parsed = urlsplit(url)
    except Exception:
        parsed = None
    if not parsed or parsed.scheme not in ("http", "https") or not parsed.netloc:
        return _error(ref, "외부 참조 URL은 http/https 절대 URL이어야 합니다.")
    return {"id": str(ref.get("id") or url), "kind": "external", "resolved": True,
            "label": str(ref.get("label") or parsed.netloc), "url": url}


def resolve_references(refs: list[dict]) -> dict:
    """typed references를 검증하고 canonical label/url/metadata로 해석한다."""
    out, seen = [], set()
    for raw in refs or []:
        ref = raw if isinstance(raw, dict) else {}
        rid = str(ref.get("id") or "").strip()
        if not rid:
            out.append(_error(ref, "reference id가 비었습니다.")); continue
        if rid in seen:
            out.append(_error(ref, f"중복 reference id: {rid}")); continue
        seen.add(rid)
        kind = str(ref.get("kind") or "").strip().lower()
        try:
            item = {"ticket": _ticket, "person": _person, "document": _document,
                    "external": _external}.get(kind, lambda r: _error(r, f"지원하지 않는 kind: {kind}"))(ref)
        except Exception as exc:
            item = _error(ref, str(exc)[:180])
        out.append(item)
    unresolved = [x for x in out if not x.get("resolved")]
    return {"ok": not unresolved, "references": out,
            "unresolved": unresolved, "resolvedCount": len(out) - len(unresolved)}


def render_template(content_template: str, resolved: list[dict]) -> dict:
    """모델의 placeholder template을 escape한 뒤 검증된 링크/mention만 주입한다."""
    by_id = {str(x.get("id")): x for x in resolved or [] if x.get("resolved")}
    source = str(content_template or "")
    pos, chunks, missing = 0, [], []
    for match in _TOKEN.finditer(source):
        chunks.append(html.escape(source[pos:match.start()]))
        token_kind, rid = match.group(1), match.group(2)
        ref = by_id.get(rid)
        if not ref or (token_kind == "mention" and ref.get("kind") != "person"):
            missing.append(rid)
            chunks.append(html.escape(match.group(0)))
        elif token_kind == "mention":
            chunks.append(f'<span class="md-person mention" data-uid="{html.escape(ref["userId"])}">'
                          f'{html.escape(ref["label"])}</span>')
        else:
            css = {"ticket": "jira-badge tkt", "document": "conf-link",
                   "external": "ref-link", "person": "md-person"}.get(ref.get("kind"), "ref-link")
            chunks.append(f'<a class="{css}" href="{html.escape(ref.get("url") or "#")}" '
                          f'target="_blank" rel="noopener">{html.escape(ref.get("label") or rid)}</a>')
        pos = match.end()
    chunks.append(html.escape(source[pos:]))
    return {"ok": not missing, "html": "".join(chunks).replace("\n", "<br>"),
            "missing": sorted(set(missing))}


def _attrs(attrs) -> dict[str, str]:
    return {str(key).lower(): str(value or "") for key, value in attrs}


def _ticket_key_from_anchor(attrs: dict[str, str]) -> str:
    key = str(attrs.get("data-key") or "").strip().upper()
    if re.fullmatch(r"[A-Z][A-Z0-9]{1,9}-\d+", key):
        return key
    href = str(attrs.get("href") or "").strip()
    match = (re.fullmatch(r"([A-Z][A-Z0-9]{1,9}-\d+)", href, re.I)
             or re.search(r"/browse/([A-Z][A-Z0-9]{1,9}-\d+)(?:$|[?#])", href, re.I))
    return match.group(1).upper() if match else ""


def render_editor_references(content_html: str, resolved: list[dict]) -> str:
    """Render resolver-approved identities into the editor's canonical HTML IR.

    The model-provided label is deliberately discarded for typed references.  Jira keys,
    people, and Confluence documents use the resolver's exact identity/label, while an
    external link keeps only its validated URL and resolver label.
    """
    tickets = {str(item.get("key") or "").upper(): item for item in resolved or []
               if item.get("resolved") and item.get("kind") == "ticket"}
    people = {str(item.get("userId") or ""): item for item in resolved or []
              if item.get("resolved") and item.get("kind") == "person"}
    urls = {str(item.get("url") or ""): item for item in resolved or []
            if item.get("resolved") and item.get("kind") in {"document", "external"}}

    class Renderer(HTMLParser):
        def __init__(self):
            super().__init__(convert_charrefs=False)
            self.out: list[str] = []
            self.skip_tag = ""
            self.skip_depth = 0

        def _canonical(self, tag: str, attrs) -> str:
            row = _attrs(attrs)
            if tag == "a":
                key = _ticket_key_from_anchor(row)
                ticket = tickets.get(key)
                if ticket:
                    safe = html.escape(key, quote=True)
                    return (f'<a class="jira-badge tkt" data-key="{safe}" '
                            f'href="/browse/{safe}">{html.escape(key)}</a>')
                ref = urls.get(row.get("href") or "")
                if ref:
                    css = "conf-link" if ref.get("kind") == "document" else "ref-link"
                    url = html.escape(str(ref.get("url") or ""), quote=True)
                    label = html.escape(str(ref.get("label") or ref.get("url") or ""))
                    return (f'<a class="{css}" href="{url}" target="_blank" '
                            f'rel="noopener">{label}</a>')
            if tag == "span" and row.get("data-type") == "mention":
                uid = str(row.get("data-id") or row.get("data-uid") or "")
                person = people.get(uid)
                if person:
                    actual = html.escape(str(person.get("userId") or uid), quote=True)
                    label = html.escape(str(person.get("label") or actual))
                    return (f'<span data-type="mention" data-id="{actual}" '
                            f'data-label="{label}">@{label}</span>')
            return ""

        def handle_starttag(self, tag, attrs):
            lower = tag.lower()
            if self.skip_tag:
                self.skip_depth += 1
                return
            canonical = self._canonical(lower, attrs)
            if canonical:
                self.out.append(canonical)
                self.skip_tag = lower
                self.skip_depth = 0
                return
            rendered = "".join(
                f' {html.escape(str(key), quote=True)}="{html.escape(str(value or ""), quote=True)}"'
                for key, value in attrs)
            self.out.append(f"<{tag}{rendered}>")

        def handle_startendtag(self, tag, attrs):
            if self.skip_tag:
                return
            rendered = "".join(
                f' {html.escape(str(key), quote=True)}="{html.escape(str(value or ""), quote=True)}"'
                for key, value in attrs)
            self.out.append(f"<{tag}{rendered}/>")

        def handle_endtag(self, tag):
            if self.skip_tag:
                if self.skip_depth:
                    self.skip_depth -= 1
                elif tag.lower() == self.skip_tag:
                    self.skip_tag = ""
                return
            self.out.append(f"</{tag}>")

        def handle_data(self, data):
            if not self.skip_tag:
                self.out.append(data)

        def handle_entityref(self, name):
            if not self.skip_tag:
                self.out.append(f"&{name};")

        def handle_charref(self, name):
            if not self.skip_tag:
                self.out.append(f"&#{name};")

        def handle_comment(self, data):
            if not self.skip_tag:
                self.out.append(f"<!--{data}-->")

    parser = Renderer()
    try:
        parser.feed(str(content_html or ""))
        parser.close()
        return "".join(parser.out)
    except Exception:
        return str(content_html or "")


def validate_editor_html(content_html: str, resolved: list[dict]) -> dict:
    """Validate the last editor boundary after all deterministic rendering.

    This checker never repairs or deletes prose.  It reports the exact unsafe identity or
    rendering token so the caller can fail closed instead of returning insertable HTML with
    a low-salience warning.
    """
    resolved_tickets = {str(item.get("key") or "").upper() for item in resolved or []
                        if item.get("resolved") and item.get("kind") == "ticket"}
    resolved_people = {str(item.get("userId") or "") for item in resolved or []
                       if item.get("resolved") and item.get("kind") == "person"}
    resolved_urls = {str(item.get("url") or "") for item in resolved or []
                     if item.get("resolved") and item.get("kind") in {"document", "external"}}
    issues: list[dict] = []
    seen: set[tuple[str, str]] = set()

    def add(code: str, value: str):
        item = (str(code), str(value or "")[:180])
        if item not in seen:
            seen.add(item)
            issues.append({"code": item[0], "value": item[1]})

    for item in resolved or []:
        if not item.get("resolved"):
            add("unresolved_reference", str(item.get("id") or ""))

    class Inspector(HTMLParser):
        def __init__(self):
            super().__init__(convert_charrefs=False)
            self.stack: list[tuple[str, dict[str, str]]] = []

        def handle_starttag(self, tag, attrs):
            lower, row = tag.lower(), _attrs(attrs)
            self.stack.append((lower, row))
            if lower in {"script", "style", "iframe", "object", "embed", "template",
                         "form", "input", "button", "textarea", "select", "option",
                         "meta", "link", "base"}:
                add("unsafe_html", lower)
            for name, value in row.items():
                folded = value.strip().casefold()
                if (name.startswith("on") or name in {"srcdoc", "formaction"}
                        or "javascript:" in folded or "data:text/html" in folded
                        or (name == "style" and ("expression(" in folded or "url(" in folded))):
                    add("unsafe_html", f"{lower}.{name}")
                # Compose HTML is inserted into TipTap before Jira's save-time sanitizer.
                # A model-controlled image/video/SVG URL can therefore trigger an immediate
                # browser GET to an external or LAN host. Editor output has no typed media
                # contract, so only canonical ``a[href]`` references resolved below may carry
                # a network location; every other network-bearing attribute fails closed.
                if ((name in {"src", "srcset", "poster", "ping", "background",
                              "xlink:href", "action"}
                     or (name == "href" and lower != "a")) and value.strip()):
                    add("unsafe_html", f"{lower}.{name}")
            if lower == "a":
                key = _ticket_key_from_anchor(row)
                href = row.get("href") or ""
                css = set((row.get("class") or "").split())
                if key:
                    if (key not in resolved_tickets or row.get("data-key", "").upper() != key
                            or not {"jira-badge", "tkt"}.issubset(css)
                            or href != f"/browse/{key}"):
                        add("noncanonical_reference", key)
                elif href not in resolved_urls:
                    add("unresolved_reference", href or "anchor-without-href")
                else:
                    ref = next((item for item in resolved or []
                                if item.get("resolved") and item.get("url") == href), {})
                    expected = "conf-link" if ref.get("kind") == "document" else "ref-link"
                    if expected not in css:
                        add("noncanonical_reference", href)
            elif lower == "span" and row.get("data-type") == "mention":
                uid = row.get("data-id") or ""
                if not uid or uid not in resolved_people:
                    add("unresolved_person", uid or "mention-without-id")

        def handle_endtag(self, tag):
            lower = tag.lower()
            for index in range(len(self.stack) - 1, -1, -1):
                if self.stack[index][0] == lower:
                    del self.stack[index:]
                    break

        def handle_data(self, data):
            inside_anchor = any(tag == "a" for tag, _ in self.stack)
            inside_mention = any(tag == "span" and row.get("data-type") == "mention"
                                 for tag, row in self.stack)
            if not inside_anchor:
                for match in re.finditer(r"\b([A-Z][A-Z0-9]{0,9}-\d+)\b", data, re.I):
                    add("unresolved_ticket", match.group(1).upper())
                for match in re.finditer(r"https?://[^\s<>\"']+", data, re.I):
                    add("bare_url", match.group(0).rstrip(".,;:!?)]}"))
            if not inside_mention:
                raw_mentions = re.findall(
                    r"\[~([A-Za-z0-9._-]+)\]|(?<![\w@])@([A-Za-z0-9][A-Za-z0-9._-]{1,63})\b",
                    data)
                for bracketed, at_value in raw_mentions:
                    add("raw_mention", bracketed or at_value)
            if re.search(r"```|!?\[[^\]\n]+\]\([^)\n]+\)|\*\*[^*\n]+\*\*", data, re.I):
                add("markdown", data.strip())
            if re.search(r"(?m)^\s*(?:#{1,6}\s+|[-+*]\s+|\d+[.)]\s+|>\s+)", data):
                add("markdown", data.strip())
            # `h2. 제목` is a malformed heading only when it occupies a short block-like
            # text node.  A prose sentence discussing the literal string `h2.` is allowed.
            plain = data.strip()
            if (len(plain) <= 80 and not re.search(r"[.!?。]\s*$", plain)
                    and re.match(r"(?i)^h[1-6]\.\s+\S", plain)):
                add("heading_marker", plain)

    parser = Inspector()
    try:
        parser.feed(str(content_html or ""))
        parser.close()
        dangling = [tag for tag, row in parser.stack
                    if tag == "a" or (tag == "span" and row.get("data-type") == "mention")]
        if dangling:
            add("invalid_html", "unclosed canonical reference: " + ", ".join(dangling))
    except Exception as exc:
        add("invalid_html", str(exc))
    return {"ok": not issues, "issues": issues}


__all__ = ["resolve_references", "render_template", "render_editor_references",
           "validate_editor_html"]
