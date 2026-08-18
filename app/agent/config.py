"""agent/config.py — LLM provider 추상화 (4-way) + 연결 진단.

**왜 추상화하나** — 세 가지가 각각 다른 환경이다.
  · 채점/사내 환경: **AOAI**(Private Endpoint). 환경변수가 프로세스에 주입돼 온다.
  · 개발 PC: 사내 AOAI 가 `403 Public access is disabled` 로 막혀 있다 → 개인 **OpenAI** 키.
  · 향후 prod: **OpenAI 호환이지만 인증·헤더가 다른 자체 LLM**.
그래서 provider 를 갈아끼우는 지점을 한 곳으로 모은다. `auth/AuthProvider` 가 Jira 인증에 대해
하는 일과 같은 역할이다 — 나머지 코드는 어떤 LLM 인지 몰라야 한다.

**설정 우선순위**: 환경변수 > 저장값(prefs/secrets) > 기본값.
환경변수가 항상 이기는 이유는, 채점/사내 환경이 `AOAI_*` 를 주입해 주므로 설정 화면을 거치지
않아도 그대로 도는 것이 정상 경로이기 때문이다.

**설치 안 돼 있어도 import 는 된다** — langchain 은 선택 설치(`requirements-agent.txt`)라
`available()` 로 게이팅한 뒤에만 실제 객체를 만든다(devtools 게이팅과 같은 방식).
"""

from __future__ import annotations

import json
import logging
import os
import time

from app.agent import secrets as _secrets
from app.agent import profiles as _profiles
from app.agent.model_profiles import EXECUTION_LAYERS as _EXECUTION_LAYERS
from app.agent.model_profiles import profile_for_contract as _profile_for_contract
from app.agent.model_profiles import resolve as _resolve_model_config
from app.agent.model_profiles import supports_execution_layer as _supports_execution_layer
from app.agent.providers import ModelDefinition, adapter as _provider_adapter

log = logging.getLogger("agent.config")

PROVIDERS = ("aoai", "openai", "openai_compat", "fake")
DEFAULT_PROVIDER = "aoai"

# 채점환경이 `AOAI_API_VERSION` 은 주입하지 않는다(실측). GA 버전을 기본값으로 둔다 —
# 2024-10-21 로 chat·embeddings·function calling·structured output(strict)·streaming 전부 확인했다.
DEFAULT_API_VERSION = "2024-10-21"

DEFAULT_OPENAI_CHAT = "gpt-4o-mini"
DEFAULT_OPENAI_EMBED = "text-embedding-3-small"
_LANGFUSE_CACHE: dict[str, object] = {"signature": None, "client": None}


# ── 설치 게이팅 ────────────────────────────────────────────────────
def available() -> tuple[bool, str]:
    """(사용 가능?, 사유). 라우트 등록·설정 패널이 이걸 먼저 본다."""
    try:
        import langchain_core  # noqa: F401
        import langgraph       # noqa: F401
    except Exception as e:
        # 의존은 requirements.txt 에 들어 있다 — 여기 오면 **설치가 덜 끝난 것**이지
        # 사용자가 뭘 안 한 게 아니다. 그래서 pip 명령이 아니라 재시작을 안내한다.
        return False, f"에이전트 의존이 아직 설치되지 않았습니다({e}). " \
                      "앱을 다시 시작하면 설치가 이어집니다 — 반복되면 run.bat setup."
    return True, ""


# ── 이중 확인 게이트 ───────────────────────────────────────────────
# 사용자 지시: "인증정보 확인 AND 모델 연결 확인 → 저장까지 완료해야 해당 AI config 가
# 활성화되고 LLM 에서 활용 가능하게."
#
# 왜 필요한가 — 값이 **채워져 있다**와 **그 조합이 실제로 된다**는 다른 말이다. 예전에는
# 앞엣것만 보고 챗·에디터 AI 를 켰다. 그래서 키는 맞는데 모델 이름이 비었거나, 그 팀에
# 권한이 없는 모델을 골라 둔 상태로도 화면은 "쓸 수 있음"이었고, 실패는 **사용자가 실제로
# 무언가를 시킨 뒤에** 403/404 로 나타났다. 실패를 뒤로 미룬 셈이다.
#
# 그래서 **확인에 통과한 설정 조합의 지문**을 남기고, 지금 설정이 그 지문과 같을 때만 켠다.
# 지문이 달라지면(모델을 바꿨든 키를 갈았든) 다시 확인해야 한다 — 바뀐 조합은 확인된 적이 없다.
#
# ★ 환경변수로 주입된 환경은 **면제한다.** 채점/사내 배포는 `AOAI_*` 를 프로세스에 넣어 주고
#   설정 화면을 아무도 안 연다. 거기서 게이트를 걸면 정상 경로가 죽는다.
# 사용자가 정한 절차(2026-08-10):
#   ① 인증정보 변경 → [저장하고 연결 확인] → API·토큰 유효성 검증 후 저장
#   ② 모델정보 변경 → 목록 받아 콤보에서 고름 → [저장하고 모델 확인] → 사용 가능 여부 검증 후 저장
#   ③ ①②가 **모두** 확인된 경우에만 [이 설정 사용] 이 눌린다 → 그때 활성화된다
# 지문을 셋으로 나눠 두는 이유: 어느 단계까지 됐는지를 **상태로** 남겨야 화면이 그것을
# 말할 수 있고, 무엇을 고쳐야 하는지도 그 자리에서 정해진다.
_AUTH_KEY = "agentAuthOkSig"       # ① 인증만 (모델 이름 제외)
_MODEL_KEY = "agentModelOkSig"     # ② 모델까지 (인증 + 모델 3종)
_ACTIVE_KEY = "agentActiveSig"     # ③ 사용자가 '이 설정 사용'을 누른 조합


def _active_config_id() -> str:
    """환경변수가 provider를 고정하면 named config 선택을 사용하지 않는다."""
    if os.getenv("LAKE_AGENT_PROVIDER"):
        return ""
    row = _profiles.active()
    return str((row or {}).get("id") or "")


def _profile(config_id: str = "") -> dict | None:
    if config_id:
        return _profiles.get(config_id)
    return _profiles.active() if not os.getenv("LAKE_AGENT_PROVIDER") else None


def _secret(field: str, config_id: str = "") -> str:
    row = _profile(config_id)
    return _secrets.get_for(row["id"], field) if row else _secrets.get(field)


def _auth_signature(config_id: str = "") -> str:
    """**인증에만** 관계된 값들의 지문 — 모델 이름은 안 들어간다(별개의 확인이므로)."""
    import hashlib
    p = provider(config_id)
    key = (_secret("aoaiApiKey", config_id) if p == "aoai" else
           _secret("openaiApiKey", config_id) if p == "openai" else
           _secret("compatApiKey", config_id))
    base = (_secret("aoaiEndpoint", config_id) if p == "aoai" else
            compat_base(config_id) if p == "openai_compat" else "")
    parts = [config_id or _active_config_id(), p, base, key or "",
             _secret("compatHeaders", config_id) or "",
             _secret("simpleBaseUrl", config_id) or "",
             _secret("simpleApiKey", config_id) or "",
             _secret("simpleHeaders", config_id) or "",
             api_version(config_id) if p == "aoai" else "",
             embedding_provider(config_id), _secret("embeddingBaseUrl", config_id) or "",
             _secret("embeddingApiKey", config_id) or "",
             _secret("embeddingHeaders", config_id) or ""]
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:32]


def _mark(key: str, sig: str) -> str:
    try:
        from app.infra import prefs
        prefs.save({key: sig})
    except Exception:
        pass
    return sig


def auth_ok(config_id: str = "") -> bool:
    """① 인증 확인을 통과한 조합인가(모델과 무관)."""
    if not (config_id or _active_config_id()):
        return _env_supplied() or _pref(_AUTH_KEY) == _auth_signature()
    cid = config_id or _active_config_id()
    return (_pref("agentAuthOkByConfig", {}) or {}).get(cid) == _auth_signature(cid)


def models_ok(config_id: str = "") -> bool:
    """② 지금 고른 모델로 실제 호출이 통과한 조합인가."""
    if not (config_id or _active_config_id()):
        return _env_supplied() or _pref(_MODEL_KEY) == settings_signature()
    cid = config_id or _active_config_id()
    return (_pref("agentModelOkByConfig", {}) or {}).get(cid) == settings_signature(cid)


