"""Lossless evidence coverage from successful responses, not requested arguments."""
from hashlib import sha256
import json
import math
import re
from urllib.parse import parse_qs, urljoin, urlsplit


def source_observed(calls, source):
    identity, url = source.get("id"), source.get("url")
    fields = {"ticket": ("key",), "comment": ("id",), "document": ("id",), "person": ("id", "name", "key")}.get(source.get("kind"))
    if not identity or not url or not fields:
        return False
    def matches(value):
        if isinstance(value, dict):
            identities = {str(value[field]) for field in fields if value.get(field) is not None}
            if identity in identities:
                if url == value.get("url"):
                    return True
                if source.get("kind") == "person" and url == value.get("self"):
                    try:
                        address = urlsplit(url)
                        user_ids = set(parse_qs(address.query).get("username", []) + parse_qs(address.query).get("key", []))
                        if (re.search(r"/rest/api/(?:2|latest)/user$", address.path)
                                and address.scheme in {"", "http", "https"} and not address.fragment
                                and not address.username and not address.password and identities & user_ids):
                            return True
                    except ValueError:
                        pass
            return any(matches(item) for item in value.values())
        return isinstance(value, list) and any(matches(item) for item in value)
    successful = [c for c in calls if not c.get("isError") and (c.get("result") or {}).get("ok") is True]
    if any(matches(c["result"].get("data")) for c in successful):
        return True
    if source.get("kind") not in {"comment", "ticket"}:
        return False
    tickets = {}
    for call in successful:
        data = call["result"].get("data")
        if not isinstance(data, dict):
            continue
        rows = [data] if call.get("name") == "get_ticket" else []
        if call.get("name") in {"search_issues", "get_tickets"}:
            rows = data.get("issues") or []
        elif call.get("name") == "get_descendants":
            rows = [row["issue"] for row in data.get("issues") or []
                    if isinstance(row, dict) and isinstance(row.get("issue"), dict)
                    and isinstance(row.get("key"), str) and re.fullmatch(r"[A-Z][A-Z0-9_]*-\d+", row["key"])
                    and row["issue"].get("key") == row["key"]]
        elif call.get("name") == "get_children":
            rows = [row for row in data.get("children") or []
                    if isinstance(row, dict) and isinstance(row.get("key"), str)
                    and re.fullmatch(r"[A-Z][A-Z0-9_]*-\d+", row["key"])]
        for row in rows:
            if not isinstance(row, dict) or not row.get("key"):
                continue
            key = row["key"]
            identifiers = {key}
            if re.fullmatch(r"\d+", str(row.get("id") or "")):
                identifiers.add(str(row["id"]))
            endpoints = [(row.get("url"), "/browse/" + key)] + [
                (row.get("self"), "/rest/api/2/issue/" + token) for token in identifiers]
            for address, suffix in endpoints:
                if not isinstance(address, str):
                    continue
                try:
                    base = urlsplit(address)
                except ValueError:
                    continue
                if (base.scheme in {"http", "https"} and base.netloc and not base.username and not base.password
                        and not base.query and not base.fragment and base.path.endswith(suffix)):
                    tickets.setdefault(key, []).append((address, base, base.path[:-len(suffix)], identifiers))
    if source.get("kind") == "ticket":
        return any(url == address for address, *_ in tickets.get(identity, []))
    decoded = {}
    for call in successful:
        page = call["result"].get("data") or {}
        if call.get("name") != "get_evidence_page" or not isinstance(page, dict) or page.get("kind") != "comments":
            continue
        key = page.get("key")
        records = list(page.get("items") or [])
        if any("recordJson" in item and str(item.get("id")) == identity for item in records):
            if key not in decoded:
                decoded[key] = evidence_collection(successful, key)["records"]
            records.extend(decoded[key])
        for record in records:
            if str(record.get("id")) != identity or not isinstance(record.get("self"), str):
                continue
            for address, base, context, identifiers in tickets.get(key, []):
                try:
                    normalized = urljoin(address, record["self"])
                    target = urlsplit(normalized)
                except ValueError:
                    continue
                paths = {f"{context}/rest/api/2/issue/{token}/comment/{identity}" for token in identifiers}
                if (normalized == url and target.scheme == base.scheme and target.netloc == base.netloc
                        and target.path in paths and not target.username and not target.password
                        and not target.query and not target.fragment):
                    return True
    return False


def observed_scope_references(calls, root):
    """Context/missing references are not proof of an unread ticket's body/status."""
    ancestors, unavailable = set(), set()
    for call in calls:
        result = call.get("result") or {}
        if call.get("isError") or result.get("ok") is not True:
            continue
        data = result.get("data") or {}
        if call.get("name") == "get_ticket" and data.get("key") == root:
            key = data.get("epicKey")
            if isinstance(key, str) and re.fullmatch(r"[A-Z][A-Z0-9_]*-\d+", key):
                ancestors.add(key)
        elif (call.get("name") == "get_descendants" and data.get("rootKey") == root
              and (result.get("partial") is True or data.get("partial") is True)):
            for row in data.get("missing") or []:
                key = row.get("key")
                if isinstance(key, str) and re.fullmatch(r"[A-Z][A-Z0-9_]*-\d+", key):
                    unavailable.add(key)
    return {"ancestors": ancestors, "unavailable": unavailable}


