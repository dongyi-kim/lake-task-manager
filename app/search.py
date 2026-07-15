"""통합 검색 — Jira(JQL text~) + Confluence(CQL) + Bitbucket(mock) 를 병렬 fan-out 해
정규화된 결과로 반환. 각 소스는 실패해도 다른 소스 결과에 영향 주지 않는다(소스별 error).

인증/환경:
- Jira/Confluence 는 client.provider(SSO/basic/in-process) 경유.
- Confluence 는 prod 에서 별도 호스트(confluence_base) → 절대 URL. mock/local 은 jira820 이 같은
  호스트로 CQL 을 서빙하므로 상대 경로.
- Bitbucket 은 당장 mock (사내 버전 확인 전) — 결정적 가짜 결과 + mock 플래그.
"""

import hashlib
import re
from concurrent.futures import ThreadPoolExecutor

from .names import real_name

_CAT = {"new": "todo", "indeterminate": "inprogress", "done": "done", "undefined": "todo"}


def _cat(status):
    return _CAT.get(((status.get("statusCategory") or {}).get("key") or "").lower(), "todo")


def _q_escape(q):
    return q.replace("\\", "\\\\").replace('"', '\\"')


def _clean_excerpt(s):
    s = re.sub(r"@@@(?:end)?hl@@@", "", s or "")          # Confluence 하이라이트 마커 제거
    s = re.sub(r"\s+", " ", s).strip()
    return s[:200]


def search_all(client, settings, q, scope="scoped", limit=8):
    q = (q or "").strip()
    base = {"query": q, "scope": scope,
            "jira": {"items": []}, "confluence": {"items": []}, "bitbucket": {"items": []}}
    if not q:
        return base
    funcs = {
        "jira": lambda: _search_jira(client, settings, q, scope, limit),
        "confluence": lambda: _search_confluence(client, settings, q, scope, limit),
        "bitbucket": lambda: _search_bitbucket(settings, q, limit),
    }
    out = dict(base)
    with ThreadPoolExecutor(max_workers=3) as ex:
        futs = {k: ex.submit(f) for k, f in funcs.items()}
        for k, fut in futs.items():
            try:
                out[k] = fut.result()
            except Exception as e:
                out[k] = {"items": [], "error": str(e)}
    return out


def _search_jira(client, s, q, scope, limit):
    jql = 'text ~ "%s"' % _q_escape(q)
    if scope == "scoped" and s.search_jira_projects:
        jql = "project in (%s) AND %s" % (", ".join(s.search_jira_projects), jql)
    jql += " ORDER BY updated DESC"
    data = client.provider.get_json("/rest/api/2/search", params={
        "jql": jql, "fields": "summary,status,issuetype,assignee,updated,project",
        "maxResults": limit})
    base = (s.jira_base or "").rstrip("/")
    items = []
    for it in data.get("issues", []):
        f = it.get("fields", {}) or {}
        st = f.get("status") or {}
        a = f.get("assignee") or {}
        items.append({
            "type": "jira", "key": it.get("key", ""),
            "title": f.get("summary", ""),
            "status": st.get("name", ""), "statusCategory": _cat(st),
            "assignee": real_name(a.get("displayName") or a.get("name")) or None,
            "issuetype": (f.get("issuetype") or {}).get("name", ""),
            "project": (f.get("project") or {}).get("key", ""),
            "updated": f.get("updated"),
            "url": (base + "/browse/" + it.get("key", "")) if base else "",
        })
    return {"items": items}


def _search_confluence(client, s, q, scope, limit):
    base = (s.confluence_base or "").rstrip("/")
    if s.jira_env == "prod" and not base:
        return {"items": [], "error": "confluence_base 미설정"}
    cql = 'siteSearch ~ "%s"' % _q_escape(q)
    if scope == "scoped" and s.search_confluence_spaces:
        joined = ", ".join('"%s"' % x for x in s.search_confluence_spaces)
        cql = "space in (%s) AND %s" % (joined, cql)
    # prod: 별도 호스트 절대 URL / mock·local: jira820 이 같은 호스트로 서빙 → 상대 경로
    url = (base + "/rest/api/search") if (s.jira_env == "prod" and base) else "/rest/api/search"
    data = client.provider.get_json(url, params={"cql": cql, "limit": limit})
    items = []
    for r in data.get("results", []):
        c = r.get("content") or {}
        webui = r.get("url") or ((c.get("_links") or {}).get("webui") or "")
        items.append({
            "type": "confluence",
            "title": r.get("title") or c.get("title", ""),
            "excerpt": _clean_excerpt(r.get("excerpt", "")),
            "space": (c.get("space") or {}).get("key", ""),
            "id": c.get("id", ""),
            "url": (base + webui) if base and webui.startswith("/") else webui,
        })
    return {"items": items}


_BB_REPOS = ["etl-pipeline", "catalog-service", "query-engine", "platform-infra", "governance-svc"]
_BB_KINDS = [("code", "코드"), ("pullrequest", "PR"), ("repository", "저장소")]


def _search_bitbucket(s, q, limit):
    """mock — 사내 Bitbucket 버전/연동 확인 전까지 결정적 가짜 결과. 여러 project 지원."""
    projs = s.search_bitbucket_projects or ["DATA"]
    seed = int(hashlib.md5(q.encode("utf-8")).hexdigest()[:6], 16)
    items = []
    for i in range(min(max(limit, 1), 3)):
        proj = projs[(seed + i) % len(projs)]
        repo = _BB_REPOS[(seed + i) % len(_BB_REPOS)]
        kind, klabel = _BB_KINDS[i % len(_BB_KINDS)]
        items.append({
            "type": "bitbucket", "kind": kind, "kindLabel": klabel,
            "title": ("%s — %s" % (q, klabel)),
            "repo": "%s/%s" % (proj, repo),
            "path": ("src/%s/main.py" % repo.replace("-", "_")) if kind == "code" else "",
            "excerpt": "…%s… (Bitbucket 연동 예정)" % q,
            "url": "", "mock": True,
        })
    return {"items": items, "mock": True}
