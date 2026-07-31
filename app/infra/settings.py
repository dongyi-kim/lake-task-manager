"""
환경설정(config/jira.yml) + 매핑 config(YAML) 로더/검증.
- 코드에 하드코딩 금지. 모든 환경값은 config/jira.yml, 모든 매핑은 config/*.yaml.
- 환경변수(JIRA_ENV·JIRA_BASE 등)가 있으면 jira.yml 값보다 우선(override) — 테스트·빠른 전환용.
"""

import os
import sys
from functools import lru_cache
from pathlib import Path

import yaml

# 디렉터리 구조:
#   <repo>/                 ← 최종 사용자 파일: config/  (배포 시 exe 도 여기)
#     ├── config/{jira.yml,wbs_config.yaml,people.yaml}
#     └── app/…             ← 코드/리소스(static 번들)
#
# frozen(.exe): 외부 파일(config, cache)은 exe 옆, 번들 리소스(static)는 내부(_MEIPASS).
SRC_DIR = Path(__file__).resolve().parent.parent.parent   # repo 루트(=app 의 부모). settings.py 는 app/infra/ 에 있어 3단계 위.


def _find_app_root(candidates):
    """config/ 가 있는 첫 디렉터리 = 사용자 파일 루트 (dev·컨테이너 모두 대응)."""
    for c in candidates:
        if (c / "config").is_dir():
            return c
    return candidates[0]


if getattr(sys, "frozen", False):
    APP_ROOT = Path(sys.executable).parent                # exe 옆
    RESOURCE_DIR = Path(getattr(sys, "_MEIPASS", APP_ROOT))
else:
    # dev: config 는 repo 루트(src 의 부모) / 컨테이너: /srv (app 의 부모=SRC_DIR)
    APP_ROOT = _find_app_root([SRC_DIR.parent, SRC_DIR, Path.cwd()])
    RESOURCE_DIR = SRC_DIR

BASE_DIR = APP_ROOT                    # 외부 파일 기준 (config, cache)
STATIC_DIR = RESOURCE_DIR / "app" / "static"   # 번들 리소스

# ── .cache/ — 임시/비밀 산출물은 전부 이 아래 ─────────────────────────────────────
# SSO 세션(jira_state.json·sso/)·DB 캐시(*.sqlite3)·앱 창 프로필(.appwin-profile)·
# 런타임 설정(app_prefs.json)이 루트에 흩어져 gitignore 도 안내도 파일별로 늘어났다.
# → 전부 `.cache/` 한 폴더로: 지워도 되는 것(캐시)과 비밀(세션)이 한눈에 구분되고,
#   gitignore 는 `.cache/` 한 줄이면 된다.
# 규칙: config 의 상대경로(state_path·db_path)는 **CACHE_DIR 기준**으로 해석한다
#       (절대경로는 그대로 존중 — 다른 위치를 원하는 사람의 선택을 막지 않는다).
CACHE_DIR = BASE_DIR / ".cache"
CACHE_DIR.mkdir(exist_ok=True)


def _cache_path(p):
    """상대경로 → CACHE_DIR 기준 절대경로. 절대경로는 그대로."""
    p = str(p)
    return p if os.path.isabs(p) else str(CACHE_DIR / p)


def _migrate_legacy_cache():
    """구버전이 BASE_DIR 루트에 만들던 산출물을 .cache/ 로 1회 이동(하위호환).
    이동 실패(다른 인스턴스가 잡고 있는 등)는 조용히 넘어간다 — 새 위치에 새로 만들어질 뿐,
    비밀이 새거나 동작이 깨지지는 않는다. 새 위치에 이미 있으면 옛것을 건드리지 않는다."""
    import shutil
    names = ["jira_state.json", "sso", ".appwin-profile", "app_prefs.json"]
    for db in ("cache.sqlite3", "cache-dev.sqlite3", "cache-prod.sqlite3"):
        names += [db, db + "-journal", db + "-wal", db + "-shm"]
    # BASE_DIR(=배포 루트) + SRC_DIR(dev 에서 CWD 상대 해석이 남긴 잔재) 둘 다 쓸어 담는다.
    roots = [BASE_DIR] + ([SRC_DIR] if SRC_DIR != BASE_DIR else [])
    for root in roots:
        for name in names:
            old, new = root / name, CACHE_DIR / name
            try:
                if old.exists() and not new.exists():
                    shutil.move(str(old), str(new))
            except Exception:
                pass


