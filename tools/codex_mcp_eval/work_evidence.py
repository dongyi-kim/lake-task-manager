"""Lossless evidence coverage from successful responses, not requested arguments."""
from hashlib import sha256
import json
import math
import re
from urllib.parse import urljoin, urlsplit


def source_observed(calls, source):
    identity, url = source.get("id"), source.get("url")
    fields = {"ticket": ("key",), "comment": ("id",), "document": ("id",), "person": ("id", "name", "key")}.get(source.get("kind"))
    if not identity or not url or not fields:
        return False
    def matches(value):
        if isinstance(value, dict):
            if (identity in {str(value[field]) for field in fields if value.get(field) is not None}
                    and url == value.get("url")):
                return True
            return any(matches(item) for item in value.values())
        return isinstance(value, list) and any(matches(item) for item in value)
    successful = [c for c in calls if not c.get("isError") and (c.get("result") or {}).get("ok") is True]
    if any(matches(c["result"].get("data")) for c in successful):
        return True
    if source.get("kind") != "comment":
        return False
    tickets = {c["result"]["data"].get("key"): c["result"]["data"].get("url") for c in successful
               if c.get("name") == "get_ticket" and isinstance(c["result"].get("data"), dict)}
    for call in successful:
        page = call["result"].get("data") or {}
        if call.get("name") != "get_evidence_page" or not isinstance(page, dict) or page.get("kind") != "comments":
            continue
        key = page.get("key")
        ticket_url = tickets.get(key)
        if not ticket_url:
            continue
        base = urlsplit(ticket_url)
        suffix = "/browse/" + str(key)
        if base.scheme not in {"http", "https"} or not base.netloc or not base.path.endswith(suffix):
            continue
        context = base.path[:-len(suffix)]
        expected_path = f"{context}/rest/api/2/issue/{key}/comment/{identity}"
        for record in page.get("items") or []:
            if str(record.get("id")) != identity or not isinstance(record.get("self"), str):
                continue
            normalized = urljoin(ticket_url, record["self"])
            target = urlsplit(normalized)
            if (normalized == url and target.scheme == base.scheme and target.netloc == base.netloc
                    and target.path == expected_path and not target.query and not target.fragment):
                return True
    return False


def count_ratio_labeled(reply, done, total, include_root):
    """Tie scope, a count unit and arithmetic to one clause, not unrelated words."""
    scopes = re.compile(r"전체|부모(?:를)?\s*포함|하위|자식|자손")
    fraction = rf"(?<!\d){done}\s*/\s*{total}(?!\d)"
    counted = rf"(?<!\d){total}\s*(?:건|개)\s*중\s*{done}\s*(?:건|개)(?!\d)"
    for clause in re.split(r"[\n;!?]|(?<!\d)\.|\.(?!\d)", reply):
        markers = list(scopes.finditer(clause))
        prefix = clause[:markers[0].start()] if markers else ""
        for index, marker in enumerate(markers):
            if bool(re.match(r"전체|부모", marker.group())) != include_root:
                continue
            end = markers[index + 1].start() if index + 1 < len(markers) else len(clause)
            segment = clause[marker.start():end]
            if re.search(r"업무량|작업량|스토리\s*포인트|story\s*points", segment, re.I):
                continue
            percentages = [float(value) for value in re.findall(r"(\d+(?:\.\d+)?)\s*%", segment)]
            if any(not math.isclose(value, 100 * done / total, abs_tol=0.1) for value in percentages):
                continue
            has_unit = re.search(r"티켓|개수|건수|(?<=\d)\s*(?:건|개)", prefix + segment)
            explicit_ratio = re.search(f"{fraction}|{counted}", segment)
            count_basis = re.search(r"티켓\s*(?:수|개수|건수)\s*(?:기준|대비)", prefix + segment)
            if has_unit and (explicit_ratio or count_basis and percentages):
                return True
    return False


def optional_count_progress(calls, progress, reply, root="DL-94010"):
    """Allow labeled ticket-count ratios only after a full hierarchy and status read."""
    if not isinstance(progress, dict) or any(type(progress.get(k)) is not int for k in ("done", "total")):
        return False
    percent = progress.get("percent")
    if type(percent) not in (int, float) or not math.isfinite(percent):
        return False
    successful = [c for c in calls if not c.get("isError") and (c.get("result") or {}).get("ok") is True]
    tickets = {c["result"]["data"].get("key"): c["result"]["data"] for c in successful
               if c.get("name") == "get_ticket" and isinstance(c["result"].get("data"), dict)}
    rows, total, ended, valid, included = {}, None, False, False, None
    for call in successful:
        data = call["result"].get("data") or {}
        if call.get("name") != "get_descendants" or data.get("rootKey") != root:
            continue
        start, count = data.get("startAt"), data.get("total")
        if start == 0:
            rows, total, ended, valid = {}, count, False, True
            included = data.get("includeRoot")
        if (type(start) is not int or type(count) is not int or count != total
                or type(included) is not bool or data.get("includeRoot") != included):
            valid = False
            continue
        valid &= (data.get("complete") is True and not data.get("partial")
                  and not data.get("missing") and not data.get("missingTotal") and not data.get("missingHasMore"))
        for index, row in enumerate(data.get("issues") or [], start):
            if index in rows and rows[index] != row:
                valid = False
            rows[index] = row
        ended |= data.get("hasMore") is False and data.get("nextStartAt") is None
    if not valid or not ended or not total or set(rows) != set(range(total)):
        return False
    by_key = {row.get("key"): row for row in rows.values()}
    if (len(by_key) != total or (root in by_key) != included or root not in tickets
            or not tickets[root].get("statusCategory") or not set(by_key) <= tickets.keys()):
        return False
    for key, row in by_key.items():
        category = (row.get("issue", {}).get("fields", {}).get("status", {}).get("statusCategory") or {}).get("key")
        if not category or not tickets[key].get("statusCategory") or (category == "done") != (tickets[key]["statusCategory"] == "done"):
            return False
        if key != root and (row.get("parentKey") != root or row.get("depth") != 1):
            return False
    for include_root in (True, False):
        keys = set(by_key) | {root} if include_root else set(by_key) - {root}
        done = sum(tickets[key]["statusCategory"] == "done" for key in keys)
        if (keys and progress["done"] == done and progress["total"] == len(keys)
                and math.isclose(percent, 100 * done / len(keys), abs_tol=0.1)
                and count_ratio_labeled(reply, done, len(keys), include_root)):
            return True
    return False


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