def activate(config_id: str = "") -> dict:
    """③ '이 설정 사용' — **①②가 다 끝났을 때만** 켠다.

    확인이 통과했다고 자동으로 켜지 않는 이유: 확인은 '되는지 보는 일'이고 활성화는
    '이걸 쓰겠다는 결정'이다. 둘을 붙이면 시험 삼아 눌러 본 조합이 그대로 운영 설정이 된다.
    """
    cid = config_id or _active_config_id()
    if cid and not _profiles.get(cid):
        return {"ok": False, "error": "설정을 찾을 수 없습니다."}
    if not auth_ok(cid):
        return {"ok": False, "error": "① 인증 확인이 아직입니다 — 인증 정보를 저장하고 연결을 확인하세요."}
    if not models_ok(cid):
        return {"ok": False, "error": "② 모델 확인이 아직입니다 — 모델을 고르고 저장하고 모델 확인을 누르세요."}
    if cid:
        _profiles.set_active(cid)
    return {"ok": True, "configId": cid,
            "sig": _mark(_ACTIVE_KEY, settings_signature(cid))}


def settings_signature(config_id: str = "") -> str:
    """지금 '연결에 쓰이는 값들'의 지문. 비밀 원문은 SHA-256 결과 밖으로 나오지 않는다."""
    import hashlib
    p = provider(config_id)
    key = (_secret("aoaiApiKey", config_id) if p == "aoai" else
           _secret("openaiApiKey", config_id) if p == "openai" else
           _secret("compatApiKey", config_id))
    base = (_secret("aoaiEndpoint", config_id) if p == "aoai" else
            compat_base(config_id) if p == "openai_compat" else "")
    parts = [config_id or _active_config_id(), p, base, key or "",
             api_version(config_id) if p == "aoai" else "",
             chat_model("complex", config_id), chat_model("simple", config_id), embed_model(config_id),
             _secret("simpleBaseUrl", config_id) or "",
             embedding_provider(config_id), _secret("embeddingBaseUrl", config_id) or "",
             str((_profile(config_id) or {}).get("chatModelProfile") or ""),
             str((_profile(config_id) or {}).get("chatModelSimpleProfile") or ""),
             str((_profile(config_id) or {}).get("embedRevision") or ""),
             str((_profile(config_id) or {}).get("embedPrecision") or ""),
             str((_profile(config_id) or {}).get("embedDimension") or ""),
             str((_profile(config_id) or {}).get("embedNormalization") or "")]
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:32]


def _env_supplied() -> bool:
    """이 provider 의 연결값이 **환경변수에서** 오고 있나 — 그러면 게이트를 면제한다.

    ★ `LAKE_AGENT_SKIP_VERIFY=1` 도 면제다 — **사람이 없는 경로**(배터리·평가 도구·헤드리스
      실행)를 위해서다. 게이트는 "설정 화면에서 사람이 확인했는가"를 묻는 장치인데, 화면을
      열 사람이 없는 실행에서는 물어볼 상대가 없다. 실제로 이 게이트를 넣은 직후 compose
      평가 배터리가 9/9 전부 "설정이 아직 확인되지 않았습니다"로 떨어졌다 —
      **가드가 자기 검증 수단을 막은 것**이고, 그러면 다음 회귀를 볼 눈이 사라진다.
    """
    if str(os.getenv("LAKE_AGENT_SKIP_VERIFY") or "").strip().lower() in ("1", "true", "yes"):
        return True
    # named config는 자기 비밀 저장소만 쓴다. 같은 프로세스에 우연히 있는 OPENAI_API_KEY가
    # 후보 검증을 면제하거나 활성 상태로 보이게 해서는 안 된다.
    if _active_config_id():
        return False
    ov = _secrets.env_overrides()
    need = {"aoai": ("aoaiApiKey",), "openai": ("openaiApiKey",),
            "openai_compat": ("compatBaseUrl",)}.get(provider(), ())
    return any(k in ov for k in need)


def mark_verified(config_id: str = "") -> str:
    """② 모델 확인 통과를 기록한다. probe() 가 **완전히** 성공했을 때만 불린다.

    인증 지문도 함께 찍는다 — 모델 호출이 됐다면 인증은 당연히 통과한 것이다.
    ★ 여기서 **활성화하지는 않는다.** 그건 사용자가 [이 설정 사용]으로 결정한다.
    """
    cid = config_id or _active_config_id()
    if cid:
        from app.infra import prefs
        auth = dict(prefs.load().get("agentAuthOkByConfig") or {})
        models = dict(prefs.load().get("agentModelOkByConfig") or {})
        auth[cid] = _auth_signature(cid)
        models[cid] = settings_signature(cid)
        prefs.save({"agentAuthOkByConfig": auth, "agentModelOkByConfig": models})
        return models[cid]
    _mark(_AUTH_KEY, _auth_signature())
    return _mark(_MODEL_KEY, settings_signature())


def verified() -> bool:
    """지금 이 조합이 **활성**인가 — 사용자가 '이 설정 사용'을 누른 그 조합인가."""
    if _env_supplied():
        return True
    cid = _active_config_id()
    if not cid and not os.getenv("LAKE_AGENT_PROVIDER"):
        return False
    if cid and _pref("agentActiveConfigId") != cid:
        return False
    return _pref(_ACTIVE_KEY) == settings_signature(cid)


def llm_ready() -> tuple[bool, str]:
    """**연결에 필요한 값이 하나라도 있는가** — 없으면 챗·에디터 AI 를 비활성으로 보인다.

    실제 호출까지 해 보는 것은 probe() 의 몫이다. 여기서는 "키를 안 넣었다"를 빠르게
    가른다 — 그 상태로 버튼을 살려 두면 사용자는 눌러 보고 나서야 에러로 알게 된다.
    """
    if (not _profiles.active() and not os.getenv("LAKE_AGENT_PROVIDER")
            and not _env_supplied()):
        return False, "사용할 연결 설정을 추가하고 적용하세요."
    p = provider()
    if p == "fake":
        return True, ""                     # 테스트 provider — 키가 필요 없다
    if p == "openai":
        if _secret("openaiApiKey"):
            return _gate()
        return False, "OpenAI API 키가 설정되지 않았습니다."
    if p == "aoai":
        if (_secret("aoaiEndpoint")
                and _secret("aoaiApiKey")):
            return _gate()
        return False, "Azure OpenAI 엔드포인트/키가 설정되지 않았습니다."
    if p == "openai_compat":
        if _secret("compatBaseUrl"):
            return _gate()
        return False, "호환 API 주소가 설정되지 않았습니다."
    return False, f"알 수 없는 provider: {p}"


def _gate() -> tuple[bool, str]:
    """값은 다 있다 — **그 조합이 확인됐나**가 남은 질문이다(위 '이중 확인 게이트' 참고)."""
    if verified():
        return True, ""
    return False, ("설정이 아직 확인되지 않았습니다 — 설정 → AI 에이전트에서 모델을 고르고 "
                   "**저장하고 확인**을 누르면 켜집니다. (키·모델을 바꾸면 다시 확인해야 합니다)")


# ── 설정 해석 ──────────────────────────────────────────────────────
def _pref(key, default=None):
    try:
        from app.infra import prefs
        v = prefs.load().get(key)
        return default if v in (None, "") else v
    except Exception:
        return default


def provider(config_id: str = "") -> str:
    row = _profile(config_id)
    p = ((row or {}).get("provider") or os.getenv("LAKE_AGENT_PROVIDER") or DEFAULT_PROVIDER).strip().lower()
    if p not in PROVIDERS:
        return DEFAULT_PROVIDER
    # ★ **prod 에서 'fake' 는 없는 것으로 친다**(사용자 지적). 실 Jira 를 보는 화면에서 가짜
    #   모델이 답을 만들면 그 답이 진짜처럼 보인다 — 화면에서 고르지 못하게 한 것만으로는
    #   부족하다(예전에 고른 값이 prefs 에 남아 있거나, 환경변수로 들어올 수 있다).
    #   가드는 **고르는 자리와 쓰는 자리 양쪽**에 있어야 한다 — 이 저장소가 반복해 배운 것.
    #   env 판정이 실패하면 막지 않는다(모르면 막지 않는다 — 개발 경로를 죽이지 않기 위해).
    if p == "fake":
        try:
            from app.infra.settings import get_settings
            if (get_settings().jira_env or "").lower() == "prod":
                return DEFAULT_PROVIDER
        except Exception:
            pass
    return p


