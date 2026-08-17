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
from typing import Any, Callable, Mapping


ROOT = Path(__file__).resolve().parents[1]
RUNTIME_CACHE_ROOT = ROOT / ".cache" / "agent-evaluation" / "runtime-cache"
REAL_LLM_PROVIDERS = frozenset({"openai", "openai_compat", "aoai"})
NETWORK_PREFLIGHT_MARKER = "LTM_EVAL_NETWORK_PREFLIGHTED"
_PREFLIGHT: dict[str, Any] = {"suite": "", "identity": "", "passed": False}


class EvaluationPreflightError(RuntimeError):
    """A real-provider battery cannot start safely in the current process."""

    def __init__(self, category: str, provider: str):
        self.category = str(category or "provider")
        self.provider = str(provider or "unknown")
        super().__init__(
            "evaluation provider preflight failed "
            f"(provider={self.provider}, category={self.category}); no cases started"
        )


def _failure_category(error: BaseException | str) -> str:
    """Classify a provider failure without returning its endpoint, response, or secret."""
    text = str(error or "").casefold()
    if isinstance(error, TimeoutError) or any(
            marker in text for marker in ("timeout", "timed out", "readtimeout")):
        return "timeout"
    if any(marker in text for marker in (
            "401", "403", "unauthorized", "forbidden", "authentication",
            "invalid api key", "incorrect api key", "api-key invalid",
    )):
        return "auth"
    if any(marker in text for marker in (
            "connection", "connecterror", "connect error", "refused", "unreachable",
            "name resolution", "dns", "no route", "network", "ssl", "certificate",
    )):
        return "connection"
    if any(marker in text for marker in (
            "404", "405", "not found", "method not allowed", "not implemented",
            "unsupported",
    )):
        return "unsupported"
    if not text.strip():
        return "empty"
    return "provider"


def _provider_context() -> tuple[str, str]:
    """Return provider plus a non-secret identity for the routed chat endpoints."""
    from app.agent import config

    provider = str(config.provider() or "").strip().lower()
    if provider == "fake":
        return provider, "fake"
    definitions = [config.chat_definition("complex"), config.chat_definition("simple")]
    # The digest invalidates a passed probe when routing changes between cases, while neither
    # endpoint text nor credentials can appear in an exception or raw evaluation artifact.
    identity_payload = [
        {
            "provider": definition.provider,
            "model": definition.model,
            "baseUrl": str(definition.base_url or "").rstrip("/"),
            "apiVersion": definition.api_version,
            "modelProfile": definition.model_profile,
        }
        for definition in definitions
    ]
    encoded = json.dumps(identity_payload, sort_keys=True, separators=(",", ":"))
    return provider, hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _tiny_chat_probe(*, tier: str, timeout: float) -> None:
    """Fallback only for compatible servers that do not implement ``/models``."""
    from app.agent import config

    message = config.get_llm(
        temperature=0,
        tier=tier,
        profile="fast_structured",
        role_id="EvaluationPreflight",
        max_tokens=4,
        timeout=timeout,
    ).invoke("Reply only with OK.")
    if not str(getattr(message, "content", message) or "").strip():
        raise RuntimeError("empty model response")


def _probe_real_provider(provider: str, timeout: float) -> None:
    """Probe endpoint/auth once; spend a tiny model call only when ``/models`` is absent."""
    from app.agent import config

    result = config.list_models(timeout=timeout)
    error = str(result.get("error") or "").strip()
    if error:
        category = _failure_category(error)
        if category != "unsupported":
            raise RuntimeError(error)
        _tiny_chat_probe(tier="complex", timeout=timeout)
        complex_definition = config.chat_definition("complex")
        simple_definition = config.chat_definition("simple")
        split_simple = (
            simple_definition.provider != complex_definition.provider
            or str(simple_definition.base_url or "").rstrip("/")
            != str(complex_definition.base_url or "").rstrip("/")
            or simple_definition.model != complex_definition.model
        )
        if split_simple:
            _tiny_chat_probe(tier="simple", timeout=timeout)
        return

    # A separately routed simple endpoint is used by the same graph. Its catalog warning must
    # not be ignored; unsupported /models gets the same bounded chat fallback. Embedding model
    # catalogs are intentionally excluded because TEI commonly omits /models and this guard is
    # for the chat-provider battery path.
    simple_warnings = [
        str(warning).split(":", 1)[-1].strip()
        for warning in (result.get("warnings") or [])
        if str(warning).startswith("simple model catalog:")
    ]
    for warning in simple_warnings:
        category = _failure_category(warning)
        if category != "unsupported":
            raise RuntimeError(warning)
        _tiny_chat_probe(tier="simple", timeout=timeout)


def preflight_evaluation_provider(
    *, timeout: float | None = None,
    probe: Callable[[str, float], None] | None = None,
) -> dict[str, Any]:
    """Verify a real LLM route exactly once before any evaluation case graph starts.

    This is deliberately process-local and is called by :func:`begin_case`, not at module
    import. Therefore importing a battery remains network-free, while calling a case directly
    cannot bypass the guard. Provider response bodies, URLs, and credentials are never returned.
    """
    if not _PREFLIGHT["suite"]:
        # Unit graph tests legitimately use the deterministic fake without a battery launcher.
        provider, _ = _provider_context()
        if provider == "fake":
            return {"provider": "fake", "status": "skipped"}
        raise EvaluationPreflightError("isolation", provider)

    try:
        provider, identity = _provider_context()
    except Exception:
        raise EvaluationPreflightError("configuration", "unknown") from None
    if provider == "fake":
        _PREFLIGHT.update(identity=identity, passed=True)
        return {"provider": provider, "status": "skipped"}
    if provider not in REAL_LLM_PROVIDERS:
        raise EvaluationPreflightError("configuration", provider)
    # The runner itself may be executing in a network-denied sandbox. Never turn a direct
    # invocation into another misleading connection failure: only the explicit local launcher
    # may hand off this marker after its own endpoint check succeeds.
    if os.getenv(NETWORK_PREFLIGHT_MARKER) != "1":
        raise EvaluationPreflightError("network-authorization", provider)
    if _PREFLIGHT["passed"] and _PREFLIGHT["identity"] == identity:
        return {"provider": provider, "status": "cached"}

    raw_timeout = timeout if timeout is not None else os.getenv("LTM_EVAL_PREFLIGHT_TIMEOUT", "10")
    try:
        bounded_timeout = max(1.0, min(float(raw_timeout), 60.0))
    except (TypeError, ValueError):
        raise EvaluationPreflightError("configuration", provider) from None
    try:
        (probe or _probe_real_provider)(provider, bounded_timeout)
    except Exception as exc:
        raise EvaluationPreflightError(_failure_category(exc), provider) from None
    _PREFLIGHT.update(identity=identity, passed=True)
    return {"provider": provider, "status": "passed"}


def configure_process_isolation(suite: str) -> Path:
    """Select a process-private cache before app settings/main are imported."""
    safe_suite = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in suite).strip("-")
    if not safe_suite:
        raise ValueError("evaluation suite name is required")
    _PREFLIGHT.update(suite=safe_suite, identity="", passed=False)
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
    # This call is intentionally before all graph imports/resets. A connection, authentication,
    # or timeout failure aborts the whole manual run instead of being misreported as N bad cases.
    provider_preflight = preflight_evaluation_provider()
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
        "providerPreflight": provider_preflight,
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
