"""Lossless evidence coverage from successful responses, not requested arguments."""
from hashlib import sha256
import json


def source_observed(calls, source):
    identity, url = source.get("id"), source.get("url")
    if not identity or not url:
        return False
    def matches(value):
        if isinstance(value, dict):
            if (identity in {str(value.get(field)) for field in ("key", "id", "name")}
                    and url == value.get("url")):
                return True
            return any(matches(item) for item in value.values())
        return isinstance(value, list) and any(matches(item) for item in value)
    return any(not c.get("isError") and (c.get("result") or {}).get("ok") is True
               and matches(c["result"].get("data")) for c in calls)


def evidence_collection(calls, key, kind="comments"):
    records, chunks, total, started, ended, valid = {}, {}, None, False, False, True
    for call in calls:
        result = call.get("result") or {}
        if call.get("name") != "get_evidence_page" or call.get("isError") or result.get("ok") is not True:
            continue
        page = result.get("data") or {}
        if page.get("key") != key or page.get("kind") != kind:
            continue
        start, count, items = page.get("startAt"), page.get("total"), page.get("items") or []
        first_offset = items[0].get("recordOffset", 0) if items else 0
        if start == 0 and first_offset == 0:
            records, chunks, started, ended, valid = {}, {}, True, False, True
        if type(start) is not int or type(count) is not int or count < 0:
            valid = False
            continue
        if total is not None and count != total and start != 0:
            valid = False
        total = count
        valid &= page.get("sourceComplete") is True and not page.get("partial")
        ended |= page.get("hasMore") is False
        for index, item in enumerate(items, start):
            if "recordJson" not in item:
                records[index] = item
                continue
            source = item.get("sourceIndex")
            records.pop(source, None)
            digest = item.get("recordSha256")
            state = chunks.setdefault((source, digest), {})
            state[item["recordOffset"]] = item["recordJson"]
            text, offset = "", 0
            while offset in state and state[offset]:
                text += state[offset]
                offset = len(text)
            if (len(text) == item.get("totalCharacters") and item.get("nextRecordOffset") is None
                    and sha256(text.encode()).hexdigest() == digest):
                try:
                    restored = json.loads(text)
                    if restored.get("id") == item.get("id"):
                        records[source] = restored
                except (ValueError, AttributeError):
                    valid = False
    complete = (valid and started and ended and total is not None and set(records) == set(range(total))
                and all(row.get("id") for row in records.values())
                and len({str(row.get("id")) for row in records.values()}) == total)
    return {"complete": complete, "records": [records[i] for i in sorted(records)], "total": total}


def document_complete(calls, identifier, *, version=None):
    groups = {}
    for call in calls:
        result = call.get("result") or {}
        if call.get("name") != "read_confluence_document" or call.get("isError") or result.get("ok") is not True:
            continue
        data = result.get("data") or {}
        revision = (data.get("version") or {}).get("number")
        if str(data.get("id")) != str(identifier) or revision is None or version is not None and version != revision:
            continue
        total, offset, text = data.get("totalCharacters"), data.get("offset"), data.get("content")
        if type(total) is not int or type(offset) is not int or not isinstance(text, str):
            continue
        chunks = groups.setdefault((revision, total), {})
        chunks[offset] = text
    for (_, total), chunks in groups.items():
        offset = 0
        while offset in chunks and chunks[offset]:
            offset += len(chunks[offset])
        if offset == total and (total > 0 or 0 in chunks):
            return True
    return False
