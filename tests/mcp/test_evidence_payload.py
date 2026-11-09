"""Evidence transport remains lossless when records exceed a model-sized page."""

import copy
import hashlib
import json
from types import SimpleNamespace

import pytest
import anyio
from jsonschema import Draft202012Validator
from mcp import Client

from app.jira.evidence_service import get_evidence_page


def client_for(kind, rows, source_cap=100):
    cached, calls = {}, []

    def get_or_set(key, ttl, producer):
        if key not in cached:
            cached[key] = producer()
        return cached[key], False

    def get_json(path, params):
        calls.append(copy.deepcopy(params))
        if kind == "comments":
            start, size = params["startAt"], min(source_cap, params["maxResults"])
            return {"startAt": start, "total": len(rows),
                    "comments": copy.deepcopy(rows[start:start + size])}
        return {"changelog": {"startAt": 0, "total": len(rows), "histories": copy.deepcopy(rows)}}

    return SimpleNamespace(env="mock", s=SimpleNamespace(cache_ttl_seconds=60),
        cache=SimpleNamespace(get_or_set_strict=get_or_set),
        provider=SimpleNamespace(get_json=get_json), _looks_anonymous=lambda data: False,
        source_calls=calls)


def assert_bounded(page):
    assert len(json.dumps(page, ensure_ascii=False).encode("utf-8")) <= 8192
    assert page["complete"] and page["sourceComplete"] and not page["partial"]


def test_evidence_page_stops_between_records_without_skipping_or_truncating():
    rows = [{"id": str(index), "body": "본문 " * 150} for index in range(20)]
    client = client_for("comments", rows)
    start, collected, pages = 0, [], []
    while True:
        page = get_evidence_page(client, "DL-1", start_at=start)
        assert_bounded(page)
        collected.extend(page["items"])
        pages.append(page)
        if not page["hasMore"]:
            break
        assert page["nextStartAt"] == start + page["returned"] > start
        start = page["nextStartAt"]
    assert len(pages) > 1
    assert [(row["id"], row["body"]) for row in collected] == [(row["id"], row["body"]) for row in rows]
    assert all("recordJson" not in row for row in collected)


@pytest.mark.parametrize("source_cap", [100, 17])
def test_adaptive_comment_pages_reuse_aligned_source_and_respect_server_clamps(source_cap):
    rows = [{"id": str(index), "body": "세부 근거 " * 100} for index in range(205)]
    client = client_for("comments", rows, source_cap=source_cap)
    start, collected = 0, []
    while True:
        page = get_evidence_page(client, "DL-1", start_at=start)
        assert_bounded(page)
        collected.extend(row["id"] for row in page["items"])
        if not page["hasMore"]:
            break
        assert page["nextStartAt"] > start
        start = page["nextStartAt"]
    assert collected == [row["id"] for row in rows]
    if source_cap == 100:
        assert len(client.source_calls) == 3
        assert [call["startAt"] for call in client.source_calls] == [0, 100, 200]


def test_205_real_jira_comments_need_only_three_upstream_pages(build_env):
    env = build_env()
    key = env.client.create_child(None, "Task", "bounded evidence")["key"]
    path = f"/rest/api/2/issue/{key}/comment"
    for index in range(205):
        env.provider.post_json(path, json_body={"body": f"원본 {index}: " + "상세 근거 " * 80})
    before, start, collected, pages = len(env.calls), 0, [], 0
    while True:
        page = get_evidence_page(env.client, key, start_at=start)
        assert_bounded(page)
        collected.extend(page["items"])
        pages += 1
        if not page["hasMore"]:
            break
        assert page["nextStartAt"] > start
        start = page["nextStartAt"]
    reads = [call for call in env.calls[before:] if call[0] == "get" and call[1] == path]
    assert len(collected) == len({row["id"] for row in collected}) == 205
    assert pages > 3 and len(reads) == 3
    assert [call[2]["params"]["startAt"] for call in reads] == [0, 100, 200]


