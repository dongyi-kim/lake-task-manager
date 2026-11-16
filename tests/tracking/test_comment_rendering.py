"""Stored Tracking comments render safely without fetching Jira again."""
from copy import deepcopy

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
