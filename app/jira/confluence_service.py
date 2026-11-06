"""Shared Confluence reads. Published heads and immutable source revisions have separate ages."""

import copy
import hashlib
import json
import math
import re
import time
from urllib.parse import parse_qs, urlsplit

from app.auth.base import PermissionDenied, UpstreamError, UpstreamUnavailable


def scope_key(client):
    return hashlib.sha256(json.dumps([client.env, (client.s.confluence_base or "").rstrip("/")]).encode()).hexdigest()[:24]


def resolve_page(client, value):
    identifier = str(value).strip()
    if identifier.isdigit():
        return identifier
    url, base = urlsplit(identifier), urlsplit(client.s.confluence_base or "")
    if (url.scheme not in ("http", "https") or url.netloc != base.netloc or url.username or url.password
            or not url.path.startswith(base.path.rstrip("/") + "/")):
        raise ValueError("설정된 Confluence의 페이지 URL 또는 숫자 ID를 사용하세요.")
    match = re.search(r"/pages/([0-9]+)(?:/|$)", url.path)
    identifier = match.group(1) if match else parse_qs(url.query).get("pageId", [""])[0]
    if not identifier.isdigit():
        raise ValueError("URL에 페이지 ID가 없습니다. 제목 검색으로 문서 ID를 찾으세요.")
    return identifier


def raw_read(client, path, params):
    base = (client.s.confluence_base or "").rstrip("/")
    if client.env == "prod" and not base:
        raise ValueError("confluence_base 미설정")
    try:
        data = client._conf_get_json((base + path) if base else path, params=params)
        if isinstance(data, dict) and data.get("statusCode") in (403, 404):
            raise UpstreamError(data["statusCode"], path)
        if (not isinstance(data, dict) or data.get("error") or data.get("errors")
                or data.get("partial") or data.get("complete") is False):
            raise UpstreamUnavailable("Confluence가 완전한 응답을 반환하지 않았습니다.")
        return data
    except Exception as exc:
        exc.service = "confluence"
        raise


def _denied(exc):
    return isinstance(exc, PermissionDenied) or getattr(exc, "status", None) in (403, 404)


def _page_prefixes(client, cid, bodies=True):
    scope = scope_key(client)
    kinds = ["confhead", "confview", "confrelated"] + (["confbody"] if bodies else [])
    return [f"{kind}:{scope}:{cid}:" for kind in kinds]


def invalidate(client, ids, *, remove_bodies=False):
    """Writes invalidate mutable data; permission loss/trash also removes old source copies."""
    scope = scope_key(client)
    prefixes = [f"confsearch:{scope}:", f"conftree:{scope}:"]
    for cid in ids:
        prefixes.extend(_page_prefixes(client, str(cid), bodies=remove_bodies))
    client.cache.forget_prefixes(prefixes)


def cached_read(client, kind, path, params, validate, *, key=None, force=False, ttl=None, denied_prefixes=None, resource_id=None):
    signature = json.dumps([path, params], sort_keys=True, ensure_ascii=False)
    key = key or f"{kind}:{scope_key(client)}:" + hashlib.sha256(signature.encode()).hexdigest()
    denied = []
    def fetch():
        fence = client.cache.fence()
        try:
            data = raw_read(client, path, params)
            if not validate(data):
                raise UpstreamUnavailable("Confluence가 완전한 응답을 반환하지 않았습니다.")
            if kind == "confhead":
                previous = client.cache.get_stale(key)
                if previous and (previous.get("version") or {}).get("number", 0) > (data.get("version") or {}).get("number", 0):
                    raise UpstreamUnavailable("Confluence 최신 버전 응답이 이전 버전으로 되돌아갔습니다.")
            return data
        except Exception as exc:
            exc.service = "confluence"
            if _denied(exc):
                denied.append(exc)
                if resource_id and (isinstance(exc, PermissionDenied) or getattr(exc, "status", None) == 403):
                    client.cache.forget_prefixes(_page_prefixes(client, resource_id), fence=fence)
                elif denied_prefixes:
                    client.cache.forget_prefixes(denied_prefixes, fence=fence)
                else:
                    client.cache.forget_if_fence(key, fence)
            raise
    ttl = ttl or getattr(getattr(client.cache, "policy", None), "freshnessSeconds", 600)
    # On Windows multiple calls can share one clock tick. Require a strictly later
    # timestamp so an explicit check cannot accidentally return that tick's cached hit.
    result = (client.cache.refresh(key, ttl, fetch, min_fetched_at=math.nextafter(time.time(), math.inf)) if force
              else client.cache.get_or_set(key, ttl, fetch)[0])
    if denied:
        raise denied[0]
    return result


def search(client, cql, start=0, limit=50):
    _pagination(start, limit)
    return cached_read(client, "confsearch", "/rest/api/search",
                       {"cql": cql, "start": start, "limit": limit, "expand": "content.space,content.version,content.ancestors"},
                       lambda data: isinstance(data.get("results"), list)
                       and all(isinstance(row, dict) and ((row.get("content") or {}).get("space") or {}).get("key")
                               for row in data["results"]))


def _valid_page(data, cid):
    return (str(data.get("id")) == cid and bool((data.get("space") or {}).get("key"))
            and isinstance((data.get("version") or {}).get("number"), int)
            and data["version"]["number"] > 0)