def chat_model(tier: str = "complex", config_id: str = "") -> str:
    """provider 별 '모델/배포' 이름. AOAI 는 **모델명이 아니라 배포명**이다(흔한 실수).

    `tier="simple"` 은 manifest execution layer와 model-profile 자격 검사를 통과한 호출만
    사용한다. 설정이 비어 있으면 기본 모델로 폴백한다 — 모델 하나로 쓰는 사람에게는
    아무 변화가 없다.
    """
    row = _profile(config_id)
    p = provider(config_id)
    simple = tier == "simple"
    if row:
        if simple and str(row.get("chatModelSimple") or "").strip():
            return str(row["chatModelSimple"]).strip()
        return str(row.get("chatModel") or "").strip()
    if p == "aoai":
        if simple:
            m = os.getenv("LAKE_AGENT_AOAI_CHAT_SIMPLE") or _pref("agentAoaiChatSimple")
            if m:
                return m
        return (os.getenv("LAKE_AGENT_AOAI_CHAT") or _pref("agentAoaiChat")
                or os.getenv("AOAI_DEPLOY_GPT4O_MINI") or os.getenv("AOAI_DEPLOY_GPT4O") or "")
    if p == "openai":
        if simple:
            m = os.getenv("LAKE_AGENT_OPENAI_CHAT_SIMPLE") or _pref("agentOpenaiChatSimple")
            if m:
                return m
        return os.getenv("LAKE_AGENT_OPENAI_CHAT") or _pref("agentOpenaiChat") or DEFAULT_OPENAI_CHAT
    if p == "openai_compat":
        if simple:
            m = os.getenv("LAKE_AGENT_COMPAT_CHAT_SIMPLE") or _pref("agentCompatChatSimple")
            if m:
                return m
        return os.getenv("LAKE_AGENT_COMPAT_CHAT") or _pref("agentCompatChat") or ""
    return "fake-chat"


def embed_model(config_id: str = "") -> str:
    row = _profile(config_id)
    p = provider(config_id)
    if row:
        return str(row.get("embedModel") or "").strip()
    if p == "aoai":
        return (os.getenv("LAKE_AGENT_AOAI_EMBED") or _pref("agentAoaiEmbed")
                or os.getenv("AOAI_DEPLOY_EMBED_3_SMALL") or os.getenv("AOAI_DEPLOY_EMBED_3_LARGE") or "")
    if p == "openai":
        return os.getenv("LAKE_AGENT_OPENAI_EMBED") or _pref("agentOpenaiEmbed") or DEFAULT_OPENAI_EMBED
    if p == "openai_compat":
        return os.getenv("LAKE_AGENT_COMPAT_EMBED") or _pref("agentCompatEmbed") or ""
    return "fake-embed"


def embedding_provider(config_id: str = "") -> str:
    """Embedding provider. 비어 있으면 기존 chat provider를 그대로 사용한다."""
    row = _profile(config_id)
    value = (os.getenv("LAKE_AGENT_EMBED_PROVIDER") or
             (row or {}).get("embeddingProvider") or provider(config_id))
    value = str(value or "").strip().lower()
    return value if value in PROVIDERS else provider(config_id)


def api_version(config_id: str = "") -> str:
    row = _profile(config_id)
    return (os.getenv("AOAI_API_VERSION") or (row or {}).get("apiVersion")
            or _pref("agentApiVersion") or DEFAULT_API_VERSION)


def compat_base(config_id: str = "") -> str:
    """OpenAI 호환 엔드포인트의 base URL — **경로가 없으면 `/v1` 을 붙인다.**

    OpenAI SDK 는 base_url 뒤에 `/models`·`/chat/completions` 를 상대로 붙인다. 그래서
    사용자가 `https://llm.example` 만 넣으면 `https://llm.example/models` 를 부르고, 대부분의
    호환 서버(vLLM · Ollama · LM Studio · TGI)는 거기에 아무것도 없어 404 다. 화면에는
    "목록 조회 실패"로만 보이고, 무엇이 잘못인지는 안 보인다.

    ★ 이미 경로가 있으면 **그대로 둔다** — `/v1` 이든 `/openai/v1` 이든 `/api/v1` 이든
      사용자가 적어 넣은 것이 정답이다. 우리가 아는 것은 "경로가 아예 없으면 `/v1`" 하나뿐.

    ★ 이 함수를 **채팅·임베딩·모델목록 세 곳이 다 쓴다.** 한 곳만 고치면 "대화는 되는데
      모델 목록만 빈다"(또는 그 반대)가 되고, 그건 원인을 찾기 가장 어려운 종류의 어긋남이다.
    """
    raw = (_secret("compatBaseUrl", config_id) or "").strip().rstrip("/")
    if not raw:
        return raw
    try:
        from urllib.parse import urlsplit
        if not urlsplit(raw).path:            # 호스트만 적었다 — 규격 경로를 붙여 준다
            return raw + "/v1"
    except Exception:
        pass
    return raw


def _compat_headers(config_id: str = "") -> dict:
    """자체 LLM 이 요구하는 추가 헤더(JSON 문자열로 보관). 인증 방식이 표준과 다를 때 쓴다."""
    raw = _secret("compatHeaders", config_id)
    if not raw:
        return {}
    try:
        d = json.loads(raw)
        return {str(k): str(v) for k, v in d.items()} if isinstance(d, dict) else {}
    except Exception:
        return {}


def _json_headers(field: str, config_id: str = "") -> dict:
    raw = _secret(field, config_id)
    if not raw:
        return {}
    try:
        value = json.loads(raw)
        return {str(k): str(v) for k, v in value.items()} if isinstance(value, dict) else {}
    except Exception:
        return {}


def _normalise_compat_base(raw: str) -> str:
    raw = str(raw or "").strip().rstrip("/")
    if not raw:
        return raw
    try:
        from urllib.parse import urlsplit
        if not urlsplit(raw).path:
            return raw + "/v1"
    except Exception:
        pass
    return raw


def _chat_route_identity(provider_name: str, tier: str, model: str,
                         config_id: str = "") -> tuple[str, str, str]:
    """Connection identity used to decide whether two tiers are truly the same lane."""
    if provider_name == "aoai":
        base = str(_secret("aoaiEndpoint", config_id) or "").strip().rstrip("/")
    elif provider_name == "openai_compat":
        split = tier == "simple" and bool(_secret("simpleBaseUrl", config_id))
        base = (_normalise_compat_base(_secret("simpleBaseUrl", config_id))
                if split else compat_base(config_id))
    else:
        base = ""
    return provider_name, str(model or "").strip(), str(base or "").strip().rstrip("/")


def chat_definition(tier: str = "complex", model_override: str = "",
                    config_id: str = "") -> ModelDefinition:
    p = provider(config_id)
    model = (model_override or "").strip() or chat_model(tier, config_id)
    row = _profile(config_id) or {}
    complex_profile = str(row.get("chatModelProfile") or "")
    simple_profile = str(row.get("chatModelSimpleProfile") or "")
    if tier == "simple":
        complex_model = chat_model("complex", config_id)
        same_lane = _chat_route_identity(p, "simple", model, config_id) == \
            _chat_route_identity(p, "complex", complex_model, config_id)
        # Empty preserves old configs: one physical lane inherits the existing explicit
        # profile, while a distinct model or endpoint auto-matches and fails closed. An
        # arbitrary AOAI deployment alias can opt in through chatModelSimpleProfile.
        declared_profile = simple_profile or (complex_profile if same_lane else "")
    else:
        declared_profile = complex_profile
    if p == "aoai":
        return ModelDefinition(p, model, _secret("aoaiEndpoint", config_id),
                               _secret("aoaiApiKey", config_id), api_version(config_id),
                               model_profile=declared_profile)
    if p == "openai":
        return ModelDefinition(p, model, api_key=_secret("openaiApiKey", config_id),
                               model_profile=declared_profile)
    split_simple = tier == "simple" and bool(_secret("simpleBaseUrl", config_id))
    base = (_normalise_compat_base(_secret("simpleBaseUrl", config_id))
            if split_simple else compat_base(config_id))
    key = (_secret("simpleApiKey", config_id) if split_simple else "") \
        or _secret("compatApiKey", config_id) or "unused"
    headers = (_json_headers("simpleHeaders", config_id) if split_simple else {}) \
        or _compat_headers(config_id)
    return ModelDefinition(p, model, base, key, headers=headers,
                           model_profile=declared_profile)


