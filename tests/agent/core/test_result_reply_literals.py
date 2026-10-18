"""Result prose deduplication must leave literal Markdown regions intact."""
import pytest

from app.agent.workflow.agents import result_integrator as result


REPEAT = "검토 범위를 먼저 확인해야 합니다."
CODE = "print(message)"


@pytest.mark.parametrize("marker", ["```", "````", "~~~", "~~~~"])
@pytest.mark.parametrize("indent", ["", " ", "   "])
def test_fenced_lines_and_blank_lines_remain_verbatim(marker, indent):
    value = f"{indent}{marker}python\r\n{CODE}\r\n\r\n\r\n\r\n{CODE}\r\n{indent}{marker}\r\n"
    assert result._dedupe_sentences(value) == value


@pytest.mark.parametrize("value", [
    f"````\n{CODE}\n```\n{CODE}\n````",
    f"~~~\n{CODE}\n```\n{CODE}\n~~~",
    f"```\n{CODE}\n```not-a-close\n{CODE}\n```",
    f"```\n{CODE}\n{CODE}",
    f"~~~\n{CODE}\n{CODE}",
    f"    {CODE}\n    {CODE}",
    f"\t{CODE}\n\t{CODE}",
    f"- {REPEAT}\n- {REPEAT}",
    f"+ {REPEAT}\n+ {REPEAT}",
    f"| {REPEAT} |\n| {REPEAT} |",
    f"> {REPEAT}\n> {REPEAT}",
    f"`{CODE}`\n`{CODE}`",
    f"``a ` {CODE}``\n``a ` {CODE}``",
    "한국어 `inline_code` 구간 보존.\n한국어 `inline_code` 구간 보존.",
])
def test_literal_and_structured_regions_are_not_deduplicated(value):
    assert result._dedupe_sentences(value) == value


@pytest.mark.parametrize("marker", ["```", "~~~", "````"])
def test_plain_prose_still_dedupes_across_code(marker):
    block = f"{marker}\n{REPEAT}\n{REPEAT}\n{marker}"
    output = result._dedupe_sentences(f"{REPEAT}\n\n{block}\n\n{REPEAT}")
    assert block in output and output.count(REPEAT) == 3
    assert output.startswith(REPEAT)


def test_original_plain_sentence_contract_remains():
    output = result._dedupe_sentences(
        "DL-101 의 마감을 옮길 계획입니다. 아래 카드에서 확인 후 승인해 주세요.\n\n"
        "아래 카드에서 확인 후 승인해 주세요.")
    assert output.count("아래 카드에서 확인 후 승인해 주세요") == 1
    assert "마감을 옮길 계획입니다" in output
    assert result._dedupe_sentences("짧음.\n짧음.") == "짧음.\n짧음."


def test_invalid_backtick_info_does_not_open_multiline_fence():
    value = f"```language`invalid\n{REPEAT}\n{REPEAT}"
    assert result._dedupe_sentences(value).count(REPEAT) == 1


@pytest.mark.parametrize("value, expected", [
    ("", ""), ("  ", ""), ("\n\n\n", ""), ("a\n\n\n\nb", "a\n\nb"),
])
def test_plain_empty_and_blank_normalization(value, expected):
    assert result._dedupe_sentences(value) == expected


@pytest.mark.parametrize("marker", ["```", "~~~", "````", "~~~~"])
@pytest.mark.parametrize("closed", [True, False])
def test_style_protects_whole_fence_and_never_inserts_heading_inside(marker, closed):
    body = "검토합니다.\n\n\n검토합니다."
    value = marker + "text\n" + body + ("\n" + marker if closed else "")
    assert result._enforce_reply_style(value) == value


@pytest.mark.parametrize("marker", ["```", "~~~", "````", "~~~~"])
@pytest.mark.parametrize("indent", ["", " ", "   "])
def test_style_and_dedupe_preserve_indented_fence_with_crlf(marker, indent):
    value = f"{indent}{marker}text\r\n검토합니다.\r\n\r\n\r\n검토합니다.\r\n{indent}{marker}\r\n"
    assert result._enforce_reply_style(result._dedupe_sentences(value)) == value


@pytest.mark.parametrize("value", [
    "`검토합니다.`와 `검토합니다.` 코드 유지",
    "    검토합니다.\n\n\n\n    검토합니다.\n",
    "\t검토합니다.\n\n\t검토합니다.\n",
    "````text\n검토합니다.\n```\n검토합니다.\n````",
    "~~~text\n검토합니다.\n```\n검토합니다.\n~~~",
    "~~~text\n검토합니다.\n~~~not-a-close\n검토합니다.\n~~~",
])
def test_shared_literal_exclusion(value):
    assert result._enforce_reply_style(result._dedupe_sentences(value)) == value


def test_mixed_code_keeps_prose_style_but_avoids_automatic_block_splitting():
    value = "검토합니다.\n\n~~~text\n원문입니다.\n\n원문입니다.\n~~~\n\n다음 단계입니다."
    expected = "검토\n\n~~~text\n원문입니다.\n\n원문입니다.\n~~~\n\n다음 단계"
    assert result._enforce_reply_style(value) == expected


def test_plain_style_headings_and_structured_rows_unchanged():
    assert result._enforce_reply_style("검토합니다.\n\n다음 단계입니다.") == (
        "### 요약\n\n검토\n\n### 상세\n\n다음 단계")
    assert result._enforce_reply_style("### 상태\n\n검토합니다.\n\n다음 단계입니다.") == (
        "### 상태\n\n검토\n\n다음 단계")
    assert result._enforce_reply_style("- 검토합니다.\n- 검토합니다.") == "- 검토\n- 검토"


@pytest.mark.parametrize("marker", ["```", "~~~", "````", "~~~~"])
@pytest.mark.parametrize("closed", [True, False])
def test_ordinary_full_apply_preserves_literal_code(marker, closed):
    from langchain_core.messages import HumanMessage
    value = marker + "text\n검토합니다.\n검토합니다." + ("\n" + marker if closed else "")
    state = {"request_text": "원문을 보존해 정리", "messages": [HumanMessage(content="원문을 보존해 정리")]}
    patch = result.ResultIntegrator().apply_routed(state, {"text": value})
    assert patch["reply"] == value
    assert patch["messages"][0].content == value
