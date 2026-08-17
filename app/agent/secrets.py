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
    # named config에서 embedding endpoint를 chat endpoint와 분리할 때 사용한다.
    "embeddingBaseUrl", "embeddingApiKey", "embeddingHeaders",
    "langfusePublicKey", "langfuseSecretKey", "langfuseHost",
)

# 화면에 돌려줄 때 **절대 원문을 실어 보내지 않는** 필드
SECRET_FIELDS = ("aoaiApiKey", "openaiApiKey", "compatApiKey", "compatHeaders",
                 "embeddingApiKey", "embeddingHeaders",
                 "langfuseSecretKey")


def _path() -> Path:
    from app.infra.settings import CACHE_DIR
    return Path(CACHE_DIR) / "agent_secrets.json"


def _load_raw() -> dict:
    """레거시 전역 비밀과 config별 비밀을 함께 읽는 내부 함수."""
    try:
        with _path().open(encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def load() -> dict:
    """레거시 전역 비밀. 환경변수 기반 배포와 이전 설정 호환용."""
    return {k: v for k, v in _load_raw().items() if k in FIELDS}


def save(patch: dict) -> dict:
    """일부 키만 바꿔 저장하고 **마스킹된** 결과를 돌려준다.

    값이 빈 문자열이면 그 키를 지운다 — 화면에서 지우는 동작이 곧 삭제여야 한다.
    """
    with _LOCK:
        raw = _load_raw()
        cur = {k: v for k, v in raw.items() if k in FIELDS}
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
                json.dump({**cur, "_configs": raw.get("_configs", {})}, f,
                          ensure_ascii=False, indent=2)
            tmp.replace(p)                      # 원자적 교체 — 쓰다 죽어도 잘린 파일이 안 남는다
        except Exception:
            pass
        return masked(cur)


def load_for(config_id: str) -> dict:
    """named config 하나의 저장값. 환경변수는 섞지 않는다."""
    rows = _load_raw().get("_configs") or {}
    row = rows.get(str(config_id)) if isinstance(rows, dict) else {}
    return {k: v for k, v in (row or {}).items() if k in FIELDS}


def get_for(config_id: str, field: str) -> str:
    return str(load_for(config_id).get(field) or "").strip()


def save_for(config_id: str, patch: dict) -> dict:
    """config별 비밀을 원자적으로 저장하고 마스킹된 값만 반환."""
    cid = str(config_id or "").strip()
    if not cid:
        raise ValueError("config id가 필요합니다.")
    with _LOCK:
        raw = _load_raw()
        rows = raw.get("_configs") if isinstance(raw.get("_configs"), dict) else {}
        cur = {k: v for k, v in (rows.get(cid) or {}).items() if k in FIELDS}
        for k, v in (patch or {}).items():
            if k not in FIELDS:
                continue
            if v is None or str(v).strip() == "":
                cur.pop(k, None)
            else:
                cur[k] = str(v).strip()
        rows[cid] = cur
        legacy = {k: v for k, v in raw.items() if k in FIELDS}
        payload = {**legacy, "_configs": rows}
        p = _path()
        tmp = p.with_suffix(".json.tmp")
        with tmp.open("w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        tmp.replace(p)
        return masked(cur)


def delete_for(config_id: str) -> None:
    with _LOCK:
        raw = _load_raw()
        rows = raw.get("_configs") if isinstance(raw.get("_configs"), dict) else {}
        rows.pop(str(config_id), None)
        legacy = {k: v for k, v in raw.items() if k in FIELDS}
        p = _path()
        tmp = p.with_suffix(".json.tmp")
        with tmp.open("w", encoding="utf-8") as f:
            json.dump({**legacy, "_configs": rows}, f, ensure_ascii=False, indent=2)
        tmp.replace(p)


def masked_for(config_id: str) -> dict:
    return masked(load_for(config_id))


# 필드 -> 이 값을 덮어쓸 수 있는 환경변수. **여기가 유일한 정의다.**
# 예전에는 호출하는 쪽이 각자 이름을 적어 넣었고, 그 결과 같은 필드를 서로 다르게 읽었다:
#   llm_ready()   는 aoaiEndpoint 를 AOAI_ENDPOINT + AZURE_OPENAI_ENDPOINT 로 봤고
#   get_llm()     은 AOAI_ENDPOINT 만 봤다
# → 설정 화면은 "연결 준비됨"인데 실제 호출은 엔드포인트 없이 나간다. 이런 어긋남은
#   증상이 엉뚱한 곳에서 터져서 원인을 못 찾는다.
ENV_NAMES = {
    "aoaiEndpoint": ("AOAI_ENDPOINT", "AZURE_OPENAI_ENDPOINT"),
    "aoaiApiKey": ("AOAI_API_KEY", "AZURE_OPENAI_API_KEY"),
    "openaiApiKey": ("OPENAI_API_KEY",),
    "compatBaseUrl": ("LAKE_AGENT_COMPAT_BASE",),
    "compatApiKey": ("LAKE_AGENT_COMPAT_KEY",),
    "compatHeaders": ("LAKE_AGENT_COMPAT_HEADERS",),
    "embeddingBaseUrl": ("LAKE_AGENT_EMBED_BASE",),
    "embeddingApiKey": ("LAKE_AGENT_EMBED_KEY",),
    "embeddingHeaders": ("LAKE_AGENT_EMBED_HEADERS",),
    "langfusePublicKey": ("LANGFUSE_PUBLIC_KEY",),
    "langfuseSecretKey": ("LANGFUSE_SECRET_KEY",),
    "langfuseHost": ("LANGFUSE_HOST",),
}


def get(field: str, *env_names: str) -> str:
    """환경변수 우선, 없으면 저장된 값. 채점/사내 환경의 주입을 이기지 않게 이 순서를 지킨다.

    env_names 를 따로 주지 않으면 ENV_NAMES 를 쓴다 — 그쪽이 정본이다.
    """
    for name in (env_names or ENV_NAMES.get(field, ())):
        v = os.getenv(name)
        if v:
            return v.strip()
    return (load().get(field) or "").strip()


def env_overrides() -> dict:
    """지금 **환경변수가 이기고 있는** 필드 -> 그 변수 이름.

    화면이 이걸 보여 줘야 하는 이유: 설정창에 저장한 값이 보이는데 실제로는 환경변수가
    쓰이면, 사용자는 "저장이 안 되나?" 로 읽는다(실사용 지적). 저장은 됐고 **가려져 있을**
    뿐이라는 것을 말해 줘야 고칠 수 있다 — 우선순위 자체는 바꾸지 않는다(채점/사내 환경이
    주입한 값이 이기는 것이 정상 경로다).
    """
    out = {}
    for f, names in ENV_NAMES.items():
        for n in names:
            if os.getenv(n):
                out[f] = n
                break
    return out


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
