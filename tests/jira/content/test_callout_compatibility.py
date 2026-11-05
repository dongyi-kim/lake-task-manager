"""Jira storage must work without LTM CSS and keep legacy editing identities."""

from types import SimpleNamespace

import pytest

from app.content.htmlsafe import sanitize_html, serialize_jira_html
from app.content.wikihtml import html_to_wiki, wiki_to_html
from app.jira.jira_client import JiraClient


@pytest.fixture(params=["desc_field_value", "comment_field_value"], ids=["description", "comment"])
def store(request):
    # Pure format conversion: no auth, cache, mock server, or network initialization.
    client = object.__new__(JiraClient)
    client.s = SimpleNamespace(description_format="html", comment_format="html")
    return getattr(client, request.param)


@pytest.mark.parametrize("kind,native", [
    ("info", "info"), ("note", "grey"), ("tip", "idea"),
    ("success", "idea"), ("warning", "warning"), ("error", "warning"),
])
def test_existing_ltm_callouts_keep_identity_and_gain_native_jira_format(store, kind, native):
    source = f'<div class="callout callout-{kind}"><p><strong>내용</strong></p></div>'
    stored = store(source)
    # Legacy class must precede native class: older LTM takes the first callout-*.
    assert f'class="callout callout-{kind} jePanel_{native}"' in stored
    assert 'style="border:1px solid #' in stored
    assert "padding:.5em 1em .5em " in stored
    assert "<strong>내용</strong>" in stored
    assert store(stored) == stored
    assert store(sanitize_html(stored)) == stored
    # Keeping wiki installations and a format switch compatible is independent
    # of Jira's HTML renderer; macros must retain all six historical types.
    wiki = html_to_wiki(source)
    assert html_to_wiki(stored) == wiki
    assert f"{{{kind}}}" in wiki
    assert store(wiki_to_html(wiki)) == stored


@pytest.mark.parametrize("native,kind,border", [
    ("info", "info", "solid #9eb6d4"),
    ("warning", "warning", "solid #d4a19e"),
    ("idea", "tip", "solid #d4d39e"),
    ("grey", "note", "solid #b4b4b4"),
    ("dashed", "note", "dashed #b4b4b4"),
])
def test_native_panels_survive_read_edit_save(store, native, kind, border):
    # The info style is the production sample supplied by the user.
    source = (f'<div style="border:1px solid #9eb6d4;padding: .5em 1em .5em 2.5em;" '
              f'class="jePanel_{native}"><p>원문 &amp; 내용</p></div>')
    editing = sanitize_html(source)
    assert f'class="callout callout-{kind}"' in editing
    assert "jePanel_" not in editing
    assert "border:" not in editing   # LTM retains its theme instead of Jira inline CSS.
    stored = store(editing)
    assert f"jePanel_{native}" in stored
    assert f"border:1px {border}" in stored
    assert "원문 &amp; 내용" in stored
    assert store(sanitize_html(stored)) == stored
    assert ('data-jira-panel="dashed"' in editing) == (native == "dashed")


@pytest.mark.parametrize("native,kind", [
    ("note", "note"), ("tip", "tip"), ("success", "success"), ("error", "error"),
    ("unknown", "note"),
])
def test_old_vendor_aliases_and_unknown_panels_remain_readable(native, kind):
    rendered = sanitize_html(f'<div class="jePanel_{native}"><p>기존 내용</p></div>')
    assert rendered == f'<div class="callout callout-{kind}"><p>기존 내용</p></div>'


def test_dual_classes_do_not_let_native_alias_overwrite_ltm_identity():
    for classes in ("jePanel_idea callout-success callout",
                    "callout callout-success jePanel_idea"):
        source = f'<div class="{classes}"><p>완료</p></div>'
        assert sanitize_html(source) == '<div class="callout callout-success"><p>완료</p></div>'
        assert 'class="callout callout-success jePanel_idea"' in serialize_jira_html(source)


def test_native_output_does_not_expand_user_css_or_attribute_permissions():
    source = (
        '<div class="callout callout-info evil" data-jira-panel="dashed" onclick="alert(1)" '
        'style="padding:999px;border:999px solid red;position:fixed;'
        'background:url(https://untrusted.invalid/track);color:#123456">'
        '<p>安全</p><script>bad()</script></div>'
    )
    stored = serialize_jira_html(source)
    assert "color:#123456" in stored
    assert "border:1px solid #9eb6d4;padding:.5em 1em .5em 2.5em" in stored
    for bad in ("evil", "onclick", "alert", "999", "position", "url(", "script", "bad()",
                "data-jira-panel", "jePanel_dashed"):
        assert bad not in stored
    assert serialize_jira_html(stored) == stored


def test_native_type_edits_override_stale_ltm_class_and_dashed_marker():
    # JEditor may replace its own style class without removing LTM metadata.
    changed = '<div class="callout callout-info jePanel_warning"><p>수정</p></div>'
    assert sanitize_html(changed) == '<div class="callout callout-warning"><p>수정</p></div>'
    assert 'class="callout callout-warning jePanel_warning"' in serialize_jira_html(changed)
    solid = ('<div class="callout callout-note jePanel_grey" data-jira-panel="dashed">'
             '<p>실선으로 변경</p></div>')
    stored = serialize_jira_html(solid)
    assert "jePanel_grey" in stored and "border:1px solid #b4b4b4" in stored
    assert "dashed" not in stored
    assert "data-jira-panel" not in sanitize_html(solid)


def test_nested_callouts_and_other_content_survive_storage():
    source = (
        '<div class="callout callout-info"><p>앞</p>'
        '<div class="jePanel_dashed"><table><tr><td>표</td></tr></table>'
        '<ul><li><strong>항목</strong></li></ul></div>'
        '<p><a href="https://example.com">링크</a>'
        '<img src="/secure/attachment/1/image.png" alt="그림"></p></div>'
    )
    stored = serialize_jira_html(source)
    assert "jePanel_info" in stored and "jePanel_dashed" in stored
    assert "border:1px solid #dfe1e6" in stored
    assert "<ul><li><strong>항목</strong></li></ul>" in stored
    assert 'src="/secure/attachment/1/image.png"' in stored
    assert serialize_jira_html(sanitize_html(stored)) == stored


def test_plain_html_and_wiki_content_do_not_gain_panel_markup():
    plain = '<p>본문 <strong>강조</strong></p><pre><code>x &lt; y</code></pre>'
    assert serialize_jira_html(plain) == sanitize_html(plain)
    assert html_to_wiki(plain) == "본문 *강조*\n\n{code}\nx < y\n{code}"
