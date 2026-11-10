"""User-facing freshness and display lifetimes; never reset source timestamps."""
from contextlib import contextmanager
from contextvars import ContextVar, copy_context
from dataclasses import dataclass, field
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, model_validator


class CachePolicy(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)
    displaySeconds: int = Field(default=86400, ge=3600, le=2592000, multiple_of=3600)
    freshnessSeconds: int = Field(default=600, ge=60, le=86400, multiple_of=60)
    resolvedFreshnessSeconds: int = Field(default=86400, ge=3600, le=2592000, multiple_of=3600)
    resolvedCommentsFreshnessSeconds: int = Field(default=3600, ge=60, le=86400, multiple_of=60)

    metadataDisplaySeconds: int = Field(default=2592000, ge=86400, le=31536000, multiple_of=86400)
    metadataFreshnessSeconds: int = Field(default=86400, ge=3600, le=2592000, multiple_of=3600)
    confluenceBodyRetentionSeconds: int = Field(default=2592000, ge=86400, le=31536000, multiple_of=86400)

    @model_validator(mode="after")
    def display_covers_freshness(self):
        if self.displaySeconds < max(self.freshnessSeconds, self.resolvedFreshnessSeconds,
                                     self.resolvedCommentsFreshnessSeconds):
            raise ValueError("화면 표시 보존 기간은 모든 갱신 간격 이상이어야 합니다.")
        if self.metadataDisplaySeconds < self.metadataFreshnessSeconds:
            raise ValueError("Epic 이름·사진 보존 기간은 갱신 간격 이상이어야 합니다.")
        return self

    def ttl(self, key, value, original, done=False):
        kind = key.split(":", 1)[0]
        if kind == "confbody":
            return self.confluenceBodyRetentionSeconds
        if kind in LONG_DISPLAY_KINDS:
            return self.metadataFreshnessSeconds
        if kind in {"issue", "issueL", "issueview"}:
            return self.resolvedFreshnessSeconds if is_done(value) else self.freshnessSeconds
        if kind == "comments":
            return self.resolvedCommentsFreshnessSeconds if done else self.freshnessSeconds
        if kind == "jqlcurrent":
            return min(self.freshnessSeconds, original)
        if kind in DISPLAY_KINDS or kind in LIST_KINDS or kind.startswith("jqlidx"):
            return self.freshnessSeconds
        return original


LONG_DISPLAY_KINDS = frozenset({"epicmeta", "avatar_url", "avatar_bytes"})
DISPLAY_KINDS = LONG_DISPLAY_KINDS | frozenset({"issue", "issueL", "issueview", "comments", "epicmeta",
    "user", "userbadge", "ancestors", "siblings", "children", "related", "attachments",
    "documents", "remotelinks", "timeline", "changelog", "mentionctx", "epic_tree", "confpage",
    "confhead", "confview", "confrelated", "conftree", "confbranch"})
# jqlrow/jqlsnap retain their snapshot lifetime, independent of fresh-query reuse.
LIST_KINDS = frozenset({"jqlleaf", "jqlcurrent", "mt", "mytasks", "workload", "workload_bucket",
    "activity", "wbs_build", "vit_build", "vit_bases", "vit_list", "search", "epic_cand",
    "epic_options", "epic_children", "direct_children", "confsearch"})
SWR_AGGREGATE_KINDS = frozenset({"workload", "workload_bucket", "activity", "wbs_build",
    "vit_build", "vit_bases", "vit_list", "mt", "mytasks"})


def is_done(value):
    if not isinstance(value, dict):
        return False
    category = value.get("statusCategory")
    fields = value.get("fields") or value
    if not category:
        category = (fields.get("status") or {}).get("statusCategory") if isinstance(fields.get("status"), dict) else None
    return (category.get("key") if isinstance(category, dict) else category) == "done"


_reads = ContextVar("cache_reads", default=None)
_strict = ContextVar("cache_revalidate", default=False)
_policy = ContextVar("request_read_policy", default=None)


