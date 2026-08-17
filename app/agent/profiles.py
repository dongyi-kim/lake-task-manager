"""사용자 정의 LLM 연결 설정.

설정 편집과 실제 활성화를 분리한다. 후보 config를 저장하거나 검증해도 현재 대화가 쓰는
config는 바뀌지 않으며, 같은 provider 종류도 이름만 다르면 여러 개 둘 수 있다.
"""
from __future__ import annotations

import uuid

from app.agent import secrets
from app.infra import prefs

PROVIDERS = ("aoai", "openai", "openai_compat", "fake")
FIELDS = ("name", "provider", "chatModel", "chatModelSimple", "embedModel", "apiVersion",
          "chatModelProfile", "embeddingProvider", "embeddingApiVersion",
          "embedRevision", "embedPrecision", "embedDimension", "embedNormalization")


def _rows() -> list[dict]:
    rows = prefs.load().get("agentConfigs") or []
    return [dict(x) for x in rows if isinstance(x, dict) and x.get("id")]


def list_all() -> list[dict]:
    state = prefs.load()
    active = str(state.get("agentActiveConfigId") or "")
    auth = state.get("agentAuthOkByConfig") or {}
    models = state.get("agentModelOkByConfig") or {}
    return [{**row, "active": row["id"] == active,
             "authOk": bool(auth.get(row["id"])), "modelsOk": bool(models.get(row["id"])),
             "secrets": secrets.masked_for(row["id"])} for row in _rows()]


def get(config_id: str) -> dict | None:
    return next((x for x in _rows() if x["id"] == str(config_id)), None)


def active() -> dict | None:
    return get(str(prefs.load().get("agentActiveConfigId") or ""))


def _validate_name(name: str, except_id: str = "") -> str:
    value = str(name or "").strip()
    if not value:
        raise ValueError("설정 이름을 입력하세요.")
    if any(x["id"] != except_id and str(x.get("name") or "").casefold() == value.casefold()
           for x in _rows()):
        raise ValueError("같은 이름의 설정이 이미 있습니다.")
    return value


def create(name: str, provider: str) -> dict:
    p = str(provider or "").strip().lower()
    if p not in PROVIDERS:
        raise ValueError("지원하지 않는 연결 방식입니다.")
    row = {"id": uuid.uuid4().hex, "name": _validate_name(name), "provider": p,
           "chatModel": "fake-chat" if p == "fake" else "",
           "chatModelSimple": "", "embedModel": "fake-embed" if p == "fake" else "",
           "apiVersion": "2024-10-21" if p == "aoai" else "",
           "chatModelProfile": "", "embeddingProvider": "", "embeddingApiVersion": "",
           "embedRevision": "", "embedPrecision": "", "embedDimension": "",
           "embedNormalization": ""}
    prefs.save({"agentConfigs": _rows() + [row]})
    return row


def update(config_id: str, patch: dict) -> dict:
    row = get(config_id)
    if not row:
        raise KeyError("설정을 찾을 수 없습니다.")
    clean = {}
    for key in FIELDS:
        if key not in (patch or {}):
            continue
        value = str(patch[key] or "").strip()
        if key == "name":
            value = _validate_name(value, row["id"])
        if key == "provider":
            value = value.lower()
            if value not in PROVIDERS:
                raise ValueError("지원하지 않는 연결 방식입니다.")
            if value != row.get("provider"):
                raise ValueError("연결 방식은 만든 뒤 바꿀 수 없습니다. 새 설정을 추가하세요.")
        if key == "embeddingProvider" and value and value not in PROVIDERS:
            raise ValueError("지원하지 않는 임베딩 연결 방식입니다.")
        if key == "embedDimension" and value:
            if not value.isdigit() or int(value) <= 0:
                raise ValueError("임베딩 dimension은 양의 정수여야 합니다.")
        clean[key] = value
    updated = {**row, **clean}
    prefs.save({"agentConfigs": [updated if x["id"] == row["id"] else x for x in _rows()]})
    return updated


