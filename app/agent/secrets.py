"""agent/secrets.py — Agent 가 쓰는 **비밀값**(API 키) 저장소.

왜 prefs 가 아닌가:
    `infra/prefs.py` 는 "비밀은 없지만 개인 설정"을 담는 곳이라고 스스로 못 박고 있다.
    API 키는 성격이 다르다. SSO 세션(`auth/sso_store.py`)과 같은 급이라 **파일을 나눈다** —
    수명이 다른 것을 같은 저장소에 두지 않는다.

왜 캐시 DB 가 아닌가:
    `cache.sqlite3` 는 언제든 지워도 되는 캐시다. 거기 섞으면 "캐시 지웠더니 키가 날아감" 이 된다.

찢어진 쓰기 방지는 임시파일 + `os.replace`(원자적 교체)로 끝난다 — sso_store 와 같은 방식.
저장 위치는 `CACHE_DIR/agent_secrets.json` 이며 `.cache/` 는 통째로 gitignore 대상이다.

**환경변수가 항상 이긴다.** 채점/사내 환경은 `AOAI_*` 를 프로세스에 주입해 주므로, 그 경우
파일에 아무것도 없어도 그대로 동작해야 한다(설정 화면을 거치지 않아도 되는 게 정상 경로다).
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path

_LOCK = threading.Lock()

# 저장 가능한 비밀 키 — 화이트리스트(prefs 와 같은 방식). 이 밖의 키는 저장하지 않는다.
FIELDS = (
    "aoaiEndpoint", "aoaiApiKey",
    "openaiApiKey",
    "compatBaseUrl", "compatApiKey", "compatHeaders",   # compatHeaders = JSON 문자열
    "langfusePublicKey", "langfuseSecretKey", "langfuseHost",
)

# 화면에 돌려줄 때 **절대 원문을 실어 보내지 않는** 필드
SECRET_FIELDS = ("aoaiApiKey", "openaiApiKey", "compatApiKey", "langfuseSecretKey")


def _path() -> Path:
    from app.infra.settings import CACHE_DIR
    return Path(CACHE_DIR) / "agent_secrets.json"


def load() -> dict:
    """저장된 값. 파일이 없거나 깨졌으면 빈 dict(앱이 죽지 않게)."""
    try:
        with _path().open(encoding="utf-8") as f:
            data = json.load(f)
        return {k: v for k, v in data.items() if k in FIELDS} if isinstance(data, dict) else {}
    except Exception:
        return {}


def save(patch: dict) -> dict:
    """일부 키만 바꿔 저장하고 **마스킹된** 결과를 돌려준다.

    값이 빈 문자열이면 그 키를 지운다 — 화면에서 지우는 동작이 곧 삭제여야 한다.
    """
    with _LOCK:
        cur = load()
        for k, v in (patch or {}).items():
            if k not in FIELDS:
                continue
            if v is None or str(v).strip() == "":
                cur.pop(k, None)
            else:
                cur[k] = str(v).strip()
        p = _path()
        tmp = p.with_suffix(".json.tmp")
        try:
            with tmp.open("w", encoding="utf-8") as f:
                json.dump(cur, f, ensure_ascii=False, indent=2)
            tmp.replace(p)                      # 원자적 교체 — 쓰다 죽어도 잘린 파일이 안 남는다
        except Exception:
            pass
        return masked(cur)


def get(field: str, *env_names: str) -> str:
    """환경변수 우선, 없으면 저장된 값. 채점/사내 환경의 주입을 이기지 않게 이 순서를 지킨다."""
    for name in env_names:
        v = os.getenv(name)
        if v:
            return v.strip()
    return (load().get(field) or "").strip()


def masked(values: dict = None) -> dict:
    """화면용 — 비밀 필드는 존재 여부와 끝 4자만 남긴다."""
    src = load() if values is None else values
    out = {}
    for k in FIELDS:
        v = (src.get(k) or "").strip()
        if k in SECRET_FIELDS:
            out[k] = ("설정됨 (…%s)" % v[-4:]) if len(v) >= 4 else ("설정됨" if v else "")
        else:
            out[k] = v
    return out
