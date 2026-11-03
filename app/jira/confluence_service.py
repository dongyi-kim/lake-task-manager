"""Shared Confluence reads for UI search, built-in Agent and local MCP."""

import hashlib
import json

from app.auth.base import PermissionDenied, UpstreamError, UpstreamUnavailable


def cached_read(client, kind, path, params, validate):
    base = (client.s.confluence_base or "").rstrip("/")
    if client.env == "prod" and not base:
        raise ValueError("confluence_base 미설정")
    identity = client._jql_user_context()
    signature = json.dumps([base, path, params], sort_keys=True, ensure_ascii=False)
    key = f"{kind}:{client.env}:{identity}:" + hashlib.sha256(signature.encode()).hexdigest()
    denied = []
    def fetch():
        fence = client.cache.fence()
        try:
            data = client._conf_get_json((base + path) if base else path, params=params)
            if isinstance(data, dict) and data.get("statusCode") in (403, 404):
                raise UpstreamError(data["statusCode"], path)
            if (not validate(data) or isinstance(data, dict)
                    and (data.get("error") or data.get("errors") or data.get("partial") or data.get("complete") is False)):
                raise UpstreamUnavailable("Confluence가 완전한 응답을 반환하지 않았습니다.")
            return data
        except Exception as exc:
            # Jira and Confluence have separate SSO sessions even in the same app.
            # Propagate the failing service so consumers do not kill Jira's healthy session.
            exc.service = "confluence"
            if isinstance(exc, PermissionDenied) or getattr(exc, "status", None) in (403, 404):
                denied.append(exc)
                # A denial from a superseded request cannot erase a newer successful read.
                client.cache.forget_if_fence(key, fence)
            raise
    ttl = getattr(getattr(client.cache, "policy", None), "freshnessSeconds", 600)
    result = client.cache.get_or_set(key, ttl, fetch)[0]
    if denied:
        raise denied[0]  # Older cache implementations must not revive a revoked display copy.
    return result


def search(client, cql, start=0, limit=50):
    # Confluence Search only expands nested content space on request:
    # https://developer.atlassian.com/server/confluence/rest/v924/api-group-search/
    return cached_read(client, "confsearch", "/rest/api/search",
                       {"cql": cql, "start": start, "limit": limit, "expand": "content.space,content.version"},
                       lambda data: isinstance(data, dict) and isinstance(data.get("results"), list)
                       and all(isinstance(row, dict) and ((row.get("content") or {}).get("space") or {}).get("key")
                               for row in data["results"]))


def page(client, cid, expand):
    if not str(cid).isdigit():
        raise ValueError("Confluence 숫자 페이지 ID가 필요합니다.")
    def complete(data):
        if not isinstance(data, dict) or str(data.get("id")) != str(cid):
            return False
        if "body.storage" in expand and not isinstance(((data.get("body") or {}).get("storage") or {}).get("value"), str):
            return False
        if "space" in expand.split(",") and not (data.get("space") or {}).get("key"):
            return False
        return True
    return cached_read(client, "confpage", f"/rest/api/content/{cid}", {"expand": expand}, complete)