def head(client, cid, force=False):
    cid = resolve_page(client, cid)
    return cached_read(client, "confhead", f"/rest/api/content/{cid}",
                       {"status": "current", "expand": "version,space,ancestors"},
                       lambda data: _valid_page(data, cid), key=f"confhead:{scope_key(client)}:{cid}:current",
                       force=force, denied_prefixes=_page_prefixes(client, cid))


def check_revision(client, cid, known_version=None):
    cid = resolve_page(client, cid)
    if known_version is not None and (isinstance(known_version, bool) or known_version < 1):
        raise ValueError("known_version은 1 이상의 정수여야 합니다.")
    data = head(client, cid, force=True)
    version = data["version"]
    key = f"confhead:{scope_key(client)}:{cid}:current"
    source = client.cache.metadata(key) or {}
    stored = client.cache.get_stale(key) or {}
    verified = source.get("state") == "fresh" and (stored.get("version") or {}).get("number") == version["number"]
    return {"pageId": cid, "latestPublishedVersion": version["number"],
            "lastModifiedAt": version.get("when"), "lastModifiedBy": version.get("by"),
            "checkedAt": source.get("fetchedAt") if verified else None,
            "isLatest": None if known_version is None or not verified else known_version == version["number"],
            "draftState": "unknown"}


def body(client, cid, version=None, representation="storage"):
    cid = resolve_page(client, cid)
    if representation not in ("storage", "view"):
        raise ValueError("본문 형식은 storage 또는 view여야 합니다.")
    current = head(client, cid)
    version = current["version"]["number"] if version is None else version
    if isinstance(version, bool) or not isinstance(version, int) or version < 1:
        raise ValueError("version은 1 이상의 정수여야 합니다.")
    kind = "confbody" if representation == "storage" else "confview"
    key = f"{kind}:{scope_key(client)}:{cid}:{version}:{representation}:1"
    ttl = getattr(getattr(client.cache, "policy", None), "confluenceBodyRetentionSeconds", 30 * 86400) if kind == "confbody" else None
    def validate(data):
        return (_valid_page(data, cid) and data["version"]["number"] == version
                and isinstance(((data.get("body") or {}).get(representation) or {}).get("value"), str))
    result = cached_read(client, kind, f"/rest/api/content/{cid}",
                         {"version": version, "expand": f"body.{representation},space,version"},
                         validate, key=key, ttl=ttl, resource_id=cid)
    result = copy.deepcopy(result)
    source = client.cache.metadata(key) or {}
    current_source = client.cache.metadata(f"confhead:{scope_key(client)}:{cid}:current") or {}
    result["cacheInfo"] = {"bodyVersion": version, "bodyFetchedAt": source.get("fetchedAt"),
                           "latestPublishedVersion": current["version"]["number"],
                           "headCheckedAt": current_source.get("fetchedAt"),
                           "isLatest": version == current["version"]["number"] if current_source.get("state") == "fresh" else None}
    return result


def page(client, cid, expand):
    """Compatibility wrapper for existing LTM title, body and Agent consumers."""
    if "body.storage" in expand:
        return body(client, cid)
    if "body.view" in expand:
        return body(client, cid, representation="view")
    return head(client, cid)


def _pagination(start, limit):
    if start < 0 or not 1 <= limit <= 100:
        raise ValueError("start는 0 이상, limit은 1~100이어야 합니다.")


def _rows(data):
    return isinstance(data.get("results"), list) and all(isinstance(row, dict) for row in data["results"])


def list_spaces(client, start=0, limit=50):
    _pagination(start, limit)
    return cached_read(client, "conftree", "/rest/api/space", {"start": start, "limit": limit}, _rows)


def list_pages(client, space_key="", parent_id="", start=0, limit=50):
    _pagination(start, limit)
    params = {"start": start, "limit": limit, "expand": "space,version,ancestors"}
    if parent_id:
        parent_id = resolve_page(client, parent_id)
        parent = head(client, parent_id)
        if space_key and parent["space"]["key"] != space_key:
            raise ValueError("상위 문서가 요청한 Space에 속하지 않습니다.")
        path = f"/rest/api/content/{parent_id}/child/page"
    else:
        from urllib.parse import quote
        if not space_key:
            raise ValueError("루트 문서 조회에는 space_key가 필요합니다.")
        path = f"/rest/api/space/{quote(space_key, safe='')}/content/page"
        params["depth"] = "root"
    return cached_read(client, "conftree", path, params, _rows)


def ancestors(client, cid):
    return head(client, cid).get("ancestors") or []


def list_related(client, cid, kind, start=0, limit=50):
    _pagination(start, limit)
    cid = resolve_page(client, cid)
    head(client, cid)
    paths = {"comment": "child/comment", "attachment": "child/attachment", "label": "label",
             "restriction": "restriction/byOperation"}
    if kind not in paths:
        raise ValueError("지원하지 않는 Confluence 부속 정보 종류입니다.")
    params = {"start": start, "limit": limit}
    if kind in ("comment", "attachment"):
        params["expand"] = "version,body.storage" if kind == "comment" else "version"
    signature = hashlib.sha256(json.dumps(params, sort_keys=True).encode()).hexdigest()[:16]
    return cached_read(client, "confrelated", f"/rest/api/content/{cid}/{paths[kind]}", params,
                       (lambda data: isinstance(data, dict)) if kind == "restriction" else _rows,
                       key=f"confrelated:{scope_key(client)}:{cid}:{kind}:{signature}")
