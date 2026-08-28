from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.agent.editor_author import _badgeify
from app.agent.references import (normalize_editor_markup, run_editor_stage,
                                  render_editor_references, render_template,
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


def test_editor_markup_boundary_canonicalizes_mixed_html_and_markdown_blocks():
    source = (
        "<p>측정 결과 검토를 요청드립니다.</p>\n\n"
        "### 확인 대상\n\n"
        "- **측정 기준:** 2홉 100 노드\n"
        "- [검토 문서](https://docs.example/guide)\n\n"
        "<p>확인 후 의견을 남겨 주세요.</p>"
    )

    normalized = normalize_editor_markup(source)

    assert normalized.ok is True
    assert normalized.input_format == "mixed"
    assert "<h3>확인 대상</h3>" in normalized.html
    assert "<ul>" in normalized.html and "<strong>측정 기준:</strong>" in normalized.html
    assert '<a href="https://docs.example/guide">검토 문서</a>' in normalized.html
    assert not any(token in normalized.html for token in ("###", "**", "[검토 문서]("))


def test_editor_markup_boundary_reports_unsupported_markdown_link_without_erasing_input():
    source = '<p>검토 자료</p>\n- [로컬 파일](file:///tmp/private.txt)'

    normalized = normalize_editor_markup(source)

    assert normalized.ok is False
    assert normalized.html == source
    assert [(item.stage, item.code) for item in normalized.diagnostics] == [
        ("markup_normalization", "unsupported_link_destination"),
    ]


def test_editor_markup_boundary_preserves_typed_elements_while_normalizing_inline_markdown():
    source = (
        '<p>**담당:** <span data-type="mention" data-id="skcc.x1001">@홍길동</span> '
        '<a class="jira-badge tkt" data-key="AAA-1" href="/browse/AAA-1">AAA-1</a></p>'
    )

    normalized = normalize_editor_markup(source)

    assert normalized.ok is True
    assert "<strong>담당:</strong>" in normalized.html
    assert normalized.html.count('data-type="mention"') == 1
    assert normalized.html.count('data-key="AAA-1"') == 1
    assert "&lt;span" not in normalized.html and "&lt;a" not in normalized.html


def test_editor_markup_boundary_keeps_entity_inside_one_markdown_construct():
    normalized = normalize_editor_markup("**AT&amp;T 검토**")

    assert normalized.ok is True
    assert normalized.html == "<p><strong>AT&amp;T 검토</strong></p>"


@pytest.mark.parametrize("source", [
    "<strong>Alpha</strong> <em>Beta</em>",
    "prefix <span>middle</span> suffix",
])
def test_editor_markup_boundary_preserves_root_inline_html_flow_and_whitespace(source):
    normalized = normalize_editor_markup(source)

    assert normalized.ok is True
    assert normalized.input_format == "html"
    assert normalized.html == source
    assert validate_editor_html(normalized.html, [])["ok"] is True


@pytest.mark.parametrize(("source", "expected"), [
    (
        "**Alpha** <em>Beta</em>",
        "<p><strong>Alpha</strong> <em>Beta</em></p>",
    ),
    (
        "prefix **bold** <span>middle</span> suffix",
        "<p>prefix <strong>bold</strong> <span>middle</span> suffix</p>",
    ),
])
def test_editor_markup_boundary_groups_contiguous_mixed_root_inline_flow(source, expected):
    normalized = normalize_editor_markup(source)

    assert normalized.ok is True
    assert normalized.input_format == "mixed"
    assert normalized.html == expected
    assert validate_editor_html(normalized.html, [])["ok"] is True


def test_editor_markup_boundary_keeps_true_root_blocks_as_flow_boundaries():
    normalized = normalize_editor_markup(
        "**before** <p>middle</p> **after**",
    )

    assert normalized.ok is True
    assert normalized.html == (
        "<p><strong>before</strong></p>"
        "<p>middle</p>"
        "<p><strong>after</strong></p>"
    )


@pytest.mark.parametrize("entity", ("&amp;", "&#38;"))
def test_editor_markup_boundary_joins_entity_split_text_before_inline_markdown(entity):
    normalized = normalize_editor_markup(f"<p>**AT{entity}T 검토**</p>")

    assert normalized.ok is True
    assert normalized.html == "<p><strong>AT&amp;T 검토</strong></p>"
    assert validate_editor_html(normalized.html, [])["ok"] is True


@pytest.mark.parametrize(("source", "expected"), [
    (
        "<p>**Alpha <em>Beta</em>**</p>",
        "<p><strong>Alpha <em>Beta</em></strong></p>",
    ),
    (
        '<p>**담당 <span data-type="mention" data-id="skcc.x1001">'
        "@홍길동</span>**</p>",
        '<p><strong>담당 <span data-type="mention" data-id="skcc.x1001">'
        "@홍길동</span></strong></p>",
    ),
])
def test_editor_markup_boundary_applies_inline_delimiters_across_inline_children(
        source, expected):
    normalized = normalize_editor_markup(source)

    assert normalized.ok is True
    assert normalized.input_format == "mixed"
    assert normalized.html == expected
    resolved = ([{"resolved": True, "kind": "person", "userId": "skcc.x1001"}]
                if 'data-type="mention"' in source else [])
    assert validate_editor_html(normalized.html, resolved)["ok"] is True


def test_editor_markup_boundary_does_not_span_inline_delimiters_across_block_children():
    normalized = normalize_editor_markup(
        "<blockquote>**before**<p>middle</p>**after**</blockquote>",
    )

    assert normalized.ok is True
    assert normalized.html == (
        "<blockquote><strong>before</strong><p>middle</p>"
        "<strong>after</strong></blockquote>"
    )
    assert validate_editor_html(normalized.html, [])["ok"] is True


@pytest.mark.parametrize(("source", "expected"), [
    (
        "<code>**literal <em>value</em>**</code>",
        "<p><code>**literal <em>value</em>**</code></p>",
    ),
    (
        "<pre>**literal <em>value</em>**</pre>",
        "<pre>**literal <em>value</em>**</pre>",
    ),
    (
        '<a href="https://docs.example/guide">**literal <em>value</em>**</a>',
        '<p><a href="https://docs.example/guide">**literal <em>value</em>**</a></p>',
    ),
])
def test_editor_markup_boundary_keeps_verbatim_element_bodies(source, expected):
    normalized = normalize_editor_markup(source)

    assert normalized.ok is True
    assert normalized.html == expected


def test_editor_runtime_stage_diagnostic_omits_exception_message_and_secret():
    secret = "sk-runtimeSecret123456"

    def fail():
        raise RuntimeError(f"Bearer {secret} at https://user:pass@example.test")

    result = run_editor_stage("reference_resolution", fail)

    assert result.ok is False and result.value is None
    assert result.diagnostic is not None
    assert result.diagnostic.as_dict() == {
        "stage": "reference_resolution", "code": "runtime_failure",
        "detail": "RuntimeError",
    }
    assert secret not in str(result.diagnostic)


def test_editor_validator_does_not_treat_literal_h2_inside_prose_as_a_heading():
    result = validate_editor_html(
        "<p>사용자 seed의 literal h2. 문자열은 그대로 보존해 주세요.</p>", [])

    assert result["ok"] is True


def test_editor_validator_rejects_unsafe_link_protocols():
    unresolved = ["javascript:alert(1)", "data:text/html,boom"]
    unresolved_failures = []
    for href in unresolved:
        result = validate_editor_html(f'<p><a href="{href}">unsafe</a></p>', [])
        if result["ok"] or not any(item["code"] == "unresolved_reference" for item in result["issues"]):
            unresolved_failures.append(href)

    markdown = ["javascript:alert(1)", "data:text/html,boom", "file:///tmp/private.txt"]
    markdown_failures = [
        destination for destination in markdown
        if validate_editor_html(f"[검토 자료]({destination})", [])["ok"]
    ]

    assert not unresolved_failures, unresolved_failures
    assert not markdown_failures, markdown_failures


def test_canonical_anchor_with_a_void_child_preserves_following_html():
    source = '<p><a href="https://example.test/doc">line<br>link</a> after</p>'
    resolved = [{
        "kind": "external", "resolved": True,
        "url": "https://example.test/doc", "label": "Doc",
    }]

    rendered = render_editor_references(source, resolved)

    assert rendered == (
        '<p><a class="ref-link" href="https://example.test/doc" target="_blank" '
        'rel="noopener">Doc</a> after</p>'
    )
    assert validate_editor_html(rendered, resolved)["ok"] is True


def test_editor_validator_rejects_all_noncanonical_html_shapes():
    payloads = [
        "<section><p>본문</p>",
        '<img src="https://example.invalid/track"/>',
        '<p>safe<img src="https://evil.invalid/pixel" src></p>',
        '<p style="background:url(https://evil.invalid/pixel)" style="color:red">safe</p>',
        '<p style="background-image:image-set(&#x27;https://evil.invalid/p.png&#x27; 1x)">safe</p>',
        '<svg><rect fill="url(https://evil.invalid/filter.svg)"></rect></svg>',
        "<p>before<div>block</div>after</p>",
        "<p>before<p>inner</p>after</p>",
    ]
    failures = []
    for payload in payloads:
        result = validate_editor_html(payload, [])
        if result["ok"] or not any(item["code"] == "noncanonical_html" for item in result["issues"]):
            failures.append(payload)

    assert not failures, failures


def test_editor_reference_renderer_rejects_all_unsafe_html_and_network_media():
    payloads = [
        '<script>alert(1)</script>',
        '<p onclick="steal()">본문</p>',
        '<iframe srcdoc="<script>x</script>"></iframe>',
        '<img src="javascript:alert(1)">',
        '<span style="background:url(javascript:alert(1))">본문</span>',
        '<p>review</p><img src="http://127.0.0.1:4457/api/private">',
        '<img src="https://example.invalid/track?token=copied-secret">',
        '<video poster="http://10.0.0.5/private"></video>',
        '<svg><use xlink:href="https://example.invalid/icon.svg#x"></use></svg>',
        '<img srcset="https://example.invalid/a 1x, https://example.invalid/b 2x">',
    ]
    failures = []
    for payload in payloads:
        result = validate_editor_html(render_editor_references(payload, []), [])
        if result["ok"] or not any(item["code"] == "noncanonical_html" for item in result["issues"]):
            failures.append(payload)

    assert not failures, failures