def embedding_definition(config_id: str = "") -> ModelDefinition:
    """Resolve the independent embedding connection, with legacy chat fallback."""
    p = embedding_provider(config_id)
    # Keep the no-argument call path for older integrations/tests that override ``embed_model``.
    model = embed_model(config_id) if config_id else embed_model()
    row = _profile(config_id) or {}
    split_base = (_secret("embeddingBaseUrl", config_id) or "").strip()
    split_key = _secret("embeddingApiKey", config_id)
    split_headers = _json_headers("embeddingHeaders", config_id)
    version = (os.getenv("LAKE_AGENT_EMBED_API_VERSION") or
               row.get("embeddingApiVersion") or api_version(config_id))
    if p == "aoai":
        return ModelDefinition(p, model, split_base or _secret("aoaiEndpoint", config_id),
                               split_key or _secret("aoaiApiKey", config_id), str(version or ""))
    if p == "openai":
        return ModelDefinition(p, model, api_key=split_key or _secret("openaiApiKey", config_id))
    if p == "fake":
        return ModelDefinition(p, model)
    return ModelDefinition(p, model,
                           _normalise_compat_base(split_base) or compat_base(config_id),
                           split_key or _secret("compatApiKey", config_id) or "unused",
                           headers=split_headers or _compat_headers(config_id))


def embedding_identity(config_id: str = "", chunking_version: str = "v1") -> dict:
    """Stable vector namespace identity. Different identities must never share an index."""
    row = _profile(config_id) or {}
    definition = embedding_definition(config_id)
    return {
        "embedding_model": definition.model,
        "provider": definition.provider,
        "model_revision": str(row.get("embedRevision") or "unknown"),
        "precision": str(row.get("embedPrecision") or "unknown"),
        "dimension": int(row["embedDimension"]) if str(row.get("embedDimension") or "").isdigit() else None,
        "normalization": str(row.get("embedNormalization") or "unknown"),
        "chunking_version": chunking_version,
        "source_commit": os.getenv("LTM_BUILD_SHA") or "unknown",
        "config_version": 1,
    }


def sampling_unsupported(model: str) -> bool:
    """이 모델이 temperature 등 샘플링 파라미터를 거부하는가.

    OpenAI reasoning 계열(gpt-5 시리즈, o1/o3/o4 시리즈)은 temperature 를 기본값(1)
    외에 못 받는다. AOAI 는 배포명이라 모델명을 못 믿지만, 배포명도 대개 모델명을
    포함하므로(gpt-5-mini 등) 부분 일치로 본다 — 틀려도 방향이 안전하다(temperature
    를 빼는 쪽은 동작하고, 넣는 쪽은 400 으로 죽는다).
    """
    import re as _re
    m = (model or "").lower()
    return bool(_re.search(r"(?:^|[/_-])(gpt-5|o[134])(?:$|[.-])", m))


# ── 팩토리 ─────────────────────────────────────────────────────────
def execution_tier(layer: str, config_id: str = "") -> str:
    """Resolve a semantic execution layer to an evaluated model tier.

    Deep semantic judgment always stays on the configured complex model. Projection and
    lightweight semantic work may use the split-simple endpoint only when that endpoint's
    model profile explicitly qualifies for the requested layer. JSON/tool support is not
    semantic qualification and therefore never participates in this decision.
    """
    if layer not in _EXECUTION_LAYERS:
        raise ValueError(f"알 수 없는 execution layer: {layer}")
    if layer == "deterministic":
        raise ValueError("deterministic execution layer는 LLM을 호출하지 않습니다.")
    if layer == "deep_semantic":
        return "complex"

    complex_definition = chat_definition("complex", config_id=config_id)
    simple_definition = chat_definition("simple", config_id=config_id)
    complex_identity = (complex_definition.provider, complex_definition.model,
                        complex_definition.base_url)
    simple_identity = (simple_definition.provider, simple_definition.model,
                       simple_definition.base_url)
    if not simple_definition.model or simple_identity == complex_identity:
        return "complex"
    if _supports_execution_layer(
            simple_definition.model, layer,
            explicit_model_profile=simple_definition.model_profile):
        return "simple"
    return "complex"


def typed_projection_tier(tier: str = "complex", config_id: str = "") -> str:
    """Return the configured transport tier for literal typed projection.

    The model profile, not a model-name branch, owns this capability.  Keeping the
    lookup separate from :func:`get_llm` lets StructuredAgent decide whether a Role's
    semantic contract calls for a two-stage invocation before it sends any prompt.
    """
    delegate_tier = execution_tier("projection", config_id)
    if not delegate_tier or delegate_tier == tier:
        return ""
    delegated = chat_definition(delegate_tier, config_id=config_id)
    return delegate_tier if delegated.model else ""


def get_llm(temperature: float | None = None, tier: str = "complex", model_override: str = "",
            config_id: str = "", profile: str = "balanced", role_id: str = "",
            output_contract: str = "", **kwargs):
    """provider 에 맞는 chat 모델. 나머지 코드는 이 함수만 부른다.

    `tier` 로 역할별 모델을 가른다 — simple(의도 분류·결정적 실행)은 저렴한 모델,
    complex(조사·초안·검토·작문)는 기본 모델. simple 모델 미설정이면 기본 모델 하나로 돈다.
    """
    p = provider(config_id)
    if p == "fake":
        from app.agent.fake import FakeChat
        return FakeChat(**{k: v for k, v in kwargs.items() if k == "responses"})

    ok, why = available()
    if not ok:
        raise RuntimeError(why)

    # 429(TPM 한도)는 몇 초 뒤 그냥 풀린다 — SDK 가 Retry-After 를 존중하며 재시도한다.
    # 기본 2회로는 상위 모델(TPM 30k 조직에서 한 턴 ~70k 토큰)에서 실측으로 죽었다.
    kwargs.setdefault("max_retries", 6)
    # 스트리밍 응답에도 usage 를 싣게 한다 — 토큰 스트리밍(stream_mode="messages")을 켜자
    # 계측이 전부 0 이 됐다(실측). 마지막 청크에 usage 가 실려야 Meter 가 잡는다.
    kwargs.setdefault("stream_usage", True)

    # model_override 는 '권한 확인'처럼 **특정 모델 하나를 시험**할 때만 쓴다(설정 화면).
    definition = chat_definition(tier, model_override, config_id)
    # Only a literal typed projection may change endpoints. ``structured`` describes a
    # wire contract, not the semantic difficulty of the work, so JSON capability must
    # never silently move Request/Query/People judgment to a smaller model.
    if output_contract == "typed_projection" and not model_override:
        delegate_tier = typed_projection_tier(tier, config_id)
        if delegate_tier:
            delegated = chat_definition(delegate_tier, config_id=config_id)
            log.debug(
                "typed projection delegated contract=%s tier=%s->%s model=%s->%s",
                output_contract, tier, delegate_tier, definition.model, delegated.model,
            )
            definition = delegated
    model = definition.model
    # ★ **모델 이름이 비었으면 여기서 멈춘다.** 빈 이름으로 부르면 서버는 대개
    #   `404 /v1/chat/completions` 로 답하고(모델을 못 찾았다는 뜻인데 경로가 없다는 말로
    #   읽힌다), 사용자는 주소·키를 의심하며 시간을 버린다(실사용 지적).
    #   AOAI 는 배포명, 나머지는 모델명 — 어느 쪽이든 **비어 있으면 호출이 성립하지 않는다.**
    if not str(model or "").strip():
        raise RuntimeError(_no_model_msg(p, tier))
    # ★ reasoning 계열(gpt-5*, o1/o3/o4*)은 temperature 를 못 받는다 — 실측:
    #   "'temperature' does not support 0.4 ... Only the default (1)" 400 으로 전 역할 사망.
    #   역할별 temperature 는 그 계열에선 의미가 없으니 **아예 넘기지 않는다**.
    explicit = dict(kwargs)
    if temperature is not None:
        explicit["temperature"] = temperature
    effective_profile = _profile_for_contract(
        model, profile, output_contract, explicit_model_profile=definition.model_profile,
    )
    effective = _resolve_model_config(model, p, effective_profile,
                                      explicit_model_profile=definition.model_profile,
                                      output_contract=output_contract,
                                      semantic_profile=profile,
                                      explicit=explicit)
    log.debug("LLM role=%s requestedProfile=%s outputContract=%s definition=%s effective=%s",
              role_id, profile, output_contract, definition.debug(), effective.debug())
    return _provider_adapter(p).chat(definition, effective.parameters)