class CacheMode(str, Enum):
    LIVE = "live"
    HOT = "hot"
    WARM = "warm"
    COOL = "cool"
    COLD = "cold"


@dataclass(frozen=True)
class RequestReadPolicy:
    mode: CacheMode | None = None
    max_age_seconds: int | None = None
    request_id: object = field(default_factory=object, compare=False, repr=False)
    # Shared by copied async/thread contexts; cache access is protected by the cache lock.
    validations: dict = field(default_factory=dict, compare=False, repr=False)

    @property
    def explicit(self):
        return self.mode is not None


_DEFAULT_POLICY = RequestReadPolicy()


def resolve_read_policy(cache_mode=None, *, force_refresh=False):
    if isinstance(cache_mode, RequestReadPolicy):
        if force_refresh and cache_mode.mode not in (None, CacheMode.LIVE):
            raise ValueError("force refresh conflicts with cache_mode")
        return cache_mode if not force_refresh else resolve_read_policy(CacheMode.LIVE)
    mode = CacheMode(cache_mode) if cache_mode is not None else None
    if force_refresh:
        if mode not in (None, CacheMode.LIVE):
            raise ValueError("force refresh conflicts with cache_mode")
        mode = CacheMode.LIVE
    ages = {CacheMode.LIVE: 0, CacheMode.HOT: 600, CacheMode.WARM: 86400,
            CacheMode.COOL: 604800, CacheMode.COLD: 2592000}
    return RequestReadPolicy(mode, ages.get(mode))


def current_read_policy():
    return _policy.get() or _DEFAULT_POLICY


@contextmanager
def read_scope(strict=False, cache_mode=None):
    rows = []
    inherited = _policy.get()
    policy = inherited if cache_mode is None and inherited is not None else resolve_read_policy(cache_mode)
    a, b, c = _reads.set(rows), _strict.set(strict or _strict.get()), _policy.set(policy)
    try:
        yield rows
    finally:
        _reads.reset(a)
        _strict.reset(b)
        _policy.reset(c)


def note_read(metadata):
    rows = _reads.get()
    if rows is not None and metadata:
        rows.append(metadata)


@contextmanager
def observed_reads(factory, *args, **kwargs):
    """Capture iterator reads in its own context, forwarding each step outside scope tokens."""
    with read_scope(strict=revalidating()) as sources:
        context = copy_context()
    iterator, forwarded = None, 0

    def forward():
        nonlocal forwarded
        pending = sources[forwarded:]
        forwarded += len(pending)
        for source in pending:
            note_read(source)

    def observed():
        while True:
            try:
                value = context.run(next, iterator)
            except StopIteration:
                return
            forward()
            yield value

    try:
        iterator = iter(context.run(factory, *args, **kwargs))
        yield observed(), sources
    finally:
        try:
            close = getattr(iterator, "close", None)
            if close:
                context.run(close)
        finally:
            forward()


def revalidating():
    return _strict.get()


def primary_sources(rows):
    """Long-lived labels/photos do not date the core ticket or list they decorate."""
    core = [row for row in rows if row.get("kind") not in LONG_DISPLAY_KINDS | {"confbody"}]
    return core or rows


def verified_immutable_revision(row):
    checked = row.get("latestRevisionCheckedAt")
    return (row.get("kind") == "confbody" and row.get("immutableRevision") is True
            and row.get("compliant") is True and isinstance(checked, (int, float))
            and not isinstance(checked, bool))


def freshness_sources(rows):
    """An immutable revision's freshness is its head check; its source age stays untouched."""
    return [dict(row, fetchedAt=row["latestRevisionCheckedAt"]) if verified_immutable_revision(row)
            else row for row in rows]


