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


def _hl(s, maxlen=220):
    """Confluence 하이라이트 마커(@@@hl@@@…@@@endhl@@@) → <mark> 강조 HTML.
    **title·excerpt 둘 다** 이 마커가 붙어 온다(검색어가 제목에 맞으면 제목에도). 그래서
    제목도 이걸 태워야 raw 마커가 화면에 안 뜬다. 평문 escape 후 마커만 <mark> 로 치환하므로
    XSS 안전(프론트는 해당 필드를 v-html 로 렌더)."""
    s = (s or "")
    s = re.sub(r"\s+", " ", s).strip()[:maxlen]
    # 자른 뒤 짝이 안 맞는 마커 제거(열림만/닫힘만 남는 경우)
    if s.count("@@@hl@@@") != s.count("@@@endhl@@@"):
        s = s.replace("@@@hl@@@", "").replace("@@@endhl@@@", "")
    parts = s.split("@@@hl@@@")
    out = _esc(parts[0])
    for seg in parts[1:]:
        a, _, b = seg.partition("@@@endhl@@@")
        out += "<mark>" + _esc(a) + "</mark>" + _esc(b)
    return out


def _clean_excerpt(s):
    return _hl(s, 220)


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
        "assigneeId": a.get("name"),          # 프로필 사진 조회용(없으면 이니셜로 폴백)
        "issuetype": (f.get("issuetype") or {}).get("name", ""),
        "project": (f.get("project") or {}).get("key", ""),
        "updated": f.get("updated"),
        "url": (base + "/browse/" + it.get("key", "")) if base else "",
    }


def search_all(client, settings, q, scope="scoped", limit=8, only=None):
    """only=['jira'] 처럼 소스를 좁힐 수 있다. 링크 추가 팝업처럼 한 소스만 필요할 때 쓴다
    (prod SSO 는 직렬이라 안 쓰는 소스를 부르면 그만큼 느려진다)."""
    q = (q or "").strip()
    base = {"query": q, "scope": scope,
            "jira": {"items": []}, "confluence": {"items": []}, "bitbucket": {"items": []}}
    if not q:
        return base
    funcs = {
        "jira": lambda: _search_jira(client, settings, q, scope, limit),
        "confluence": lambda: _search_confluence(client, settings, q, scope, limit),
    }
    # Bitbucket 은 **켰을 때만** 검색한다. 안 켰으면 아예 호출하지 않는다 — 안 쓰는 서비스에
    # 검색을 보내면 prod 에선 인증 오류의 소음이 되고, 직렬 큐라 그만큼 느려진다.
    if getattr(settings, "bitbucket_enabled", False):
        funcs["bitbucket"] = lambda: _search_bitbucket(settings, q, limit)
    if only:
        funcs = {k: f for k, f in funcs.items() if k in set(only)}
    out = dict(base)
    if not funcs:
        return out
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
    # 최근 고친 문서가 위로 — Jira 검색(ORDER BY updated DESC)과 같은 기준이라 두 목록이
    # 같은 감각으로 읽힌다. CQL 은 lastModified(=최종 수정) 로 정렬한다.
    cql += " ORDER BY lastModified DESC"
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
            # title 도 하이라이트 마커가 붙어 오므로 _hl 로 정제(raw @@@hl@@@ 방지 + 제목 강조).
            # 프론트는 이 필드를 v-html 로 렌더한다(_hl 이 escape+<mark> 만 내므로 안전).
            "title": _hl(r.get("title") or c.get("title", ""), 200),
            "excerpt": _clean_excerpt(r.get("excerpt", "")),
            "space": sp.get("key", ""),
            "path": [p for p in path if p],     # 예: ["데이터플랫폼","엔지니어링","파이프라인"]
            "id": c.get("id", ""),
            "url": (base + webui) if base and webui.startswith("/") else webui,
        })
    return {"items": items}


_BB_REPOS = ["etl-pipeline", "catalog-service", "query-engine", "platform-infra", "governance-svc"]
_BB_KINDS = [("code", "코드"), ("pullrequest", "PR"), ("repository", "저장소")]