def _no_model_msg(p: str, tier: str = "complex", embed: bool = False) -> str:
    what = "임베딩 모델" if embed else ("간단한 역할 모델" if tier == "simple" else "채팅 모델")
    where = "배포명" if p == "aoai" else "모델 이름"
    return (f"{what}이 설정되지 않았습니다 — 설정 → AI 에이전트 → 모델에서 {where}을 "
            f"고르거나 직접 입력하세요. (빈 이름으로 부르면 서버가 404 로 답합니다)")


def get_embeddings(config_id: str = "", **kwargs):
    """임베딩 클라이언트.

    ★ **`check_embedding_ctx_length=False` 를 기본으로 준다**(실사용 사고: "임베딩 모델만
      자꾸 SSL verify failed, max retries").

      원인은 SSL 설정도 임베딩 서버도 아니었다. langchain 의 `OpenAIEmbeddings` 는 기본값에서
      **보내기 전에 tiktoken 으로 토큰 수를 세어** 모델 한도에 맞춰 쪼갠다. 그 tiktoken 이
      인코딩 파일을 처음 쓸 때 `openaipublic.blob.core.windows.net` 에서 **requests 로
      내려받는다** — 사내망의 TLS 가로채기에 걸려 `SSLError ... Max retries exceeded` 가 난다.
      채팅은 토큰을 안 세니 멀쩡하다. 그래서 **임베딩만** 실패한다(실측으로 확인:
      tiktoken 을 막아 두면 채팅은 통과하고 임베딩만 죽는다).

      우리에게 그 쪼개기는 **필요가 없다.** 색인에 넣기 전에 `retrieval/chunk.py` 가 이미
      1200자 상한으로 자른다(어떤 임베딩 모델의 한도보다도 한참 아래다). 즉 이 기능은
      우리 쪽에서 얻는 것 없이 **외부 네트워크 의존만 늘린다.**

      호출부가 명시적으로 넘기면 그 값을 존중한다(kwargs 우선).
    """
    kwargs.setdefault("check_embedding_ctx_length", False)
    definition = embedding_definition(config_id)
    p = definition.provider
    if p == "fake":
        from app.agent.fake import FakeEmbeddings
        return FakeEmbeddings()

    ok, why = available()
    if not ok:
        raise RuntimeError(why)

    if not str(definition.model or "").strip():
        raise RuntimeError(_no_model_msg(p, embed=True))
    return _provider_adapter(p).embeddings(definition, kwargs)


def get_langfuse_handler(session_id: str = None):
    """Langfuse 콜백. **설정이 없으면 None** — 관측이 없다고 앱이 죽으면 안 된다."""
    pk = _secrets.get("langfusePublicKey")
    sk = _secrets.get("langfuseSecretKey")
    if not (pk and sk):
        return None
    try:
        from langfuse import Langfuse
        from langfuse.langchain import CallbackHandler
        host = _secrets.get("langfuseHost") or None
        signature = (str(pk), str(sk), str(host or ""))
        if _LANGFUSE_CACHE.get("signature") != signature:
            _LANGFUSE_CACHE.update(
                signature=signature,
                client=Langfuse(
                    public_key=pk, secret_key=sk, base_url=host,
                ),
            )
        # Langfuse v4 binds the conversation via LangChain invocation metadata rather
        # than a CallbackHandler constructor argument. ``session_id`` remains accepted
        # here so existing callers do not need a second observability API.
        return CallbackHandler(public_key=pk)
    except Exception:
        return None


def callbacks(session_id: str = None) -> list:
    h = get_langfuse_handler(session_id)
    return [h] if h else []


def _runtime_config_source() -> str:
    """화면에 표시할 실제 연결 설정의 출처.

    ``provider()`` 의 AOAI 기본값은 내부 해석을 안전하게 유지하기 위한 폴백이지,
    사용자가 AOAI 연결을 선택했다는 뜻이 아니다. named config도 환경 주입도 없을 때
    그 기본값을 상태 UI에 내보내면 설정하지 않은 연결이 활성화된 것처럼 보인다.
    """
    if os.getenv("LAKE_AGENT_PROVIDER"):
        return "environment"
    if _profiles.active():
        return "named"

    overrides = _secrets.env_overrides()
    required = {"aoai": ("aoaiEndpoint", "aoaiApiKey"),
                "openai": ("openaiApiKey",),
                "openai_compat": ("compatBaseUrl",)}.get(provider(), ())
    return "environment" if any(field in overrides for field in required) else "none"


# ── 진단 (설정 패널의 '연결 테스트') ────────────────────────────────
def status() -> dict:
    """화면용 현재 설정 — **비밀값 원문은 절대 싣지 않는다**."""
    ok, why = available()
    from app.infra import prefs as _prefs
    from app.agent.prompts.base import _project_prompt
    ready, ready_why = llm_ready()
    configs = []
    for row in _profiles.list_all():
        cid = row["id"]
        configs.append({**row, "authOk": auth_ok(cid), "modelsOk": models_ok(cid),
                        "verified": bool(row.get("active") and verified())})
    active = _profiles.active()
    source = _runtime_config_source()
    show_runtime = source != "none"
    runtime_provider = provider() if show_runtime else ""
    return {"available": ok, "reason": why,
            "runtimeConfigSource": source,
            "provider": runtime_provider,
            "configs": configs,
            "activeConfigId": str((active or {}).get("id") or ""),
            "activeConfig": ({**active, "secrets": _secrets.masked_for(active["id"])}
                             if active else None),
            "legacyCandidate": _profiles.legacy_candidate(),
            "legacyCandidates": _profiles.legacy_candidates(),
            # 하나 이상의 LLM 연결값이 있는가 — 챗·에디터 AI 버튼의 활성/비활성 근거.
            "llmReady": ready, "llmReason": ready_why,
            "chatModel": chat_model() if show_runtime else "",
            "simpleTarget": (chat_definition("simple").debug() if show_runtime else {}),
            "embedModel": embed_model() if show_runtime else "",
            "embeddingProvider": embedding_provider() if show_runtime else "",
            "embeddingTarget": (embedding_definition().debug() if show_runtime else {}),
            # 간단한 역할(의도 분류·결정적 실행) 전용 모델 — **설정된 값만**(폴백 없이) 보여
            # 준다. 폴백값을 보여 주면 화면에서 "따로 설정돼 있다"로 오해된다.
            "chatModelSimple": (str((active or {}).get("chatModelSimple") or "") if active else
                                (_pref("agentAoaiChatSimple") if runtime_provider == "aoai"
                                 else _pref("agentOpenaiChatSimple") if runtime_provider == "openai"
                                 else _pref("agentCompatChatSimple") if runtime_provider == "openai_compat"
                                 else "")),
            "apiVersion": api_version() if runtime_provider == "aoai" else None,
            "langfuse": bool(get_langfuse_handler()),
            "secrets": (_secrets.masked_for(active["id"]) if active else _secrets.masked()),
            # ★ **환경변수가 이기고 있는 필드**. 저장은 됐는데 가려져 있는 상태를 화면이
            #   말해 주지 않으면 사용자는 "저장이 안 되나?"로 읽는다(실사용 지적).
            #   우선순위 자체는 안 바꾼다 — 채점/사내 환경의 주입이 이기는 것이 정상 경로다.
            "envOverrides": _secrets.env_overrides(),
            # 이중 확인 통과 여부 — 화면이 "왜 아직 안 켜졌나"를 말할 수 있어야 한다.
            "verified": verified(), "envSupplied": _env_supplied(),
            # 두 확인을 **따로** 보고한다 — 화면이 "어디까지 됐나"를 말할 수 있어야 한다.
            "authOk": auth_ok(), "modelsOk": models_ok(),
            # 프롬프트 레이어 — 사용자별은 편집 가능, 프로젝트 공용은 읽기 전용 표시.
            "userPrompt": str(_prefs.load().get("agentUserPrompt") or ""),
            "projectPrompt": _project_prompt()}


