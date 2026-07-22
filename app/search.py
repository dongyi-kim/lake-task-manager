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

from .auth.base import SessionExpired
from .names import real_name

_CAT = {"new": "todo", "indeterminate": "inprogress", "done": "done", "undefined": "todo"}


def _cat(status):
    return _CAT.get(((status.get("statusCategory") or {}).get("key") or "").lower(), "todo")


def _q_escape(q):
    return q.replace("\\", "\\\\").replace('"', '\\"')


from html import escape as _esc


def _clean_excerpt(s):
    """검색 스니펫 → 안전한 강조 HTML.
    Confluence 하이라이트 마커(@@@hl@@@…@@@endhl@@@)를 **검색어 강조**로 살린다.
    평문을 먼저 escape 하고 마커만 <mark> 로 바꾸므로 XSS 안전(프론트는 v-html 로 렌더)."""
    s = (s or "")
    s = re.sub(r"\s+", " ", s).strip()[:220]
    # 자른 뒤 짝이 안 맞는 마커 제거(열림만/닫힘만 남는 경우)
    if s.count("@@@hl@@@") != s.count("@@@endhl@@@"):
        s = s.replace("@@@hl@@@", "").replace("@@@endhl@@@", "")
    parts = s.split("@@@hl@@@")
    out = _esc(parts[0])
    for seg in parts[1:]:
        a, _, b = seg.partition("@@@endhl@@@")
        out += "<mark>" + _esc(a) + "</mark>" + _esc(b)
    return out


# 검색어가 티켓을 직접 가리키는 형태인지 — "DL-1234"(키) 또는 "1234"(번호만).
# 번호만 쓴 경우 검색 대상 프로젝트들의 키를 붙여 후보를 만든다.
_KEY_RE = re.compile(r"^([A-Za-z][A-Za-z0-9_]*)-(\d+)$")
_NUM_RE = re.compile(r"^(\d+)$")


def _exact_keys(s, q):
    m = _KEY_RE.match(q)
    if m:
        return ["%s-%s" % (m.group(1).upper(), m.group(2))]
    m = _NUM_RE.match(q)
    if m:
        projects = list(s.search_jira_projects or []) or [s.project_key]
        return ["%s-%s" % (p, m.group(1)) for p in projects if p]
    return []


def _jira_item(it, base):
    f = it.get("fields", {}) or {}
    st = f.get("status") or {}
    a = f.get("assignee") or {}
    return {
        "type": "jira", "key": it.get("key", ""),
        "title": f.get("summary", ""),
        "status": st.get("name", ""), "statusCategory": _cat(st),
        "assignee": real_name(a.get("displayName") or a.get("name")) or None,
        "issuetype": (f.get("issuetype") or {}).get("name", ""),
        "project": (f.get("project") or {}).get("key", ""),
        "updated": f.get("updated"),
        "url": (base + "/browse/" + it.get("key", "")) if base else "",
    }


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
    # 티켓 키/번호를 그대로 친 경우 그 티켓을 먼저 조회해 맨 앞에 둔다.
    # text~ 검색만으로는 본문에 그 키가 언급된 다른 티켓이 위에 올 수 있다
    # (예: "DL-9001" 이 코멘트에 적힌 DL-9007). 정확히 그 티켓을 찾는 게 의도다.
    exact = []
    for key in _exact_keys(s, q):
        try:
            raw = client.provider.get_json(
                "/rest/api/2/issue/" + key,
                params={"fields": "summary,status,issuetype,assignee,updated,project"})
        except Exception:
            continue                                  # 없는 키 — 조용히 건너뛴다
        if raw and raw.get("key"):
            exact.append(raw)

    jql = 'text ~ "%s"' % _q_escape(q)
    if scope == "scoped" and s.search_jira_projects:
        jql = "project in (%s) AND %s" % (", ".join(s.search_jira_projects), jql)
    jql += " ORDER BY updated DESC"
    data = client.provider.get_json("/rest/api/2/search", params={
        "jql": jql, "fields": "summary,status,issuetype,assignee,updated,project",
        "maxResults": limit})
    base = (s.jira_base or "").rstrip("/")
    items = [dict(_jira_item(it, base), exact=True) for it in exact]
    seen = {x["key"] for x in items}
    for it in data.get("issues", []):
        row = _jira_item(it, base)
        if row["key"] in seen:                        # 정확 일치와 중복 제거
            continue
        seen.add(row["key"])
        items.append(row)
    return {"items": items[:max(limit, len(exact))]}


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
    try:
        data = client.provider.get_json(url, params={"cql": cql, "limit": limit})
    except SessionExpired:
        # SSO 쿠키는 **도메인별** — Jira 세션이 살아 있어도 Confluence 는 따로 인증이 필요하다.
        # 원문 메시지("세션 만료")는 Jira 가 끊긴 것처럼 읽혀 오해를 부르므로 바꿔 준다.
        return {"items": [], "needLogin": True,
                "error": "로그인 필요 — 상단 [SSO 로그인] 을 다시 실행하세요"}
    items = []
    for r in data.get("results", []):
        c = r.get("content") or {}
        webui = r.get("url") or ((c.get("_links") or {}).get("webui") or "")
        sp = c.get("space") or {}
        # 문서 경로: [스페이스, 최상위폴더 … 직계부모] (표시는 프론트가 역순 breadcrumb 로).
        # ancestors 는 Confluence 순서([최상위 … 직계부모])라 그대로 이어 붙인다.
        path = [sp.get("name") or sp.get("key") or ""]
        path += [a.get("title", "") for a in (c.get("ancestors") or []) if a.get("title")]
        items.append({
            "type": "confluence",
            "title": r.get("title") or c.get("title", ""),
            "excerpt": _clean_excerpt(r.get("excerpt", "")),
            "space": sp.get("key", ""),
            "path": [p for p in path if p],     # 예: ["데이터플랫폼","엔지니어링","파이프라인"]
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
