"""Expose observed cache age without claiming unobserved responses are fresh."""

from contextlib import nullcontext

from app.infra.cache_policy import read_scope, reduce_read_metadata, resolve_read_policy


def read_result(fn, kwargs, client):
    # Both the scope and read run inside the same worker: ContextVars from sibling
    # workers do not report reads back to a scope opened around await to_thread.
    arguments = dict(kwargs)
    policy = resolve_read_policy(arguments.pop("cache_mode", None), force_refresh=bool(arguments.get("fresh")))
    begin, count = getattr(client, "miss_begin", None), getattr(client, "miss_count", None)
    if begin:
        begin()
    try:
        with read_scope(strict=bool(arguments.get("fresh")), cache_mode=policy) if read_scope else nullcontext([]) as sources:
            data = fn(**arguments)
        missing = count() if count else 0
    finally:
        if begin:
            begin()  # Thread-pool reuse must not leak a previous request's omissions.
    cache = {"available": bool(sources), "state": "unknown"}
    if sources:
        reduced = reduce_read_metadata(sources, policy, source_selection="all")
        # MCP has always dated the result by its oldest observed component, including
        # decorative metadata; HTTP deliberately uses primary sources for fetchedAt.
        reduced.pop("displayKind", None)
        cache.update(reduced, invalidated=any(row.get("invalidated", False) for row in sources),
                     sourceCount=len(sources))
    elif policy.explicit:
        cache.update(requestedCacheMode=policy.mode.value, effectiveCacheMode=policy.mode.value,
                     maxAgeSeconds=policy.max_age_seconds, compliant=False, upstreamValidated=False)
    revision = getattr(getattr(client, "cache", None), "policy_revision", None)
    if revision is not None:
        cache["policyRevision"] = revision
    partial = bool(missing) or has_partial(data, requested_components=policy.explicit)
    if policy.explicit and partial:
        cache["compliant"] = False
    return {"ok": True, "data": data, "cache": cache, "partial": partial, "missing": missing}


def has_partial(data, *, requested_components=False):
    """Expose signalled omissions; explicit policies also require requested body coverage."""
    if isinstance(data, dict):
        if data.get("partial") is True or data.get("complete") is False or data.get("ok") is False:
            return True
        if any(data.get(key) for key in ("error", "errors", "failed", "missing")):
            return True
        if requested_components and data.get("descriptionRequested") and data.get("descriptionMissingKeys"):
            return True
        return any(has_partial(value, requested_components=requested_components) for value in data.values())
    return isinstance(data, (list, tuple)) and any(
        has_partial(row, requested_components=requested_components) for row in data)
