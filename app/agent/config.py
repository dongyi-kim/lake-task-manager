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
import os
import time

from app.agent import secrets as _secrets

PROVIDERS = ("aoai", "openai", "openai_compat", "fake")
DEFAULT_PROVIDER = "aoai"

# 채점환경이 `AOAI_API_VERSION` 은 주입하지 않는다(실측). GA 버전을 기본값으로 둔다 —
# 2024-10-21 로 chat·embeddings·function calling·structured output(strict)·streaming 전부 확인했다.
DEFAULT_API_VERSION = "2024-10-21"

DEFAULT_OPENAI_CHAT = "gpt-4o-mini"
DEFAULT_OPENAI_EMBED = "text-embedding-3-small"


# ── 설치 게이팅 ────────────────────────────────────────────────────
def available() -> tuple[bool, str]:
    """(사용 가능?, 사유). 라우트 등록·설정 패널이 이걸 먼저 본다."""
    try:
        import langchain_core  # noqa: F401
        import langgraph       # noqa: F401
    except Exception as e:
        return False, f"에이전트 의존이 설치되지 않았습니다: {e} " \
                      "(pip install -r requirements-agent.txt)"
    return True, ""


# ── 설정 해석 ──────────────────────────────────────────────────────
def _pref(key, default=None):
    try:
        from app.infra import prefs
        v = prefs.load().get(key)
        return default if v in (None, "") else v
    except Exception:
        return default


def provider() -> str:
    p = (os.getenv("LAKE_AGENT_PROVIDER") or _pref("agentProvider") or DEFAULT_PROVIDER).strip().lower()
    return p if p in PROVIDERS else DEFAULT_PROVIDER


def chat_model(tier: str = "complex") -> str:
    """provider 별 '모델/배포' 이름. AOAI 는 **모델명이 아니라 배포명**이다(흔한 실수).

    `tier="simple"` 은 **간단한 역할 전용 모델** — 의도 분류·결정적 실행처럼 판단이 얕은
    역할이 쓴다(역할→tier 매핑은 각 Agent 클래스의 `tier` 속성). 설정이 비어 있으면
    기본 모델로 폴백한다 — 모델 하나로 쓰는 사람에게는 아무 변화가 없다.
    """
    p = provider()
    simple = tier == "simple"
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


def embed_model() -> str:
    p = provider()
    if p == "aoai":
        return (os.getenv("LAKE_AGENT_AOAI_EMBED") or _pref("agentAoaiEmbed")
                or os.getenv("AOAI_DEPLOY_EMBED_3_SMALL") or os.getenv("AOAI_DEPLOY_EMBED_3_LARGE") or "")
    if p == "openai":
        return os.getenv("LAKE_AGENT_OPENAI_EMBED") or _pref("agentOpenaiEmbed") or DEFAULT_OPENAI_EMBED
    if p == "openai_compat":
        return os.getenv("LAKE_AGENT_COMPAT_EMBED") or _pref("agentCompatEmbed") or ""
    return "fake-embed"


def api_version() -> str:
    return os.getenv("AOAI_API_VERSION") or _pref("agentApiVersion") or DEFAULT_API_VERSION


def _compat_headers() -> dict:
    """자체 LLM 이 요구하는 추가 헤더(JSON 문자열로 보관). 인증 방식이 표준과 다를 때 쓴다."""
    raw = _secrets.get("compatHeaders", "LAKE_AGENT_COMPAT_HEADERS")
    if not raw:
        return {}
    try:
        d = json.loads(raw)
        return {str(k): str(v) for k, v in d.items()} if isinstance(d, dict) else {}
    except Exception:
        return {}


