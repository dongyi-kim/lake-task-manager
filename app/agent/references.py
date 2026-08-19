"""Agent 산출물의 ticket/person/document/external 참조를 결정적으로 해석·렌더링한다."""

from __future__ import annotations

import html
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Callable, Generic, Literal, TypeVar
from urllib.parse import urlsplit

import nh3

from app.agent.tools._ctx import (client, jira_key_allowed, search_spaces,
                                  settings)


_TOKEN = re.compile(r"\{\{(ref|mention):([A-Za-z0-9_.:-]+)\}\}")
_EDITOR_TAG_RE = re.compile(
    r"</?(?:p|br|hr|h[1-6]|ul|ol|li|a|span|strong|em|s|code|pre|blockquote|"
    r"table|thead|tbody|tfoot|tr|td|th)\b",
    re.I,
)
_MARKDOWN_BLOCK_RE = re.compile(
    r"(?m)^\s{0,3}(?:#{1,6}\s+\S|[-+*]\s+(?:\[[ xX]\]\s*)?\S|"
    r"\d+[.)]\s+\S|>\s+\S|```|\|.*\|\s*$)",
)
_MARKDOWN_INLINE_RE = re.compile(
    r"!?\[[^\]\n]+\]\([^)\n]+\)|\*\*[^*\n]+\*\*|~~[^~\n]+~~|`[^`\n]+`",
)
_MARKDOWN_LINK_RE = re.compile(r"!?\[[^\]\n]*\]\(([^)\s]+)\)")
_VOID_TAGS = {"area", "base", "br", "col", "embed", "hr", "img", "input",
              "link", "meta", "param", "source", "track", "wbr"}
_ROOT_BLOCK_TAGS = {
    "address", "article", "aside", "blockquote", "div", "dl", "fieldset", "figure",
    "footer", "form", "h1", "h2", "h3", "h4", "h5", "h6", "header", "hr", "li",
    "main", "nav", "ol", "p", "pre", "section", "table", "tbody", "td", "tfoot",
    "th", "thead", "tr", "ul",
}
_EDITOR_ALLOWED_TAGS = {
    "p", "div", "section", "h1", "h2", "h3", "h4", "h5", "h6", "br", "hr",
    "blockquote", "strong", "b", "em", "i", "u", "s", "del", "code", "pre",
    "ul", "ol", "li", "table", "thead", "tbody", "tfoot", "tr", "th", "td",
    "span", "a",
}
_EDITOR_ALLOWED_ATTRIBUTES = {
    "a": {"href", "data-key", "target", "rel"},
    "span": {"data-type", "data-id", "data-label"},
    "ul": {"data-type"},
    "li": {"data-checked"},
    "ol": {"start"},
    "th": {"colspan", "rowspan", "colwidth"},
    "td": {"colspan", "rowspan", "colwidth"},
}
_EDITOR_ALLOWED_CLASSES = {
    "a": {"jira-badge", "tkt", "conf-link", "ref-link"},
    "span": {"md-person", "mention"},
}
_EDITOR_HTML_CLEANER = nh3.Cleaner(
    tags=_EDITOR_ALLOWED_TAGS,
    attributes=_EDITOR_ALLOWED_ATTRIBUTES,
    allowed_classes=_EDITOR_ALLOWED_CLASSES,
    clean_content_tags={
        "script", "style", "iframe", "object", "embed", "template", "form", "svg",
    },
    url_schemes={"http", "https"},
    url_relative="pass_through",
    link_rel=None,
    strip_comments=True,
)


@dataclass(frozen=True)
class EditorRenderDiagnostic:
    """A non-prose diagnostic emitted by the editor rendering boundary."""

    stage: str
    code: str
    detail: str = ""

    def as_dict(self) -> dict[str, str]:
        return {"stage": self.stage, "code": self.code, "detail": self.detail}


@dataclass(frozen=True)
class EditorMarkupNormalization:
    """Typed result of accepting HTML, Markdown, or a mixed provider response."""

    html: str
    input_format: Literal["empty", "html", "markdown", "mixed"]
    diagnostics: tuple[EditorRenderDiagnostic, ...] = ()

    @property
    def ok(self) -> bool:
        return not self.diagnostics


_StageValue = TypeVar("_StageValue")


@dataclass(frozen=True)
class EditorStageResult(Generic[_StageValue]):
    """Runtime adapter for one render stage; raw functions remain directly testable."""

    value: _StageValue | None = None
    diagnostic: EditorRenderDiagnostic | None = None

    @property
    def ok(self) -> bool:
        return self.diagnostic is None