def probe_auth(timeout: float = 20.0, config_id: str = "") -> dict:
    """**인증만** 확인한다 — 모델 이름을 쓰지 않는 호출로.

    ★ 사용자 지적: "인증정보 변경하는데 왜 모델 403 이 나냐. 인증 확인이랑 모델 설정은
      별개의 확인을 거치게 하라."  맞는 말이고, 예전 구현이 틀렸다.
      키를 바꾸면 곧바로 `chat/completions` 를 불렀는데 그 호출에는 **모델 이름이 실린다.**
      아직 모델을 안 골랐거나 그 팀에 권한이 없으면 403/404 가 나고, 화면에는 "방금 넣은
      인증이 틀렸다"로 보인다. **키는 멀쩡한데** 사용자는 키를 다시 친다.

    그래서 인증 확인은 **모델이 필요 없는 엔드포인트**로 한다:
      · OpenAI · 호환 : `GET {base_url}/models`
      · AOAI          : `GET {endpoint}/openai/deployments` (api-key 로 열린다)
    이 호출이 되면 "주소·키는 옳다"가 증명된다. 모델이 되는지는 **그다음 질문**이다.
    """
    out = {"provider": provider(config_id), "configId": config_id, "ok": False}
    ok, why = available()
    if not ok:
        return {**out, "error": why}
    t0 = time.time()
    r = list_models(timeout=timeout, config_id=config_id)
    out["ms"] = int((time.time() - t0) * 1000)
    if r.get("error"):
        return {**out, "error": r["error"]}
    out["ok"] = True
    out["models"] = {"chat": len(r.get("chat") or []), "embed": len(r.get("embed") or []),
                     "total": r.get("total")}
    if config_id:
        from app.infra import prefs
        auth = dict(prefs.load().get("agentAuthOkByConfig") or {})
        auth[config_id] = _auth_signature(config_id)
        prefs.save({"agentAuthOkByConfig": auth})
    else:
        _mark(_AUTH_KEY, _auth_signature())
    return out


def probe(timeout: float = 30.0, config_id: str = "") -> dict:
    """실제로 한 번씩 호출해 본다. 설정 화면이 '되는지'를 눈으로 확인하는 용도.

    실패를 삼키지 않는다 — 어디서 막혔는지(설정 누락 / 네트워크 / 인증)가 화면에 보여야
    사용자가 고칠 수 있다. 사내 AOAI 는 개발 PC 에서 403 이 정상이므로 그 문구를 그대로 보인다.
    """
    out = {"provider": provider(config_id), "configId": config_id,
           "chat": None, "simple": None, "embeddings": None, "ok": False}
    ok, why = available()
    if not ok:
        out["error"] = why
        return out

    t0 = time.time()
    try:
        msg = get_llm(temperature=0, max_tokens=5, config_id=config_id).invoke(
            "reply with the single word: pong")
        out["chat"] = {"ok": True, "ms": int((time.time() - t0) * 1000),
                       "sample": str(getattr(msg, "content", msg))[:60]}
    except Exception as e:
        out["chat"] = {"ok": False, "ms": int((time.time() - t0) * 1000), "error": _brief(e)}

    complex_definition = chat_definition("complex", config_id=config_id)
    simple_definition = chat_definition("simple", config_id=config_id)
    simple_is_split = (
        simple_definition.model != complex_definition.model
        or str(simple_definition.base_url or "").rstrip("/")
        != str(complex_definition.base_url or "").rstrip("/")
    )
    if simple_is_split:
        ts = time.time()
        try:
            msg = get_llm(temperature=0, max_tokens=5, tier="simple",
                          config_id=config_id).invoke("reply with the single word: pong")
            out["simple"] = {"ok": True, "ms": int((time.time() - ts) * 1000),
                             "sample": str(getattr(msg, "content", msg))[:60]}
        except Exception as e:
            out["simple"] = {"ok": False, "ms": int((time.time() - ts) * 1000),
                             "error": _brief(e)}

    t1 = time.time()
    try:
        vec = get_embeddings(config_id=config_id).embed_query("연결 테스트")
        import math
        norm = math.sqrt(sum(float(x) * float(x) for x in vec))
        embedding_meta = _profile(config_id) or {}
        expected_dim = str(embedding_meta.get("embedDimension") or "")
        expected_norm = str(embedding_meta.get("embedNormalization") or "").casefold()
        if expected_dim.isdigit() and len(vec) != int(expected_dim):
            raise ValueError(f"임베딩 dimension 불일치: expected={expected_dim}, actual={len(vec)}")
        if expected_norm == "l2" and not 0.98 <= norm <= 1.02:
            raise ValueError(f"임베딩 L2 normalization 불일치: norm={norm:.6f}")
        out["embeddings"] = {"ok": True, "ms": int((time.time() - t1) * 1000), "dim": len(vec)}
        out["embeddings"]["l2Norm"] = round(norm, 6)
    except Exception as e:
        out["embeddings"] = {"ok": False, "ms": int((time.time() - t1) * 1000), "error": _brief(e)}

    # plain chat 성공만으로 structured output/tool calling까지 된다고 간주하지 않는다.
    # 프로젝트 데이터가 전혀 없는 합성 요청으로 tier별 capability를 분리 진단한다.
    if out["chat"]["ok"]:
        try:
            from app.agent.capabilities import probe_all
            out["capabilities"] = probe_all(config_id)
            out["degraded"] = any(x.get("degraded") for x in out["capabilities"].values())
        except Exception as e:
            out["capabilities"] = {"error": _brief(e)}
            out["degraded"] = True

    out["ok"] = bool(out["chat"]["ok"] and out["embeddings"]["ok"]
                     and (out["simple"] is None or out["simple"]["ok"]))
    # ★ **둘 다 통과했을 때만** 이 조합을 확인된 것으로 남긴다(사용자 지시: 이중 확인).
    #   채팅만 되고 임베딩이 막힌 상태를 '됐다'로 치면, 색인이 필요한 순간에 다시 터진다.
    if out["ok"]:
        out["verifiedSig"] = mark_verified(config_id)
    return out


