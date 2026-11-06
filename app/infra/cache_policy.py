"""User-facing freshness and display lifetimes; never reset source timestamps."""
from contextlib import contextmanager
from contextvars import ContextVar

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


@contextmanager
def read_scope(strict=False):
    rows = []
    a, b = _reads.set(rows), _strict.set(strict)
    try:
        yield rows
    finally:
        _reads.reset(a)
        _strict.reset(b)


def note_read(metadata):
    rows = _reads.get()
    if rows is not None and metadata:
        rows.append(metadata)


def revalidating():
    return _strict.get()


def primary_sources(rows):
    """Long-lived labels/photos do not date the core ticket or list they decorate."""
    core = [row for row in rows if row.get("kind") not in LONG_DISPLAY_KINDS | {"confbody"}]
    return core or rows


class CacheHeadersMiddleware:
    """Carry source age across sync FastAPI handlers without changing JSON contracts."""
    def __init__(self, app, cache):
        self.app, self.cache = app, cache

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope.get("method") != "GET" or not scope.get("path", "").startswith("/api/"):
            return await self.app(scope, receive, send)
        from urllib.parse import parse_qs
        query = parse_qs(scope.get("query_string", b"").decode())
        with read_scope(strict=query.get("revalidate") == ["1"]) as reads:
            async def tracked_send(message):
                if message["type"] == "http.response.start":
                    headers = list(message.get("headers", []))
                    headers.append((b"x-ltm-policy-revision", str(self.cache.policy_revision).encode()))
                    if reads:
                        state = max((r["state"] for r in reads), key={"fresh": 0, "stale": 1, "dead": 2}.get)
                        primary = primary_sources(reads)
                        display_kind = "metadata" if all(r.get("kind") in LONG_DISPLAY_KINDS for r in primary) else "general"
                        values = {"State": state, "Fetched-At": min(r["fetchedAt"] for r in primary),
                                  "Fresh-Until": min(r["freshUntil"] for r in reads),
                                  "Display-Until": min(r["displayUntil"] for r in reads),
                                  "Display-Kind": display_kind}
                        headers.extend((("x-ltm-cache-state" if k == "State" else f"x-ltm-{k}".lower()).encode(), str(v).encode()) for k, v in values.items())
                    message = dict(message, headers=headers)
                await send(message)
            await self.app(scope, receive, tracked_send)
