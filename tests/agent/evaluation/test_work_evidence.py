"""Full-collection gates require every page and lossless verified record chunks."""
from hashlib import sha256
import json

from tools.codex_mcp_eval.work_evidence import evidence_collection, document_complete, source_observed


def call(data, name="get_evidence_page"):
    return {"name": name, "result": {"ok": True, "data": data}}


def test_comment_chunks_require_contiguous_offsets_and_matching_digest():
    record = {"id": "7", "body": "실제 적재하지 않았음" * 100}
    text = json.dumps(record, ensure_ascii=False)
    digest = sha256(text.encode()).hexdigest()
    def chunk(start, end):
        return call({"key": "DL-1", "kind": "comments", "startAt": 0, "total": 1,
            "sourceComplete": True, "partial": False, "hasMore": end < len(text),
            "items": [{"id": "7", "sourceIndex": 0, "recordOffset": start,
                       "recordJson": text[start:end], "nextRecordOffset": end if end < len(text) else None,
                       "totalCharacters": len(text), "recordSha256": digest}]})
    first, last = chunk(0, 100), chunk(100, len(text))
    assert not evidence_collection([first], "DL-1")["complete"]
    complete = evidence_collection([first, last], "DL-1")
    assert complete["complete"] and complete["records"] == [record]
    last["result"]["data"]["items"][0]["recordSha256"] = "0" * 64
    assert not evidence_collection([first, last], "DL-1")["complete"]


def test_tail_only_preview_and_failed_page_do_not_prove_collection():
    tail = call({"key": "DL-1", "kind": "comments", "startAt": 1, "total": 2,
        "sourceComplete": True, "hasMore": False, "items": [{"id": "2", "body": "end"}]})
    assert not evidence_collection([tail], "DL-1")["complete"]
    first = call({"key": "DL-1", "kind": "comments", "startAt": 0, "total": 2,
        "sourceComplete": True, "hasMore": True, "items": [{"id": "1", "body": "start"}]})
    assert evidence_collection([first, tail], "DL-1")["complete"]
    tail["result"]["ok"] = False
    assert not evidence_collection([first, tail], "DL-1")["complete"]


def test_document_coverage_pins_revision_and_requires_every_character():
    first = call({"id": "1", "version": {"number": 1}, "offset": 0, "content": "abc", "totalCharacters": 6}, "read_confluence_document")
    last = call({"id": "1", "version": {"number": 1}, "offset": 3, "content": "def", "totalCharacters": 6}, "read_confluence_document")
    assert not document_complete([last], "1", version=1)
    assert document_complete([first, last], "1", version=1)
    last["result"]["data"]["version"]["number"] = 2
    assert not document_complete([first, last], "1", version=1)


def test_source_identity_and_url_must_belong_to_same_observed_record():
    calls = [call({"issues": [{"key": "DL-1", "url": "https://jira.test/browse/DL-1"},
                              {"key": "DL-2", "url": "https://jira.test/browse/DL-2"}]}, "search_issues")]
    source = {"kind": "ticket", "id": "DL-1", "url": "https://jira.test/browse/DL-1"}
    assert source_observed(calls, source)
    assert not source_observed(calls, {**source, "url": "https://jira.test/browse/DL-2"})
    assert not source_observed(calls, {**source, "url": ""})
