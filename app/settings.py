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
SRC_DIR = Path(__file__).resolve().parent.parent          # repo 루트(=app 의 부모)


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
        self.jira_base = str(pick("JIRA_BASE", j.get("base"), "http://localhost:8080")).rstrip("/")
        self.project_key = str(pick("PROJECT_KEY", j.get("project_key"), "DL"))
        self.jira_user = str(pick("JIRA_USER", j.get("user"), "admin"))
        self.jira_token = str(pick("JIRA_TOKEN", j.get("token"), "admin"))
        self.jira_auth = str(pick("JIRA_AUTH", j.get("auth"), "basic")).strip()   # basic | bearer
        self.jira_state_path = str(pick("JIRA_STATE_PATH", j.get("state_path"), "jira_state.json"))
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
        self.sp_field_id = str(pick("SP_FIELD_ID", f.get("story_point"), "customfield_10004"))
        self.epic_link_field_id = str(pick("EPIC_LINK_FIELD_ID", f.get("epic_link"), "customfield_10008"))
        self.confluence_base = str(pick("CONFLUENCE_BASE", conf.get("base"), "")).rstrip("/")
        # Bitbucket 은 아직 mock — base 가 설정되면 SSO 로그인 순회 대상에 포함된다.
        _bb = cfg.get("bitbucket") or {}
        self.bitbucket_base = str(pick("BITBUCKET_BASE", _bb.get("base"), "")).rstrip("/")
        # 개발자용 진단 기능 스위치 — 기본 꺼짐. config 의 dev_tools 리스트 또는
        # env LAKE_DEV_TOOLS(콤마구분)로 켠다. 운영 배포 시엔 비워서 전부 끈다.
        _dt_env = os.getenv("LAKE_DEV_TOOLS", "")
        _dt = ([x.strip() for x in _dt_env.split(",")] if _dt_env
               else (cfg.get("dev_tools") or []))
        self.dev_tools = {str(x).strip() for x in _dt if str(x).strip()}
        # SSO 로그인 순회 대상 — base 가 있는 서비스만. run.py 가 앱 창에서 하나씩 연다.
        #   (이름, base URL, 인증 판정용 REST 경로 후보들)
        self.auth_targets = [("Jira", self.jira_base, ["/rest/api/2/myself"])]
        if self.confluence_base:
            self.auth_targets.append(
                ("Confluence", self.confluence_base,
                 ["/rest/api/user/current", "/rest/api/user/current.json"]))
        if self.bitbucket_base:
            self.auth_targets.append(
                ("Bitbucket", self.bitbucket_base,
                 ["/rest/api/1.0/users?limit=1", "/plugins/servlet/applinks/whoami"]))
        self.cache_db_path = str(pick("CACHE_DB_PATH", cache.get("db_path"), str(BASE_DIR / "cache.sqlite3")))
        self.cache_ttl_seconds = int(pick("CACHE_TTL_SECONDS", cache.get("ttl_seconds"), 900))
        self.app_host = str(pick("APP_HOST", server.get("host"), "0.0.0.0"))
        self.app_port = int(pick("APP_PORT", server.get("port"), 8000))


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


def load_wbs_config(path=None):
    plan = _normalize_wbs(_read_yaml(path or (CONFIG_DIR / "wbs_config.yaml")))
    validate_plan(plan)
    return plan


load_plan = load_wbs_config          # 하위호환 별칭 (내부 코드는 load_plan 사용)


def load_people(path=None):
    return _read_yaml(path or (CONFIG_DIR / "people.yaml")) or {}


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
