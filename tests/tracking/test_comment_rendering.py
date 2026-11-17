"""Stored Tracking comments render safely without fetching Jira again."""
from copy import deepcopy

import pytest

from test_backend_manager import Provider, manager


def saved_run(tmp_path, *, body, rendered_body=None, body_format="wiki"):
    provider = Provider()
    m = manager(tmp_path, provider)
    m.client.s.comment_format = body_format
    event = {"id": "P-1:comment:1:created", "issueKey": "P-1", "kind": "comment",
             "commentId": "1", "body": body, "excerpt": body, "bodyHtml": "<script>stale()</script>"}
    snapshot = {"id": "saved", "status": "complete", "events": [event],
                "issues": [{"key": "P-1", "events": [deepcopy(event)]}]}
    m.store.insert(snapshot, *m.authorize())
    if rendered_body:
        _, _, stored, cp = m.store.read("saved")
        cp["sources"] = {"P-1": {"comments": [{"id": "1", "body": body, "renderedBody": rendered_body}]}}
        m.store.save("saved", stored, cp)
    return m, provider


def test_old_saved_wiki_comments_render_tables_lists_and_code_without_jira_reads(tmp_path):
    body = "* *Ready*\n* Next\n\n||State||Owner||\n|Done|Kim|\n\n{code:python}\nprint(1)\n{code}"
    m, provider = saved_run(tmp_path, body=body)
    run = m.get("saved")
    html = run["events"][0].get("bodyHtml", "")
    assert "<strong>Ready</strong>" in html
    assert "<ul>" in html and "<li>" in html
    assert "<table" in html and ">State</th>" in html
    assert '<code class="language-python">print(1)</code>' in html
    assert run["issues"][0]["events"][0]["bodyHtml"] == html
    assert provider.calls == []
    assert m.store.read("saved")[2]["events"][0]["body"] == body


def test_html_comments_are_sanitized_and_saved_presentation_is_never_trusted(tmp_path):
    m, provider = saved_run(tmp_path, body='<p onclick="bad()">Hi <b>team</b></p><script>bad()</script>'
        '<a href="javascript:bad()">link</a><img src="x" onerror="bad()">', body_format="html")
    html = m.get("saved")["events"][0].get("bodyHtml", "")
    assert "<p>Hi <b>team</b></p>" in html
    assert all(value not in html for value in ("script", "onclick", "onerror", "javascript:", "stale"))
    assert provider.calls == []


def test_get_prefers_already_collected_rendered_body_and_still_sanitizes(tmp_path):
    m, provider = saved_run(tmp_path, body="unsupported wiki macro",
        rendered_body='<p>Already <em>rendered</em></p><script>bad()</script>')
    html = m.get("saved")["events"][0].get("bodyHtml", "")
    assert html == "<p>Already <em>rendered</em></p>"
    assert provider.calls == []


@pytest.mark.parametrize(("body", "body_format", "expected"), [
    ('<p>Lake <b>ready</b>!</p><p>Next<br>line &amp; more</p>'
     '<pre><code>&lt;div&gt; &amp; x &lt; 3</code></pre><script>unsafe()</script>',
     "html", 'Lake ready!\nNext\nline & more\n<div> & x < 3'),
    ('*Ready*\n\n{code:html}\n<div> & x < 3\n{code}',
     "wiki", 'Ready\n<div> & x < 3'),
    ('<section>Ready</section><article>Next</article><dl><dt>Owner</dt><dd>Kim</dd></dl>'
     '<table><caption>Result</caption><tr><td>Done</td></tr></table>',
     "html", 'Ready\nNext\nOwner\nKim\nResult\nDone'),
])
def test_plain_comment_projection_preserves_visible_text_and_original_sources(tmp_path, body, body_format, expected):
    m, provider = saved_run(tmp_path, body=body, body_format=body_format)
    stored_before = m.store.read("saved")

    run = m.get("saved")

    assert run["events"][0]["bodyText"] == expected
    assert run["issues"][0]["events"][0]["bodyText"] == expected
    assert run["events"][0]["body"] == body
    assert run["events"][0]["excerpt"] == body
    assert m.store.read("saved") == stored_before
    assert provider.calls == []


def test_plain_comment_projection_uses_the_collected_sanitized_rendering(tmp_path):
    m, provider = saved_run(tmp_path, body="unsupported wiki macro",
        rendered_body='<p>Already <em>rendered</em></p><script>unsafe()</script>')

    assert m.get("saved")["events"][0]["bodyText"] == "Already rendered"
    assert provider.calls == []


