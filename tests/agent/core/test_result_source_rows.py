"""Pseudo-source cleanup preserves literal Markdown source bytes."""
import pytest

from app.agent.workflow.agents import result_integrator as result


@pytest.mark.parametrize("marker", ["```", "~~~", "````", "~~~~"])
@pytest.mark.parametrize("indent", ["", " ", "   "])
@pytest.mark.parametrize("closed", [True, False])
@pytest.mark.parametrize("newline", ["\n", "\r\n"])
def test_source_rows_preserves_fence_bytes(marker, indent, closed, newline):
    block = newline.join([indent + marker + "text", "검토합니다.", "print(message)",
                          "", "", "[1] 사용자 입력", "- [1-a] 첨부 발췌", "### 근거"])
    if closed:
        block += newline + indent + marker + newline
    assert result._drop_direct_input_source_rows(block) == block


@pytest.mark.parametrize("block", [
    "```text\n### 근거\n\n### 참조\n```",
    "~~~text\n[1] [사용자 입력](verified URL)\n- [1-a] 원문\n~~~",
    "````text\n```\n[1] 사용자 입력\n- 원문\n````",
    "~~~text\n~~~not-a-close\n[1] 사용자 입력\n~~~",
    "    [1] 사용자 입력\n    - [1-a] 원문\n\n\n",
    "\t[1] 사용자 입력\n\t- [1-a] 원문\n",
    "[1] `사용자 입력`\n",
])
def test_source_like_literal_rows_are_opaque(block):
    assert result._drop_direct_input_source_rows(block) == block


@pytest.mark.parametrize("pseudo", [
    "[1] 사용자 입력", "[1] 첨부 문서 발췌", "[1] [사용자 입력](verified URL)",
    "[1] [followup-notes.docx](verified URL)",
])
def test_same_source_rows_outside_literal_are_removed(pseudo):
    text = ("### 결론\n\n검토 필요\n\n### 근거\n\n" + pseudo
            + "\n- [1-a] 표본 확인\n\n[2] [공식 문서](https://example.com/spec)")
    got = result._drop_direct_input_source_rows(text)
    assert pseudo not in got and "표본 확인" not in got
    assert got == "### 결론\n\n검토 필요\n\n### 근거\n\n[2] [공식 문서](https://example.com/spec)"


@pytest.mark.parametrize("heading", ["근거", "참조"])
def test_empty_pseudo_source_heading_is_removed_only_outside_literal(heading):
    plain = "### 결론\n\n검토 필요\n\n### " + heading + "\n\n[1] 사용자 입력\n- 원문"
    assert result._drop_direct_input_source_rows(plain) == "### 결론\n\n검토 필요"
    block = "~~~text\n### " + heading + "\n\n[1] 사용자 입력\n- 원문\n~~~"
    assert result._drop_direct_input_source_rows(block) == block


def test_literal_interrupts_source_children_without_suppressing_later_prose():
    block = "~~~text\n[1] 사용자 입력\n- [1-a] 원문\n\n\n~~~\n"
    text = "[1] 사용자 입력\n- 지워질 행\n" + block + "- 유지할 행\n[2] 첨부 문서 발췌\n- 지워질 행"
    assert result._drop_direct_input_source_rows(text) == block + "- 유지할 행"


def test_heading_before_literal_still_has_content():
    block = "~~~text\n[1] 사용자 입력\n~~~"
    assert result._drop_direct_input_source_rows("### 근거\n\n" + block) == "### 근거\n\n" + block


def test_ordinary_prose_blank_line_cleanup_is_unchanged():
    assert result._drop_direct_input_source_rows("  검토 필요\n\n\n추가 확인\n ") == "검토 필요\n\n추가 확인"


@pytest.mark.parametrize("literal_child", ["- [1-a] `field_name`", "    - [1-a] literal sample"])
def test_literal_child_does_not_release_remaining_pseudo_source_children(literal_child):
    value = "[1] 사용자 입력\n" + literal_child + "\n- [1-b] 이 행은 계속 삭제 대상\n[2] https://example.com/spec"
    assert result._drop_direct_input_source_rows(value) == literal_child + "\n[2] https://example.com/spec"
