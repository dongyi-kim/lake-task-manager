"""Semantic downgrade remains active for literals; unchanged bytes are not rendering work."""
import pytest

from app.agent.workflow import claim_grounding as grounding


@pytest.mark.parametrize("value", [
    "  원문  유지 .\r\n\r\n\r\n", "   ```python\r\n    print(  'x'  )\r\n\r\n\r\n   ```\r\n",
    "\t자료이며.  항목되며.\n\t값하며.\n", "  ~~~text\n문의가 있으면 알려 주세요.\n  ~~~\n",
])
def test_unchanged_claim_decision_preserves_exact_input(value):
    assert grounding._rewrite_sentences(value, lambda sentence: sentence) == value
    assert grounding.drop_unsupported_guarantees(value, {}) == value


@pytest.mark.parametrize("newline", ["\n", "\r\n"])
@pytest.mark.parametrize("wrapper", ["prose", "fence", "inline", "indent"])
@pytest.mark.parametrize("kind,claim,forbidden,state", [
    ("guarantee", "이 방식은 결과 일관성을 보장함.", "결과 일관성을 보장", {}),
    ("benefit", "QuartzStats 통계는 쿼리 최적화에 사용될 수 있음.", "최적화에 사용될 수 있음", {}),
    ("capability", "AtlasReader는 QuartzStats 통계를 소비할 수 있음.", "소비할 수 있음",
     {"topic_dossier": "AtlasReader의 QuartzStats 소비 지원 여부는 미확인"}),
])
def test_semantic_rejection_applies_inside_and_outside_literals(newline, wrapper, kind, claim, forbidden, state):
    body = "  " + claim + "  "
    if wrapper == "fence":
        value = "   ~~~text" + newline + body + newline + "   ~~~" + newline
    elif wrapper == "inline":
        value = "`" + body + "`" + newline
    elif wrapper == "indent":
        value = "    " + body + newline
    else:
        value = body + newline
    got = grounding.drop_unsupported_guarantees(value, state)
    assert forbidden not in got
    if wrapper == "fence":
        assert got.startswith("   ~~~text" + newline)
        assert got.endswith(newline + "   ~~~" + newline)


@pytest.mark.parametrize("newline", ["\n", "\r\n"])
def test_changed_claim_preserves_unrelated_literal_lines(newline):
    prefix = "   ~~~text" + newline + "    keep(  'a'  )" + newline + newline + newline
    suffix = newline + "    keep(  'b'  )" + newline + "   ~~~" + newline
    got = grounding.drop_unsupported_guarantees(prefix + "  이 방식은 일관성을 보장.  " + suffix, {})
    assert got.startswith(prefix)
    assert got.endswith(suffix)
    assert "일관성을 보장" not in got


def test_guarantee_removal_repairs_only_its_newly_dangling_korean_ending():
    raw = "그대로이며.\n\n형식이며, 일관성과 신선도를 보장.\n\n  그대로하며.  "
    assert grounding.drop_unsupported_guarantees(raw, {}) == "그대로이며.\n\n형식임.\n\n  그대로하며.  "


def test_supported_benefit_retains_all_spacing():
    state = {"evidence": [{"text": "AtlasReader는 QuartzStats를 소비하며 쿼리 최적화 성능을 개선함"}]}
    raw = "  AtlasReader는 QuartzStats 소비로 쿼리 최적화 성능을 개선할 수 있음.  \r\n"
    assert grounding.drop_unsupported_guarantees(raw, state) == raw