# ── 팩토리 ─────────────────────────────────────────────────────────
def get_llm(temperature: float = 0.2, tier: str = "complex", **kwargs):
    """provider 에 맞는 chat 모델. 나머지 코드는 이 함수만 부른다.

    `tier` 로 역할별 모델을 가른다 — simple(의도 분류·결정적 실행)은 저렴한 모델,
    complex(조사·초안·검토·작문)는 기본 모델. simple 모델 미설정이면 기본 모델 하나로 돈다.
    """
    p = provider()
    if p == "fake":
        from app.agent.fake import FakeChat
        return FakeChat(**{k: v for k, v in kwargs.items() if k == "responses"})

    ok, why = available()
    if not ok:
        raise RuntimeError(why)

    # 429(TPM 한도)는 몇 초 뒤 그냥 풀린다 — SDK 가 Retry-After 를 존중하며 재시도한다.
    # 기본 2회로는 상위 모델(TPM 30k 조직에서 한 턴 ~70k 토큰)에서 실측으로 죽었다.
    kwargs.setdefault("max_retries", 6)

    if p == "aoai":
        from langchain_openai import AzureChatOpenAI
        return AzureChatOpenAI(
            azure_endpoint=_secrets.get("aoaiEndpoint", "AOAI_ENDPOINT"),
            api_key=_secrets.get("aoaiApiKey", "AOAI_API_KEY"),
            azure_deployment=chat_model(tier),       # ★ 모델명이 아니라 배포명
            api_version=api_version(),
            temperature=temperature, **kwargs)

    from langchain_openai import ChatOpenAI
    if p == "openai":
        return ChatOpenAI(api_key=_secrets.get("openaiApiKey", "OPENAI_API_KEY"),
                          model=chat_model(tier), temperature=temperature, **kwargs)
    # openai_compat — base_url + 커스텀 헤더. 인증이 표준과 달라도 여기서 흡수한다.
    return ChatOpenAI(api_key=_secrets.get("compatApiKey", "LAKE_AGENT_COMPAT_KEY") or "unused",
                      base_url=_secrets.get("compatBaseUrl", "LAKE_AGENT_COMPAT_BASE"),
                      model=chat_model(tier), temperature=temperature,
                      default_headers=_compat_headers() or None, **kwargs)


def get_embeddings(**kwargs):
    p = provider()
    if p == "fake":
        from app.agent.fake import FakeEmbeddings
        return FakeEmbeddings()

    ok, why = available()
    if not ok:
        raise RuntimeError(why)

    if p == "aoai":
        from langchain_openai import AzureOpenAIEmbeddings
        return AzureOpenAIEmbeddings(
            azure_endpoint=_secrets.get("aoaiEndpoint", "AOAI_ENDPOINT"),
            api_key=_secrets.get("aoaiApiKey", "AOAI_API_KEY"),
            model=embed_model(), openai_api_version=api_version(), **kwargs)

    from langchain_openai import OpenAIEmbeddings
    if p == "openai":
        return OpenAIEmbeddings(api_key=_secrets.get("openaiApiKey", "OPENAI_API_KEY"),
                                model=embed_model(), **kwargs)
    return OpenAIEmbeddings(api_key=_secrets.get("compatApiKey", "LAKE_AGENT_COMPAT_KEY") or "unused",
                            base_url=_secrets.get("compatBaseUrl", "LAKE_AGENT_COMPAT_BASE"),
                            model=embed_model(), **kwargs)


def get_langfuse_handler(session_id: str = None):
    """Langfuse 콜백. **설정이 없으면 None** — 관측이 없다고 앱이 죽으면 안 된다."""
    pk = _secrets.get("langfusePublicKey", "LANGFUSE_PUBLIC_KEY")
    sk = _secrets.get("langfuseSecretKey", "LANGFUSE_SECRET_KEY")
    if not (pk and sk):
        return None
    try:
        from langfuse.callback import CallbackHandler
        return CallbackHandler(public_key=pk, secret_key=sk,
                               host=_secrets.get("langfuseHost", "LANGFUSE_HOST") or None,
                               session_id=session_id)
    except Exception:
        return None


def callbacks(session_id: str = None) -> list:
    h = get_langfuse_handler(session_id)
    return [h] if h else []


# ── 진단 (설정 패널의 '연결 테스트') ────────────────────────────────
def status() -> dict:
    """화면용 현재 설정 — **비밀값 원문은 절대 싣지 않는다**."""
    ok, why = available()
    from app.infra import prefs as _prefs
    from app.agent.prompts.base import _project_prompt
    return {"available": ok, "reason": why, "provider": provider(),
            "chatModel": chat_model(), "embedModel": embed_model(),
            # 간단한 역할(의도 분류·결정적 실행) 전용 모델 — **설정된 값만**(폴백 없이) 보여
            # 준다. 폴백값을 보여 주면 화면에서 "따로 설정돼 있다"로 오해된다.
            "chatModelSimple": (_pref("agentAoaiChatSimple") if provider() == "aoai"
                                else _pref("agentOpenaiChatSimple") if provider() == "openai"
                                else _pref("agentCompatChatSimple") if provider() == "openai_compat"
                                else ""),
            "apiVersion": api_version() if provider() == "aoai" else None,
            "langfuse": bool(get_langfuse_handler()),
            "secrets": _secrets.masked(),
            # 프롬프트 레이어 — 사용자별은 편집 가능, 프로젝트 공용은 읽기 전용 표시.
            "userPrompt": str(_prefs.load().get("agentUserPrompt") or ""),
            "projectPrompt": _project_prompt()}


