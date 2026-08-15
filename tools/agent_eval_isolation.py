"""Process and case isolation for manual LTM Agent batteries.

Every suite normally runs in its own Python process.  This module also gives each
process a private SQLite cache and resets mutable singletons before every case, so
case order cannot change retrieval results or timing through a warm/stale cache.
The mock world is fingerprinted before and after each case; a read-only battery that
mutates Jira, comments, documents, or attachments fails immediately.
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import date, datetime
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
RUNTIME_CACHE_ROOT = ROOT / ".cache" / "agent-evaluation" / "runtime-cache"


def configure_process_isolation(suite: str) -> Path:
    """Select a process-private cache before app settings/main are imported."""
    safe_suite = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in suite).strip("-")
    if not safe_suite:
        raise ValueError("evaluation suite name is required")
    RUNTIME_CACHE_ROOT.mkdir(parents=True, exist_ok=True)
    requested = str(os.getenv("LTM_EVAL_CACHE_DB_PATH") or "").strip()
    path = Path(requested) if requested else RUNTIME_CACHE_ROOT / f"{safe_suite}-{os.getpid()}.sqlite3"
    path = path.resolve()
    try:
        path.relative_to((ROOT / ".cache" / "agent-evaluation").resolve())
    except ValueError as exc:
        raise ValueError("LTM_EVAL_CACHE_DB_PATH must stay under .cache/agent-evaluation") from exc
    path.parent.mkdir(parents=True, exist_ok=True)
    os.environ["CACHE_DB_PATH"] = str(path)
    os.environ["LTM_EVAL_CACHE_POLICY"] = "cold-private-cache-each-case"
    os.environ["LTM_EVAL_PROCESS_ISOLATION"] = "separate-process-private-cache"
    return path


def _canonical(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _canonical(value[key]) for key in sorted(value, key=str)}
    if isinstance(value, (list, tuple)):
        return [_canonical(item) for item in value]
    if isinstance(value, set):
        return sorted((_canonical(item) for item in value), key=repr)
    if isinstance(value, bytes):
        return {"bytesSha256": hashlib.sha256(value).hexdigest(), "size": len(value)}
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return {"type": type(value).__name__, "value": str(value)}


def world_sha256() -> str:
    """Hash all mutable mock sources used by Jira/Confluence tools."""
    from app.mock.world import get_world

    world = get_world()
    payload = {
        "today": world.today,
        "counter": getattr(world, "_counter", None),
        "issues": world.issues,
        "confluence": world.confluence,
        "attachments": getattr(world, "attachments", {}),
    }
    body = json.dumps(_canonical(payload), ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"))
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def _provider_store_sha256(client: Any) -> tuple[str, int]:
    """Hash jira820's actual mutable Store without a slow paginated REST scan."""
    provider = client.provider
    store = provider._client.app.state.store
    payload = {
        "issues": store.issues,
        "confluence": store.confluence,
        "attachments": store.attachments,
        "activity": store.activity,
    }
    body = json.dumps(_canonical(payload), ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"))
    return hashlib.sha256(body.encode("utf-8")).hexdigest(), len(store.issues)


def begin_case(case_id: str) -> dict[str, Any]:
    """Reset cache, mock world, approvals, identity, and graph state for one case."""
    from app.agent import approval
    from app.agent.tools import _ctx
    from app.agent.workflow import graph, session
    from app.mock.world import get_world

    graph.reset()
    approval.clear()
    session._IDENTITY_CACHE.update(at=0.0, val=None)
    client = _ctx.client()
    # A previous case must not repopulate the next case's cache from an SWR thread.
    # Cold evaluation runs do not need background refresh: every case starts from a miss.
    client.cache.always_revalidate = ()
    client.cache.revalidator = None
    client.mark_upstream_ok()
    client.cache.last_upstream_ok = 0.0
    client.cache.served_stale_at = 0.0
    # `invalidate()` only clears TTL rows. Evaluation also isolates progress snapshots
    # and recent-item history because those can alter a later agent answer.
    with client.cache._lock:
        client.cache._conn.executescript(
            "DELETE FROM cache; DELETE FROM snapshot; DELETE FROM recent;"
        )
        client.cache._conn.commit()
    get_world.cache_clear()
    # In mock mode the provider owns a jira820 Store copied from World. Clearing only
    # get_world() leaves that Store—and any created ticket—alive. Force lazy rebuild.
    with client._provider_lock:
        previous_provider = client._provider
        if previous_provider is not None:
            try:
                previous_provider._client.close()
            except Exception:
                pass
        client._provider = None
        client._provider_built = False
    before = world_sha256()
    provider_before, provider_count = _provider_store_sha256(client)
    return {
        "caseId": str(case_id),
        "processId": os.getpid(),
        "cachePolicy": "cold-private-cache-each-case",
        "processIsolation": "separate-process-private-cache",
        "backgroundRevalidation": False,
        "worldSha256Before": before,
        "providerStoreSha256Before": provider_before,
        "providerIssueCountBefore": provider_count,
    }


def finish_case(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    """Verify that a supposedly read-only case did not overwrite the mock world."""
    from app.agent.tools import _ctx

    after = world_sha256()
    provider_after, provider_count = _provider_store_sha256(_ctx.client())
    world_unchanged = after == snapshot.get("worldSha256Before")
    provider_unchanged = provider_after == snapshot.get("providerStoreSha256Before")
    result = {
        **dict(snapshot),
        "worldSha256After": after,
        "providerStoreSha256After": provider_after,
        "providerIssueCountAfter": provider_count,
        "worldUnchanged": world_unchanged,
        "providerStoreUnchanged": provider_unchanged,
    }
    if not world_unchanged or not provider_unchanged:
        raise RuntimeError(
            f"evaluation case {snapshot.get('caseId')} mutated mock data: "
            f"world {snapshot.get('worldSha256Before')} -> {after}; provider store "
            f"{snapshot.get('providerStoreSha256Before')} -> {provider_after}"
        )
    return result