@pytest.mark.parametrize("kind", ["comments", "history"])
def test_large_unicode_record_roundtrips_all_json_chunks(kind):
    text = '한글🙂 e\u0301 "인용" \\ 경로\n' * 3000
    record = {"id": "17", "author": {"name": "alice", "displayName": "작성자"},
              "created": "2026-11-04T09:00:00+09:00"}
    if kind == "comments":
        record.update(body=text, renderedBody="<p>" + text + "</p>")
    else:
        record["items"] = [{"field": "description", "fromString": text, "toString": text[::-1]}]
    client = client_for(kind, [record])
    offset, fragments, digests = 0, [], set()
    while True:
        page = get_evidence_page(client, "DL-1", kind=kind, max_results=1, record_offset=offset)
        assert_bounded(page)
        assert page["total"] == page["returned"] == 1
        item, = page["items"]
        assert item["id"] == "17" and item["sourceIndex"] == 0 and item["recordOffset"] == offset
        assert item["recordJson"]
        fragments.append(item["recordJson"])
        digests.add(item["recordSha256"])
        if item["nextRecordOffset"] is None:
            assert not page["hasMore"] and page["nextStartAt"] is None
            break
        assert page["hasMore"] and page["nextStartAt"] == 0
        assert item["nextRecordOffset"] == offset + len(item["recordJson"]) > offset
        offset = item["nextRecordOffset"]
    combined = "".join(fragments)
    assert len(fragments) > 1 and len(combined) == item["totalCharacters"]
    assert digests == {hashlib.sha256(combined.encode("utf-8")).hexdigest()}
    assert json.loads(combined) == {**record, "authorId": "alice", "authorDisplayName": "작성자"}
    assert len(client.source_calls) == 1


def test_oversized_middle_record_keeps_collection_cursor_until_its_final_chunk():
    rows = [{"id": "1", "body": "before"}, {"id": "2", "body": "large " * 6000},
            {"id": "3", "body": "after"}]
    client = client_for("comments", rows)
    first = get_evidence_page(client, "DL-1")
    assert_bounded(first)
    assert [row["id"] for row in first["items"]] == ["1"] and first["nextStartAt"] == 1
    page = get_evidence_page(client, "DL-1", start_at=first["nextStartAt"])
    fragments = []
    while True:
        assert_bounded(page)
        item, = page["items"]
        fragments.append(item["recordJson"])
        if item["nextRecordOffset"] is None:
            break
        assert page["nextStartAt"] == 1
        page = get_evidence_page(client, "DL-1", start_at=1, max_results=1,
                                 record_offset=item["nextRecordOffset"])
    assert json.loads("".join(fragments))["body"] == rows[1]["body"]
    assert page["hasMore"] and page["nextStartAt"] == 2
    final = get_evidence_page(client, "DL-1", start_at=page["nextStartAt"])
    assert_bounded(final)
    assert final["items"][0]["id"] == "3" and not final["hasMore"]
    assert len(client.source_calls) == 1


def test_record_digest_exposes_edits_between_continuation_reads():
    rows = [{"id": "1", "body": "old " * 6000}]
    client = client_for("comments", rows)
    # Simulate cache invalidation after a successful comment edit.
    client.cache.get_or_set_strict = lambda key, ttl, producer: (producer(), False)
    first = get_evidence_page(client, "DL-1", max_results=1)["items"][0]
    rows[0]["body"] = "new " * 6000
    second = get_evidence_page(client, "DL-1", max_results=1,
                              record_offset=first["nextRecordOffset"])["items"][0]
    assert first["id"] == second["id"] and first["totalCharacters"] == second["totalCharacters"]
    assert first["recordSha256"] != second["recordSha256"]


@pytest.mark.parametrize("arguments", [{"record_offset": -1}, {"record_offset": True},
    {"max_results": 1, "record_offset": 1000000}])
def test_record_continuation_rejects_invalid_ranges(arguments):
    with pytest.raises(ValueError):
        get_evidence_page(client_for("comments", [{"id": "1", "body": "hello"}]), "DL-1", **arguments)


