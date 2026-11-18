"""Small version indexes and immutable description payloads in the shared cache.

Never inspect canonical full-issue rows to discover body coverage. Their payloads
may be huge and do not prove the current authenticated provider session.
"""
import hashlib
import json

from app.auth.base import QueryStateChanged
from app.infra.cache_policy import read_scope
from app.infra.json_payload import encoded_size
from app.infra.resource_pressure import resource_pressure


_MAX_BYTES = 16 * 1024 * 1024
_LIMIT_MESSAGE = (
    "Heavy-field search page exceeds 16 MiB. Reduce max_results; "
    "for one oversized body use get_ticket(detail='read'); "
    "for comments use get_evidence_page(kind='comments').")


def _content_digest(payload):
    digest = hashlib.sha256()
    for piece in json.JSONEncoder(ensure_ascii=False, separators=(",", ":"), sort_keys=True).iterencode(payload):
        for offset in range(0, len(piece), 16384):
            digest.update(piece[offset:offset + 16384].encode("utf-8"))
    return digest.hexdigest()


class TicketBodyCache:
    def __init__(self, cache, scope, ttl, fence, *, field="description"):
        if field not in {"description", "comment"}:
            raise ValueError("Unsupported heavy ticket field")
        self.cache, self.ttl, self.fence = cache, ttl, fence
        self.field = field
        self.kind = "ticketbody" if field == "description" else "ticketcomment"
        self.scope = hashlib.sha256(str(scope).encode()).hexdigest()

    def _head_key(self, key):
        return f"{self.kind}head:{self.scope}:" + hashlib.sha256(str(key).encode()).hexdigest()

    def covered(self, keys):
        """Cheap planning hint only; actual reuse still checks discovery's version."""
        heads = {self._head_key(key): key for key in keys}
        with read_scope():  # Planning hints are not evidence used in the result.
            return {heads[key] for key in self.cache.get_many(heads)}

    def read_latest(self, keys, max_bytes):
        """Reuse the last observed version under the current source-age policy.

        This is a cached snapshot, not proof of the ticket's current Jira version.
        Unlike covered(), these heads are evidence and retain their original age.
        """
        heads = {self._head_key(key): key for key in keys}
        rows = self.cache.get_many_with_metadata(heads)
        issues = []
        for head_key, (head, _source) in rows.items():
            if isinstance(head, dict):
                issues.append({"key": heads[head_key], "fields": {"updated": head.get("updated")}})
        return self.read(issues, max_bytes)

    def _identity(self, issue):
        key = issue.get("key")
        updated = (issue.get("fields") or {}).get("updated")
        if not key or not isinstance(updated, str) or not updated.strip():
            return None
        digest = hashlib.sha256((str(key) + "\0" + updated).encode()).hexdigest()
        suffix = f"{self.scope}:{digest}"
        return str(key), updated, f"{self.kind}meta:{suffix}", f"{self.kind}:{suffix}:"

    def _valid(self, value):
        if self.field == "description":
            return True  # Explicit null/empty descriptions are known values.
        rows = value.get("comments") if isinstance(value, dict) else value
        return isinstance(rows, list) and all(isinstance(row, dict) and row.get("id") for row in rows)

    def read(self, issues, max_bytes):
        """Return present bodies only; missing/expired/versionless pairs are misses."""
        identities = [identity for issue in issues if (identity := self._identity(issue))]
        indexes = self.cache.get_many_with_metadata(identity[2] for identity in identities)
        candidates, total = [], 0
        for key, updated, index_key, payload_prefix in identities:
            entry = indexes.get(index_key)
            if entry is None:
                continue
            index, _source = entry
            pointer, size = index.get("payloadKey"), index.get("bytes")
            if (not isinstance(pointer, str) or not pointer.startswith(payload_prefix)
                    or type(size) is not int or size < 0):
                continue
            total += size
            if total > max_bytes:
                raise ValueError(_LIMIT_MESSAGE)
            candidates.append((key, updated, pointer, size))
        result = {}
        for key, updated, pointer, size in candidates:
            resource_pressure.check()
            entry = self.cache.get_with_metadata(pointer)
            if entry is None:
                continue  # Independent quota eviction is not a known-empty body.
            payload, _source = entry
            if (payload.get("key") != key or payload.get("updated") != updated
                    or self.field not in payload or not self._valid(payload[self.field])
                    or encoded_size(payload) != size):
                continue
            result[key] = payload[self.field]
        return result

    def store(self, issues, fetched_at):
        """Atomically publish a bounded physical response at its original source age."""
        entries, total = [], 0
        for issue in issues:
            fields = issue.get("fields") or {}
            identity = self._identity(issue)
            if identity is None or self.field not in fields or not self._valid(fields[self.field]):
                continue
            key, updated, index_key, payload_prefix = identity
            payload = {"key": key, "updated": updated, self.field: fields[self.field]}
            size = encoded_size(payload)
            total += size
            if total > _MAX_BYTES:
                raise ValueError(_LIMIT_MESSAGE)
            # Content addresses keep the preflight immutable without duplicating
            # an unchanged body every time a live request revalidates it.
            pointer = payload_prefix + _content_digest(payload)
            entries.extend(((pointer, payload), (index_key, {"payloadKey": pointer, "bytes": size}),
                            (self._head_key(key), {"updated": updated})))
        if entries:
            resource_pressure.check()
            if not self.cache.set_many_if_fence(entries, self.ttl, self.fence,
                    fetched_at=fetched_at, preserve_newer_keys=[key for key, _ in entries],
                    revalidate_equal_preserved=True):
                raise QueryStateChanged("티켓이 변경되어 본문을 다시 확인해야 합니다.")


class TicketFieldCache(TicketBodyCache):
    """Independent raw heavy field cache; comment windows retain all source metadata."""

    def __init__(self, cache, scope, ttl, fence, field):
        super().__init__(cache, scope, ttl, fence, field=field)