def reduce_read_metadata(rows, policy=None, *, source_selection="primary"):
    """Shared HTTP/MCP reduction, preserving component age and explicit policy compliance."""
    if source_selection not in {"primary", "all"}:
        raise ValueError("source_selection must be primary or all")
    if not rows:
        return {}
    policy = policy or current_read_policy()
    primary = primary_sources(rows) if source_selection == "primary" else rows
    order = {"fresh": 0, "unknown": 1, "stale": 2, "dead": 3}
    states = [row.get("state") if isinstance(row.get("state"), str) and row["state"] in order
              else "unknown" for row in rows]
    result = {
        "state": max(states, key=order.get),
        "fetchedAt": min(r["fetchedAt"] for r in primary),
        "freshUntil": min(r["freshUntil"] for r in rows),
        "displayUntil": min(r["displayUntil"] for r in rows),
        "displayKind": "metadata" if all(r.get("kind") in LONG_DISPLAY_KINDS for r in primary) else "general",
    }
    if policy.explicit:
        result.update(requestedCacheMode=policy.mode.value, effectiveCacheMode=policy.mode.value,
                      maxAgeSeconds=policy.max_age_seconds,
                      compliant=all(r.get("compliant", False) for r in rows),
                      upstreamValidated=all(r.get("upstreamValidated", False) for r in rows),
                      oldestComponentFetchedAt=min(r["fetchedAt"] for r in rows))
    return result


class CacheHeadersMiddleware:
    """Carry source age across sync FastAPI handlers without changing JSON contracts."""
    def __init__(self, app, cache):
        self.app, self.cache = app, cache

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope.get("method") != "GET" or not scope.get("path", "").startswith("/api/"):
            return await self.app(scope, receive, send)
        from urllib.parse import parse_qs
        try:
            query = parse_qs(scope.get("query_string", b"").decode("utf-8", errors="strict"), keep_blank_values=True)
            modes = query.get("cache_mode", [])
            if len(modes) > 1:
                raise ValueError("cache_mode must occur once")
            force = query.get("revalidate") == ["1"]
            # Only the ticket detail route owns the existing integer `fresh` option.
            parts = scope.get("path", "").strip("/").split("/")
            if len(parts) == 3 and parts[:2] == ["api", "ticket"] and "fresh" in query:
                flags = query["fresh"]
                if len(flags) != 1:
                    raise ValueError("fresh must occur once")
                try:
                    force = force or bool(int(flags[0]))
                except ValueError:
                    pass  # FastAPI retains ownership of the integer validation response.
            if (len(parts) == 4 and parts[:3] == ["api", "confluence", "pages"]
                    and "refresh" in query):
                flags = query["refresh"]
                if len(flags) != 1:
                    raise ValueError("refresh must occur once")
                force = force or flags[0].lower() in {"1", "true", "on", "yes"}
            policy = resolve_read_policy(modes[0] if modes else None, force_refresh=force)
        except (ValueError, UnicodeError) as exc:
            from starlette.responses import JSONResponse
            return await JSONResponse({"detail": str(exc)}, status_code=400)(scope, receive, send)
        with read_scope(strict=force, cache_mode=policy) as reads:
            async def tracked_send(message):
                if message["type"] == "http.response.start":
                    headers = list(message.get("headers", []))
                    headers.append((b"x-ltm-policy-revision", str(self.cache.policy_revision).encode()))
                    if reads:
                        reduced = reduce_read_metadata(reads)
                        values = {"State": reduced["state"], "Fetched-At": reduced["fetchedAt"],
                                  "Fresh-Until": reduced["freshUntil"], "Display-Until": reduced["displayUntil"],
                                  "Display-Kind": reduced["displayKind"]}
                        if policy.explicit:
                            values.update({"Cache-Mode": policy.mode.value, "Max-Age-Seconds": policy.max_age_seconds,
                                           "Cache-Compliant": str(reduced["compliant"]).lower()})
                        headers.extend((("x-ltm-cache-state" if k == "State" else f"x-ltm-{k}".lower()).encode(), str(v).encode()) for k, v in values.items())
                    message = dict(message, headers=headers)
                await send(message)
            await self.app(scope, receive, tracked_send)
