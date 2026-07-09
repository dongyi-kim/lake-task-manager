"""
JiraClient — AuthProvider 를 주입받아 REST 호출 (어떤 인증인지 모름).
캐시 경유. mock 모드는 HTTP 없이 mockdata 로 동일 형태를 반환.

Phase A 범위: Epic 자식 SP 롤업. (기능2·3 의 검색/활동은 후속 Phase 에서 확장)
"""

from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta

from . import mockdata, progress
from .names import real_name


# 실 Jira DC statusCategory.key → 내부 vocab (new=todo, indeterminate=inprogress, done=done)
_CAT_MAP = {"new": "todo", "indeterminate": "inprogress", "done": "done", "undefined": "todo"}


def _norm_cat(key):
    return _CAT_MAP.get((key or "").lower(), "todo")


def _started_from(created, updated, cat):
    """착수일(Started) 추정 — 진행중/완료면 created~updated 사이 1/3 지점, To Do 면 None. (결정적)"""
    if cat == "todo" or not created or not updated:
        return None
    try:
        da, db = date.fromisoformat(created[:10]), date.fromisoformat(updated[:10])
        return (da + timedelta(days=max((db - da).days, 0) // 3)).isoformat()
    except Exception:
        return None


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


def _display_node(raw, sp_field, with_subs=False):
    """WBS Gantt 트리 표시용 상세 노드(요약·타입·상태·일정·SP + Sub-Task). progress 계산과 별개."""
    f = raw.get("fields", {}) or {}
    st = f.get("status") or {}
    node = {
        "key": raw.get("key"),
        "type": (f.get("issuetype") or {}).get("name", ""),
        "summary": f.get("summary") or raw.get("key"),
        "statusName": st.get("name", ""),
        "statusCat": _norm_cat((st.get("statusCategory") or {}).get("key")),
        "start": (f.get("created") or "")[:10] or None,
        "end": (f.get("duedate") or f.get("resolutiondate") or f.get("updated") or "")[:10] or None,
        "sp": f.get(sp_field),
    }
    if with_subs:
        subs = []
        for s in (f.get("subtasks") or []):
            sf = s.get("fields", {}) or {}
            sst = sf.get("status") or {}
            subs.append({
                "key": s.get("key"),
                "type": (sf.get("issuetype") or {}).get("name", "Sub-Task"),
                "summary": sf.get("summary") or s.get("key"),
                "statusName": sst.get("name", ""),
                "statusCat": _norm_cat((sst.get("statusCategory") or {}).get("key")),
            })
        node["children"] = subs
    return node


class JiraClient:
    def __init__(self, settings, cache):
        self.s = settings
        self.cache = cache
        self.env = settings.jira_env
        # provider 는 lazy 생성 — 임포트/기동 시 Chrome 을 띄우거나 세션 없음으로 크래시하지 않는다.
        self._provider = None
        self._provider_built = False

    @property
    def provider(self):
        if not self._provider_built:
            self._provider = self._make_provider()
            self._provider_built = True
        return self._provider

    def _make_provider(self):
        if self.env == "local":
            from .auth.basic import BasicAuthProvider
            return BasicAuthProvider(self.s.jira_base, self.s.jira_user,
                                     self.s.jira_token, self.s.jira_auth)
        if self.env == "prod":
            # 세션 없으면 SsoSessionProvider 가 LoginRequired 를 던짐 → 라우트가 needLogin 처리.
            from .auth.sso_session import SsoSessionProvider
            return SsoSessionProvider(self.s.jira_base, self._state_path())
        return None   # mock

    def _state_path(self):
        """세션 파일 절대 경로 (상대면 APP_ROOT 기준 — run.py 의 존재검사와 일치)."""
        from pathlib import Path
        from .settings import APP_ROOT
        p = Path(self.s.jira_state_path)
        return str(p if p.is_absolute() else APP_ROOT / p)

    def needs_login(self):
        """prod 이고 세션 파일이 없으면 True (mock/local 은 항상 False)."""
        if self.env != "prod":
            return False
        from pathlib import Path
        return not Path(self._state_path()).exists()

    def reset_provider(self):
        """세션 갱신 후 다음 호출 때 새 세션으로 provider 를 재생성하도록 초기화."""
        if self._provider is not None:
            try:
                self._provider.close()
            except Exception:
                pass
        self._provider = None
        self._provider_built = False

    def login(self, timeout=300):
        """[prod] 설치된 Chrome 을 띄워 SSO 로그인(폴링 감지) 후 세션 저장. 성공 시 provider 재설정."""
        if self.env != "prod":
            return True
        from .auth.sso_session import login_wait
        ok = login_wait(self.s.jira_base, self._state_path(), timeout=timeout)
        if ok:
            self.reset_provider()
        return ok

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

    def epic_tree(self, epic_key):
        """Epic 자식(Story/Task/Bug) + 각 자식의 Sub-Task — WBS Gantt 다계층 트리(표시용).
        같은 티켓 단위 캐시(epic_children/issue)를 재사용하므로 추가 상위호출 거의 없음."""
        real = self._resolve(epic_key)
        return self.cache.get_or_set(
            f"epic_tree:{self.env}:{real}", self.s.cache_ttl_seconds,
            lambda: self._build_epic_tree(real))[0]

    def _build_epic_tree(self, epic_key):
        sp = self.s.sp_field_id
        if self.env == "mock":
            raws = mockdata.epic_raw_children(epic_key)
        else:
            raws = self._search(f'"Epic Link" = {epic_key}',
                                cache_key=f"epic_children:{self.env}:{epic_key}")
            if not raws:
                try:  # 폴백: Agile API
                    data = self.provider.get_json(
                        f"/rest/agile/1.0/epic/{epic_key}/issue",
                        params={"fields": self._issue_fields(), "maxResults": 100})
                    raws = data.get("issues", [])
                except Exception:
                    raws = []
        return [_display_node(it, sp, with_subs=True) for it in raws]

    def epic_name(self, epic_key):
        """Epic 이름(summary) — Jira/world 에서. (config 엔 이름 없음)"""
        if self.env == "mock":
            return mockdata.epic_name(epic_key)
        try:
            it = self.get_issue(self._resolve(epic_key))
            return (it.get("fields") or {}).get("summary") or epic_key
        except Exception:
            return epic_key

    def _pmap(self, items, fn):
        """items 를 fn 으로 매핑 — provider 가 병렬 안전(basic/PAT)이면 스레드풀, 아니면 순차.
        (SSO/mock 은 순차. cache.get_or_set 은 producer 중 lock 을 안 잡아 네트워크 병렬 유효.)"""
        items = list(items)
        prov = self.provider
        if prov is not None and getattr(prov, "supports_parallel", False) and len(items) > 1:
            with ThreadPoolExecutor(max_workers=min(8, len(items))) as ex:
                return list(ex.map(fn, items))
        return [fn(x) for x in items]

    def epic_progress_map(self, plan):
        """plan 의 모든 Epic 에 대해 진척률 dict 반환 {key -> {...,name}}. Epic 단위 병렬 조회."""
        keys, seen = [], set()
        for w in plan["wbs"]:
            for e in w["epics"]:
                if e["key"] not in seen:
                    seen.add(e["key"]); keys.append(e["key"])
        results = self._pmap(keys, self.epic_progress_one)
        return {k: r for k, r in zip(keys, results)}

    # ── 기능2: PMO_VIT 현안 ──
    # 진척 = Root 현안의 자손 티켓 "개수 기반"(done/total). 시작·마감·소식·코멘트 포함.
    # (local/prod 경로는 라이브 Jira 대상 Phase B 에서 검증/심화 — 자손 깊이 등)
    def vit_issues(self, plan, people, epic_prog=None):
        if self.env == "mock":
            return mockdata.vit_issues(plan, people, epic_prog)
        # 조립 결과를 캐시 → /api/vit·/api/vit/{key} 가 매번 forest 를 재조립하지 않음
        data, _ = self.cache.get_or_set(f"vit_build:{self.env}", self.s.cache_ttl_seconds, self._fetch_vit)
        return data

    def _fetch_vit(self):
        # PMO_VIT 목록 — 검색 결과를 티켓 단위 캐시로 write-through. 루트 단위 병렬 조립.
        roots = self._search(
            f'project={self.s.project_key} AND labels="PMO_VIT" ORDER BY updated DESC',
            cache_key=f"vit_list:{self.env}")
        def build(it):
            base = self._vit_base(it)
            base["tree"] = self._vit_tree(base["key"], base["type"])   # 자손(카운트용). 코멘트는 [자세히]에서 lazy
            return base
        return self._pmap(roots, build)

    def _vit_base(self, issue):
        f = issue.get("fields", {}) or {}
        comps = f.get("components") or []
        status = f.get("status") or {}
        assignee = f.get("assignee") or {}
        cat = _norm_cat((status.get("statusCategory") or {}).get("key"))
        created = (f.get("created") or "")[:10] or None
        updated = (f.get("updated") or "")[:10] or None
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
            "start": created, "created": created,
            "started": _started_from(created, updated, cat), "updated": updated,
            "due": f.get("duedate"),
            "statusCategory": cat, "status": status.get("name", ""),
            "ancestors": [a for a in ancestors if a],
        }

    def _node_from_issue(self, issue):
        f = issue.get("fields", {}) or {}
        node = {
            "key": issue.get("key", ""),
            "summary": f.get("summary", ""),
            "type": (f.get("issuetype") or {}).get("name", ""),
            "assignee": real_name((f.get("assignee") or {}).get("displayName")
                                  or (f.get("assignee") or {}).get("name")),
            "statusCategory": _norm_cat(((f.get("status") or {}).get("statusCategory") or {}).get("key")),
            "status": ((f.get("status") or {}).get("name") or ""),
            "created": f.get("created") or None,          # 전체 datetime 유지(뉴스 시간표시)
            "resolved": f.get("resolutiondate") or None,
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

    # ── 범용 단일 리소스 (env 무관 — /api/issue·/api/epic 리소스 엔드포인트용) ──
    def issue_detail(self, key):
        """단일 티켓 상세 노드(요약·타입·상태·일정·SP + Sub-Task). 없으면 None."""
        raw = mockdata.raw_issue(key) if self.env == "mock" else self.get_issue(key)
        return _display_node(raw, self.s.sp_field_id, with_subs=True) if raw else None

    def issue_comments(self, key, limit=5):
        """단일 티켓 코멘트 — mock/local/prod 동일 형태."""
        if self.env == "mock":
            return mockdata.issue_comments(key, limit)
        return self._issue_comments(key, limit)

    def _display_name(self, pid):
        """Jira 사용자 displayName('{본명} {회사}').

        **성공만 캐시**(user:{env}:{pid}). 실패하면 로그 남기고 id 폴백하되 **캐시하지 않는다**
        → 일시적 실패(예: 세션 만료)로 username 이 15분간 굳는 문제 방지(다음 호출에 재시도).
        """
        ck = f"user:{self.env}:{pid}"
        hit = self.cache.get(ck)
        if hit is not None:
            return hit
        dn = None
        try:
            u = self.provider.get_json("/rest/api/2/user", params={"username": pid})
            dn = u.get("displayName")
        except Exception as e:
            import sys
            print(f"[workload] displayName lookup failed pid={pid}: {e}", file=sys.stderr)
        if dn:
            self.cache.set(ck, dn, self.s.cache_ttl_seconds)
            return dn
        return pid

    def epic_progress_one(self, key):
        """단일 Epic 진척률 {doneSp,totalSp,mockSp,progressPct,name}."""
        pr = progress.epic_progress(self.epic_issues(key))
        pr["name"] = self.epic_name(key)
        return pr

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
                    comp = "사용자 VoC" if "사용자 VoC" in comps else (comps[0] if comps else "")
                    t = (f.get("issuetype") or {}).get("name", "")
                    c = mockdata.wl_category(comp, t)
                    if c:
                        by[c] += 1
            except Exception:
                pass
            return by
        def person(pid):
            key = f'workload:{self.env}:{pid}'
            bundle, _ = self.cache.get_or_set(key, self.s.cache_ttl_seconds, lambda: {
                "id": pid,
                "inProgress": counts(f'assignee = "{pid}" AND statusCategory = "In Progress"'),
                "done7d": counts(f'assignee = "{pid}" AND statusCategory = Done AND resolved >= -7d'),
            })
            # displayName 은 카운트 번들과 분리해 별도 해석(자체 캐시·실패 자가치유)
            # → 카운트가 캐시돼 있어도 이름은 매번 재해석되어 username 이 굳지 않는다.
            return dict(bundle, displayName=self._display_name(pid))
        pids = [pid for module in plan["modules"] for pid in people.get(module, [])]
        by_pid = {b["id"]: b for b in self._pmap(pids, person)}   # 인력 단위 병렬
        return {module: [by_pid[pid] for pid in people.get(module, []) if pid in by_pid]
                for module in plan["modules"]}

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
        """실 Jira Activity Streams ATOM 파싱: category term=kind, alternate link=/browse/KEY,
        activity:object/summary=요약, updated=일시. (title 은 HTML 이라 요약원이 아님 — object/summary 사용)"""
        import re as _re
        import xml.etree.ElementTree as ET
        A = "{http://www.w3.org/2005/Atom}"          # Atom
        V = "{http://activitystrea.ms/spec/1.0/}"    # activity
        out = []
        try:
            xml = self.provider.get_text("/activity",
                                         params={"maxResults": limit, "streams": f"user IS {user}"})
            root = ET.fromstring(xml)
            for e in root.findall(f"{A}entry"):
                cat = e.find(f"{A}category")
                kind = cat.get("term") if cat is not None else ""
                key = ""
                for ln in e.findall(f"{A}link"):
                    if ln.get("rel") == "alternate":
                        m = _re.search(r"/browse/([A-Z][A-Z0-9]+-\d+)", ln.get("href") or "")
                        if m:
                            key = m.group(1)
                obj = e.find(f"{V}object")
                summary = ""
                if obj is not None:
                    summary = (obj.findtext(f"{A}summary") or "").strip()
                    if not key:                       # 폴백: activity:object/title = KEY
                        key = (obj.findtext(f"{A}title") or "").strip()
                if not summary:                       # 폴백: HTML title 태그 제거
                    t = _re.sub(r"<[^>]+>", "", (e.findtext(f"{A}title") or ""))
                    summary = t.split(" - ", 1)[1].strip() if " - " in t else t.strip()
                out.append({
                    "date": (e.findtext(f"{A}updated") or e.findtext(f"{A}published") or ""),
                    "kind": kind, "key": key, "summary": summary,
                })
        except Exception:
            pass
        return out

    def _fetch_confluence(self, user):
        if not self.s.confluence_base:
            return []
        try:
            # expand 필수: 실 Confluence 는 expand 없으면 version/space 를 안 준다(당시 date/space 누락).
            data = self.provider.get_json("/rest/api/content/search", params={
                "cql": f'contributor = "{user}" and lastmodified >= now("-14d")',
                "expand": "version,space", "limit": 25})
            return [{"date": ((r.get("version") or {}).get("when") or ""),
                     "title": r.get("title", ""),
                     "space": ((r.get("space") or {}).get("key") or "")}
                    for r in data.get("results", [])]
        except Exception:
            return []

    def close(self):
        if self.provider:
            self.provider.close()
