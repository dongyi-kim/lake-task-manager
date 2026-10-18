"""No-op entity rendering keeps original bytes; real badge transformations remain."""
import pytest

from app.agent.workflow.agents import result_integrator as result


@pytest.mark.parametrize("owner", ["_normalize_ticket_detail_sections", "_normalize_badge_repetitions"])
@pytest.mark.parametrize("newline", ["\n", "\r\n", "\r"])
@pytest.mark.parametrize("value", [
    "요약\n\n~~~text\n  source(  1  )  \n\n\n~~~\n",
    "   unchanged  .  \n\n", "    source\n    source  \n", "`  source  `\n",
])
def test_noop_normalizer_preserves_all_bytes(owner, newline, value):
    raw = value.replace("\n", newline)
    assert getattr(result, owner)(raw) == raw


@pytest.mark.parametrize("newline", ["\n", "\r\n"])
def test_genuine_ticket_detail_transformation_keeps_existing_behavior(newline):
    raw = newline.join(["### 현재 진행 중인 Task", "", "- DL-9047 title", "", "### 다음", "- DL-9048 keep"])
    expected = "### 현재 진행 중인 Task\n\n- {{ticket-detail:DL-9047}}\n\n### 다음\n- DL-9048 keep"
    assert result._normalize_ticket_detail_sections(raw) == expected


@pytest.mark.parametrize("newline", ["\n", "\r\n"])
def test_genuine_badge_deduplication_keeps_existing_behavior(newline):
    raw = '{{ticket-inline:DL-9095}} "repeated title" 진행 중' + newline + '[1] {{ticket-detail:DL-9095}} — 상태 Open'
    expected = '{{ticket-inline:DL-9095}} 진행 중\n[1] {{ticket-detail:DL-9095}}'
    assert result._normalize_badge_repetitions(raw) == expected