def list_models(timeout: float = 10.0, config_id: str = "") -> dict:
    """지금 설정된 provider 에서 **실제로 쓸 수 있는** 모델/배포 목록.

    설정 화면의 콤보박스 재료다. 목록 조회가 막히는 환경이 있으므로(권한 없는 키, 프록시,
    자체 LLM 의 미구현) 실패해도 빈 목록+사유만 준다 — 화면은 자유 입력으로 폴백한다.
    ★ 목록은 참고이지 제약이 아니다. 조회가 안 된다고 입력까지 막으면 안 된다.

    반환: {"chat": [...], "embed": [...], "error": ""}
    """
    p = provider(config_id)
    if p == "fake":
        return {"chat": ["fake-chat"], "simple": ["fake-chat"],
                "embed": ["fake-embed"], "total": 2, "error": ""}
    ok, why = available()
    if not ok:
        return {"chat": [], "embed": [], "total": 0, "error": why}

    try:
        if p == "aoai":
            # AOAI 는 모델이 아니라 **배포**를 골라야 한다. 데이터플레인 deployments 목록은
            # api-key 로 열린다(관리플레인 자격 증명 불필요).
            import httpx
            base = (_secret("aoaiEndpoint", config_id) or "").rstrip("/")
            key = _secret("aoaiApiKey", config_id)
            if not (base and key):
                return {"chat": [], "embed": [], "total": 0,
                        "error": "엔드포인트/키가 설정되지 않았습니다."}
            r = httpx.get(f"{base}/openai/deployments",
                          params={"api-version": "2023-03-15-preview"},
                          headers={"api-key": key}, timeout=timeout)
            r.raise_for_status()
            rows = r.json().get("data") or []
            pairs = [((d.get("id") or ""), str(d.get("model") or "")) for d in rows]
            is_embed = lambda n, m: "embed" in n.lower() or "embed" in m.lower()  # noqa: E731
            result = {"chat": sorted(n for n, m in pairs if n and not is_embed(n, m)),
                      "simple": [],
                      "embed": sorted(n for n, m in pairs if n and is_embed(n, m)), "error": ""}
            return _with_split_embedding_models(
                _with_split_simple_models(result, timeout, config_id), timeout, config_id)

        from openai import OpenAI
        if p == "openai":
            cli = OpenAI(api_key=_secret("openaiApiKey", config_id), timeout=timeout)
        else:
            cli = OpenAI(api_key=_secret("compatApiKey", config_id) or "unused",
                         base_url=compat_base(config_id),
                         default_headers=_compat_headers(config_id) or None, timeout=timeout)
        rows = [(m.model_dump() if hasattr(m, "model_dump") else {"id": getattr(m, "id", "")})
                for m in cli.models.list()]           # GET {base_url}/models
        ids = [str(d.get("id") or "") for d in rows if d.get("id")]
        # ★ **권한 없는 모델을 목록에서 뺀다**(사용자 요청). 게이트웨이는 대개 자기가 아는
        #   모델을 전부 늘어놓고, 그중 내 키로 못 부르는 것이 섞여 있다 — 골라 놓고 나서야
        #   403 을 본다. 서버가 알려 주는 신호가 있으면 그것부터 쓴다(호출 0회, 정확).
        #   신호가 없으면 여기서는 아무것도 안 뺀다 — 짐작으로 지우면 쓸 수 있는 모델이
        #   사라지고, 그건 더 나쁘다. 그때는 화면의 '권한 확인'(실제 호출)이 가른다.
        denied = {i for i, d in zip(ids, rows) if _model_denied(d)}
        if denied:
            ids = [i for i in ids if i not in denied]
        # 임베딩 판정도 이름에 기댄다. 호환 서버의 임베딩 모델은 'embed' 를 안 달고 오는
        # 일이 흔해서(bge-m3 · e5-large · gte · jina · nomic) 그 이름들을 함께 본다.
        _emb = ("embed", "bge", "e5-", "gte-", "jina", "minilm", "nomic")
        embed = sorted(i for i in ids
                       if ("embed" in i if p != "openai_compat"
                           else any(x in i.lower() for x in _emb)))
        # 채팅에 못 쓰는 것(음성·이미지·중재 등)을 걸러낸다 — 다 보여 주면 목록이 소음이 된다.
        noise = ("audio", "tts", "whisper", "image", "dall-e", "realtime",
                 "transcribe", "moderation", "computer-use", "codex")
        # 채팅 후보에서 **임베딩으로 분류된 것을 뺀다** — 판정을 넓히면(bge·e5·gte…) 그
        # 이름들이 양쪽에 다 뜬다. 한 모델이 두 칸에 있으면 어느 쪽이 맞는지 화면이 말을
        # 못 한다(실측: 스텁 서버에서 bge-m3 가 채팅 목록에도 있었다).
        _emb_set = set(embed)
        keep = [i for i in ids if i not in _emb_set and not any(x in i for x in noise)]
        # ★ **이름 화이트리스트는 OpenAI 에만 건다.** `gpt` 를 포함하거나 `o` 로 시작하는
        #   것만 남기는 규칙은 OpenAI 카탈로그를 두고 만든 것이라, 호환 서버에 대면
        #   `llama-3-70b`·`qwen2.5`·`solar-pro`·`mistral` 이 **한 줄도 안 남는다** —
        #   조회는 성공했는데 목록이 비어, 화면에는 "실패"조차 안 뜨고 그냥 빈 채로 보인다.
        #   호환 쪽은 소음만 걷어내고 **서버가 준 것을 그대로 보여 준다**: 무엇이 채팅
        #   모델인지 아는 것은 우리가 아니라 그 서버다.
        chat = sorted(keep if p == "openai_compat"
                      else [i for i in keep if "gpt" in i or i.startswith("o")])
        # `total` — **서버가 실제로 준 개수**. 우리가 걸러낸 것이 있으면 화면이 그 사실을
        #   말해 줘야 한다(사용자 지적: "직접 /v1/models 날려본 것과 목록이 다르다").
        #   거르는 것 자체는 필요하지만, **몇 개를 거를지는 사용자가 알아야 할 사실**이다.
        result = {"chat": chat, "simple": [], "embed": embed,
                  "total": len(ids), "error": ""}
        return _with_split_embedding_models(
            _with_split_simple_models(result, timeout, config_id), timeout, config_id)
    except Exception as e:
        return {"chat": [], "embed": [], "total": 0, "error": _brief(e)}


def _with_split_simple_models(result: dict, timeout: float, config_id: str = "") -> dict:
    """Load the simple-role model catalog from its independent compatible endpoint."""
    complex_definition = chat_definition("complex", config_id=config_id)
    simple = chat_definition("simple", config_id=config_id)
    same_connection = (
        simple.provider == complex_definition.provider
        and str(simple.base_url or "").rstrip("/")
        == str(complex_definition.base_url or "").rstrip("/")
    )
    if same_connection:
        result["simple"] = list(result.get("chat") or [])
        return result
    configured = [simple.model] if str(simple.model or "").strip() else []
    discovered: list[str] = []
    warning = ""
    try:
        if simple.provider == "fake":
            discovered = configured or ["fake-chat"]
        elif simple.provider == "aoai":
            # A separate AOAI endpoint is not currently configurable; retain direct input only.
            discovered = configured
        else:
            from openai import OpenAI
            kwargs = {"api_key": simple.api_key or "unused", "timeout": timeout}
            if simple.provider == "openai_compat":
                kwargs.update({"base_url": simple.base_url,
                               "default_headers": simple.headers or None})
            client = OpenAI(**kwargs)
            discovered = [str(getattr(model, "id", "") or "")
                          for model in client.models.list()]
    except Exception as exc:
        warning = "simple model catalog: " + _brief(exc)
    result["simple"] = sorted(dict.fromkeys([*configured, *discovered]))
    if warning:
        result.setdefault("warnings", []).append(warning)
    return result


def _with_split_embedding_models(result: dict, timeout: float, config_id: str = "") -> dict:
    """Load a dedicated embedding catalog without making ``/models`` mandatory.

    TEI guarantees ``/v1/embeddings`` but deployments can omit ``/v1/models``. In that case the
    configured embedding model remains selectable and the later embedding probe is authoritative.
    """
    chat = chat_definition(config_id=config_id)
    embedding = embedding_definition(config_id)
    same_connection = (
        embedding.provider == chat.provider
        and str(embedding.base_url or "").rstrip("/") == str(chat.base_url or "").rstrip("/")
    )
    if same_connection:
        return result

    configured = [embedding.model] if str(embedding.model or "").strip() else []
    discovered: list[str] = []
    warning = ""
    try:
        if embedding.provider == "fake":
            discovered = configured or ["fake-embed"]
        elif embedding.provider == "aoai":
            import httpx
            base = str(embedding.base_url or "").rstrip("/")
            response = httpx.get(
                f"{base}/openai/deployments",
                params={"api-version": "2023-03-15-preview"},
                headers={"api-key": embedding.api_key},
                timeout=timeout,
            )
            response.raise_for_status()
            rows = response.json().get("data") or []
            discovered = [str(row.get("id") or "") for row in rows
                          if "embed" in str(row.get("id") or row.get("model") or "").casefold()]
        else:
            from openai import OpenAI
            kwargs = {"api_key": embedding.api_key or "unused", "timeout": timeout}
            if embedding.provider == "openai_compat":
                kwargs.update(base_url=embedding.base_url,
                              default_headers=embedding.headers or None)
            client = OpenAI(**kwargs)
            ids = [str(getattr(model, "id", "") or "") for model in client.models.list()]
            markers = ("embed", "bge", "e5-", "gte-", "jina", "minilm", "nomic")
            discovered = [model for model in ids if any(marker in model.casefold() for marker in markers)]
            if not discovered and len(ids) == 1:
                discovered = ids
    except Exception as exc:
        warning = "embedding /models unavailable; configured model retained: " + _brief(exc)

    merged = sorted(dict.fromkeys([*configured, *discovered]))
    out = {**result, "embed": merged}
    out["total"] = len(set(out.get("chat") or [])) + len(set(merged))
    if warning:
        out["warnings"] = [*(out.get("warnings") or []), warning]
    return out