@pytest.mark.parametrize("encoding", ["raw", "escaped", "double-escaped"])
@pytest.mark.parametrize("body_format", ["wiki", "html"])
def test_legacy_html_document_is_rendered_from_its_stored_format(tmp_path, encoding, body_format):
    from html import escape

    html = ('<p onclick="bad()">First <strong>ready</strong></p><p>Second</p><p>Third</p>'
            '<table><tr><th>State</th></tr><tr><td>Done</td></tr></table>'
            '<pre><code>&lt;div&gt; &amp; x &lt; 3</code></pre><script>unsafe()</script>')
    body = html
    for _ in range({"raw": 0, "escaped": 1, "double-escaped": 2}[encoding]):
        body = escape(body, quote=False)
    m, provider = saved_run(tmp_path, body=body, body_format=body_format)
    stored = m.store.read("saved")

    event = m.get("saved")["events"][0]

    assert '<p>First <strong>ready</strong></p>' in event["bodyHtml"]
    assert '<table' in event["bodyHtml"]
    assert '<pre><code>&lt;div&gt; &amp; x &lt; 3</code></pre>' in event["bodyHtml"]
    assert event["bodyText"] == 'First ready\nSecond\nThird\nState\nDone\n<div> & x < 3'
    assert all(value not in event["bodyHtml"] for value in ("onclick", "unsafe()", "<script"))
    assert m.store.read("saved") == stored
    assert provider.calls == []


def test_matching_jira_rendering_that_quotes_html_source_does_not_show_tags(tmp_path):
    from html import escape

    body = '<p>First</p><p>Second</p><p>Third</p>'
    m, provider = saved_run(tmp_path, body=body, body_format="html",
                            rendered_body='<p>' + escape(body, quote=False) + '</p>')

    event = m.get("saved")["events"][0]

    assert event["bodyHtml"] == body
    assert event["bodyText"] == 'First\nSecond\nThird'
    assert provider.calls == []


def test_mismatching_checkpoint_cannot_replace_the_observed_comment(tmp_path):
    body = '<p>Observed</p><p>Second</p><p>Third</p>'
    m, provider = saved_run(tmp_path, body=body, body_format="wiki", rendered_body='<p>Newer text</p>')
    _, _, snapshot, checkpoint = m.store.read("saved")
    checkpoint["sources"]["P-1"]["comments"][0]["body"] = '<p>Newer text</p>'
    m.store.save("saved", snapshot, checkpoint)

    assert m.get("saved")["events"][0]["bodyHtml"] == body
    assert provider.calls == []


@pytest.mark.parametrize("body,expected", [
    ('{code:html}\n<p>Literal sample</p>\n{code}', '<p>Literal sample</p>'),
    ('{noformat}\n&lt;p&gt;Literal sample&lt;/p&gt;\n{noformat}', '&lt;p&gt;Literal sample&lt;/p&gt;'),
    ('Compare a < b and c > d', 'Compare a < b and c > d'),
    ('Use &lt;p&gt;sample&lt;/p&gt; in your template', 'Use &lt;p&gt;sample&lt;/p&gt; in your template'),
])
def test_legacy_detection_preserves_literal_samples_and_comparisons(tmp_path, body, expected):
    m, provider = saved_run(tmp_path, body=body)

    assert m.get("saved")["events"][0]["bodyText"] == expected
    assert provider.calls == []


def test_plain_multiline_comment_in_html_configuration_keeps_visible_breaks(tmp_path):
    body = 'First a < b and c > d\nSecond\nThird'
    m, provider = saved_run(tmp_path, body=body, body_format="html")

    event = m.get("saved")["events"][0]

    assert event["bodyHtml"] == 'First a &lt; b and c &gt; d<br>Second<br>Third'
    assert event["bodyText"] == body
    assert provider.calls == []


def test_matching_rendered_code_sample_is_never_reparsed_as_comment_markup(tmp_path):
    body = '{code:html}\n<p>Literal sample</p>\n{code}'
    rich = '<pre><code>&lt;p&gt;Literal sample&lt;/p&gt;</code></pre>'
    m, provider = saved_run(tmp_path, body=body, rendered_body=rich)

    event = m.get("saved")["events"][0]

    assert event["bodyHtml"] == rich
    assert event["bodyText"] == '<p>Literal sample</p>'
    assert provider.calls == []


def test_encoded_collected_rendering_is_sanitized_after_decoding(tmp_path):
    from html import escape

    m, provider = saved_run(tmp_path, body="unsupported wiki macro",
                            rendered_body=escape('<p onclick="bad()">Ready</p><script>unsafe()</script>'))

    event = m.get("saved")["events"][0]

    assert event["bodyHtml"] == '<p>Ready</p>'
    assert event["bodyText"] == 'Ready'
    assert provider.calls == []
