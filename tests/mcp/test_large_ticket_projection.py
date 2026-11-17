"""Compact ticket reads must not render discarded copies of a large description."""
from types import SimpleNamespace

import pytest

from app.jira import jira_client
from app.local_mcp.read_tools import register_reads


@pytest.mark.parametrize("detail,body_field", [
    ("summary", None), ("read", "descriptionHtml"), ("edit", "descriptionEditHtml"),
])
def test_large_ticket_projection_only_renders_the_requested_body(build_env, monkeypatch, detail, body_field):
    env = build_env()
    # More than 1 MiB, with distinct trailing content to catch accidental clipping.
    text = "complete large description " * 45000 + "TAIL-OF-COMPLETE-DESCRIPTION"
    raw = {"key": "DL-1", "fields": {
        "summary": "Large description", "description": text,
        "issuetype": {"name": "Task"},
        "status": {"name": "Open", "statusCategory": {"key": "new"}},
    }, "renderedFields": {"description": "<p>" + text + "</p>"}}
    reads = []

    def upstream(key, fresh=False):
        reads.append((key, fresh))
        return raw

    monkeypatch.setattr(env.client, "_get_issue_view", upstream)
    full = env.client.ticket_view("DL-1")
    assert text in full["descriptionHtml"]
    assert text in full["descriptionEditHtml"]
    assert full["descriptionSections"]

    def discarded(*args, **kwargs):
        pytest.fail("compact ticket read rendered a discarded description representation")

    monkeypatch.setattr(jira_client, "split_sections", discarded)
    if detail == "summary":
        for name in ("_looks_like_html", "sanitize_html", "tidy_html", "text_to_html"):
            monkeypatch.setattr(jira_client, name, discarded)
        monkeypatch.setattr(env.client, "_description_edit_html", discarded)
        monkeypatch.setattr(env.client, "_proxy_media", discarded)
    elif detail == "read":
        monkeypatch.setattr(env.client, "_description_edit_html", discarded)

    proxy = env.client._proxy_media
    proxied = []

    def proxy_once(html):
        proxied.append(True)
        assert len(proxied) <= 1, "compact ticket read proxied a discarded body"
        return proxy(html)

    monkeypatch.setattr(env.client, "_proxy_media", proxy_once)
    tools = {}
    runtime = SimpleNamespace(client=env.client, check_key=env.runtime.check_key)
    register_reads(lambda fn, **kwargs: tools.setdefault(fn.__name__, fn), runtime)
    reads.clear()
    result = tools["get_ticket"]("DL-1", fresh=True, detail=detail)

    assert reads == [("DL-1", True)]
    assert result["key"] == "DL-1" and result["summary"] == "Large description"
    assert result["detail"] == detail
    body_fields = {"descriptionHtml", "descriptionEditHtml", "descriptionSections"}
    assert result.keys() & body_fields == ({body_field} if body_field else set())
    if body_field:
        assert result[body_field] == full[body_field]
        assert result[body_field].endswith("TAIL-OF-COMPLETE-DESCRIPTION</p>")
    assert raw["fields"]["description"] is text
    # The projection is response-only: the original raw body and default UI stay complete.
    monkeypatch.undo()
    monkeypatch.setattr(env.client, "_get_issue_view", upstream)
    assert env.client.ticket_view("DL-1") == full
