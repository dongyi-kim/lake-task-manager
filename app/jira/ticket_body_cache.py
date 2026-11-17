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
    "Description search page exceeds 16 MiB. Reduce max_results; "
    "for one oversized body use get_ticket(detail='read').")


def _content_digest(payload):
    digest = hashlib.sha256()
    for piece in json.JSONEncoder(ensure_ascii=False, separators=(",", ":"), sort_keys=True).iterencode(payload):
        for offset in range(0, len(piece), 16384):
            digest.update(piece[offset:offset + 16384].encode("utf-8"))
    return digest.hexdigest()


class TicketBodyCache:
    def __init__(self, cache, scope, ttl, fence):
        self.cache, self.ttl, self.fence = cache, ttl, fence
        self.scope = hashlib.sha256(str(scope).encode()).hexdigest()

    def _head_key(self, key):
        return f"ticketbodyhead:{self.scope}:" + hashlib.sha256(str(key).encode()).hexdigest()

    def covered(self, keys):
        """Cheap planning hint only; actual reuse still checks discovery's version."""
        heads = {self._head_key(key): key for key in keys}
        with read_scope():  # Planning hints are not evidence used in the result.
            return {heads[key] for key in self.cache.get_many(heads)}

    def _identity(self, issue):
        key = issue.get("key")
        updated = (issue.get("fields") or {}).get("updated")
        if not key or not isinstance(updated, str) or not updated.strip():
            return None
        digest = hashlib.sha256((str(key) + "\0" + updated).encode()).hexdigest()
        suffix = f"{self.scope}:{digest}"
        return str(key), updated, f"ticketbodymeta:{suffix}", f"ticketbody:{suffix}:"

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
                    or "description" not in payload or encoded_size(payload) != size):
                continue
            result[key] = payload["description"]
        return result

    def store(self, issues, fetched_at):
        """Atomically publish a bounded physical response at its original source age."""
        entries, total = [], 0
        for issue in issues:
            fields = issue.get("fields") or {}
            identity = self._identity(issue)
            if identity is None or "description" not in fields:
                continue
            key, updated, index_key, payload_prefix = identity
            payload = {"key": key, "updated": updated, "description": fields["description"]}
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