_migrate_legacy_cache()

# config 는 prod/dev 분리:
#   - prod(exe·배포) → repo 루트 `config/`  (사용자 노출, 실제 데이터)
#   - dev(소스 체크아웃) → `src/config/`     (fake/샘플 데이터)
#   - `CONFIG_DIR` 환경변수로 강제 지정 가능.
_cfg_env = os.getenv("CONFIG_DIR")
if _cfg_env:
    CONFIG_DIR = Path(_cfg_env) if os.path.isabs(_cfg_env) else (APP_ROOT / _cfg_env)
elif not getattr(sys, "frozen", False) and (SRC_DIR / "config").is_dir():
    CONFIG_DIR = SRC_DIR / "config"    # dev 체크아웃
else:
    CONFIG_DIR = APP_ROOT / "config"   # prod / exe


def _load_jira_config():
    """config/jira.yml 로드. 없으면 {} (→ 전부 기본값 = mock)."""
    p = CONFIG_DIR / "jira.yml"
    if not p.exists():
        return {}
    with open(p, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


class Settings:
    """config/jira.yml(중첩) 을 읽어 평면 속성으로 노출. 환경변수가 있으면 우선."""

    def __init__(self):
        cfg = _load_jira_config()
        j = cfg.get("jira") or {}
        f = j.get("fields") or {}
        conf = cfg.get("confluence") or {}
        cache = cfg.get("cache") or {}
        server = cfg.get("server") or {}

        def pick(env_key, value, default):
            v = os.getenv(env_key)
            if v is None:
                v = value if value is not None else default
            return v

        self.jira_env = str(pick("JIRA_ENV", cfg.get("env"), "mock")).strip()
        # description 필드 저장 형식. 사내 prod 은 description 이 **JEditor(HTML) 필드**라 HTML 을
        # 그대로 넣어야 한다(wiki 로 넣으면 'h3.' 이 글자로 남고 줄바꿈이 뭉치며 '<' 뒤가 태그로 먹힌다).
        # mock/local(jira820)은 wiki 필드다. 미지정이면 env 로 정한다(prod=html, 그 외=wiki).
        self.description_format = str(pick("DESCRIPTION_FORMAT", cfg.get("description_format"),
                                           "html" if self.jira_env == "prod" else "wiki")).strip().lower()
        # 코멘트 저장 형식 — 사내 prod 은 코멘트도 **HTML(JEditor)** 로 렌더된다(위키가 아니다):
        # 인라인코드 SQL 의 '(*)' 가 별 이모티콘으로 자동변환되고 '{{}}' 가 글자로 새는 버그가 그 증거다.
        # 미지정이면 description_format 을 따른다(prod=html, 그 외 jira820=wiki). 위키로 되돌리려면 'wiki'.
        self.comment_format = str(pick("COMMENT_FORMAT", cfg.get("comment_format"),
                                       self.description_format)).strip().lower()
        self.jira_base = str(pick("JIRA_BASE", j.get("base"), "http://localhost:8080")).rstrip("/")
        self.project_key = str(pick("PROJECT_KEY", j.get("project_key"), "DL"))
        self.jira_user = str(pick("JIRA_USER", j.get("user"), "admin"))
        self.jira_token = str(pick("JIRA_TOKEN", j.get("token"), "admin"))
        self.jira_auth = str(pick("JIRA_AUTH", j.get("auth"), "basic")).strip()   # basic | bearer
        self.jira_state_path = _cache_path(pick("JIRA_STATE_PATH", j.get("state_path"), "jira_state.json"))   # 상대경로 → .cache/
        # 이미지 프록시 허용 호스트(사내 CDN 등). jira base 호스트·동일 상위도메인은 자동 허용.
        self.image_hosts = [str(h).strip() for h in (j.get("image_hosts") or []) if str(h).strip()]
        # 통합 검색 기본 스코프 — 모두 복수(list). jira projects / confluence spaces / bitbucket projects.
        _search = cfg.get("search") or {}

        def _slist(node, *keys, default=None):
            for k in keys:
                v = node.get(k)
                if v is not None:
                    return [str(x).strip() for x in (v if isinstance(v, list) else [v]) if str(x).strip()]
            return list(default or [])

        _sj = _search.get("jira") or {}
        _sc = _search.get("confluence") or {}
        _sb = _search.get("bitbucket") or {}
        self.search_jira_projects = _slist(_sj, "projects", "project", default=[self.project_key])
        self.search_confluence_spaces = _slist(_sc, "spaces", "space")
        self.search_bitbucket_projects = _slist(_sb, "projects", "project")
        # 매니저(PM/PL) Jira 사용자 ID 화이트리스트. WBS Dashboard·인력 워크로드는 여기 있는
        # 사람에게만 보인다(그 외 기능은 누구나). 화이트리스트만 두는 이유: 역할이 늘 때마다
        # 사람 목록을 두 벌 관리하게 되면 반드시 어긋난다 — 매니저만 적고 나머지는 '그 외' 다.
        # ★ 비어 있으면 **제한 없음**(모두 매니저). 안 그러면 config 를 안 채운 dev·초기 설치에서
        #   아무에게도 아무것도 안 보이는 상태가 된다 — 빈 목록은 '아무도 없음' 이 아니라 '미설정'.
        self.managers = [x.lower() for x in
                         _slist(cfg, "manager", "managers", default=[]) or []]
        # 사용자 VoC — 컴포넌트 이름으로 식별한다. 인스턴스마다 다를 수 있어 config 로 받는다.
        # 워크로드 Epic 분포에서 **전용 Epic 처럼** 따로 세는 기준이기도 하다.
        from app.domain.progress import VOC_COMPONENT as _VOC_DEFAULT   # 기본값 단일 소스
        self.voc_component = str(pick("VOC_COMPONENT", (cfg.get("jira") or {}).get("voc_component"),
                                      _VOC_DEFAULT))
        self.sp_field_id = str(pick("SP_FIELD_ID", f.get("story_point"), "customfield_10004"))
        self.epic_link_field_id = str(pick("EPIC_LINK_FIELD_ID", f.get("epic_link"), "customfield_10008"))
        # Epic Name — Epic 의 **단축어**(보드 칸에 뜨는 이름). 요약과 별개 필드다.
        self.epic_name_field_id = str(pick("EPIC_NAME_FIELD_ID", f.get("epic_name"), "customfield_10011"))
        self.confluence_base = str(pick("CONFLUENCE_BASE", conf.get("base"), "")).rstrip("/")
        _bb = cfg.get("bitbucket") or {}
        self.bitbucket_base = str(pick("BITBUCKET_BASE", _bb.get("base"), "")).rstrip("/")
        # Bitbucket 연동은 **사람이 화면에서 켜야** 쓴다(기본 꺼짐). base 가 config 에 있어도 이게
        # False 면 인증 순회·검색에 안 낀다 — 아직 mock 이고, 안 켠 서비스에 SSO 창을 띄우거나
        # 검색을 보내면 그게 인증 오류 소음이 된다.
        from app.infra import prefs as _prefs
        self.bitbucket_enabled = bool(_prefs.load().get("bitbucketEnabled"))
        # 빠른 열기 전역 단축키(데스크톱 앱, 저장됨). run.py 가 이 값으로 등록·재등록.
        self.quick_open_hotkey = str(_prefs.load().get("quickOpenHotkey") or "ctrl+alt+space")
        # 개발자용 진단 기능 — 지금은 **전부 열림**(config 무관). devtools.DEV_TOOLS 가 곧 목록.
        # 노출 제어는 나중에 유저 역할이 생기면 devtools.enabled() 에서 가른다.
        from app.infra import devtools as _devtools
        self.dev_tools = set(_devtools.DEV_TOOLS)
        # SSO 로그인 순회 대상 — base 가 있는 서비스만. run.py 가 앱 창에서 하나씩 연다.
        #   (이름, base URL, 인증 판정용 REST 경로 후보들)
        # 서비스 정의 — base 미설정이면 configured=False. 인증 판정 경로:
        #   Jira /myself · Confluence /user/current · Bitbucket inbox count(인증 필요+JSON, 권한 불필요)
        #   (Bitbucket 의 /users 는 관리자 전용, whoami 는 plain text 라 오판 → 안 씀)
        self.services = [
            {"name": "Jira", "base": self.jira_base, "configured": bool(self.jira_base),
             "paths": ["/rest/api/2/myself"]},
            {"name": "Confluence", "base": self.confluence_base, "configured": bool(self.confluence_base),
             "paths": ["/rest/api/user/current", "/rest/api/user/current.json"]},
            {"name": "Bitbucket", "base": self.bitbucket_base,
             "configured": bool(self.bitbucket_base) and self.bitbucket_enabled,
             "paths": ["/rest/api/1.0/inbox/pull-requests/count", "/rest/api/1.0/repos?limit=1"]},
        ]
        # SSO 로그인 순회·판정에는 **설정된 것만** (base 없는 서비스는 창을 못 연다).
        self.auth_targets = [(s["name"], s["base"], s["paths"]) for s in self.services if s["configured"]]
        self.cache_db_path = _cache_path(pick("CACHE_DB_PATH", cache.get("db_path"), "cache.sqlite3"))   # 상대경로 → .cache/
        # outdated — 이 시각이 지나면 '낡음'(온라인이면 다시 받는다)
        self.cache_ttl_seconds = int(pick("CACHE_TTL_SECONDS", cache.get("ttl_seconds"), 900))
        # dead — 이 시각이 지나야 '없는 값'. 그 전까지는 오프라인·미인증에서도 낡은 값을 준다.
        # 기본 24시간: 하루 안에 본 것은 망이 끊겨도 다시 볼 수 있어야 한다는 기준.
        self.cache_dead_ttl_seconds = int(pick("CACHE_DEAD_TTL_SECONDS",
                                               cache.get("dead_ttl_seconds"), 24 * 3600))
        self.app_host = str(pick("APP_HOST", server.get("host"), "0.0.0.0"))
        self.app_port = int(pick("APP_PORT", server.get("port"), 8000))

    def _recompute_targets(self):
        """Bitbucket 토글이 바뀌면 인증 순회 대상을 다시 계산한다(재시작 불필요)."""
        for svc in self.services:
            if svc["name"] == "Bitbucket":
                svc["configured"] = bool(self.bitbucket_base) and self.bitbucket_enabled
        self.auth_targets = [(x["name"], x["base"], x["paths"])
                             for x in self.services if x["configured"]]

    def set_bitbucket_enabled(self, on):
        """Bitbucket 연동 on/off — 저장 + 인증 순회 대상 즉시 반영."""
        from app.infra import prefs as _prefs
        self.bitbucket_enabled = bool(on)
        _prefs.save({"bitbucketEnabled": self.bitbucket_enabled})
        self._recompute_targets()
        return self.bitbucket_enabled

    def set_quick_open_hotkey(self, spec):
        """빠른 열기 단축키 조합 저장. run.py 는 훅(set_hotkey_hook)으로 즉시 재등록한다."""
        from app.infra import prefs as _prefs
        self.quick_open_hotkey = str(spec or "ctrl+alt+space").strip().lower()
        _prefs.save({"quickOpenHotkey": self.quick_open_hotkey})
        return self.quick_open_hotkey


@lru_cache
def get_settings():
    return Settings()


# ── config 로더 ──────────────────────────────────────────────
def _read_yaml(path):
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def _normalize_wbs(raw):
    """wbs_config.yaml(모듈→tasks→epics[ticket,weight]) → 내부 plan dict."""
    plan = {"project_key": raw.get("project_key", "DL"), "modules": [], "epics": {}, "wbs": []}
    seen = set()
    for grp in raw.get("wbs", []) or []:
        module = grp["module"]
        if module not in seen:
            plan["modules"].append(module)
            seen.add(module)
        for i, t in enumerate(grp.get("tasks", []) or [], 1):
            plan["wbs"].append({
                "id": t.get("id") or f"{module}-{i}",
                "module": module, "name": t["name"],
                "start": t["start"], "end": t["end"],
                "epics": [{"key": e["ticket"], "weight": e["weight"]} for e in t.get("epics", []) or []],
            })
    return plan


def is_manager(settings, user):
    """이 사용자가 매니저인가. user 는 id 문자열 또는 /myself 응답 dict.
    화이트리스트가 비어 있으면(미설정) 전원 매니저로 본다."""
    if not settings.managers:
        return True
    # 후보를 **전부** 대조한다. 표현이 여러 가지라(우리 내부 {id: 사번, name: 표시이름} /
    # Jira 원본 {name: 사번, key, displayName}) 하나만 골라 비교하면, 설정에 적은 값이
    # 하필 다른 필드에 있을 때 조용히 거부된다 — 본인은 매니저인데 "매니저 전용" 만 본다.
    # 로컬 1인 앱의 화이트리스트라 관대하게 받는 편이 낫다(막는 게 목적이 아니라 화면 분기다).
    if isinstance(user, dict):
        cands = [user.get(k) for k in ("id", "name", "key", "displayName", "emailAddress")]
    else:
        cands = [user]
    return any(str(c or "").strip().lower() in settings.managers for c in cands)


def load_wbs_config(path=None):
    plan = _normalize_wbs(_read_yaml(path or (CONFIG_DIR / "wbs_config.yaml")))
    validate_plan(plan)
    return plan


load_plan = load_wbs_config          # 하위호환 별칭 (내부 코드는 load_plan 사용)


# people.yaml 은 **디스크 읽기**라 매 요청마다 다시 읽으면 낭비다(모듈 필터가 자주 부른다).
# 앱 시작 후 1회 읽어 캐시하고, 파생 디렉토리(id→모듈)도 함께 만들어 둔다.
# config 를 고쳤으면 /api/refresh 가 reload_people() 로 캐시를 비운다(그다음 조회부터 반영).
_PEOPLE_CACHE = {"data": None, "dir": None}


def load_people(path=None):
    if path is not None:                       # 명시 경로(테스트 등)는 캐시 우회
        return _read_yaml(path) or {}
    if _PEOPLE_CACHE["data"] is None:
        _PEOPLE_CACHE["data"] = _read_yaml(CONFIG_DIR / "people.yaml") or {}
    return _PEOPLE_CACHE["data"]


def module_dir():
    """모듈 디렉토리(캐시) — {people, byUser(id소문자→[모듈]), modules}. 시작 후 1회 구성.
    '내가 속한 모듈'·'모듈 인력' 조회의 단일 소스 — 매번 config 를 훑지 않는다."""
    d = _PEOPLE_CACHE["dir"]
    if d is None:
        people = load_people()
        by = {}
        for mod, ids in people.items():
            for pid in (ids or []):
                by.setdefault(str(pid or "").strip().lower(), []).append(mod)
        d = _PEOPLE_CACHE["dir"] = {"people": people, "byUser": by,
                                    "modules": list(people.keys())}
    return d


def modules_of(user_id):
    """그 사용자가 속한 모듈 목록(캐시된 디렉토리에서)."""
    return list(module_dir()["byUser"].get(str(user_id or "").strip().lower(), []))


def reload_people():
    """people.yaml 캐시 무효화 — config 를 고쳤을 때(/api/refresh) 다음 조회부터 반영."""
    _PEOPLE_CACHE["data"] = None
    _PEOPLE_CACHE["dir"] = None


def validate_plan(plan):
    """weight 합=1.0, start<end, module 정합성 검증. 실패 시 ValueError.
    (Epic 이름·상태는 Jira 에 있으므로 config 에서 검증하지 않는다)"""
    errors = []
    modules = set(plan.get("modules", []))
    seen_ids = set()
    for w in plan.get("wbs", []):
        wid = w.get("id", "?")
        if wid in seen_ids:
            errors.append(f"{wid}: 중복 WBS id")
        seen_ids.add(wid)
        if w.get("module") not in modules:
            errors.append(f"{wid}: module '{w.get('module')}' 가 modules 목록에 없음")
        try:
            if str(w["start"]) >= str(w["end"]):
                errors.append(f"{wid}: start >= end")
        except KeyError:
            errors.append(f"{wid}: start/end 누락")
        wsum = sum(e.get("weight", 0) for e in w.get("epics", []))
        if wsum <= 0:
            errors.append(f"{wid}: epic weight 합이 0 이하 (양수 필요)")   # 합=1 강제 아님(상대값)
    if errors:
        raise ValueError("wbs_config.yaml 검증 실패:\n  - " + "\n  - ".join(errors))
    return True