def observed_status_categories(calls):
    """Use typed status fields only; never parse description/comment text as metadata."""
    observed = {}
    categories = {"done": "done", "indeterminate": "inprogress", "inprogress": "inprogress",
                  "new": "todo", "todo": "todo"}
    def add(key, category):
        if not isinstance(key, str) or not re.fullmatch(r"[A-Z][A-Z0-9_]*-\d+", key) or not isinstance(category, str):
            return
        value = categories.get(category)
        observed[key] = value if key not in observed else value if observed[key] == value else None
    def issue(row):
        if not isinstance(row, dict):
            return
        fields = row.get("fields") or {}
        status = fields.get("status") or {}
        add(row.get("key"), (status.get("statusCategory") or {}).get("key"))
    for call in calls:
        result = call.get("result") or {}
        if call.get("isError") or result.get("ok") is not True or not isinstance(result.get("data"), dict):
            continue
        data = result["data"]
        if call.get("name") == "get_ticket":
            add(data.get("key"), data.get("statusCategory"))
        elif call.get("name") == "get_children":
            for row in data.get("children") or []:
                add(row.get("key"), row.get("statusCategory"))
        elif call.get("name") in {"search_issues", "get_tickets"}:
            for row in data.get("issues") or []:
                issue(row)
        elif call.get("name") == "get_descendants":
            for row in data.get("issues") or []:
                raw = row.get("issue") or {}
                issue(raw)
                issue((raw.get("fields") or {}).get("parent"))
    return observed


def leaf_subtask_membership(calls, root):
    """Equivalent complete hierarchy for this fixture's Task -> leaf SubTask route."""
    from app.jira.jira_client import JiraClient
    root_types, leaf_proofs, membership = [], {}, None
    for call in calls:
        result = call.get("result") or {}
        if (call.get("isError") or result.get("ok") is not True or result.get("partial") is not False
                or result.get("missing") != 0 or (result.get("cache") or {}).get("state") != "fresh"):
            continue
        data = result.get("data") or {}
        name = call.get("name")
        if name == "get_ticket" and data.get("key") == root:
            root_types.append(data.get("type") == "Task")
        elif name in {"get_tickets", "search_issues"}:
            for issue in data.get("issues") or []:
                key, fields = issue.get("key"), issue.get("fields") or {}
                kind = fields.get("issuetype") or {}
                if key == root:
                    root_types.append(kind.get("name") == "Task" and kind.get("subtask") is False)
                else:
                    parent = (fields.get("parent") or {}).get("key")
                    if kind.get("subtask") is False or parent is not None and parent != root:
                        leaf_proofs[key] = False
                    elif kind.get("subtask") is True and parent == root:
                        leaf_proofs[key] = leaf_proofs.get(key, True)
        elif name == "get_children" and (call.get("arguments") or {}).get("key") == root:
            children = data.get("children")
            if not isinstance(children, list) or len(children) >= JiraClient.CHILD_DISPLAY_LIMIT:
                return None
            keys = [row.get("key") for row in children]
            if (len(set(keys)) != len(keys) or not all(isinstance(key, str) for key in keys)
                    or any(row.get("isSubtask") is not True for row in children)):
                return None
            current = set(keys)
            if membership is not None and membership != current:
                return None
            membership = current
    if (not root_types or not all(root_types) or membership is None
            or any(leaf_proofs.get(key) is False for key in membership)):
        return None
    return membership


def optional_count_progress(calls, progress, reply, root="DL-94010"):
    """Prove ratio arithmetic/coverage; mandatory direct review judges the reply's scope."""
    if not isinstance(progress, dict) or any(type(progress.get(k)) is not int for k in ("done", "total")):
        return False
    percent = progress.get("percent")
    if type(percent) not in (int, float) or not math.isfinite(percent):
        return False
    successful = [c for c in calls if not c.get("isError") and (c.get("result") or {}).get("ok") is True]
    statuses = observed_status_categories(successful)
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
    if valid and ended and total and set(rows) == set(range(total)):
        by_key = {row.get("key"): row for row in rows.values()}
        if len(by_key) != total or (root in by_key) != included:
            return False
        for key, row in by_key.items():
            if key != root and (row.get("parentKey") != root or row.get("depth") != 1):
                return False
    else:
        children = leaf_subtask_membership(successful, root)
        if children is None:
            return False
        by_key = dict.fromkeys(children)
    for include_root in (True, False):
        keys = set(by_key) | {root} if include_root else set(by_key) - {root}
        if any(not statuses.get(key) for key in keys):
            continue
        done = sum(statuses[key] == "done" for key in keys)
        if (keys and progress["done"] == done and progress["total"] == len(keys)
                and math.isclose(percent, 100 * done / len(keys), abs_tol=0.1)):
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
            # Membership is fresh; same-ID/same-digest source bytes remain reusable.
            records, started, ended, valid = {}, True, False, True
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
            state = chunks.setdefault((str(item.get("id")), digest), {})
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


def has_stalled_pagination(calls):
    """Three identical successful continuation reads are an observed no-progress loop."""
    source_tools = {"get_evidence_page", "search_issues", "get_descendants",
                    "search_confluence_documents", "read_confluence_document"}
    previous, repeated = None, 0
    for call in calls:
        result = call.get("result") or {}
        if call.get("name") not in source_tools or call.get("isError") or result.get("ok") is not True:
            continue
        data = result.get("data")
        if not isinstance(data, dict):
            previous, repeated = None, 0
            continue
        pending = (data.get("hasMore") is True or data.get("nextStartAt") is not None
                   or data.get("nextOffset") is not None)
        if not pending:
            previous, repeated = None, 0
            continue
        signature = sha256(json.dumps([call["name"], call.get("arguments"), data],
                                      ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        repeated = repeated + 1 if signature == previous else 1
        previous = signature
        if repeated > 2:
            return True
    return False


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
