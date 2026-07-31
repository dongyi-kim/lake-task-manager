"""app_prefs.py — 런타임에 사람이 켜고 끄는 설정(파일에 저장, 프로세스 재시작에도 유지).

config/jira.yml 은 **배포 시 손으로 채우는 템플릿**이라, 앱을 쓰다가 화면에서 바꾸는 값은
거기 두면 안 된다(커밋 대상이고, 실행 중 다시 읽지도 않는다). 그런 값은 이 작은 JSON 에 둔다.

지금 담는 것:
  bitbucketEnabled  Bitbucket 연동 사용 여부(기본 False). True 일 때만 SSO 인증 순회·통합
                    검색에 Bitbucket 이 낀다. base 가 config 에 있어도 이게 False 면 안 쓴다.

**커밋 금지** — 사용자별 상태다(.gitignore 에 넣는다). 비밀은 없지만 개인 설정이다.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path

_LOCK = threading.Lock()
_DEFAULTS = {"bitbucketEnabled": False,
             # 빠른 열기 전역 단축키(데스크톱 앱). 설정/트레이에서 바꾼다. run.py 가 이 값으로 등록.
             "quickOpenHotkey": "ctrl+alt+space"}


def _path():
    from app.infra.settings import CACHE_DIR
    return Path(CACHE_DIR) / "app_prefs.json"


def load() -> dict:
    """저장된 설정 + 기본값. 파일이 없거나 깨졌으면 기본값(앱이 죽지 않게)."""
    out = dict(_DEFAULTS)
    try:
        with _path().open(encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            for k in _DEFAULTS:
                if k in data:
                    out[k] = data[k]
    except Exception:
        pass
    return out


def save(patch: dict) -> dict:
    """일부 키만 바꿔 저장하고 병합 결과를 돌려준다. 원자적 교체(중간에 깨진 파일이 안 남게)."""
    with _LOCK:
        cur = load()
        for k, v in (patch or {}).items():
            if k in _DEFAULTS:
                cur[k] = v
        p = _path()
        tmp = p.with_suffix(".json.tmp")
        try:
            with tmp.open("w", encoding="utf-8") as f:
                json.dump(cur, f, ensure_ascii=False, indent=2)
            tmp.replace(p)
        except Exception:
            pass
        return cur