def search_users(client, s, q, limit=8):
    """@사람 멘션 자동완성 — Jira 유저 검색. [{id, name, display, avatar}].
    id 는 사번(username) → 본문에 [~id] 로 직렬화(실 Jira 가 사용자 링크로 렌더).
    name=본명(멘션에 박히는 짧은 이름) / display='{본명} {회사}'(팝업 표시 — 동명이인 구분).
    (문서/웹 링크는 멘션이 아니라 '붙여넣기 시 뱃지'로 처리 — 프론트가 담당.)"""
    q = (q or "").strip()
    try:
        data = client.provider.get_json(
            "/rest/api/2/user/search", params={"username": q or ".", "maxResults": limit})
    except Exception:
        return []
    out = []
    for u in (data or [])[:limit]:
        uid = u.get("name") or u.get("key") or ""
        if not uid:
            continue
        disp = u.get("displayName") or uid
        out.append({"id": uid, "name": real_name(disp) or uid, "display": disp,
                    "avatar": "/api/avatar/" + uid})
    return out


_MENTION_RE = re.compile(r"\[~([^\]]+)\]")


def _ticket_people(client, key):
    """이 티켓에 등장한 사람 uid 목록(리포터·담당·댓글작성·본문/댓글 멘션). 순서 보존, 짧게 캐시.
    (검색 결과 정렬 가중치용 — 매 타건마다 댓글을 다시 긁지 않게 티켓 단위로 캐시한다.)"""
    if not key:
        return []

    def do():
        ids, seen = [], set()

        def add(uid):
            if uid and uid not in seen:
                seen.add(uid); ids.append(uid)
        try:
            f = (client.get_issue(key) or {}).get("fields") or {}
            add((f.get("reporter") or {}).get("name"))
            add((f.get("assignee") or {}).get("name"))
            for uid in _MENTION_RE.findall(f.get("description") or ""):
                add(uid)
        except Exception:
            pass
        try:
            data = client.provider.get_json(
                f"/rest/api/2/issue/{key}/comment", params={"maxResults": 50, "orderBy": "-created"})
            for c in data.get("comments", []):
                add((c.get("author") or {}).get("name"))
                for uid in _MENTION_RE.findall(c.get("body") or ""):
                    add(uid)
        except Exception:
            pass
        return ids
    try:
        return client.cache.get_or_set(f"tpeople:{client.env}:{key}", client.OPTIONS_TTL, do)[0]
    except Exception:
        return do()


def _manager_people_uids(s):
    """매니저(settings.managers) ∪ 등록 인력(people.yaml 전체) — 소문자 uid 집합."""
    from .settings import load_people
    ids = set(s.managers or [])                                    # settings.managers 는 이미 소문자
    try:
        for lst in (load_people() or {}).values():
            for uid in (lst or []):
                if uid:
                    ids.add(str(uid).strip().lower())
    except Exception:
        pass
    return ids


def _mention_rank(client, s, key):
    """사람 검색 결과 정렬 가중치 함수 → uid 를 받아 0·1·2 를 돌려준다(작을수록 위).
      0) 이 티켓에 등장한 사람   1) 매니저 또는 등록 인력   2) 그 외.
    같은 등급 안에서는 호출부가 stable sort 라 기존 Jira 검색 순서가 유지된다."""
    tset = {str(u).strip().lower() for u in _ticket_people(client, key) if u}
    mpset = _manager_people_uids(s)
    def rank(uid):
        u = (uid or "").strip().lower()
        if u in tset:
            return 0
        if u in mpset:
            return 1
        return 2
    return rank


def _module_people(client, s, key):
    """티켓 소속 모듈의 사람들(config 추론). 못 찾으면 [] (요구: 모듈 정보 없으면 무시).
    모듈 신호: (a) Jira 컴포넌트=모듈(아무 티켓이나) (b) WBS config epic→module 역인덱스."""
    from .settings import load_people, load_plan
    try:
        f = (client.get_issue(key) or {}).get("fields") or {}
    except Exception:
        return []
    modules = []
    comps = f.get("components") or []
    if comps and (comps[0] or {}).get("name"):
        modules.append(comps[0]["name"])
    epic = f.get(s.epic_link_field_id)
    if not epic and (f.get("parent") or {}).get("key"):        # Sub-Task → 부모의 Epic
        try:
            pf = (client.get_issue(f["parent"]["key"]) or {}).get("fields") or {}
            epic = pf.get(s.epic_link_field_id) or f["parent"]["key"]
        except Exception:
            epic = None
    if epic:
        try:
            for w in (load_plan().get("wbs") or []):
                if any((e or {}).get("key") == epic for e in (w.get("epics") or [])):
                    if w.get("module") and w["module"] not in modules:
                        modules.append(w["module"])
        except Exception:
            pass
    if not modules:
        return []
    people = load_people() or {}
    ids = []
    for m in modules:
        for uid in (people.get(m) or []):
            if uid and uid not in ids:
                ids.append(uid)
    return ids


