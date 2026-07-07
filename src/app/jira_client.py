"""
JiraClient — AuthProvider 를 주입받아 REST 호출 (어떤 인증인지 모름).
캐시 경유. mock 모드는 HTTP 없이 mockdata 로 동일 형태를 반환.

Phase A 범위: Epic 자식 SP 롤업. (기능2·3 의 검색/활동은 후속 Phase 에서 확장)
"""

from . import mockdata, progress


# 실 Jira DC statusCategory.key → 내부 vocab (new=todo, indeterminate=inprogress, done=done)
_CAT_MAP = {"new": "todo", "indeterminate": "inprogress", "done": "done", "undefined": "todo"}


def _norm_cat(key):
    return _CAT_MAP.get((key or "").lower(), "todo")


def _normalize_issue(raw, sp_field):
    f = raw.get("fields", {}) or {}
    status = (f.get("status") or {})
    cat = _norm_cat((status.get("statusCategory") or {}).get("key"))
    return {
        "key": raw.get("key"),
        "type": (f.get("issuetype") or {}).get("name", ""),
        "sp": f.get(sp_field),          # None 이면 누락 → progress.sp_of 가 기본값 적용
        "statusCategory": cat,          # todo | inprogress | done
        "labels": f.get("labels", []) or [],
        "assignee": ((f.get("assignee") or {}).get("name")),
        "updated": f.get("updated"),
    }


