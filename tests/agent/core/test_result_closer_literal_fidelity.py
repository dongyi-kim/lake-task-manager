"""Only real prose boilerplate may be removed; literal/prose no-ops stay exact."""
import pytest

from app.agent.workflow.agents import result_integrator as result

CLOSER = "추가 문의가 있으면 알려 주세요."


@pytest.mark.parametrize("newline", ["\n", "\r\n"])
@pytest.mark.parametrize("marker", ["```", "~~~", "````"])
@pytest.mark.parametrize("closed", [True, False])
def test_literal_closers_and_surrounding_bytes_are_preserved(newline, marker, closed):
    block = "   " + marker + "text" + newline + "  " + CLOSER + newline + newline + newline
    if closed:
        block += "   " + marker + newline
    assert result._drop_boilerplate_closers(block) == block


@pytest.mark.parametrize("value", [
    "  그대로  유지.  \r\n\r\n\r\n", "    " + CLOSER + "\n",
    "`" + CLOSER + "`\r\n", "- " + CLOSER + "\n", "DL-123 문의는 알려 주세요.\n",
])
def test_no_prose_closer_change_means_no_byte_change(value):
    assert result._drop_boilerplate_closers(value) == value


@pytest.mark.parametrize("newline", ["\n", "\r\n"])
def test_true_prose_closer_is_removed_beside_preserved_literal(newline):
    block = "   ~~~text" + newline + "  " + CLOSER + newline + "   ~~~" + newline
    value = block + newline + "확인  완료.  " + CLOSER + newline
    assert result._drop_boilerplate_closers(value) == block + newline + "확인  완료." + newline