def _my_module_people(client, s):
    """현재 사용자와 **같은 모듈** 사람들(people.yaml). 내가 people 에 없으면 [] (요구: 제외)."""
    from .settings import load_people
    me = ((client.current_user() or {}).get("id") or "").strip().lower()
    if not me:
        return []
    people = load_people() or {}
    my_mods = [m for m, ids in people.items()
               if any(str(u).strip().lower() == me for u in (ids or []))]
    if not my_mods:
        return []                                   # 내가 어느 모듈에도 없다 → 이 그룹은 통째로 생략
    out = []
    for m in my_mods:
        for uid in (people.get(m) or []):
            if uid and uid not in out:
                out.append(uid)
    return out


def mention_suggestions(client, s, q, key, limit=8):
    """@사람 멘션 피드. q 있으면 유저 검색(3등급 정렬). 비어 있으면(팝업 첫 오픈) 기본 노출:
       1) 이 티켓 유관자(**티켓 뷰에서 열었을 때만**) 2) 내 모듈 사람(내가 people 에 없으면 생략)
       3) 매니저 — 순서대로 채워 중복 제거."""
    q = (q or "").strip()
    if q:
        # 넉넉히 받아 3등급(티켓 등장인물 > 매니저·등록인력 > 그 외)으로 올린 뒤 limit 만큼 자른다.
        # (Jira 검색 순위로만 자르면 우선순위 사람이 하필 9번째라 잘려 안 보인다.)
        results = search_users(client, s, q, min(max(limit * 4, limit), 40))
        rank = _mention_rank(client, s, key)
        # stable sort 라 같은 등급 안에선 기존 Jira 검색 순서가 유지된다.
        results.sort(key=lambda u: rank(u.get("id")))
        return results[:limit]
    acc, order = {}, []                     # uid -> displayName('{본명} {회사}', or None), 순서 보존

    def add(uid, display=None):
        if uid and uid not in acc:
            acc[uid] = display
            order.append(uid)

    # 1) 이 티켓 유관자 — key 가 있을 때만(= 티켓 뷰에서 연 경우). 일반 사람검색엔 티켓 맥락이 없다.
    if key:
        try:
            f = (client.get_issue(key) or {}).get("fields") or {}
            rep = f.get("reporter") or {}
            asg = f.get("assignee") or {}
            add(rep.get("name"), rep.get("displayName") or rep.get("name"))          # 만든사람
            add(asg.get("name"), asg.get("displayName") or asg.get("name"))          # 담당자
            for uid in _MENTION_RE.findall(f.get("description") or ""):               # 본문 멘션
                add(uid)
        except Exception:
            pass
        try:
            data = client.provider.get_json(
                f"/rest/api/2/issue/{key}/comment", params={"maxResults": 50, "orderBy": "-created"})
            for c in data.get("comments", []):
                a = c.get("author") or {}
                add(a.get("name"), a.get("displayName") or a.get("name"))             # 댓글 작성자
                for uid in _MENTION_RE.findall(c.get("body") or ""):                  # 댓글 멘션
                    add(uid)
        except Exception:
            pass
    # 2) 내 모듈 사람(내가 people 에 있을 때만)  3) 매니저
    try:
        for uid in _my_module_people(client, s):
            add(uid)
    except Exception:
        pass
    for uid in (s.managers or []):
        add(uid)
    if not order:
        return []
    out = []
    for uid in order[:limit]:
        disp = acc[uid] or client._display_name(uid)
        out.append({"id": uid, "name": real_name(disp) or uid, "display": disp,
                    "avatar": "/api/avatar/" + uid})
    return out


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