class JiraClient:
    def __init__(self, settings, cache):
        self.s = settings
        self.cache = cache
        self.env = settings.jira_env
        self.provider = self._make_provider()

    def _make_provider(self):
        if self.env == "local":
            from .auth.basic import BasicAuthProvider
            return BasicAuthProvider(self.s.jira_base, self.s.jira_user,
                                     self.s.jira_token, self.s.jira_auth)
        if self.env == "prod":
            from .auth.sso_session import SsoSessionProvider
            return SsoSessionProvider(self.s.jira_base, self.s.jira_state_path)
        return None   # mock

    def _resolve(self, epic_key):
        """epic 키 그대로 (wbs_config 가 실 티켓 DL-xxxx 사용 — 논리 id 매핑 없음)."""
        return epic_key

    # ── 티켓 단위 캐시 레이어 (모든 이슈/하위이슈를 key 단위로 캐싱) ──
    def _issue_fields(self):
        return ("summary,description,issuetype,status,assignee,reporter,components,created,duedate,"
                "resolutiondate,updated,labels,parent,subtasks,"
                + self.s.sp_field_id + "," + self.s.epic_link_field_id)

    def get_issue(self, key):
        """단일 티켓 원본(fields 포함) — `issue:{env}:{key}` 로 티켓 단위 캐시."""
        ck = f"issue:{self.env}:{key}"
        data, _ = self.cache.get_or_set(
            ck, self.s.cache_ttl_seconds,
            lambda: self.provider.get_json(f"/rest/api/2/issue/{key}",
                                           params={"fields": self._issue_fields()}))
        return data

    def _search(self, jql, cache_key=None, max_results=200):
        """JQL 검색 → 원본 이슈 리스트. 결과를 티켓 단위 캐시에 write-through.
        (검색 자체도 cache_key 주면 캐시 — 같은 목록 재조회 절약)."""
        def do():
            issues, start = [], 0
            while True:
                data = self.provider.get_json("/rest/api/2/search", params={
                    "jql": jql, "fields": self._issue_fields(),
                    "startAt": start, "maxResults": 100})
                batch = data.get("issues", [])
                issues.extend(batch)
                start += 100
                if start >= data.get("total", 0) or not batch or start >= max_results:
                    break
            return issues
        issues = self.cache.get_or_set(cache_key, self.s.cache_ttl_seconds, do)[0] if cache_key else do()
        for it in issues:                       # write-through: 각 티켓 개별 캐시
            if it.get("key"):
                self.cache.set(f"issue:{self.env}:{it['key']}", it, self.s.cache_ttl_seconds)
        return issues

    # ── Epic 자식 조회 ──
    def epic_issues(self, epic_key):
        if self.env == "mock":
            return mockdata.epic_issues(epic_key)
        real = self._resolve(epic_key)
        cache_key = f"epic_issues:{self.env}:{real}"
        data, _hit = self.cache.get_or_set(
            cache_key, self.s.cache_ttl_seconds, lambda: self._fetch_epic_children(real)
        )
        return data

    def _fetch_epic_children(self, epic_key):
        """Epic 자식 조회 → 정규화. 검색 결과는 티켓 단위 캐시로 write-through."""
        sp = self.s.sp_field_id
        raw = self._search(f'"Epic Link" = {epic_key}',
                           cache_key=f"epic_children:{self.env}:{epic_key}")
        if not raw:
            try:  # 폴백: Agile API
                data = self.provider.get_json(
                    f"/rest/agile/1.0/epic/{epic_key}/issue",
                    params={"fields": self._issue_fields(), "maxResults": 100})
                raw = data.get("issues", [])
                for it in raw:
                    if it.get("key"):
                        self.cache.set(f"issue:{self.env}:{it['key']}", it, self.s.cache_ttl_seconds)
            except Exception:
                pass
        return [_normalize_issue(it, sp) for it in raw]

    def epic_name(self, epic_key):
        """Epic 이름(summary) — Jira/world 에서. (config 엔 이름 없음)"""
        if self.env == "mock":
            return mockdata.epic_name(epic_key)
        try:
            it = self.get_issue(self._resolve(epic_key))
            return (it.get("fields") or {}).get("summary") or epic_key
        except Exception:
            return epic_key

    def epic_progress_map(self, plan):
        """plan 의 모든 Epic 에 대해 진척률 dict 반환 {key -> {...,name}}."""
        out = {}
        for w in plan["wbs"]:
            for e in w["epics"]:
                k = e["key"]
                if k in out:
                    continue
                pr = progress.epic_progress(self.epic_issues(k))
                pr["name"] = self.epic_name(k)
                out[k] = pr
        return out

    # ── 기능2: PMO_VIT 현안 ──
    # 진척 = Root 현안의 자손 티켓 "개수 기반"(done/total). 시작·마감·소식·코멘트 포함.
    # (local/prod 경로는 라이브 Jira 대상 Phase B 에서 검증/심화 — 자손 깊이 등)
    def vit_issues(self, plan, people, epic_prog=None):
        if self.env == "mock":
            return mockdata.vit_issues(plan, people, epic_prog)
        return self._fetch_vit()   # 내부에서 목록·루트 티켓 "단위로" 캐시

    def _fetch_vit(self):
        # 1) PMO_VIT 목록 — 검색 결과를 티켓 단위 캐시로 write-through
        roots = self._search(
            f'project={self.s.project_key} AND labels="PMO_VIT" ORDER BY updated DESC',
            cache_key=f"vit_list:{self.env}")
        out = []
        for it in roots:
            base = self._vit_base(it)
            base["tree"] = self._vit_tree(base["key"], base["type"])   # 자손 = 티켓 단위 캐시
            base["comments"] = self._issue_comments(base["key"])       # 코멘트 = 티켓 단위 캐시
            out.append(base)
        return out

    def _vit_base(self, issue):
        f = issue.get("fields", {}) or {}
        comps = f.get("components") or []
        status = f.get("status") or {}
        assignee = f.get("assignee") or {}
        ancestors = []
        if f.get("parent"):
            ancestors.append(f["parent"].get("key"))
        if f.get(self.s.epic_link_field_id):
            ancestors.append(f[self.s.epic_link_field_id])
        return {
            "key": issue.get("key", ""), "summary": f.get("summary", ""),
            "type": (f.get("issuetype") or {}).get("name", ""),
            "module": comps[0]["name"] if comps else "Module 미지정",
            "assignee": assignee.get("displayName") or assignee.get("name"),
            "start": (f.get("created") or "")[:10] or None,
            "due": f.get("duedate"),
            "statusCategory": _norm_cat((status.get("statusCategory") or {}).get("key")),
            "status": status.get("name", ""),
            "ancestors": [a for a in ancestors if a],
        }

    def _node_from_issue(self, issue):
        f = issue.get("fields", {}) or {}
        node = {
            "key": issue.get("key", ""),
            "summary": f.get("summary", ""),
            "type": (f.get("issuetype") or {}).get("name", ""),
            "statusCategory": _norm_cat(((f.get("status") or {}).get("statusCategory") or {}).get("key")),
            "status": ((f.get("status") or {}).get("name") or ""),
            "created": (f.get("created") or "")[:10] or None,
            "resolved": (f.get("resolutiondate") or "")[:10] or None,
            "children": [],
        }
        # 하위(Sub-task)도 개별 티켓으로 조회 → 티켓 단위 캐시
        for s in (f.get("subtasks") or []):
            skey = s.get("key")
            if not skey:
                continue
            try:
                node["children"].append(self._node_from_issue(self.get_issue(skey)))
            except Exception:
                pass
        return node

    def _vit_tree(self, key, itype):
        """Root 자손 트리 — 모든 노드를 get_issue/_search(티켓 단위 캐시)로 조회."""
        try:
            if itype == "Epic":
                children = self._search(f'"Epic Link" = {key}',
                                        cache_key=f"epic_children:{self.env}:{key}")
                return [self._node_from_issue(c) for c in children]
            root = self.get_issue(key)
            subs = ((root.get("fields") or {}).get("subtasks")) or []
            return [self._node_from_issue(self.get_issue(s["key"])) for s in subs if s.get("key")]
        except Exception:
            return []

    def _issue_comments(self, key, limit=5):
        """코멘트 — `comments:{env}:{key}` 로 티켓 단위 캐시."""
        def do():
            data = self.provider.get_json(f"/rest/api/2/issue/{key}/comment",
                                          params={"maxResults": limit, "orderBy": "-created"})
            return [{
                "date": (c.get("created") or "")[:10],
                "author": ((c.get("author") or {}).get("displayName") or (c.get("author") or {}).get("name")),
                "text": (c.get("body") or "")[:200],
            } for c in data.get("comments", [])[:limit]]
        return self.cache.get_or_set(f"comments:{self.env}:{key}", self.s.cache_ttl_seconds, do)[0]

    # ── 기능3: 인력 워크로드 / 활동 ──
    def workload(self, plan, people):
        if self.env == "mock":
            return mockdata.workload_people(plan, people)
        return self._fetch_workload(plan, people)

    def _fetch_workload(self, plan, people):
        """인력별 Task성/VoC성 × 진행중/최근7일완료 티켓 수."""
        def counts(jql):
            by = {"task": 0, "voc": 0}
            try:
                for it in self._search(jql, max_results=300):   # write-through: 각 티켓 캐시
                    f = it.get("fields", {}) or {}
                    comps = [c.get("name") for c in (f.get("components") or [])]
                    comp = "VoC" if "VoC" in comps else (comps[0] if comps else "")
                    t = (f.get("issuetype") or {}).get("name", "")
                    c = mockdata.wl_category(comp, t)
                    if c:
                        by[c] += 1
            except Exception:
                pass
            return by
        out = {}
        for module in plan["modules"]:
            rows = []
            for pid in people.get(module, []):
                key = f'workload:{self.env}:{pid}'
                bundle, _ = self.cache.get_or_set(key, self.s.cache_ttl_seconds, lambda pid=pid: {
                    "id": pid,
                    "inProgress": counts(f'assignee = "{pid}" AND statusCategory = "In Progress"'),
                    "done7d": counts(f'assignee = "{pid}" AND statusCategory = Done AND resolved >= -7d'),
                })
                rows.append(bundle)
            out[module] = rows
        return out

    def activity(self, user):
        if self.env == "mock":
            return mockdata.activity(user)
        key = f"activity:{self.env}:{user}"
        data, _ = self.cache.get_or_set(key, self.s.cache_ttl_seconds, lambda: self._fetch_activity(user))
        return data

    def _fetch_activity(self, user):
        """최근 Jira(/activity ATOM 파싱) + Confluence(CQL) 활동."""
        return {"user": user, "jira": self._parse_activity(user),
                "confluence": self._fetch_confluence(user)}

    def _parse_activity(self, user, limit=20):
        import re as _re
        import xml.etree.ElementTree as ET
        ns = "{http://www.w3.org/2005/Atom}"
        out = []
        try:
            xml = self.provider.get_text("/activity",
                                         params={"maxResults": limit, "streams": f"user IS {user}"})
            root = ET.fromstring(xml)
            for e in root.findall(f"{ns}entry"):
                title = (e.findtext(f"{ns}title") or "").strip()
                cat = e.find(f"{ns}category")
                key = ""
                for ln in e.findall(f"{ns}link"):
                    if ln.get("rel") == "alternate":
                        m = _re.search(r"/browse/([A-Z0-9-]+)", ln.get("href") or "")
                        if m:
                            key = m.group(1)
                out.append({
                    "date": (e.findtext(f"{ns}updated") or "")[:10],
                    "kind": cat.get("term") if cat is not None else "",
                    "key": key,
                    "summary": title.split(" - ", 1)[1] if " - " in title else title,
                })
        except Exception:
            pass
        return out

    def _fetch_confluence(self, user):
        if not self.s.confluence_base:
            return []
        try:
            data = self.provider.get_json("/rest/api/content/search", params={
                "cql": f'contributor = "{user}" and lastmodified >= now("-14d")', "limit": 25})
            return [{"date": ((r.get("version") or {}).get("when") or "")[:10],
                     "title": r.get("title", ""),
                     "space": ((r.get("space") or {}).get("key") or "")}
                    for r in data.get("results", [])]
        except Exception:
            return []

    def close(self):
        if self.provider:
            self.provider.close()
