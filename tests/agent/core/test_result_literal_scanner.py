"""Bounded literal-line preservation; not a complete Markdown renderer contract."""
import itertools

import pytest

from app.agent.workflow.agents import result_integrator as result


@pytest.mark.parametrize("delimiter", ["`", "``"])
@pytest.mark.parametrize("newline", ["\n", "\r\n"])
def test_multiline_inline_literal_bytes_survive_each_owner(delimiter, newline):
    value = newline.join([delimiter + "source sample", "[1] 사용자 입력", "[2] 메모", "sample" + delimiter])
    for function in (result._drop_direct_input_source_rows, result._dedupe_sentences,
                     result._enforce_reply_style):
        assert function(value) == value


@pytest.mark.parametrize("slashes, preserved", [(1, False), (2, True), (3, False), (4, True)])
def test_escaped_openers_extend_only_with_even_backslash_parity(slashes, preserved):
    value = "prefix " + "\\" * slashes + "`open\n[1] 사용자 입력\nclose`"
    got = result._drop_direct_input_source_rows(value)
    assert ("[1] 사용자 입력" in got) is preserved
    assert got.startswith("prefix " + "\\" * slashes + "`open\n")
    assert got.endswith("close`")


@pytest.mark.parametrize("value", [
    "open `\n[1] 사용자 입력\nplain tail",
    "open ``\n[1] 사용자 입력\nclose`",
    "open `\n[1] 사용자 입력\nclose``",
    "prefix \\`open\n[1] 사용자 입력\nclose`",
])
def test_unmatched_or_escaped_delimiters_do_not_hide_source_rows(value):
    got = result._drop_direct_input_source_rows(value)
    assert "[1] 사용자 입력" not in got
    assert value.splitlines()[0] in got and value.splitlines()[-1] in got


@pytest.mark.parametrize("delimiter", ["`", "``", "```"])
def test_exact_inline_run_length_and_backslash_inside_code(delimiter):
    wrong = "`" * (len(delimiter) + 1)
    value = "prefix " + delimiter + "open\n[1] 사용자 입력\nwrong " + wrong + "\n- 원문\nclose\\" + delimiter
    assert result._drop_direct_input_source_rows(value) == value
    text = value + "\n[2] 메모\n- 제거\nplain tail"
    assert result._drop_direct_input_source_rows(text) == value + "\nplain tail"


@pytest.mark.parametrize("barrier", ["\n", "~~~text\ncode\n~~~\n", "    code\n", "\tcode\n"])
def test_inline_matching_does_not_cross_blank_fence_or_indented_blocks(barrier):
    value = "prefix `open\n[1] 사용자 입력\n" + barrier + "close`"
    assert "[1] 사용자 입력" not in result._drop_direct_input_source_rows(value)


def test_unmatched_run_before_a_different_matched_run_does_not_hide_earlier_prose():
    value = "first `unmatched\n[1] 사용자 입력\nsecond ``open\n[2] 메모\nclose``"
    got = result._drop_direct_input_source_rows(value)
    assert "[1] 사용자 입력" not in got and "[2] 메모" in got


def test_scanner_roundtrips_all_raw_bytes_and_keeps_multiple_pair_boundaries():
    value = "prefix `one\r\n검토합니다.\r\nend`\r\nplain\r\nopen ``two\r\n[1] 사용자 입력\r\nend``\r\n"
    spans = result._reply_literal_spans(value)
    assert "".join(part for _, part in spans) == value
    assert [part for literal, part in spans if not literal] == ["plain\r\n"]


@pytest.mark.parametrize("space", ["", " ", "\t", "\n", "\n \n"])
@pytest.mark.parametrize("ending", ["", "\n", "\n\n### 결론\n검토 필요", "\n\n### 근거\n원문"])
def test_empty_heading_whitespace_contract(space, ending):
    value = "###" + space + "근거" + ending
    expected = "### 결론\n검토 필요" if "결론" in ending else "### 근거\n원문" if "원문" in ending else ""
    assert result._drop_direct_input_source_rows(value) == expected


def test_nonliteral_empty_heading_differential_matches_previous_semantics():
    # Compare explicit old semantics only; no cache helper dependency in the proposed test.
    import re

    choices = ["### 근거", "### 참조", "###", "근거", "", " \t", "### 결론", "plain"]
    for lines in itertools.product(choices, repeat=4):
        value = "\n".join(lines)
        old = re.sub(r"(?ms)^###\s*(?:근거|참조)\s*$\s*(?=^###\s|\Z)", "", value)
        expected = re.sub(r"\n{3,}", "\n\n", old).strip()
        assert result._drop_direct_input_source_rows(value) == expected
