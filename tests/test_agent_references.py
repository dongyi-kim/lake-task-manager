from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.agent.editor_author import _badgeify
from app.agent.references import render_template, resolve_references
from app.agent.tools import _ctx


class _Provider:
    def get_json(self, path, params=None):
        if path.endswith("/user") and (params or {}).get("username") == "skcc.x1001":
            return {"name": "skcc.x1001", "displayName": "홍길동"}
        return {}


class _Client:
    provider = _Provider()

    def ticket_badge(self, key):
        if key == "AAA-1":
            return {"key": key, "summary": "허용 티켓", "type": "Bug", "status": "Open"}
        return {}

    def confluence_page(self, page_id, expand=""):
        if str(page_id) == "42":
            return {"id": "42", "title": "설계 문서", "space": {"key": "SPACE1"},
                    "version": {"when": "2026-08-10"},
                    "_links": {"webui": "/pages/42"}}
        return {}


@pytest.fixture(autouse=True)
def _bind_reference_client():
    settings = SimpleNamespace(
        search_jira_projects=["AAA"], search_confluence_spaces=["SPACE1"],
        jira_base="https://jira.example", confluence_base="https://conf.example")
    _ctx.bind(_Client(), settings)
    yield
    _ctx.bind()


def test_reference_resolution_enforces_jira_and_confluence_search_scope():
    result = resolve_references([
        {"id": "t1", "kind": "ticket", "key": "AAA-1"},
        {"id": "t2", "kind": "ticket", "key": "PRIMARY-9"},
        {"id": "d1", "kind": "document", "page_id": "42"},
        {"id": "p1", "kind": "person", "user_id": "skcc.x1001"},
    ])
    by_id = {x["id"]: x for x in result["references"]}
    assert by_id["t1"]["resolved"] is True
    assert by_id["d1"]["space"] == "SPACE1"
    assert by_id["p1"]["mention"] == "[~skcc.x1001]"
    assert by_id["t2"]["resolved"] is False
    assert "search.jira.projects" in by_id["t2"]["error"]


def test_template_renders_only_resolved_placeholders_and_escapes_raw_html():
    resolved = resolve_references([
        {"id": "t1", "kind": "ticket", "key": "AAA-1"},
        {"id": "p1", "kind": "person", "user_id": "skcc.x1001"},
    ])["references"]
    result = render_template(
        "<script>alert(1)</script> {{ref:t1}} 담당 {{mention:p1}} {{ref:missing}}",
        resolved,
    )
    assert result["ok"] is False and result["missing"] == ["missing"]
    assert "&lt;script&gt;" in result["html"] and "<script>" not in result["html"]
    assert "https://jira.example/browse/AAA-1" in result["html"]
    assert 'data-uid="skcc.x1001"' in result["html"]


def test_badgeify_does_not_create_nested_anchors_or_badge_out_of_scope_keys():
    value = ('<p><a href="https://x.example">AAA-1</a> AAA-1 PRIMARY-9 '
             '[~skcc.x1001]</p>')
    rendered = _badgeify(value)
    assert rendered.count("<a ") == 2
    assert '<a href="https://x.example">AAA-1</a>' in rendered
    assert rendered.count('data-key="AAA-1"') == 1
    assert "PRIMARY-9" in rendered and 'data-key="PRIMARY-9"' not in rendered
    assert rendered.count('data-type="mention"') == 1


def test_badgeify_canonicalizes_ticket_key_href_from_model_html():
    rendered = _badgeify('<p><a href="AAA-1">AAA-1</a></p>')
    assert 'href="/browse/AAA-1"' in rendered
    assert 'class="jira-badge tkt"' in rendered
    assert 'data-key="AAA-1"' in rendered
    assert 'href="AAA-1"' not in rendered
