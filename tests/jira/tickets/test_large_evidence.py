"""Large evidence sizing must not allocate another copy of the entire source page."""

import hashlib
import json
import tracemalloc

import pytest

from app.jira.evidence_service import (
    EVIDENCE_PAGE_MAX_BYTES, _bounded_evidence_page, _json_size,
)


def test_large_evidence_size_check_has_bounded_extra_memory():
    page = {"items": [{"id": "1", "body": "한🙂" * (2 * 1024 * 1024)}]}
    tracemalloc.start()
    try:
        assert _json_size(page) > EVIDENCE_PAGE_MAX_BYTES
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert peak < 1024 * 1024


@pytest.mark.parametrize("value", [
    {"body": '한🙂 "\\\n' * 300},
    {"nested": [None, True, False, 1, 1.25, {"body": "x" * 8000}]},
    {"body": "x" * 8180},
    "a" * (EVIDENCE_PAGE_MAX_BYTES - 2),
    "a" * (EVIDENCE_PAGE_MAX_BYTES - 1),
    "\n" * (EVIDENCE_PAGE_MAX_BYTES // 2),
    ["", {}, [], {1: "numeric key", None: False}],
])
def test_evidence_size_check_is_exact_within_budget(value):
    expected = len(json.dumps(value, ensure_ascii=False, default=str).encode("utf-8"))
    actual = _json_size(value)
    assert (actual <= EVIDENCE_PAGE_MAX_BYTES) == (expected <= EVIDENCE_PAGE_MAX_BYTES)
    if expected <= EVIDENCE_PAGE_MAX_BYTES:
        assert actual == expected


def test_substantial_unicode_record_roundtrips_with_same_cursor_and_digest():
    row = {"id": "1", "body": '한🙂 "\\\n' * 16000}
    source = {"key": "DL-1", "kind": "comments", "items": [row], "total": 1,
              "maxResults": 100, "startAt": 0, "returned": 1,
              "hasMore": False, "nextStartAt": None}
    record = json.dumps(row, ensure_ascii=False, default=str)
    digest = hashlib.sha256(record.encode("utf-8")).hexdigest()
    offset, fragments = 0, []
    while True:
        page = _bounded_evidence_page(source, offset)
        assert len(json.dumps(page, ensure_ascii=False).encode("utf-8")) <= EVIDENCE_PAGE_MAX_BYTES
        item, = page["items"]
        assert item["recordOffset"] == offset
        assert item["recordSha256"] == digest
        fragments.append(item["recordJson"])
        if item["nextRecordOffset"] is None:
            assert not page["hasMore"] and page["nextCall"] is None
            break
        offset += len(item["recordJson"])
        assert item["nextRecordOffset"] == offset
        assert page["nextCall"]["arguments"]["record_offset"] == offset
    assert "".join(fragments) == record
    assert json.loads("".join(fragments)) == row
