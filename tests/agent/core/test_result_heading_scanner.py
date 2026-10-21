"""Structural coverage view reuses block/inline scanner state and raw line bounds."""
import pytest

from app.agent.workflow.agents import result_integrator as result


@pytest.mark.parametrize("newline", ["\n", "\r\n"])
@pytest.mark.parametrize("heading", [
    "### `원문`", "**`원문`**", "### 원문 ``code``", " ### 원문",
    "  ### 원문", "   ### 원문", "### \\`escaped", "### `unmatched",
])
def test_structural_view_exposes_heading_without_rewriting_raw_lines(newline, heading):
    raw = heading + newline * 2 + "  ~~~text" + newline + "  keep  " + newline + "  ~~~"
    normal = result._reply_literal_spans(raw)
    structural = result._reply_literal_spans(raw, structural_headings=True)
    assert "".join(part for _, part in normal) == raw
    assert "".join(part for _, part in structural) == raw
    assert structural[0] == (False, heading + newline * 2)
    assert structural[-1][0] is True
    if "`" in heading:
        assert normal[0][0] is True


@pytest.mark.parametrize("newline", ["\n", "\r\n"])
@pytest.mark.parametrize("raw", [
    "  ~~~~text\n### `fake`\n### 요청 출처 조사 한계\n~~~\n### fake\n  ~~~~~",
    "  ~~~~text\n### `fake`\n### 요청 출처 조사 한계",
    "````text\n### `fake`\n### 요청 출처 조사 한계\n````",
    "    ### `fake`\n    ### 요청 출처 조사 한계",
    "\t### `fake`\n\t### 요청 출처 조사 한계",
    "prefix ``open\n### `fake`\n### 요청 출처 조사 한계\nclose``",
])
def test_block_or_continued_inline_fake_heading_stays_opaque(newline, raw):
    raw = raw.replace("\n", newline)
    assert result._reply_literal_spans(raw, structural_headings=True) == [(True, raw)]


@pytest.mark.parametrize("newline", ["\n", "\r\n"])
@pytest.mark.parametrize("prefix", ["prefix ``unmatched", "prefix \\`escaped"])
def test_unmatched_or_escaped_opening_does_not_hide_next_real_heading(newline, prefix):
    heading = "  ### `원문`"
    raw = prefix + newline + heading + newline * 2 + "~~~text" + newline + "keep"
    spans = result._reply_literal_spans(raw, structural_headings=True)
    assert "".join(part for _, part in spans) == raw
    assert (False, heading + newline * 2) in spans


@pytest.mark.parametrize("newline", ["\n", "\r\n"])
def test_heading_lookalike_inside_multiline_inline_remains_opaque_until_close(newline):
    inline = "prefix ``open\n### `fake`\n**`fake`**\nclose``".replace("\n", newline)
    heading = "   ### `real`"
    raw = inline + newline + heading + newline * 2 + "  ~~~text" + newline + "keep"
    spans = result._reply_literal_spans(raw, structural_headings=True)
    assert spans[0] == (True, inline + newline)
    assert (False, heading + newline * 2) in spans
    assert "".join(part for _, part in spans) == raw
