"""Large raw sources remain complete without duplicate pagination payloads."""
from copy import deepcopy
from types import SimpleNamespace
import json
import hashlib

import pytest

from app.auth.base import UpstreamUnavailable
from app.jira.jql_scheduling import BoundedQueryScheduler
from app.tracking.source import IncompleteSource, TrackingSource
from app.tracking.storage import TrackingStore, encode


class Pages:
    supports_parallel = False

    def __init__(self, rows, kind):
        self.rows, self.kind, self.calls = rows, kind, []
        self.fail_at = None

    def get_json(self, path, params=None):
        offset = params["startAt"]
        self.calls.append(dict(params))
        if offset == self.fail_at:
            raise UpstreamUnavailable("interrupted")
        return {"startAt": offset, "total": len(self.rows), "maxResults": 1,
                self.kind: deepcopy(self.rows[offset:offset + 1])}


def source(tmp_path, provider):
    client = SimpleNamespace(provider=provider, _query_scheduler=BoundedQueryScheduler())
    metrics = {"restRequests": 0, "requestBytes": 0, "responseBytes": 0, "cacheHits": 0}
    return TrackingSource(client, TrackingStore(tmp_path / "tracking.sqlite3"), "test", metrics,
                          lambda: None, lambda: None)


def large_rows(kind):
    bodies = [f"unique-full-source-{index}:" + "가나다🙂" * (131072 if kind == "comments" and index == 0 else 32768)
              + f":tail-{index}" for index in range(2)]
    rows = ([{"key": f"P-{index + 1}", "fields": {"description": body}} for index, body in enumerate(bodies)]
            if kind == "issues" else [{"id": str(index), "body": body,
                                        "renderedBody": "<p>" + "rich🙂" * 65536 + f"{index}</p>"}
                                       for index, body in enumerate(bodies)])
    return rows, bodies


def collect(reader, kind, checkpoint, save):
    if kind == "issues":
        return reader.search("project=P", "description", checkpoint, save)
    rows, complete = reader.comments({"key": "P-1", "fields": {"updated": "revision"}}, checkpoint, save)
    assert complete
    return rows


@pytest.mark.parametrize("kind,pages_key", [("issues", "pages"), ("comments", "commentPages")])
def test_large_page_payload_is_persisted_once_without_truncation(tmp_path, kind, pages_key):
    rows, bodies = large_rows(kind)
    provider = Pages(rows, kind)
    reader = source(tmp_path, provider)
    checkpoint, saved = {}, []
    result = collect(reader, kind, checkpoint, lambda: saved.append(encode(checkpoint)))

    for row, original in zip(result, rows):
        assert row.get("fields", row) == original.get("fields", original)
        for field in ("body", "renderedBody"):
            if field in original:
                assert len(row[field]) == len(original[field])
                assert hashlib.sha256(row[field].encode()).digest() == hashlib.sha256(original[field].encode()).digest()
    serialized = encode(checkpoint)
    for body in bodies:
        assert serialized.count(json.dumps(body, ensure_ascii=False)) == 1
    assert all(kind not in page for page in checkpoint[pages_key])
    assert [page["rowCount"] for page in checkpoint[pages_key]] == [1, 1]
    assert [call["startAt"] for call in provider.calls] == [0, 1]
    assert all(call["maxResults"] == 100 for call in provider.calls)
    assert saved
    # The complete raw cache also carries one copy and produces identical output.
    cached_checkpoint = {}
    assert collect(reader, kind, cached_checkpoint, lambda: None) == result
    assert len(provider.calls) == 2


@pytest.mark.parametrize("kind,pages_key", [("issues", "pages"), ("comments", "commentPages")])
@pytest.mark.parametrize("legacy", [False, True])
def test_interrupted_large_source_resumes_all_rows_at_the_saved_offset(tmp_path, kind, pages_key, legacy):
    rows, bodies = large_rows(kind)
    provider = Pages(rows, kind)
    provider.fail_at = 1
    reader = source(tmp_path, provider)
    checkpoint = {}
    with pytest.raises(UpstreamUnavailable):
        collect(reader, kind, checkpoint, lambda: None)
    # Simulate a process restart, including the older full-page checkpoint format.
    checkpoint = json.loads(encode(checkpoint))
    if legacy:
        checkpoint[pages_key][0][kind] = deepcopy(checkpoint[kind])
        checkpoint[pages_key][0].pop("rowCount", None)
    provider.fail_at = None
    result = collect(reader, kind, checkpoint, lambda: None)

    assert [call["startAt"] for call in provider.calls] == [0, 1, 1]
    assert [row.get("key", row.get("id")) for row in result] == [row.get("key", row.get("id")) for row in rows]
    assert [row.get("fields", row).get("description", row.get("body")) for row in result] == bodies
    assert all(kind not in page for page in checkpoint[pages_key])


