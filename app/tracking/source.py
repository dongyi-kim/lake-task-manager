"""Provider-only Jira reads with shared admission, raw cache and source completeness."""
from __future__ import annotations

import hashlib
import json
import time
from contextlib import contextmanager

from app.auth.base import UpstreamError
from app.tracking.storage import encode, encoded_size


class IncompleteSource(RuntimeError):
    pass


def completeness(value, rows_key):
    if not isinstance(value, dict) or not isinstance(value.get(rows_key), list):
        return False
    rows = value[rows_key]
    ids = [str(row.get("id")) for row in rows if row.get("id") is not None]
    total = value.get("total")
    return (type(total) is int and total >= 0 and value.get("startAt", 0) == 0 and
            len(ids) == len(set(ids)) == total)


def _page_metadata(page, rows_key):
    return {**{key: value for key, value in page.items() if key != rows_key},
            "rowCount": len(page[rows_key])}


def _canonical_pages(checkpoint, pages_key, rows_key, identity_key, complete_key):
    """Migrate old full pages, retaining raw rows only in their canonical list.

    Validate both legacy copies before discarding either. A contradictory checkpoint
    must remain incomplete instead of silently resuming with missing or stale text.
    Embedded comment prefixes have no pages yet and are not pagination progress.
    """
    message = "저장된 수집 결과가 일치하지 않아 이어 수집할 수 없습니다. 새로 조회해 주세요."
    pages = checkpoint.setdefault(pages_key, [])
    if not isinstance(pages, list) or any(not isinstance(page, dict) for page in pages):
        raise IncompleteSource(message)
    if not pages:
        return pages
    rows = checkpoint.get(rows_key)
    if rows is None:
        if any(not isinstance(page.get(rows_key), list) for page in pages):
            raise IncompleteSource(message)
        rows = [row for page in pages for row in page[rows_key]]
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise IncompleteSource(message)
    metadata, offset, total = [], 0, None
    for page in pages:
        legacy_rows = page.get(rows_key)
        count = len(legacy_rows) if isinstance(legacy_rows, list) else page.get("rowCount")
        page_total = page.get("total")
        if (type(count) is not int or count < 0 or type(page_total) is not int or page_total < 0
                or page.get("startAt", offset) != offset or (total is not None and total != page_total)):
            raise IncompleteSource(message)
        if legacy_rows is not None and legacy_rows != rows[offset:offset + count]:
            raise IncompleteSource(message)
        metadata.append({**{key: value for key, value in page.items() if key != rows_key}, "rowCount": count})
        offset += count
        total = page_total
    ids = [str(row.get(identity_key)) for row in rows if row.get(identity_key) is not None]
    if (len(rows) != offset or len(ids) != len(rows) or len(set(ids)) != len(ids)
            or offset > total or (checkpoint.get(complete_key) and offset != total)
            or (checkpoint.get("total") is not None and checkpoint["total"] != total)):
        raise IncompleteSource(message)
    checkpoint[rows_key] = rows
    checkpoint[pages_key] = metadata
    return metadata


