"""
환경설정(.env) + config(YAML) 로더/검증.
- 코드에 하드코딩 금지. 모든 환경값은 .env, 모든 매핑은 config/*.yaml.
"""

import os
import sys
from functools import lru_cache
from pathlib import Path

import yaml
from dotenv import load_dotenv

# 디렉터리 구조:
#   <repo>/                 ← 최종 사용자 파일: .env, config/  (배포 시 exe 도 여기)
#     ├── config/{plan,people}.yaml
#     └── src/app/…         ← 코드/리소스(static 번들)
#
# frozen(.exe): 외부 파일(.env, config, cache)은 exe 옆, 번들 리소스(static)는 내부(_MEIPASS).
SRC_DIR = Path(__file__).resolve().parent.parent          # src/


def _find_app_root(candidates):
    """config/ 또는 .env 가 있는 첫 디렉터리 = 사용자 파일 루트 (dev·컨테이너 모두 대응)."""
    for c in candidates:
        if (c / "config").is_dir() or (c / ".env").exists():
            return c
    return candidates[0]


if getattr(sys, "frozen", False):
    APP_ROOT = Path(sys.executable).parent                # exe 옆
    RESOURCE_DIR = Path(getattr(sys, "_MEIPASS", APP_ROOT))
else:
    # dev: config 는 repo 루트(src 의 부모) / 컨테이너: /srv (app 의 부모=SRC_DIR)
    APP_ROOT = _find_app_root([SRC_DIR.parent, SRC_DIR, Path.cwd()])
    RESOURCE_DIR = SRC_DIR

BASE_DIR = APP_ROOT                    # 외부 파일 기준 (.env, cache)
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

# env 파일 선택: LAKE_DOTENV 로 지정, 없으면 .env
#   - prod/사용자: .env(.example/.prod) 는 repo 루트(=exe 옆) — 노출되는 사용자 설정
#   - dev 전용:   .env.dev 는 src/ — LAKE_DOTENV=.env.dev 로 지정
# 루트에서 먼저 찾고 없으면 src/ 에서 찾는다.
_dotenv = os.getenv("LAKE_DOTENV", ".env")
for _base in (APP_ROOT, SRC_DIR):
    _p = _base / _dotenv
    if _p.exists():
        load_dotenv(_p)
        break
else:
    load_dotenv(APP_ROOT / _dotenv)


class Settings:
    def __init__(self):
        self.jira_env = os.getenv("JIRA_ENV", "mock").strip()
        self.jira_base = os.getenv("JIRA_BASE", "http://localhost:8080").rstrip("/")
        self.project_key = os.getenv("PROJECT_KEY", "DL")
        self.jira_user = os.getenv("JIRA_USER", "admin")
        self.jira_token = os.getenv("JIRA_TOKEN", "admin")
        self.jira_auth = os.getenv("JIRA_AUTH", "basic").strip()   # basic | bearer
        self.jira_state_path = os.getenv("JIRA_STATE_PATH", "jira_state.json")
        self.sp_field_id = os.getenv("SP_FIELD_ID", "customfield_10004")
        self.epic_link_field_id = os.getenv("EPIC_LINK_FIELD_ID", "customfield_10008")
        self.confluence_base = os.getenv("CONFLUENCE_BASE", "").rstrip("/")
        self.cache_db_path = os.getenv("CACHE_DB_PATH", str(BASE_DIR / "cache.sqlite3"))
        self.cache_ttl_seconds = int(os.getenv("CACHE_TTL_SECONDS", "900"))
        self.app_host = os.getenv("APP_HOST", "0.0.0.0")
        self.app_port = int(os.getenv("APP_PORT", "8000"))


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
