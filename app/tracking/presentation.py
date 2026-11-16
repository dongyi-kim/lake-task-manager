"""Read-only rich comment projection, including runs saved before rich rendering.

Only local renderers and cached display names are used: reading a saved run must
never fetch Jira comments, attachment metadata, or mentioned users again.
"""
from html.parser import HTMLParser

from app.content.htmlsafe import mark_explicit_jira_links, sanitize_html, shorten_mention_names, tidy_html
from app.content.wikihtml import wiki_to_html


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


def present_comments(snapshot, client, checkpoint=None):
    fmt = str(getattr(client.s, "comment_format", "") or "").lower()
    if fmt not in ("html", "wiki"):
        fmt = "html" if client.env == "prod" else "wiki"
    source_comments = {
        (key, str(comment.get("id"))): comment
        for key, source in (checkpoint or {}).get("sources", {}).items()
        for comment in source.get("comments", [])
    }
    cached_name = getattr(client, "display_name_cached", None)
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
            html = str(rich) if rich else (body if fmt == "html" else wiki_to_html(body, cached_name))
            html = shorten_mention_names(mark_explicit_jira_links(tidy_html(sanitize_html(html)), body))
            rendered[cache_key] = (proxy_media(html) if proxy_media else html, _comment_text(html))
        # Never trust previously saved HTML: derive and sanitize on every response.
        event["bodyHtml"], event["bodyText"] = rendered[cache_key]
    return snapshot