def run_editor_stage(stage: str, operation: Callable[[], _StageValue]) -> EditorStageResult[_StageValue]:
    """Fail closed at a named UI boundary without serializing exception text or endpoints.

    Resolver and renderer functions themselves do not swallow exceptions, so their unit tests
    still expose programming defects. The compose adapter uses this narrow stage wrapper to
    keep a provider/client failure from becoming an unclassified HTTP 500. Only the exception
    class is retained; request URLs, headers, and credentials are deliberately omitted.
    """
    try:
        return EditorStageResult(value=operation())
    except Exception as exc:
        return EditorStageResult(diagnostic=EditorRenderDiagnostic(
            str(stage or "editor_render"), "runtime_failure", type(exc).__name__,
        ))


@dataclass
class _MarkupElement:
    tag: str
    attrs: tuple[tuple[str, str], ...] = ()
    children: list[object] = field(default_factory=list)
    self_closing: bool = False


@dataclass(frozen=True)
class _MarkupComment:
    value: str


class _MixedMarkupParser(HTMLParser):
    """Parse provider markup into a small tree so only text nodes become Markdown."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self.root: list[object] = []
        self.stack: list[_MarkupElement] = []
        self.diagnostics: list[EditorRenderDiagnostic] = []

    def _append(self, value: object) -> None:
        (self.stack[-1].children if self.stack else self.root).append(value)

    @staticmethod
    def _attrs(attrs) -> tuple[tuple[str, str], ...]:
        return tuple((str(key).lower(), str(value or "")) for key, value in attrs)

    def handle_starttag(self, tag, attrs):
        lower = str(tag).lower()
        node = _MarkupElement(lower, self._attrs(attrs), self_closing=lower in _VOID_TAGS)
        self._append(node)
        if not node.self_closing:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self._append(_MarkupElement(str(tag).lower(), self._attrs(attrs), self_closing=True))

    def handle_endtag(self, tag):
        lower = str(tag).lower()
        if not self.stack or self.stack[-1].tag != lower:
            self.diagnostics.append(EditorRenderDiagnostic(
                "markup_normalization", "malformed_html", f"unexpected closing tag: {lower}",
            ))
            return
        self.stack.pop()

    def handle_data(self, data):
        self._append(str(data))

    def handle_entityref(self, name):
        self._append(f"&{name};")

    def handle_charref(self, name):
        self._append(f"&#{name};")

    def handle_comment(self, data):
        self._append(_MarkupComment(str(data)))

    def close_tree(self) -> None:
        self.close()
        if self.stack:
            tags = ", ".join(node.tag for node in self.stack[-4:])
            self.diagnostics.append(EditorRenderDiagnostic(
                "markup_normalization", "malformed_html", f"unclosed tag: {tags}",
            ))


def _serialize_attrs(attrs: tuple[tuple[str, str], ...]) -> str:
    return "".join(
        f' {html.escape(key, quote=True)}="{html.escape(value, quote=True)}"'
        for key, value in attrs
    )


def _markdown_html(value: str) -> str:
    from app.content.mdhtml import markdown_to_html

    return markdown_to_html(html.unescape(str(value or "")))


def _inline_markdown_html(value: str) -> str:
    source = str(value or "")
    if not _MARKDOWN_INLINE_RE.search(html.unescape(source)):
        return source
    leading_size = len(source) - len(source.lstrip())
    trailing_size = len(source) - len(source.rstrip())
    end = len(source) - trailing_size if trailing_size else len(source)
    leading, body, trailing = source[:leading_size], source[leading_size:end], source[end:]
    rendered = _markdown_html(body)
    match = re.fullmatch(r"<p>(.*)</p>", rendered, re.S)
    return leading + (match.group(1) if match else rendered) + trailing


def _render_verbatim_node(node: object) -> str:
    """Serialize a code/pre/anchor subtree without interpreting Markdown in descendants."""
    if isinstance(node, _MarkupComment):
        return f"<!--{node.value}-->"
    if isinstance(node, str):
        return node
    if not isinstance(node, _MarkupElement):
        raise TypeError(f"unsupported editor markup node: {type(node).__name__}")
    attrs = _serialize_attrs(node.attrs)
    if node.self_closing:
        return f"<{node.tag}{attrs}>"
    body = "".join(_render_verbatim_node(child) for child in node.children)
    return f"<{node.tag}{attrs}>{body}</{node.tag}>"


def _render_markdown_flow(nodes: list[object], *, unwrap_paragraph: bool,
                          force_markdown: bool = False) -> str:
    """Render one contiguous inline flow through stable DOM placeholders."""
    if not nodes:
        return ""
    probe = "".join(node if isinstance(node, str) else "LTM_INLINE_NODE" for node in nodes)
    has_markdown = bool(
        _MARKDOWN_BLOCK_RE.search(html.unescape(probe))
        or _MARKDOWN_INLINE_RE.search(html.unescape(probe))
    )
    if not force_markdown and not has_markdown:
        return "".join(
            node if isinstance(node, str) else _render_mixed_node(node)
            for node in nodes
        )

    rendered_nodes = [
        node if isinstance(node, str) else _render_mixed_node(node)
        for node in nodes
    ]
    collision_text = "".join(rendered_nodes)
    token_prefix = "LTMINLINEFLOWPLACEHOLDER"
    while token_prefix in collision_text:
        token_prefix += "X"
    parts: list[str] = []
    replacements: list[tuple[str, str]] = []
    for original, rendered_node in zip(nodes, rendered_nodes):
        if isinstance(original, str):
            parts.append(rendered_node)
            continue
        token = f"{token_prefix}{len(replacements)}Z"
        parts.append(token)
        replacements.append((token, rendered_node))

    source = "".join(parts)
    leading = trailing = ""
    if unwrap_paragraph:
        leading_size = len(source) - len(source.lstrip())
        trailing_size = len(source) - len(source.rstrip())
        end = len(source) - trailing_size if trailing_size else len(source)
        leading, source, trailing = source[:leading_size], source[leading_size:end], source[end:]
    rendered = _markdown_html(source)
    if unwrap_paragraph:
        paragraph = re.fullmatch(r"<p>(.*)</p>", rendered, re.S)
        rendered = leading + (paragraph.group(1) if paragraph else rendered) + trailing
    for token, markup in replacements:
        rendered = rendered.replace(token, markup)
    return rendered


def _render_mixed_children(nodes: list[object]) -> str:
    """Render inline siblings together, while retaining real block-element boundaries."""
    out: list[str] = []
    flow: list[object] = []

    def flush() -> None:
        if flow:
            out.append(_render_markdown_flow(flow, unwrap_paragraph=True))
            flow.clear()

    for child in nodes:
        if isinstance(child, _MarkupElement) and child.tag in _ROOT_BLOCK_TAGS:
            flush()
            out.append(_render_mixed_node(child))
        else:
            flow.append(child)
    flush()
    return "".join(out)


def _render_mixed_node(node: object) -> str:
    if isinstance(node, _MarkupComment):
        return f"<!--{node.value}-->"
    if isinstance(node, str):
        return _inline_markdown_html(node)
    if not isinstance(node, _MarkupElement):
        raise TypeError(f"unsupported editor markup node: {type(node).__name__}")

    attrs = _serialize_attrs(node.attrs)
    if node.self_closing:
        return f"<{node.tag}{attrs}>"

    # A provider sometimes wraps a Markdown heading/list in ``<p>`` while mixing it with
    # valid HTML blocks.  Convert that whole text-only block instead of creating invalid
    # nested ``<p><h3>`` markup. Elements already carrying typed references stay untouched.
    text_only = all(isinstance(child, str) for child in node.children)
    raw_text = "".join(str(child) for child in node.children) if text_only else ""
    if node.tag == "p" and text_only and _MARKDOWN_BLOCK_RE.search(html.unescape(raw_text)):
        return _markdown_html(raw_text)

    preserve_markdown = node.tag in {"code", "pre", "a"}
    if text_only:
        body = raw_text if preserve_markdown else _inline_markdown_html(raw_text)
        return f"<{node.tag}{attrs}>{body}</{node.tag}>"
    body = ("".join(_render_verbatim_node(child) for child in node.children)
            if preserve_markdown else _render_mixed_children(node.children))
    return f"<{node.tag}{attrs}>{body}</{node.tag}>"


def _render_mixed_root(nodes: list[object], *, normalize_markdown: bool = True) -> str:
    """Render root flow without applying Markdown semantics to an HTML-only document."""
    if not normalize_markdown:
        return "".join(
            node if isinstance(node, str) else _render_mixed_node(node)
            for node in nodes
        )

    # Markdown and inline DOM share one flow. The same placeholder renderer is used inside
    # element bodies so delimiters can span an inline child without crossing block children.
    out: list[str] = []
    flow: list[object] = []

    def flush() -> None:
        if not flow:
            return
        rendered = _render_markdown_flow(
            flow, unwrap_paragraph=False, force_markdown=True,
        )
        if rendered:
            out.append(rendered)
        flow.clear()

    for node in nodes:
        if isinstance(node, _MarkupElement) and node.tag in _ROOT_BLOCK_TAGS:
            flush()
            out.append(_render_mixed_node(node))
            continue
        flow.append(node)
    flush()
    return "".join(out)


def normalize_editor_markup(content: str) -> EditorMarkupNormalization:
    """Normalize provider HTML/Markdown into one editor HTML dialect.

    The model-facing contract requests HTML, but compatible providers can return Markdown
    or interleave Markdown blocks with valid HTML. This boundary parses existing elements
    first and applies the shared Markdown renderer only to text nodes, so typed anchors and
    mentions are never escaped or reconstructed by regex. Unsupported Markdown destinations
    and malformed HTML remain fail-closed typed diagnostics for the caller.
    """
    source = str(content or "").strip()
    if not source:
        return EditorMarkupNormalization("", "empty")

    has_html = bool(_EDITOR_TAG_RE.search(source))
    has_markdown = bool(_MARKDOWN_BLOCK_RE.search(source) or _MARKDOWN_INLINE_RE.search(source))
    input_format: Literal["html", "markdown", "mixed"] = (
        "mixed" if has_html and has_markdown else "html" if has_html else "markdown"
    )
    unsafe_schemes: list[str] = []
    for destination in _MARKDOWN_LINK_RE.findall(html.unescape(source)):
        parsed = urlsplit(destination)
        if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
            unsafe_schemes.append(parsed.scheme.lower() or "relative")
    if unsafe_schemes:
        detail = "unsupported schemes: " + ", ".join(sorted(set(unsafe_schemes)))
        return EditorMarkupNormalization(source, input_format, (
            EditorRenderDiagnostic(
                "markup_normalization", "unsupported_link_destination", detail,
            ),
        ))

    parser = _MixedMarkupParser()
    parser.feed(source)
    parser.close_tree()
    if parser.diagnostics:
        return EditorMarkupNormalization(source, input_format, tuple(parser.diagnostics))
    rendered = _render_mixed_root(parser.root, normalize_markdown=input_format != "html")
    return EditorMarkupNormalization(rendered, input_format)


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
                if lower not in _VOID_TAGS:
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
    # Invalid identities are value-level resolver/validator diagnostics. A programming
    # defect in this deterministic renderer must remain observable to the caller/evaluator
    # rather than silently returning the unsafe pre-rendered fragment.
    parser.feed(str(content_html or ""))
    parser.close()
    return "".join(parser.out)


def validate_editor_html(content_html: str, resolved: list[dict]) -> dict:
    """Validate the last editor boundary after all deterministic rendering.

    ``nh3`` owns HTML5 parsing and the complete element/attribute/URL allowlist.  Any cleanup
    means the provider did not produce the supported editor IR, so the response fails closed.
    The remaining inspector validates only product identities and visible editor text.
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

    source = str(content_html or "")
    try:
        canonical = _EDITOR_HTML_CLEANER.clean(source)
    except Exception as exc:
        canonical = ""
        add("noncanonical_html", f"sanitizer failure: {type(exc).__name__}")
    if canonical != source:
        add("noncanonical_html", "outside the supported editor HTML IR")

    class Inspector(HTMLParser):
        def __init__(self):
            super().__init__(convert_charrefs=False)
            self.stack: list[tuple[str, dict[str, str]]] = []

        def handle_starttag(self, tag, attrs):
            lower, row = tag.lower(), _attrs(attrs)
            if lower not in _VOID_TAGS:
                self.stack.append((lower, row))
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
            if lower not in _VOID_TAGS and self.stack and self.stack[-1][0] == lower:
                self.stack.pop()

        def handle_startendtag(self, tag, attrs):
            lower = tag.lower()
            self.handle_starttag(tag, attrs)
            if lower not in _VOID_TAGS:
                if self.stack and self.stack[-1][0] == lower:
                    self.stack.pop()

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
        parser.feed(canonical)
        parser.close()
    except Exception as exc:
        add("noncanonical_html", f"product inspection failure: {type(exc).__name__}")
    return {"ok": not issues, "issues": issues}


__all__ = ["EditorMarkupNormalization", "EditorRenderDiagnostic", "EditorStageResult",
           "normalize_editor_markup", "run_editor_stage", "resolve_references",
           "render_template", "render_editor_references", "validate_editor_html"]