def test_incomplete_embedded_comment_prefix_does_not_skip_first_fallback_page(tmp_path):
    rows, _ = large_rows("comments")
    provider = Pages(rows, "comments")
    reader = source(tmp_path, provider)
    raw = {"key": "P-1", "fields": {"comment": {"startAt": 0, "total": 2, "comments": [rows[0]]}}}
    checkpoint = {}
    result, complete = reader.comments(raw, checkpoint, lambda: None)
    assert complete and result == rows
    assert [call["startAt"] for call in provider.calls] == [0, 1]
    assert all("comments" not in page for page in checkpoint["commentPages"])


@pytest.mark.parametrize("kind,pages_key,complete_key", [("issues", "pages", "complete"),
                                                       ("comments", "commentPages", "commentsComplete")])
def test_conflicting_legacy_flat_and_page_payloads_fail_closed(tmp_path, kind, pages_key, complete_key):
    rows, _ = large_rows(kind)
    reader = source(tmp_path, Pages(rows, kind))
    wrong = deepcopy(rows[0])
    if kind == "issues":
        wrong["fields"]["description"] = "stale truncated copy"
    else:
        wrong["body"] = "stale truncated copy"
    checkpoint = {kind: [wrong], pages_key: [{"startAt": 0, "total": 1, kind: [rows[0]]}],
                  "total": 1, complete_key: True}
    with pytest.raises(IncompleteSource):
        collect(reader, kind, checkpoint, lambda: None)


@pytest.mark.parametrize("kind,pages_key", [("issues", "pages"), ("comments", "commentPages")])
def test_legacy_pages_without_flat_rows_are_migrated_losslessly(tmp_path, kind, pages_key):
    rows, _ = large_rows(kind)
    provider = Pages(rows, kind)
    reader = source(tmp_path, provider)
    checkpoint = {pages_key: [{"startAt": 0, "total": 2, kind: [rows[0]]}], "total": 2}
    result = collect(reader, kind, checkpoint, lambda: None)[-1]
    assert result.get("fields", result) == rows[-1].get("fields", rows[-1])
    assert [call["startAt"] for call in provider.calls] == [1]
    assert all(kind not in page for page in checkpoint[pages_key])


@pytest.mark.parametrize("kind,pages_key", [("issues", "pages"), ("comments", "commentPages")])
def test_invalid_legacy_rows_raise_a_collection_error(tmp_path, kind, pages_key):
    reader = source(tmp_path, Pages([], kind))
    checkpoint = {kind: [None], pages_key: [{"startAt": 0, "total": 1, kind: [None]}]}
    with pytest.raises(IncompleteSource):
        collect(reader, kind, checkpoint, lambda: None)


def test_legacy_search_cache_migrates_before_returning_complete_rows(tmp_path):
    rows, _ = large_rows("issues")
    provider = Pages(rows, "issues")
    reader = source(tmp_path, provider)
    legacy = {"issues": rows, "pages": [{"startAt": 0, "total": 2, "issues": rows}],
              "total": 2, "complete": True}
    key = reader.key("search", {"jql": "project=P", "fields": "description", "generation": 0, "expand": True})
    reader.store.put_source(key, legacy, 30)
    checkpoint = {}
    assert collect(reader, "issues", checkpoint, lambda: None) == rows
    assert all("issues" not in page for page in checkpoint["pages"])
    assert provider.calls == []


@pytest.mark.parametrize("replacement", ["embedded", "cache"])
def test_complete_comment_replacement_discards_obsolete_partial_page_progress(tmp_path, replacement):
    rows, _ = large_rows("comments")
    provider = Pages(rows, "comments")
    reader = source(tmp_path, provider)
    raw = {"key": "P-1", "fields": {"updated": "revision"}}
    checkpoint = {"comments": [deepcopy(rows[0])], "commentsComplete": False,
                  "commentPages": [{"startAt": 0, "total": 2, "rowCount": 1}]}
    if replacement == "embedded":
        raw["fields"]["comment"] = {"startAt": 0, "total": 2, "comments": deepcopy(rows)}
    else:
        reader.store.put_source(reader.key("comments", ("P-1", "revision")), rows, 30)
    assert reader.comments(raw, checkpoint, lambda: None) == (rows, True)
    checkpoint = json.loads(encode(checkpoint))
    assert reader.comments(raw, checkpoint, lambda: None) == (rows, True)
    assert provider.calls == []