def probe(timeout: float = 30.0) -> dict:
    """실제로 한 번씩 호출해 본다. 설정 화면이 '되는지'를 눈으로 확인하는 용도.

    실패를 삼키지 않는다 — 어디서 막혔는지(설정 누락 / 네트워크 / 인증)가 화면에 보여야
    사용자가 고칠 수 있다. 사내 AOAI 는 개발 PC 에서 403 이 정상이므로 그 문구를 그대로 보인다.
    """
    out = {"provider": provider(), "chat": None, "embeddings": None, "ok": False}
    ok, why = available()
    if not ok:
        out["error"] = why
        return out

    t0 = time.time()
    try:
        msg = get_llm(temperature=0, max_tokens=5).invoke("reply with the single word: pong")
        out["chat"] = {"ok": True, "ms": int((time.time() - t0) * 1000),
                       "sample": str(getattr(msg, "content", msg))[:60]}
    except Exception as e:
        out["chat"] = {"ok": False, "ms": int((time.time() - t0) * 1000), "error": _brief(e)}

    t1 = time.time()
    try:
        vec = get_embeddings().embed_query("연결 테스트")
        out["embeddings"] = {"ok": True, "ms": int((time.time() - t1) * 1000), "dim": len(vec)}
    except Exception as e:
        out["embeddings"] = {"ok": False, "ms": int((time.time() - t1) * 1000), "error": _brief(e)}

    out["ok"] = bool(out["chat"]["ok"] and out["embeddings"]["ok"])
    return out


def list_models(timeout: float = 10.0) -> dict:
    """지금 설정된 provider 에서 **실제로 쓸 수 있는** 모델/배포 목록.

    설정 화면의 콤보박스 재료다. 목록 조회가 막히는 환경이 있으므로(권한 없는 키, 프록시,
    자체 LLM 의 미구현) 실패해도 빈 목록+사유만 준다 — 화면은 자유 입력으로 폴백한다.
    ★ 목록은 참고이지 제약이 아니다. 조회가 안 된다고 입력까지 막으면 안 된다.

    반환: {"chat": [...], "embed": [...], "error": ""}
    """
    p = provider()
    if p == "fake":
        return {"chat": ["fake-chat"], "embed": ["fake-embed"], "error": ""}
    ok, why = available()
    if not ok:
        return {"chat": [], "embed": [], "error": why}

    try:
        if p == "aoai":
            # AOAI 는 모델이 아니라 **배포**를 골라야 한다. 데이터플레인 deployments 목록은
            # api-key 로 열린다(관리플레인 자격 증명 불필요).
            import httpx
            base = (_secrets.get("aoaiEndpoint", "AOAI_ENDPOINT") or "").rstrip("/")
            key = _secrets.get("aoaiApiKey", "AOAI_API_KEY")
            if not (base and key):
                return {"chat": [], "embed": [], "error": "엔드포인트/키가 설정되지 않았습니다."}
            r = httpx.get(f"{base}/openai/deployments",
                          params={"api-version": "2023-03-15-preview"},
                          headers={"api-key": key}, timeout=timeout)
            r.raise_for_status()
            rows = r.json().get("data") or []
            pairs = [((d.get("id") or ""), str(d.get("model") or "")) for d in rows]
            is_embed = lambda n, m: "embed" in n.lower() or "embed" in m.lower()  # noqa: E731
            return {"chat": sorted(n for n, m in pairs if n and not is_embed(n, m)),
                    "embed": sorted(n for n, m in pairs if n and is_embed(n, m)), "error": ""}

        from openai import OpenAI
        if p == "openai":
            cli = OpenAI(api_key=_secrets.get("openaiApiKey", "OPENAI_API_KEY"), timeout=timeout)
        else:
            cli = OpenAI(api_key=_secrets.get("compatApiKey", "LAKE_AGENT_COMPAT_KEY") or "unused",
                         base_url=_secrets.get("compatBaseUrl", "LAKE_AGENT_COMPAT_BASE"),
                         default_headers=_compat_headers() or None, timeout=timeout)
        ids = [m.id for m in cli.models.list()]
        embed = sorted(i for i in ids if "embed" in i)
        # 채팅에 못 쓰는 것(음성·이미지·중재 등)을 걸러낸다 — 다 보여 주면 목록이 소음이 된다.
        noise = ("audio", "tts", "whisper", "image", "dall-e", "realtime",
                 "transcribe", "moderation", "computer-use", "codex")
        chat = sorted(i for i in ids
                      if ("gpt" in i or i.startswith("o")) and "embed" not in i
                      and not any(x in i for x in noise))
        return {"chat": chat, "embed": embed, "error": ""}
    except Exception as e:
        return {"chat": [], "embed": [], "error": _brief(e)}


def _brief(e: Exception) -> str:
    s = str(e).strip().replace("\n", " ")
    return (s[:300] + "…") if len(s) > 300 else s
