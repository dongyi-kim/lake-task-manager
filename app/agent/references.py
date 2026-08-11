"""Agent 산출물의 ticket/person/document/external 참조를 결정적으로 해석·렌더링한다."""

from __future__ import annotations

import html
import re
from urllib.parse import urlsplit

from app.agent.tools._ctx import (client, jira_key_allowed, search_spaces,
                                  settings)


_TOKEN = re.compile(r"\{\{(ref|mention):([A-Za-z0-9_.:-]+)\}\}")


def _error(ref: dict, message: str) -> dict:
    return {"id": str(ref.get("id") or ""), "kind": str(ref.get("kind") or ""),
            "resolved": False, "error": message}


def _ticket(ref: dict) -> dict:
    key = str(ref.get("key") or ref.get("value") or "").strip().upper()
    if not jira_key_allowed(key):
        return _error(ref, "ticket이 search.jira.projects 범위 밖이거나 검색 범위가 비어 있습니다.")
    badge = client().ticket_badge(key) or {}
    if not badge.get("key") and not badge.get("summary"):
        return _error(ref, f"ticket {key}을 확인하지 못했습니다.")
    base = (getattr(settings(), "jira_base", "") or "").rstrip("/")
    return {"id": str(ref.get("id") or key), "kind": "ticket", "resolved": True,
            "key": key, "label": badge.get("summary") or key,
            "url": f"{base}/browse/{key}" if base else f"/browse/{key}",
            "metadata": {"issueType": badge.get("type"), "status": badge.get("status"),
                         "assignee": badge.get("assignee")}}


def _person(ref: dict) -> dict:
    uid = str(ref.get("user_id") or ref.get("userId") or ref.get("value") or "").strip()
    if not uid:
        return _error(ref, "user_id가 비었습니다.")
    try:
        data = client().provider.get_json("/rest/api/2/user", params={"username": uid}) or {}
    except Exception as exc:
        return _error(ref, f"사용자를 확인하지 못했습니다: {str(exc)[:120]}")
    actual = data.get("name") or data.get("key")
    if not actual:
        return _error(ref, f"사용자 {uid}를 확인하지 못했습니다.")
    return {"id": str(ref.get("id") or uid), "kind": "person", "resolved": True,
            "userId": actual, "label": data.get("displayName") or actual,
            "mention": f"[~{actual}]", "avatarUrl": f"/api/avatar/{actual}"}


def _document(ref: dict) -> dict:
    from app.agent.retrieval.harvest import _conf_id
    raw = str(ref.get("url") or ref.get("page_id") or ref.get("pageId")
              or ref.get("value") or "").strip()
    cid = _conf_id(raw) or (raw if raw.isdigit() else "")
    if not cid:
        return _error(ref, "Confluence page id를 찾지 못했습니다.")
    spaces = search_spaces()
    if not spaces:
        return _error(ref, "검색 범위 미설정 — search.confluence.spaces를 지정하세요")
    data = client().confluence_page(cid, expand="space,version") or {}
    space = ((data.get("space") or {}).get("key") or "").upper()
    if space not in {x.upper() for x in spaces}:
        return _error(ref, "문서가 search.confluence.spaces 범위 밖입니다.")
    links = data.get("_links") or {}
    web = links.get("webui") or ""
    base = (getattr(settings(), "confluence_base", "") or "").rstrip("/")
    url = raw if raw.startswith(("http://", "https://")) else \
        ((base + web) if base and web.startswith("/") else web)
    return {"id": str(ref.get("id") or cid), "kind": "document", "resolved": True,
            "pageId": cid, "space": space, "label": data.get("title") or cid,
            "url": url, "metadata": {"updated": (data.get("version") or {}).get("when")}}


def _external(ref: dict) -> dict:
    url = str(ref.get("url") or ref.get("value") or "").strip()
    try:
        parsed = urlsplit(url)
    except Exception:
        parsed = None
    if not parsed or parsed.scheme not in ("http", "https") or not parsed.netloc:
        return _error(ref, "외부 참조 URL은 http/https 절대 URL이어야 합니다.")
    return {"id": str(ref.get("id") or url), "kind": "external", "resolved": True,
            "label": str(ref.get("label") or parsed.netloc), "url": url}


def resolve_references(refs: list[dict]) -> dict:
    """typed references를 검증하고 canonical label/url/metadata로 해석한다."""
    out, seen = [], set()
    for raw in refs or []:
        ref = raw if isinstance(raw, dict) else {}
        rid = str(ref.get("id") or "").strip()
        if not rid:
            out.append(_error(ref, "reference id가 비었습니다.")); continue
        if rid in seen:
            out.append(_error(ref, f"중복 reference id: {rid}")); continue
        seen.add(rid)
        kind = str(ref.get("kind") or "").strip().lower()
        try:
            item = {"ticket": _ticket, "person": _person, "document": _document,
                    "external": _external}.get(kind, lambda r: _error(r, f"지원하지 않는 kind: {kind}"))(ref)
        except Exception as exc:
            item = _error(ref, str(exc)[:180])
        out.append(item)
    unresolved = [x for x in out if not x.get("resolved")]
    return {"ok": not unresolved, "references": out,
            "unresolved": unresolved, "resolvedCount": len(out) - len(unresolved)}


def render_template(content_template: str, resolved: list[dict]) -> dict:
    """모델의 placeholder template을 escape한 뒤 검증된 링크/mention만 주입한다."""
    by_id = {str(x.get("id")): x for x in resolved or [] if x.get("resolved")}
    source = str(content_template or "")
    pos, chunks, missing = 0, [], []
    for match in _TOKEN.finditer(source):
        chunks.append(html.escape(source[pos:match.start()]))
        token_kind, rid = match.group(1), match.group(2)
        ref = by_id.get(rid)
        if not ref or (token_kind == "mention" and ref.get("kind") != "person"):
            missing.append(rid)
            chunks.append(html.escape(match.group(0)))
        elif token_kind == "mention":
            chunks.append(f'<span class="md-person mention" data-uid="{html.escape(ref["userId"])}">'
                          f'{html.escape(ref["label"])}</span>')
        else:
            css = {"ticket": "jira-badge tkt", "document": "conf-link",
                   "external": "ref-link", "person": "md-person"}.get(ref.get("kind"), "ref-link")
            chunks.append(f'<a class="{css}" href="{html.escape(ref.get("url") or "#")}" '
                          f'target="_blank" rel="noopener">{html.escape(ref.get("label") or rid)}</a>')
        pos = match.end()
    chunks.append(html.escape(source[pos:]))
    return {"ok": not missing, "html": "".join(chunks).replace("\n", "<br>"),
            "missing": sorted(set(missing))}


__all__ = ["resolve_references", "render_template"]
