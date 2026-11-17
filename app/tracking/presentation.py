"""Read-only rich comment projection, including runs saved before rich rendering.

Only local renderers and cached display names are used: reading a saved run must
never fetch Jira comments, attachment metadata, or mentioned users again.
"""
from html import unescape
from html.parser import HTMLParser
import re

from app.content.htmlsafe import mark_explicit_jira_links, sanitize_html, shorten_mention_names, text_to_html, tidy_html
from app.content.wikihtml import wiki_to_html
from app.tracking.activity import project_activity
from app.tracking.names import cached_tracking_names, observe_tracking_names


class _CommentText(HTMLParser):
    """Extract visible text from sanitized HTML without reparsing encoded code."""
    blocks = {"p", "div", "section", "article", "li", "ul", "ol", "dl", "dt", "dd", "blockquote", "pre",
              "table", "thead", "tbody", "tfoot", "caption", "tr", "td", "th",
              "h1", "h2", "h3", "h4", "h5", "h6", "hr"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []

    def boundary(self):
        if self.parts and not self.parts[-1].endswith("\n"):
            self.parts.append("\n")

    def handle_starttag(self, tag, attrs):
        if tag == "br":
            self.parts.append("\n")
        elif tag in self.blocks:
            self.boundary()

    handle_startendtag = handle_starttag

    def handle_endtag(self, tag):
        if tag in self.blocks:
            self.boundary()

    def handle_data(self, value):
        self.parts.append(value)


def _comment_text(html):
    parser = _CommentText()
    parser.feed(html)
    parser.close()
    return "".join(parser.parts).strip()


# A saved run may outlive a change from Jira's HTML editor to wiki configuration.
# Recognize whole block documents, not incidental tags in prose or wiki code.
_HTML_DOCUMENT_START = re.compile(
    r"^\s*<(?:p|div|section|article|table|ul|ol|dl|blockquote|pre|h[1-6])(?:\s[^>]*|)>", re.I)
_HTML_DOCUMENT_END = re.compile(r"</[a-z][\w:-]*>\s*$", re.I)


def _stored_html(body):
    """Recover an outer HTML document without decoding entities inside its code.

    Some legacy comments were escaped once or twice before storage. Stop at the
    first actual document; recursively unescaping its contents would turn code
    samples into markup. Wiki {code}/{noformat} and prose never match this shape.
    The returned value still MUST pass through the shared sanitizer.
    """
    candidate = body
    for _ in range(3):
        if _HTML_DOCUMENT_START.match(candidate) and _HTML_DOCUMENT_END.search(candidate):
            return candidate
        decoded = unescape(candidate)
        if decoded == candidate:
            break
        candidate = decoded
    return None


def _comment_html(body, rich, fmt, cached_name):
    document = _stored_html(body)
    if rich:
        rich = str(rich)
        # Jira's wiki renderer can quote a comment whose stored body is already
        # HTML. That is a matching source, but its renderedBody displays raw tags.
        # Only unwrap when the quoted text is precisely this observed document;
        # an intentional <pre><code> sample must remain a code sample.
        quoted = _comment_text(sanitize_html(rich))
        if document and not re.search(r"<(?:pre|code)\b", rich, re.I) and quoted in (body.strip(), document.strip()):
            return document
        return _stored_html(rich) or rich
    if document:
        return document
    if fmt == "html":
        # Even an HTML-configured Jira can contain plain legacy comments. Keep
        # their line breaks visible so the shared rendered-line fold can work.
        return body if re.search(r"</?[a-z][\w:-]*(?:\s[^<>]*|)/?>", body, re.I) else text_to_html(body)
    return wiki_to_html(body, cached_name)


def present_comments(snapshot, client, checkpoint=None):
    snapshot = project_activity(snapshot)
    observe_tracking_names(snapshot, client, checkpoint)
    cached_name = cached_tracking_names(snapshot, client)
    fmt = str(getattr(client.s, "comment_format", "") or "").lower()
    if fmt not in ("html", "wiki"):
        fmt = "html" if client.env == "prod" else "wiki"
    source_comments = {
        (key, str(comment.get("id"))): comment
        for key, source in (checkpoint or {}).get("sources", {}).items()
        for comment in source.get("comments", [])
    }
    proxy_media = getattr(client, "_proxy_media", None)
    rendered = {}
    events = list(snapshot.get("events") or [])
    events.extend(event for issue in snapshot.get("issues", []) for event in issue.get("events", []))
    for event in events:
        if (event.get("kind") or event.get("type")) not in ("comment", "commentEdited"):
            continue
        body = str(event.get("body") or "")
        identity = (event.get("issueKey"), str(event.get("commentId") or ""))
        source = source_comments.get(identity) or {}
        # The raw checkpoint belongs to this run; do not substitute a newer comment.
        rich = source.get("renderedBody") if source.get("body") == body else None
        cache_key = (*identity, body, str(rich or ""))
        if cache_key not in rendered:
            html = _comment_html(body, rich, fmt, cached_name)
            html = shorten_mention_names(mark_explicit_jira_links(tidy_html(sanitize_html(html)), body))
            rendered[cache_key] = (proxy_media(html) if proxy_media else html, _comment_text(html))
        # Never trust previously saved HTML: derive and sanitize on every response.
        event["bodyHtml"], event["bodyText"] = rendered[cache_key]
    return snapshot