def remove(config_id: str) -> None:
    cid = str(config_id)
    if prefs.load().get("agentActiveConfigId") == cid:
        raise ValueError("사용 중인 설정은 삭제할 수 없습니다. 다른 설정을 먼저 적용하세요.")
    if not get(cid):
        raise KeyError("설정을 찾을 수 없습니다.")
    state = prefs.load()
    auth = dict(state.get("agentAuthOkByConfig") or {})
    models = dict(state.get("agentModelOkByConfig") or {})
    auth.pop(cid, None)
    models.pop(cid, None)
    prefs.save({"agentConfigs": [x for x in _rows() if x["id"] != cid],
                "agentAuthOkByConfig": auth, "agentModelOkByConfig": models})
    secrets.delete_for(cid)


def set_active(config_id: str) -> dict:
    row = get(config_id)
    if not row:
        raise KeyError("설정을 찾을 수 없습니다.")
    prefs.save({"agentActiveConfigId": row["id"]})
    return row


def legacy_candidates() -> list[dict]:
    """기존 provider별 고정 슬롯을 모두 보여 주되 자동 활성화하지 않는다."""
    state = prefs.load()
    raw_secrets = secrets.load()
    masked = secrets.masked(raw_secrets)
    imported = set(state.get("agentImportedLegacyProviders") or [])
    slots = {
        "aoai": ("agentAoaiChat", "agentAoaiChatSimple", "agentAoaiEmbed",
                  ("aoaiEndpoint", "aoaiApiKey")),
        "openai": ("agentOpenaiChat", "agentOpenaiChatSimple", "agentOpenaiEmbed",
                   ("openaiApiKey",)),
        "openai_compat": ("agentCompatChat", "agentCompatChatSimple", "agentCompatEmbed",
                          ("compatBaseUrl", "compatApiKey", "compatHeaders")),
    }
    out = []
    for provider, (chat_key, simple_key, embed_key, secret_keys) in slots.items():
        if provider in imported:
            continue
        chat, simple, embed = (state.get(chat_key) or "", state.get(simple_key) or "",
                               state.get(embed_key) or "")
        if not (chat or simple or embed or any(raw_secrets.get(k) for k in secret_keys)):
            continue
        out.append({"provider": provider, "chatModel": chat, "chatModelSimple": simple,
                    "embedModel": embed, "apiVersion": state.get("agentApiVersion") or "",
                    "secrets": {k: masked.get(k, "") for k in secret_keys}})
    if state.get("agentProvider") == "fake" and "fake" not in imported:
        out.append({"provider": "fake", "chatModel": "fake-chat", "chatModelSimple": "",
                    "embedModel": "fake-embed", "apiVersion": "", "secrets": {}})
    return out


def legacy_candidate() -> dict | None:
    active_provider = str(prefs.load().get("agentProvider") or "")
    rows = legacy_candidates()
    return next((x for x in rows if x["provider"] == active_provider), rows[0] if rows else None)


def import_legacy(name: str, provider: str = "") -> dict:
    rows = legacy_candidates()
    old = next((x for x in rows if x["provider"] == provider), None) if provider else legacy_candidate()
    if not old:
        raise ValueError("가져올 이전 설정이 없습니다.")
    row = create(name, old["provider"])
    update(row["id"], {k: old[k] for k in
                        ("chatModel", "chatModelSimple", "embedModel", "apiVersion")})
    allowed = {"aoai": ("aoaiEndpoint", "aoaiApiKey"), "openai": ("openaiApiKey",),
               "openai_compat": ("compatBaseUrl", "compatApiKey", "compatHeaders")}.get(
                   old["provider"], ())
    legacy_secrets = {k: v for k, v in secrets.load().items() if k in allowed}
    if legacy_secrets:
        secrets.save_for(row["id"], legacy_secrets)
    imported = list(dict.fromkeys([*(prefs.load().get("agentImportedLegacyProviders") or []),
                                   old["provider"]]))
    prefs.save({"agentImportedLegacyProviders": imported})
    return get(row["id"])