class TrackingSource:
    HOT_TTL = 30
    COOL_TTL = 7 * 86400

    def __init__(self, client, store, namespace, metrics, check, persist, page_slots=None):
        self.client, self.store, self.namespace = client, store, namespace
        self.metrics, self.check, self.persist = metrics, check, persist
        self.source_times = []
        self.page_slots = page_slots

    @contextmanager
    def admission(self):
        if self.page_slots is None:
            yield
            return
        while not self.page_slots.acquire(timeout=0.1):
            self.check()
        try:
            self.check()
            yield
        finally:
            self.page_slots.release()

    def key(self, kind, value):
        return self.namespace + ":" + kind + ":" + hashlib.sha256(encode(value).encode()).hexdigest()

    def read(self, path, params=None):
        self.check()
        provider = self.client.provider
        with self.admission(), self.client._query_scheduler.page_scope(parallel_capable=getattr(provider, "supports_parallel", False)):
            self.check()
            self.metrics["restRequests"] += 1
            self.metrics["requestBytes"] += encoded_size({"path": path, "params": params})
            result = provider.get_json(path, params=params) if params is not None else provider.get_json(path)
            self.metrics["responseBytes"] += encoded_size(result)
            self.source_times.append(time.time())
            if isinstance(result, dict) and (result.get("errorMessages") or result.get("errors")) and not result.get("key"):
                # Some provider adapters preserve Jira's JSON validation body but not
                # the HTTP status. Never cache this as an empty successful source.
                raise UpstreamError(400, path, encode(result))
            return result

    def cached(self, kind, value, producer, ttl=None):
        key = self.key(kind, value)
        cached = self.store.source(key)
        if cached is not None:
            self.metrics["cacheHits"] += 1
            self.source_times.append(cached["fetchedAt"])
            return cached["data"]
        result = producer()
        fetched = time.time()
        self.store.put_source(key, result, ttl or self.HOT_TTL, fetched)
        self.source_times.append(fetched)
        return result

    def statuses(self):
        rows = self.cached("statuses", {}, lambda: self.read("/rest/api/2/status"), self.COOL_TTL)
        result = {}
        for status in rows:
            category = (status.get("statusCategory") or {}).get("key")
            if category:
                for field in ("id", "name"):
                    if status.get(field):
                        result[str(status[field]).casefold()] = category
        return result

    def search(self, jql, fields, checkpoint, save, *, expand=True, allow_cache=True):
        pages = _canonical_pages(checkpoint, "pages", "issues", "key", "complete")
        generation = self.client._jql_generation() if hasattr(self.client, "_jql_generation") else 0
        signature = {"jql": jql, "fields": fields, "generation": generation, "expand": expand}
        cache_key = self.key("search", signature)
        if allow_cache and not checkpoint.get("pages"):
            cached = self.store.source(cache_key)
            if cached:
                self.metrics["cacheHits"] += 1
                self.source_times.append(cached["fetchedAt"])
                checkpoint.update(cached["data"])
                _canonical_pages(checkpoint, "pages", "issues", "key", "complete")
                save()
                return checkpoint["issues"]
        while not checkpoint.get("complete"):
            offset = sum(page["rowCount"] for page in pages)
            params = {"jql": jql, "fields": fields, "startAt": offset, "maxResults": 100}
            if expand and checkpoint.get("expandSupported", True):
                params["expand"] = "changelog"
            try:
                page = self.read("/rest/api/2/search", params)
            except UpstreamError as exc:
                # Some DC extensions reject expand outright rather than ignore it.
                if exc.status != 400:
                    raise
                if "comment" in str(exc.body).casefold() and "comment" in params["fields"].split(","):
                    params["fields"] = ",".join(f for f in params["fields"].split(",") if f != "comment")
                    checkpoint["commentSupported"] = False
                elif "expand" in params:
                    checkpoint["expandSupported"] = False
                    params.pop("expand")
                elif "comment" in params["fields"].split(","):
                    params["fields"] = ",".join(f for f in params["fields"].split(",") if f != "comment")
                    checkpoint["commentSupported"] = False
                else:
                    raise
                try:
                    page = self.read("/rest/api/2/search", params)
                except UpstreamError as fallback_exc:
                    if fallback_exc.status != 400 or "comment" not in params["fields"].split(","):
                        raise
                    params["fields"] = ",".join(f for f in params["fields"].split(",") if f != "comment")
                    checkpoint["commentSupported"] = False
                    page = self.read("/rest/api/2/search", params)
            rows, total = page.get("issues"), page.get("total")
            if not isinstance(rows, list) or type(total) is not int or total < 0 or page.get("startAt", offset) != offset:
                raise IncompleteSource("검색 페이지의 전체 개수를 확인할 수 없습니다.")
            if checkpoint.get("total") is not None and checkpoint["total"] != total:
                raise IncompleteSource("검색 도중 대상이 변경되었습니다. 새 조회가 필요합니다.")
            previous = {row["key"] for row in checkpoint.get("issues", [])}
            if len({row.get("key") for row in rows}) != len(rows) or any(not row.get("key") or row.get("key") in previous for row in rows):
                raise IncompleteSource("검색 페이지에 중복되거나 식별할 수 없는 항목이 있습니다.")
            if not rows and offset < total:
                raise IncompleteSource("검색 결과가 전체 개수보다 일찍 끝났습니다.")
            checkpoint["total"] = total
            fetched_at = self.source_times[-1] if self.source_times else time.time()
            page["_trackingFetchedAt"] = fetched_at
            for row in rows:
                row["_trackingFetchedAt"] = fetched_at
            pages.append(_page_metadata(page, "issues"))
            checkpoint["complete"] = offset + len(rows) == total
            checkpoint.setdefault("issues", []).extend(rows)
            save()
            if offset + len(rows) > total:
                raise IncompleteSource("검색 전체 개수가 일치하지 않습니다.")
        self.store.put_source(cache_key, checkpoint, self.HOT_TTL)
        return checkpoint["issues"]

    def history(self, raw, cp, save, fields):
        if cp.get("historyComplete"):
            return cp["history"], True
        if completeness(raw.get("changelog"), "histories"):
            # The rich raw page is already durable. Analysis can be replayed for free,
            # so do not rewrite the complete run twice for each embedded source.
            cp.update(history=raw["changelog"], historyComplete=True)
            return raw["changelog"], True
        version = (raw["key"], (raw.get("fields") or {}).get("updated"))
        cached = self.store.source(self.key("history", version))
        if cached:
            self.metrics["cacheHits"] += 1
            self.source_times.append(cached["fetchedAt"])
            history = cached["data"]
        else:
            history = raw.get("changelog")
            cp["history"] = history or {"histories": []}
            if not completeness(history, "histories"):
                response = self.read("/rest/api/2/issue/" + raw["key"], {"fields": fields, "expand": "changelog"})
                history = response.get("changelog")
                # Issue expansion is the supported DC source. Cloud-specific /changelog
                # pagination is deliberately not assumed on Jira DC 8.20.8.
            if completeness(history, "histories"):
                self.store.put_source(self.key("history", version), history, self.COOL_TTL)
        complete = completeness(history, "histories")
        cp.update(history=history or {"histories": []}, historyComplete=complete)
        if not complete:
            save()
        return cp["history"], complete

    def comments(self, raw, cp, save):
        pages = _canonical_pages(cp, "commentPages", "comments", "id", "commentsComplete")
        if cp.get("commentsComplete"):
            return cp["comments"], True
        embedded = (raw.get("fields") or {}).get("comment")
        if completeness(embedded, "comments"):
            cp.update(comments=embedded["comments"], commentsComplete=True)
            cp.pop("commentPages", None)
            return embedded["comments"], True
        if isinstance(embedded, dict) and isinstance(embedded.get("comments"), list):
            cp.setdefault("comments", embedded["comments"])
        version = (raw["key"], (raw.get("fields") or {}).get("updated"))
        cache_key = self.key("comments", version)
        cached = self.store.source(cache_key)
        if cached:
            self.metrics["cacheHits"] += 1
            self.source_times.append(cached["fetchedAt"])
            cp.update(comments=cached["data"], commentsComplete=True)
            cp.pop("commentPages", None)
            return cp["comments"], True
        embedded = (raw.get("fields") or {}).get("comment")
        if completeness(embedded, "comments"):
            cp.update(comments=embedded["comments"], commentsComplete=True)
        else:
            while True:
                offset = sum(page["rowCount"] for page in pages)
                page = self.read("/rest/api/2/issue/" + raw["key"] + "/comment", {"startAt": offset, "maxResults": 100})
                rows, total = page.get("comments"), page.get("total")
                if not isinstance(rows, list) or type(total) is not int or page.get("startAt", offset) != offset:
                    cp["commentsComplete"] = False
                    break
                if pages and pages[0].get("total") != total:
                    cp["commentsComplete"] = False
                    break
                ids = [str(c.get("id")) for c in cp.get("comments", [])] if pages else []
                new_ids = [str(c.get("id")) for c in rows if c.get("id") is not None]
                if len(new_ids) != len(rows) or len(set(ids + new_ids)) != len(ids) + len(rows):
                    cp["commentsComplete"] = False
                    break
                if not pages:
                    cp["comments"] = []
                pages.append(_page_metadata(page, "comments"))
                cp["comments"].extend(rows)
                cp["commentsComplete"] = len(cp["comments"]) == total
                if not cp["commentsComplete"]:
                    save()
                if cp["commentsComplete"] or not rows or offset + len(rows) > total:
                    break
        if cp.get("commentsComplete"):
            self.store.put_source(cache_key, cp.get("comments", []), self.COOL_TTL)
        else:
            save()
        return cp.get("comments", []), bool(cp.get("commentsComplete"))

    def hydrate(self, rows, fields, checkpoints, save):
        """Revalidate revisions cheaply, then reuse rich raw sources across periods."""
        result, missing = {}, []
        cache_keys = {row["key"]: self.key("rawIssue", (row["key"], (row.get("fields") or {}).get("updated"))) for row in rows}
        cached_sources = self.store.sources(list(cache_keys.values()))
        for row in rows:
            version = (row["key"], (row.get("fields") or {}).get("updated"))
            key = self.key("rawIssue", version)
            cached = cached_sources.get(key)
            if cached and version[1]:
                result[row["key"]] = cached["data"]
                self.metrics["cacheHits"] += 1
                self.source_times.append(cached["fetchedAt"])
            else:
                missing.append(row)
        for offset in range(0, len(missing), 100):
            batch = missing[offset:offset + 100]
            keys = [row["key"] for row in batch]
            versions = {row["key"]: (row.get("fields") or {}).get("updated") for row in batch}
            label = "hydrate:" + ",".join(keys)
            batch_cp = checkpoints.setdefault(label, {})
            quoted = ["\"" + key.replace("\\", "\\\\").replace('"', '\\"') + "\"" for key in keys]
            fetched = self.search("key in (%s) ORDER BY key ASC" % ",".join(quoted), fields,
                                  batch_cp, save, allow_cache=False)
            cache_records = []
            for raw in fetched:
                revision = (raw.get("fields") or {}).get("updated")
                if versions.get(raw["key"]) and versions[raw["key"]] != revision:
                    raise IncompleteSource("수집 도중 티켓이 변경되었습니다. 새 조회가 필요합니다.")
                result[raw["key"]] = raw
                if revision:
                    cache_records.append((self.key("rawIssue", (raw["key"], revision)), raw, raw.get("_trackingFetchedAt") or time.time()))
            self.store.put_sources(cache_records, self.COOL_TTL)
            if set(keys) - set(result):
                raise IncompleteSource("검색된 티켓의 원본 또는 접근 권한을 확인할 수 없습니다.")
        return [result[row["key"]] for row in rows]
