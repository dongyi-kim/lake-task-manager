"""Pruning owns prose cleanup, not literal rows or their raw line boundaries."""
import itertools
import re

import pytest

from app.agent.workflow.agents import result_integrator as result


def legacy_prose_cleanup(text):
    """Frozen pre-fix behavior for no-literal differential controls."""
    out, removed = [], False
    for line in text.split("\n"):
        stripped = line.strip()
        cells = [cell.strip() for cell in stripped.strip("|").split("|")]
        if stripped.startswith("|") and (cells[0].startswith("확인된 기록 없음")
                or stripped.count("확인된 기록 없음") >= 2):
            removed = True
            continue
        if re.match(r"-?\s*\[\d+\]\s*확인된 기록 없음", stripped):
            continue
        out.append(line)
    text = "\n".join(out)
    if removed:
        text = re.sub(r"(?:^|\n)\|[^\n]*\|\n\|[\s:|-]+\|(?=\n(?!\|)|$)", "", text)
    return re.sub(r"(?:^|\n)(#{2,4}\s+[^\n]+|\*\*[^\n*]+\*\*)\n+"
                  r"(?=(#{2,4}\s|\*\*(?:근거|참조)\*\*|[^\n]*(?:궁금하면 말씀|말씀 주세요)|$))",
                  "\n", text)


@pytest.mark.parametrize("newline", ["\n", "\r\n"])
@pytest.mark.parametrize("raw", [
    "prefix `open\n### code`",
    "prefix ``open\n### `first` and ``second``",
    "~~~text\n### empty\n### next\n[3] 확인된 기록 없음\n| 확인된 기록 없음 | empty |\n~~~",
    "~~~text\n### empty\n### next\n[3] 확인된 기록 없음",
    "    ### empty\n    ### next\n    [3] 확인된 기록 없음",
    "\t### empty\n\t[3] 확인된 기록 없음",
])
def test_literal_rows_remain_exact_next_to_prose_cleanup(newline, raw):
    raw = raw.replace("\n", newline)
    suffix = newline * 2 + "### empty\n\n### next\nkeep".replace("\n", newline)
    got = result._prune_empty_rows(raw + suffix)
    assert got.startswith(raw)
    if raw.startswith("~~~") and not raw.endswith("~~~"):
        assert got == raw + suffix  # An unclosed fence also owns the suffix.
    else:
        expected = legacy_prose_cleanup(suffix)
        assert ("### empty" in got[len(raw):]) == ("### empty" in expected)
        assert "### next" in got[len(raw):]
    assert result._prune_empty_rows(raw) == raw


@pytest.mark.parametrize("newline", ["\n", "\r\n"])
@pytest.mark.parametrize("heading", ["### retained", "### `retained`", "**`retained`**"])
@pytest.mark.parametrize("literal", ["~~~text\nkeep\n~~~", "~~~text\nkeep", "    keep", "`keep`"])
def test_literal_content_is_not_document_eof_for_its_heading(newline, heading, literal):
    raw = (heading + "\n\n" + literal).replace("\n", newline)
    assert result._prune_empty_rows(raw) == raw


@pytest.mark.parametrize("newline", ["\n", "\r\n"])
def test_no_literal_cleanup_matches_existing_heading_table_reference_policy(newline):
    choices = ["", "### empty", "**근거**", "[3] 확인된 기록 없음",
               "| 확인된 기록 없음 | empty |", "keep", "말씀 주세요"]
    for rows in itertools.product(choices, repeat=3):
        raw = newline.join(rows)
        assert result._prune_empty_rows(raw) == legacy_prose_cleanup(raw)
    for body in ["| 확인된 기록 없음 | empty |", "| keep | 확인된 기록 없음 |",
                 "| keep | 확인된 기록 없음 | 확인된 기록 없음 |", "| keep | actual |"]:
        raw = ("### table\n| key | value |\n| --- | --- |\n" + body + "\n\n### next\nkeep").replace("\n", newline)
        assert result._prune_empty_rows(raw) == legacy_prose_cleanup(raw)


@pytest.mark.parametrize("newline", ["\n", "\r\n"])
def test_literal_preservation_does_not_disable_semantic_downgrade(newline):
    raw = ("~~~text\n### literal heading\n이 방식은 결과 일관성을 보장함.\n~~~").replace("\n", newline)
    got = result._drop_unsupported_guarantees(result._prune_empty_rows(raw), {})
    assert "결과 일관성을 보장" not in got
    assert "### literal heading" in got and got.startswith("~~~text" + newline)
