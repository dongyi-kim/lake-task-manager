"""Verified search candidates plus independent, weak disambiguation evidence."""

import hashlib
import json
from urllib.parse import urlsplit, urlunsplit

from app.auth.base import UpstreamUnavailable
from app.domain.names import real_name
from app.infra.settings import module_dir


_UNBOUND = object()


def _id(value):
    return str(value or "").strip().lower()


def _instance(settings):
    parts = urlsplit(settings.jira_base)
    host = (parts.hostname or "").lower()
    host = "[" + host + "]" if ":" in host else host
    port = parts.port
    if port and (parts.scheme.lower(), port) not in {("http", 80), ("https", 443)}:
        host += ":" + str(port)
    base = urlunsplit((parts.scheme.lower(), host, parts.path.rstrip("/"), "", ""))
    return settings.jira_env, base


def _scope(instance, actor):
    return hashlib.sha256(json.dumps([*instance, actor], ensure_ascii=False).encode()).hexdigest()


def _binding(client, settings):
    provider = getattr(client, "_provider", None)
    store = getattr(provider, "_store", None)
    revision = store.revision("jira") if store is not None else None
    return provider, getattr(client, "_provider_generation", None), _instance(settings), revision, client.env


def _current(client, settings, stamp):
    try:
        return stamp is not None and not getattr(client, "_session_dead", False) and _binding(client, settings) == stamp
    except Exception:
        return False


def capture_person_context(client, settings):
    """Bind a read to the provider/session/instance that actually starts it."""
    try:
        provider = client.provider
        stamp = _binding(client, settings)
        return stamp if stamp[0] is provider and _current(client, settings, stamp) else None
    except Exception:
        return None


def _actor(client, settings, expected_stamp=_UNBOUND):
    """Cached Jira identity is optional business evidence for same-module suggestions."""
    try:
        stamp = _binding(client, settings)
        if expected_stamp is not _UNBOUND and stamp != expected_stamp:
            return "", None
        raw = client.cache.get_stale(f"myself:{client.env}") or {}
        actor = _id(raw.get("id") or raw.get("name"))
        return actor, stamp
    except Exception:
        return "", None


def record_person_event(client, settings, user_id, kind, actor_hint=None, *, expected_stamp=_UNBOUND):
    """Local selection history needs no Jira identity; actor_hint is legacy input only."""
    if kind not in {"lookup", "selection", "mention"} or not _id(user_id):
        return False
    if expected_stamp is not _UNBOUND and not _current(client, settings, expected_stamp):
        return False
    try:
        instance = _instance(settings)
        with client._provider_lock:
            if expected_stamp is not _UNBOUND and not _current(client, settings, expected_stamp):
                return False
            client.cache.touch_person_recent(_scope(instance, "local"), _id(user_id), kind)
            return True
    except Exception:
        return False


def _history(client, settings, ids, actor):
    scope = _scope(_instance(settings), "local")
    # Older installations recorded the same operator under their Jira identity. Preserve
    # those rows/timestamps and combine them lazily, without a network identity lookup.
    history = client.cache.person_recent(scope, ids)
    if actor:
        legacy = client.cache.person_recent(_scope(_instance(settings), actor), ids)
        for uid, events in legacy.items():
            target = history.setdefault(uid, {})
            for event, timestamp in events.items():
                target[event] = max(target.get(event, 0), timestamp)
    return history


def search_people(client, settings, query, limit=8):
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 50:
        raise ValueError("limit은 1~50이어야 합니다.")
    operation = capture_person_context(client, settings)
    provider = operation[0] if operation is not None else client.provider
    raw = provider.get_json("/rest/api/2/user/search",
                                  params={"username": (query or "").strip() or ".", "maxResults": limit + 1})
    if not isinstance(raw, list):
        raise UpstreamUnavailable("Jira 사용자 검색 응답 형식이 올바르지 않습니다.")
    users, seen, malformed = [], set(), False
    for row in raw:
        uid = (row.get("name") or row.get("key") or row.get("accountId")) if isinstance(row, dict) else None
        if not isinstance(uid, str) or not uid.strip():
            malformed = True
            continue
        if _id(uid) in seen:
            continue
        seen.add(_id(uid))
        display = row.get("displayName")
        if not isinstance(display, str) or not display.strip():
            display = client.display_name_cached(uid) or uid
        if len(users) < limit:
            users.append({"id": uid, "display": display, "name": real_name(display) or uid,
                          "avatar": "/api/avatar/" + uid})
    if raw and not users:
        raise UpstreamUnavailable("Jira 사용자 검색에서 유효한 식별자를 확인하지 못했습니다.")
    changed = not _current(client, settings, operation)
    actor, stamp = ("", None) if changed else _actor(client, settings, operation)
    try:
        by_user = (None if changed else
                   {_id(uid): set(modules) for uid, modules in module_dir()["byUser"].items()})
    except Exception:
        by_user = None
    history = None
    if not changed:
        try:
            history = _history(client, settings, [_id(u["id"]) for u in users], actor)
        except Exception:
            pass
    changed = changed or not _current(client, settings, operation)
    if changed:
        actor, history, by_user = "", None, None
    actor_modules = by_user.get(actor) if by_user is not None and actor else None
    for user in users:
        uid = _id(user["id"])
        modules = by_user.get(uid) if by_user is not None else None
        recent = (history or {}).get(uid, {})
        user["matchContext"] = {
            "configured": uid in by_user if by_user is not None else None,
            "sameModule": bool(actor_modules & modules) if actor_modules and modules else None,
            "recentLookupAt": recent.get("lookup"), "recentSelectionAt": recent.get("selection"),
            "recentMentionAt": recent.get("mention"),
        }
    more = None if malformed else (True if len(raw) >= limit else (None if raw else False))
    return {"users": users, "returned": len(users), "limit": limit, "mayHaveMore": more,
            "partial": changed or malformed or len(raw) >= limit, "contextChanged": changed,
            "coverage": "bounded", "contextAvailable": {
                "configuration": by_user is not None, "history": history is not None, "actor": bool(actor)}}