@pytest.mark.parametrize('kind,source_cap', [('comments', 100), ('comments', 17), ('history', 100)])
@pytest.mark.parametrize('batch_size', [3, 100])
def test_ready_continuation_roundtrips_middle_chunk_and_keeps_batch_size(kind, source_cap, batch_size):
    text = '긴 원문🙂 "인용" \\ 줄\n' * 2000
    rows = [{'id': '0', 'body': 'before'}, {'id': '1', 'body': text},
            *[{'id': str(index), 'body': 'after'} for index in range(2, 32)]]
    if kind == 'history':
        rows = [{'id': row['id'], 'items': [{'field': 'description', 'toString': row['body']}]} for row in rows]
    client = client_for(kind, rows, source_cap=source_cap)
    arguments = {'key': 'DL-1', 'kind': kind, 'max_results': batch_size}
    collected, fragments, previous, resumed_batches = [], [], None, []
    for _ in range(100):
        page = get_evidence_page(client, **arguments)
        assert_bounded(page)
        position = (page['startAt'], arguments.get('record_offset', 0))
        assert previous is None or position > previous
        previous = position
        for row in page['items']:
            if 'recordJson' in row:
                fragments.append(row['recordJson'])
                if row['nextRecordOffset'] is None:
                    decoded = json.loads(''.join(fragments))
                    assert hashlib.sha256(''.join(fragments).encode()).hexdigest() == row['recordSha256']
                    collected.append(decoded)
            else:
                collected.append(row)
        if page['startAt'] >= 2:
            resumed_batches.append(page['returned'])
        continuation = page['nextCall']
        if continuation is None:
            assert page['hasMore'] is False
            break
        assert continuation['name'] == 'get_evidence_page'
        arguments = continuation['arguments']
        assert arguments['key'] == 'DL-1' and arguments['kind'] == kind and arguments['max_results'] == batch_size
    else:
        pytest.fail('continuation did not terminate')
    assert [row['id'] for row in collected] == [row['id'] for row in rows]
    assert fragments and any(size > 1 for size in resumed_batches)
    if kind == 'comments':
        assert collected[1]['body'] == text
    else:
        assert collected[1]['items'][0]['toString'] == text
    assert len(client.source_calls) == (2 if kind == 'comments' and source_cap == 17 else 1)


def test_sdk_copies_ready_arguments_without_repeating_or_refetching_comments(build_env):
    env = build_env()
    key = env.client.create_child(None, 'Task', 'continuation contract')['key']
    path = f'/rest/api/2/issue/{key}/comment'
    bodies = ['before', '긴 원문🙂 "인용" 줄\n' * 1500, 'after 1', 'after 2']
    for body in bodies:
        env.provider.post_json(path, json_body={'body': body})
    # The mock serves newest first. Pin that source order instead of depending on
    # all four writes landing within the same wall-clock second.
    for index, comment in enumerate(env.store.get_issue(key)['comments']):
        comment['tcreated'] = comment['tupdated'] = f'12:00:{59 - index:02d}'
    env.calls.clear()
    async def scenario():
        async with Client(env.runtime.server) as client:
            tool = next(row for row in (await client.list_tools()).tools if row.name == 'get_evidence_page')
            args, positions, records, fragments = {'key': key}, set(), [], []
            for _ in range(80):
                response = await client.call_tool('get_evidence_page', args)
                assert not response.is_error, response.structured_content
                Draft202012Validator(tool.output_schema).validate(response.structured_content)
                page = response.structured_content['data']
                assert_bounded(page)
                position = (page['startAt'], args.get('record_offset', 0))
                assert position not in positions
                positions.add(position)
                for row in page['items']:
                    if 'recordJson' in row:
                        fragments.append(row['recordJson'])
                        if row['nextRecordOffset'] is None:
                            combined = ''.join(fragments)
                            assert hashlib.sha256(combined.encode()).hexdigest() == row['recordSha256']
                            records.append(json.loads(combined))
                    else:
                        records.append(row)
                if page['nextCall'] is None:
                    assert not page['hasMore'] and page['complete']
                    break
                args = page['nextCall']['arguments']
                assert args['max_results'] == 100
            else:
                pytest.fail('SDK continuation did not finish')
            assert [row['body'] for row in records] == bodies
            assert len([call for call in env.calls if call[0] == 'get' and call[1] == path]) == 1
    anyio.run(scenario)
