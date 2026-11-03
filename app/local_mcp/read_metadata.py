"""Expose observed cache age without claiming unobserved responses are fresh."""

from contextlib import nullcontext

try:
    from app.infra.cache_policy import read_scope
except ModuleNotFoundError as exc:
    if exc.name != "app.infra.cache_policy":
        raise
    read_scope = None


def read_result(fn, kwargs, client):
    # Both the scope and read run inside the same worker: ContextVars from sibling
    # workers do not report reads back to a scope opened around await to_thread.
    with read_scope(strict=bool(kwargs.get("fresh"))) if read_scope else nullcontext([]) as sources:
        data = fn(**kwargs)
    cache = {"available": bool(sources), "state": "unknown"}
    if sources:
        states = {row.get("state", "unknown") for row in sources}
        state = "dead" if "dead" in states else "stale" if "stale" in states else "fresh" if states == {"fresh"} else "unknown"
        cache.update(state=state,
                     invalidated=any(row.get("invalidated", False) for row in sources),
                     sourceCount=len(sources))
        for name in ("fetchedAt", "freshUntil", "displayUntil"):
            cache[name] = min(row[name] for row in sources)
    revision = getattr(getattr(client, "cache", None), "policy_revision", None)
    if revision is not None:
        cache["policyRevision"] = revision
    return {"ok": True, "data": data, "cache": cache,
            "partial": has_partial(data)}


def has_partial(data):
    """Retain details in data while making explicitly signalled partial reads visible."""
    if isinstance(data, dict):
        if data.get("partial") is True or data.get("complete") is False or data.get("ok") is False:
            return True
        if any(data.get(key) for key in ("error", "errors", "failed", "missing")):
            return True
        return any(has_partial(value) for value in data.values())
    return isinstance(data, (list, tuple)) and any(has_partial(row) for row in data)