def _model_denied(d: dict) -> bool:
    """이 모델을 **못 쓴다고 서버가 말하고 있나.** 말이 없으면 False(막지 않는다).

    OpenAI 레거시 스키마의 `permission[].allow_sampling` 이 표준 신호다. 사내 게이트웨이는
    `enabled`/`available`/`status` 로 대신 말하는 일이 있어 함께 본다 — **명시적으로 거짓일
    때만** 뺀다(값이 없거나 모르는 모양이면 그대로 둔다).
    """
    try:
        perms = d.get("permission")
        if isinstance(perms, list) and perms and isinstance(perms[0], dict):
            if perms[0].get("allow_sampling") is False:
                return True
        for k in ("enabled", "available", "allowed", "accessible"):
            if d.get(k) is False:
                return True
        st = str(d.get("status") or "").lower()
        if st in ("disabled", "unavailable", "forbidden", "denied", "inactive"):
            return True
    except Exception:
        pass
    return False


def verify_models(names: list, timeout: float = 15.0, config_id: str = "") -> dict:
    """후보 모델을 **하나씩 실제로 불러 본다** — 권한 신호가 없는 게이트웨이용.

    ★ 자동으로 돌지 않는다(사용자가 버튼을 눌러야 한다). 모델 수만큼 호출이 나가고, 그건
      돈과 시간이다. "목록을 보여 주는 일"이 조용히 N 번의 과금을 일으키면 안 된다.

    반환: {"ok": [...], "denied": {name: 사유}}
    """
    ok, why = available()
    if not ok:
        return {"ok": [], "denied": {}, "error": why}
    good, bad = [], {}
    for name in [str(n).strip() for n in (names or []) if str(n).strip()][:40]:
        try:
            get_llm(temperature=0, max_tokens=1, tier="complex",
                    model_override=name, config_id=config_id).invoke("hi")
            good.append(name)
        except Exception as e:
            bad[name] = _brief(e)[:160]
    return {"ok": good, "denied": bad, "error": ""}


def _brief(e: Exception) -> str:
    s = str(e).strip().replace("\n", " ")
    return (s[:300] + "…") if len(s) > 300 else s


def diagnose(timeout: float = 30.0) -> dict:
    """LLM 연결 **해부 보고서** — 무엇을 어디로 어떤 이름으로 보내고 뭘 받았나.

    왜 probe() 로 부족한가(사용자 요청): probe 는 "됐다/안 됐다"만 말한다. 실사용에서 나온
    질문은 그보다 구체적이다 —

      "403 team not allowed to access this model 이 뜨는데, **목록에 있는 모델을 골랐는데도**
       계속 난다"

    이때 알아야 하는 것은 **실제로 어떤 이름이 어느 호출에 실려 나갔는가**다. 우리 설정에는
    모델 이름이 **세 개**(채팅 / 간단한 역할 / 임베딩) 있고, 셋은 각자 다른 자리에서 온다.
    하나만 고르고 나머지를 비워 두면, 화면에서 고른 것과 **다른 이름**으로 나가는 호출이
    남는다 — 그런데 오류 메시지는 어느 호출인지 말해 주지 않는다.

    그래서 셋을 **따로따로** 부르고 각각의 (요청 대상 · 실린 모델명 · 원문 응답)을 보인다.
    비밀값은 싣지 않는다 — 키는 끝 4자만, 헤더는 이름만.
    """
    p = provider()
    out = {"provider": p, "env": {}, "targets": {}, "calls": [], "hint": ""}
    ok, why = available()
    if not ok:
        out["error"] = why
        return out

    out["env"] = _secrets.env_overrides()
    key = ""
    if p == "aoai":
        base, key = _secrets.get("aoaiEndpoint"), _secrets.get("aoaiApiKey")
        out["targets"] = {"인증 방식": "api-key: <API 키> (Azure 규격 — Bearer 아님)",
                          "endpoint": base, "api-version": api_version(),
                          "url(chat)": f"{(base or '').rstrip('/')}/openai/deployments/"
                                       f"{chat_model('complex') or '(비어 있음)'}/chat/completions"}
    elif p == "openai_compat":
        base, key = compat_base(), _secrets.get("compatApiKey")
        # 인증이 **어떤 꼴로** 나가는지 못 박아 보여 준다 — 게이트웨이가 Bearer 가 아니라
        # 자체 헤더를 요구하는 경우가 있고, 그때는 '추가 헤더'에 넣어야 한다.
        out["targets"] = {"인증 방식": "Authorization: Bearer <API 키>",
                          "base_url(입력값)": _secrets.get("compatBaseUrl"),
                          "base_url(실제 사용)": base,
                          "url(chat)": f"{base}/chat/completions",
                          "url(models)": f"{base}/models",
                          "추가 헤더": sorted(_compat_headers().keys()) or "(없음)"}
    elif p == "openai":
        key = _secrets.get("openaiApiKey")
        out["targets"] = {"인증 방식": "Authorization: Bearer <API 키>",
                          "base_url": "https://api.openai.com/v1"}
    out["targets"]["api key"] = (f"…{key[-4:]} (길이 {len(key)})" if key else "(없음)")

    # ★ 세 이름을 **각각** 보인다. "골랐는데 안 된다"의 대부분이 여기서 갈린다.
    names = {"채팅(complex)": chat_model("complex"),
             "간단한 역할(simple)": chat_model("simple"),
             "임베딩": embed_model()}
    out["models"] = {k: (v or "(비어 있음)") for k, v in names.items()}
    if names["채팅(complex)"] and names["간단한 역할(simple)"] != names["채팅(complex)"]:
        out["hint"] = ("간단한 역할 모델이 채팅 모델과 **다릅니다** — 권한이 없는 쪽이 있으면 "
                       "일부 턴만 실패합니다. 하나로 맞추려면 '간단한 역할 모델'을 비우세요.")

    def _try(label, fn):
        t0 = time.time()
        row = {"단계": label}
        try:
            row["결과"] = fn()
            row["ok"] = True
        except Exception as e:
            row["ok"] = False
            row["오류"] = _brief(e)
            row["오류종류"] = type(e).__name__
            for attr in ("status_code", "code"):
                v = getattr(e, attr, None)
                if v is not None:
                    row[attr] = v
        row["ms"] = int((time.time() - t0) * 1000)
        out["calls"].append(row)

    _try(f"모델 목록 ({p})", lambda: (lambda r: f"{len(r.get('chat') or [])}개 / 오류={r.get('error') or '없음'}")(
        list_models(timeout=timeout)))
    _try(f"채팅 · model={names['채팅(complex)'] or '(비어 있음)'}",
         lambda: str(getattr(get_llm(temperature=0, max_tokens=5).invoke("ping"), "content", ""))[:60])
    if names["간단한 역할(simple)"] != names["채팅(complex)"]:
        _try(f"간단한 역할 · model={names['간단한 역할(simple)'] or '(비어 있음)'}",
             lambda: str(getattr(get_llm(temperature=0, max_tokens=5, tier="simple").invoke("ping"),
                                 "content", ""))[:60])
    _try(f"임베딩 · model={names['임베딩'] or '(비어 있음)'}",
         lambda: f"{len(get_embeddings().embed_query('ping'))}차원")

    bad = [c for c in out["calls"] if not c.get("ok")]
    # ★ **임베딩만** SSL 로 죽는 경우가 있다 — 서버가 아니라 tiktoken 이 인코딩 파일을
    #   openaipublic 에서 받으려다 사내 TLS 가로채기에 걸리는 것이다(실사용 사고).
    #   그 증상은 "임베딩 서버가 이상하다"로 읽히므로 여기서 이름을 붙여 준다.
    _emb_bad = [c for c in bad if "임베딩" in str(c.get("단계") or "")]
    if _emb_bad and "SSL" in str(_emb_bad[0].get("오류") or "").upper():
        out["hint"] = ("임베딩만 SSL 오류 — 임베딩 서버가 아니라 **토큰 계산용 인코딩 파일**을 "
                       "외부(openaipublic.blob.core.windows.net)에서 받으려다 막힌 것일 수 "
                       "있습니다. 이 앱은 그 경로를 끄고 씁니다(v2026.08.10.10+) — 옛 버전이면 "
                       "업데이트하세요.")
    if bad and not out["hint"]:
        first = bad[0]
        if "403" in str(first.get("오류") or ""):
            out["hint"] = (f"'{first['단계']}' 에서 403 — **그 단계에 실린 모델 이름**이 허용 목록에 "
                           "있는지 보세요. 채팅만 고르고 임베딩을 비워 두면 임베딩 호출이 빈 이름 "
                           "또는 기본값으로 나갑니다.")
    return out
