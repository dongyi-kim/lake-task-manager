"""agent/config — provider 4-way 해석 + fake 경로가 키 없이 도는지.

이 테스트는 **키 없이** 돌아야 한다. 개발 PC 에서는 사내 AOAI 가 Private Endpoint 로 막혀
있어(403) 실 호출을 전제로 하면 CI 도 로컬도 아무것도 검증하지 못한다.
"""
import os
import sys

import pytest

os.environ.setdefault("JIRA_ENV", "mock")

from app.agent import config as C          # noqa: E402
from app.agent import secrets as S         # noqa: E402


@pytest.fixture
def clean_env(monkeypatch):
    for k in list(os.environ):
        if k.startswith(("LAKE_AGENT_", "AOAI_", "OPENAI_", "LANGFUSE_")):
            monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(C, "_pref", lambda key, default=None: default)
    monkeypatch.setattr(S, "load", lambda: {})
    # Unit tests must not inherit the developer's persisted named config.  Tests that
    # exercise named profiles install their own temporary profile store explicitly.
    monkeypatch.setattr(C._profiles, "list_all", lambda: [])
    monkeypatch.setattr(C._profiles, "active", lambda: None)
    monkeypatch.setattr(C._profiles, "legacy_candidate", lambda: None)
    monkeypatch.setattr(C._profiles, "legacy_candidates", lambda: [])
    return monkeypatch


def test_available_reports_reason():
    ok, why = C.available()
    assert ok, why          # requirements-agent.txt 가 설치된 환경 기준
    assert why == ""


def test_provider_defaults_to_aoai(clean_env):
    assert C.provider() == "aoai"


def test_provider_from_env_wins(clean_env):
    clean_env.setenv("LAKE_AGENT_PROVIDER", "openai")
    assert C.provider() == "openai"


def test_unknown_provider_falls_back(clean_env):
    clean_env.setenv("LAKE_AGENT_PROVIDER", "gemini")
    assert C.provider() == C.DEFAULT_PROVIDER


def test_aoai_uses_deployment_names_from_injected_env(clean_env):
    """채점환경은 배포명을 환경변수로 주입한다 — 그대로 집어야 한다."""
    clean_env.setenv("LAKE_AGENT_PROVIDER", "aoai")
    clean_env.setenv("AOAI_DEPLOY_GPT4O_MINI", "aitl-prd-gpt-4o-mini")
    clean_env.setenv("AOAI_DEPLOY_EMBED_3_SMALL", "aitl-prd-text-embedding-3-small")
    assert C.chat_model() == "aitl-prd-gpt-4o-mini"
    assert C.embed_model() == "aitl-prd-text-embedding-3-small"


def test_aoai_api_version_defaults(clean_env):
    """채점환경이 AOAI_API_VERSION 을 주입하지 않는다(실측) — 기본값이 있어야 한다."""
    clean_env.setenv("LAKE_AGENT_PROVIDER", "aoai")
    assert C.api_version() == C.DEFAULT_API_VERSION == "2024-10-21"


def test_langfuse_absent_is_not_an_error(clean_env):
    """관측 설정이 없다고 앱이 죽으면 안 된다."""
    assert C.get_langfuse_handler() is None
    assert C.callbacks() == []


def test_langfuse_v4_initializes_one_client_and_uses_invocation_metadata(
        clean_env, monkeypatch):
    import langfuse
    import langfuse.langchain
    from app.agent.workflow import session

    values = {
        "langfusePublicKey": "pk-test",
        "langfuseSecretKey": "sk-test",
        "langfuseHost": "https://trace.example.test",
    }
    clients, handlers = [], []

    class Client:
        def __init__(self, **kwargs):
            clients.append(kwargs)

    class Handler:
        def __init__(self, **kwargs):
            handlers.append(kwargs)

    monkeypatch.setattr(C._secrets, "get", lambda key, *names: values.get(key, ""))
    monkeypatch.setattr(langfuse, "Langfuse", Client)
    monkeypatch.setattr(langfuse.langchain, "CallbackHandler", Handler)
    monkeypatch.setattr(C, "_LANGFUSE_CACHE", {"signature": None, "client": None})

    first = C.get_langfuse_handler("thread-a")
    second = C.get_langfuse_handler("thread-b")
    config = session._config("thread-a")

    assert first is not None and second is not None
    assert clients == [{
        "public_key": "pk-test", "secret_key": "sk-test",
        "base_url": "https://trace.example.test",
    }]
    assert handlers == [{"public_key": "pk-test"}] * 3
    assert config["metadata"] == {"langfuse_session_id": "thread-a"}


def test_status_never_leaks_secrets(clean_env, monkeypatch):
    monkeypatch.setattr(S, "load", lambda: {"aoaiApiKey": "sk-super-secret-1234",
                                            "aoaiEndpoint": "https://x.example"})
    st = C.status()
    blob = repr(st)
    assert "sk-super-secret-1234" not in blob
    assert "1234" in st["secrets"]["aoaiApiKey"]             # 끝 4자만 힌트로
    assert "super-secret" not in st["secrets"]["aoaiApiKey"]
    assert st["secrets"]["aoaiEndpoint"] == "https://x.example"   # 비밀 아님 → 그대로


def test_status_does_not_advertise_internal_provider_default_as_active(clean_env, monkeypatch):
    """named config와 환경 주입이 없으면 AOAI 폴백을 사용자 선택처럼 표시하지 않는다."""
    monkeypatch.setattr(C._profiles, "list_all", lambda: [])
    monkeypatch.setattr(C._profiles, "active", lambda: None)
    monkeypatch.setattr(C._profiles, "legacy_candidate", lambda: None)
    monkeypatch.setattr(C._profiles, "legacy_candidates", lambda: [])

    st = C.status()
    assert st["runtimeConfigSource"] == "none"
    assert st["provider"] == ""
    assert st["chatModel"] == ""
    assert st["embedModel"] == ""


def test_status_reports_environment_injected_runtime(clean_env, monkeypatch):
    monkeypatch.setattr(C._profiles, "list_all", lambda: [])
    monkeypatch.setattr(C._profiles, "active", lambda: None)
    monkeypatch.setattr(C._profiles, "legacy_candidate", lambda: None)
    monkeypatch.setattr(C._profiles, "legacy_candidates", lambda: [])
    clean_env.setenv("LAKE_AGENT_PROVIDER", "openai")
    clean_env.setenv("OPENAI_API_KEY", "test-only-key")
    clean_env.setenv("LAKE_AGENT_OPENAI_CHAT", "gpt-test")

    st = C.status()
    assert st["runtimeConfigSource"] == "environment"
    assert st["provider"] == "openai"
    assert st["chatModel"] == "gpt-test"


# ── fake 경로: 키 없이 그래프를 굴릴 수 있어야 한다 ──────────────────

def test_fake_chat_is_deterministic(clean_env):
    clean_env.setenv("LAKE_AGENT_PROVIDER", "fake")
    a = C.get_llm().invoke("CDC 도입을 검토해야 한다")
    b = C.get_llm().invoke("CDC 도입을 검토해야 한다")
    assert a.content == b.content
    assert C.get_llm().invoke("다른 입력").content != a.content


def test_fake_chat_scripted_responses(clean_env):
    clean_env.setenv("LAKE_AGENT_PROVIDER", "fake")
    llm = C.get_llm(responses=["첫 번째", "두 번째"])
    assert llm.invoke("x").content == "첫 번째"
    assert llm.invoke("y").content == "두 번째"
    assert llm.invoke("z").content.startswith("[fake]")      # 다 쓰면 되비추기로


def test_fake_structured_output_matches_schema(clean_env):
    """RequestArchitect 의 의도 분류가 fake 로도 굴러가야 그래프 분기를 테스트할 수 있다."""
    clean_env.setenv("LAKE_AGENT_PROVIDER", "fake")
    schema = {"title": "Plan", "type": "object",
              "properties": {"intent": {"type": "string", "enum": ["search", "create", "update"]},
                             "confident": {"type": "boolean"},
                             "n": {"type": "integer"}}}
    out = C.get_llm().with_structured_output(schema).invoke("티켓 만들어줘")
    assert out["intent"] in ("search", "create", "update")
    assert isinstance(out["confident"], bool)
    assert isinstance(out["n"], int)


def test_fake_rejects_a_nameless_schema_like_the_real_thing(clean_env):
    """★ **가짜가 실물보다 관대하면 안 된다.**

    OpenAI/AOAI 는 구조화 출력을 함수 호출로 구현하므로 스키마에 이름(title/name)이 있어야
    한다. fake 가 이를 받아 주던 동안 여섯 역할의 스키마가 전부 이름 없이 굴러갔고, 실 키를
    꽂는 순간 한꺼번에 `Unsupported function` 으로 죽었다. 가짜의 관대함이 곧 늦은 발견이다.
    """
    clean_env.setenv("LAKE_AGENT_PROVIDER", "fake")
    with pytest.raises(ValueError, match="이름"):
        C.get_llm().with_structured_output({"type": "object", "properties": {}})


def test_fake_embeddings_shape_and_determinism(clean_env):
    clean_env.setenv("LAKE_AGENT_PROVIDER", "fake")
    e = C.get_embeddings()
    v1, v2 = e.embed_query("같은 글"), e.embed_query("같은 글")
    assert v1 == v2 and len(v1) == 256
    assert e.embed_query("다른 글") != v1
    assert len(e.embed_documents(["a", "b", "c"])) == 3


def test_probe_reports_failure_without_swallowing(clean_env):
    """진단은 실패를 삼키면 안 된다 — 어디서 막혔는지가 화면에 보여야 고칠 수 있다."""
    clean_env.setenv("LAKE_AGENT_PROVIDER", "aoai")      # 키 없음 → 실패해야 정상
    r = C.probe()
    assert r["provider"] == "aoai"
    assert r["ok"] is False
    assert r["chat"]["ok"] is False and r["chat"]["error"]


def test_probe_ok_on_fake(clean_env):
    clean_env.setenv("LAKE_AGENT_PROVIDER", "fake")
    r = C.probe()
    assert r["ok"] is True
    assert r["embeddings"]["dim"] == 256


def test_named_profile_activation_controls_runtime_without_provider_env(monkeypatch, tmp_path):
    """후보 저장은 무효과, 검증 후 명시적 적용만 런타임 provider를 바꾼다."""
    for key in list(os.environ):
        if key.startswith(("LAKE_AGENT_", "AOAI_", "OPENAI_")):
            monkeypatch.delenv(key, raising=False)
    import app.infra.settings as Settings
    from app.agent import profiles
    monkeypatch.setattr(Settings, "CACHE_DIR", tmp_path)

    active = profiles.create("로컬 테스트", "fake")
    candidate = profiles.create("다른 후보", "fake")
    assert C.provider() == C.DEFAULT_PROVIDER
    assert C.probe_auth(config_id=active["id"])["ok"]
    assert C.probe(config_id=active["id"])["ok"]
    assert C.activate(active["id"])["ok"]
    assert C.provider() == "fake" and C.chat_model() == "fake-chat"
    status = C.status()
    assert status["runtimeConfigSource"] == "named"
    assert status["activeConfig"]["name"] == "로컬 테스트"

    profiles.update(candidate["id"], {"name": "편집한 후보"})
    assert C.provider() == "fake"
    assert profiles.active()["id"] == active["id"]


def test_legacy_flat_preferences_are_not_silently_activated(monkeypatch, tmp_path):
    for key in list(os.environ):
        if key.startswith(("LAKE_AGENT_", "AOAI_", "OPENAI_")):
            monkeypatch.delenv(key, raising=False)
    import app.infra.settings as Settings
    from app.infra import prefs
    monkeypatch.setattr(Settings, "CACHE_DIR", tmp_path)
    prefs.save({"agentProvider": "openai_compat", "agentCompatChat": "qwen2.5-32b",
                "agentCompatEmbed": "bge-m3"})
    assert C.provider() == C.DEFAULT_PROVIDER
    assert C.llm_ready()[0] is False
    assert C.status()["legacyCandidate"]["chatModel"] == "qwen2.5-32b"


def test_chat_model_tier_falls_back_to_main_when_simple_unset(clean_env):
    """simple 모델 미설정이면 기본 모델 하나로 돈다 — 모델 하나 쓰는 사람에게 무변화."""
    clean_env.setenv("LAKE_AGENT_PROVIDER", "openai")
    clean_env.setenv("LAKE_AGENT_OPENAI_CHAT", "gpt-4o")
    assert C.chat_model("simple") == C.chat_model() == "gpt-4o"


def test_chat_model_tier_splits_when_simple_set(clean_env):
    """간단한 역할 모델을 지정하면 tier=simple 만 갈라진다(기본 tier 는 그대로)."""
    clean_env.setenv("LAKE_AGENT_PROVIDER", "openai")
    clean_env.setenv("LAKE_AGENT_OPENAI_CHAT", "gpt-4o")
    clean_env.setenv("LAKE_AGENT_OPENAI_CHAT_SIMPLE", "gpt-4o-mini")
    assert C.chat_model() == "gpt-4o"
    assert C.chat_model("simple") == "gpt-4o-mini"


def test_role_model_routing_is_manifest_owned_not_class_owned(clean_env):
    """Role class의 고정 tier가 model-profile capability routing을 우회하지 않는다."""
    from app.agent.workflow.agents.action_executor import ActionExecutor
    from app.agent.workflow.agents.query_specialist import QuerySpecialist
    from app.agent.workflow.agents.request_architect import RequestArchitect
    from app.agent.workflow.role_manifest import ROLE_SPECS

    assert ROLE_SPECS["request_architect"].execution_layer == "lightweight_semantic"
    assert ROLE_SPECS["query_specialist"].execution_layer == "lightweight_semantic"
    assert ROLE_SPECS["action_executor"].execution_layer == "deterministic"
    for cls in (RequestArchitect, QuerySpecialist, ActionExecutor):
        assert "tier" not in cls.__dict__, cls.__name__


def test_reasoning_models_do_not_receive_temperature(clean_env):
    """gpt-5·o-계열은 temperature 를 거부한다(실측 400) — 아예 넘기지 않아야 한다."""
    for m in ("gpt-5", "gpt-5-mini", "gpt-5.2", "o1", "o3-mini", "my-gpt-5-deploy"):
        assert C.sampling_unsupported(m), m
    for m in ("gpt-4o", "gpt-4o-mini", "gpt-4.1", "o2-weird", "solar-pro"):
        assert not C.sampling_unsupported(m), m
    clean_env.setenv("LAKE_AGENT_PROVIDER", "openai")
    clean_env.setenv("OPENAI_API_KEY", "sk-test")
    clean_env.setenv("LAKE_AGENT_OPENAI_CHAT", "gpt-5-mini")
    llm = C.get_llm(temperature=0.4)
    assert getattr(llm, "temperature", None) in (None, 1), "reasoning 모델에 temperature 가 실렸다"
    clean_env.setenv("LAKE_AGENT_OPENAI_CHAT", "gpt-4o-mini")
    llm2 = C.get_llm(temperature=0.4)
    assert abs(getattr(llm2, "temperature", 0) - 0.4) < 1e-9


def test_playbooks_load_and_inject(clean_env):
    """전형적 요청의 사전 정의 플로우 — 파싱과 페르소나 주입을 함께 보증한다."""
    from app.agent.prompts.roles import PLAYBOOKS
    from app.agent.workflow.agents.request_architect import SCHEMA
    from app.agent.workflow.prompts import persona
    expect = {"epic_create", "task_create", "bug_report", "subtask_bulk", "find_people",
              "find_tickets", "knowledge", "history", "workload", "assign_fit"}
    assert expect <= set(PLAYBOOKS), set(PLAYBOOKS)
    for k in expect:
        assert "### Flow" in PLAYBOOKS[k] and "### Guardrails" in PLAYBOOKS[k], k
    # RequestArchitect enum 과 자산이 어긋나면 조용히 주입이 빠진다 — 함께 묶어 검증
    assert expect <= set(SCHEMA["properties"]["playbook"]["enum"])
    p = persona({"playbook": "subtask_bulk"})
    assert "## Active Standard Playbook: `subtask_bulk`" in p
    assert "Preserve user-provided item names and assignments" in p
    assert "Active Standard Playbook" not in persona({})

def test_fake_provider_is_refused_in_prod(monkeypatch):
    """prod 에서 '테스트(가짜)' 는 없는 것으로 친다(사용자 지적).

    실 Jira 를 보는 화면에서 가짜 모델이 답을 만들면 **그 답이 진짜처럼 보인다.** 화면에서
    고르지 못하게 한 것만으로는 부족하다 — 예전에 고른 값이 prefs 에 남아 있을 수도,
    환경변수로 들어올 수도 있다. 가드는 고르는 자리와 **쓰는 자리** 양쪽에 있어야 한다.
    """
    import app.agent.config as C
    import app.infra.settings as S

    monkeypatch.setenv("LAKE_AGENT_PROVIDER", "fake")

    class _S:
        jira_env = "prod"

    monkeypatch.setattr(S, "get_settings", lambda *a, **k: _S())
    assert C.provider() == C.DEFAULT_PROVIDER, "prod 에서 fake 가 살아 있다"

    _S.jira_env = "mock"                      # 개발 경로는 그대로 — 여기선 정당한 용도다
    assert C.provider() == "fake"



def test_compat_base_url_gets_the_v1_path_when_the_user_typed_only_the_host(clean_env):
    """호환 엔드포인트에 경로가 없으면 `/v1` 을 붙인다.

    OpenAI SDK 는 base_url 뒤에 `/models`·`/chat/completions` 를 **상대로** 붙인다.
    사용자가 호스트만 넣으면 `{host}/models` 를 부르고, 호환 서버(vLLM·Ollama·LM Studio·
    TGI)는 거기에 아무것도 없어 404 다 — 화면에는 "조회 실패"로만 보인다.
    이미 경로가 있으면 손대지 않는다(`/openai/v1` 같은 배치도 있다).
    """
    import app.agent.config as C
    clean_env.setenv("LAKE_AGENT_COMPAT_BASE", "https://llm.example")
    assert C.compat_base() == "https://llm.example/v1"
    clean_env.setenv("LAKE_AGENT_COMPAT_BASE", "https://llm.example/")
    assert C.compat_base() == "https://llm.example/v1"
    for typed in ("https://llm.example/v1", "https://llm.example/openai/v1",
                  "https://llm.example/api/v1"):
        clean_env.setenv("LAKE_AGENT_COMPAT_BASE", typed)
        assert C.compat_base() == typed, typed


def test_compat_model_list_is_not_filtered_by_openai_naming(monkeypatch):
    """호환 서버 목록에 **이름 화이트리스트를 걸지 않는다**(사용자 지적).

    실측: "설정창에서 불러온 모델과 직접 /v1/models 날려 본 것이 다르다."
    원인은 조회가 아니라 **거르기**였다 — `gpt` 를 포함하거나 `o` 로 시작하는 것만 남기는
    규칙은 OpenAI 카탈로그를 두고 만든 것이라, 사내 모델(llama·qwen·solar·mistral)이
    한 줄도 안 남는다. 조회는 성공했는데 목록이 비어서 실패조차 안 보인다.

    무엇이 채팅 모델인지 아는 것은 우리가 아니라 **그 서버**다 — 소음(음성·이미지)만 걷어낸다.
    """
    import app.agent.config as C

    ids = ["llama-3.1-70b-instruct", "qwen2.5-32b", "solar-pro", "bge-m3",
           "text-embedding-3-small", "whisper-large-v3", "gpt-4o"]

    class _M:
        def __init__(self, i):
            self.id = i

    class _Models:
        def list(self):
            return [_M(i) for i in ids]

    class _Cli:
        def __init__(self, *a, **k):
            self.models = _Models()

    monkeypatch.setenv("LAKE_AGENT_PROVIDER", "openai_compat")
    monkeypatch.setenv("LAKE_AGENT_COMPAT_BASE", "https://llm.example/v1")
    monkeypatch.setattr("openai.OpenAI", _Cli)

    r = C.list_models(timeout=1)
    assert not r["error"], r
    for m in ("llama-3.1-70b-instruct", "qwen2.5-32b", "solar-pro"):
        assert m in r["chat"], (m, r["chat"])      # 사내 모델이 살아 있다
    assert "whisper-large-v3" not in r["chat"]     # 소음은 여전히 걷어낸다
    assert "bge-m3" in r["embed"]                  # embed 를 안 달고 와도 임베딩으로
    assert not (set(r["chat"]) & set(r["embed"])), "한 모델이 두 칸에 있다"
    assert r["total"] == len(ids), "서버가 준 개수를 그대로 알려야 한다"


def test_settings_are_inactive_until_both_checks_pass(monkeypatch, tmp_path):
    """세 단계 게이트 — ①인증 확인 ②모델 확인 ③이 설정 사용(사용자가 정한 절차).

    **값이 있다**와 **그 조합이 된다**와 **이걸 쓰겠다**는 각각 다른 말이다.

    예전에는 앞엣것만 보고 챗·에디터 AI 를 켰다. 그래서 키는 맞는데 모델 이름이 비었거나
    팀에 권한이 없는 모델이 골라져 있어도 화면은 "쓸 수 있음"이었고, 실패는 사용자가 실제로
    무언가를 시킨 뒤 403/404 로 나타났다 — 실패를 뒤로 미룬 셈이다.
    """
    import app.agent.config as C
    import app.infra.prefs as P
    import app.infra.settings as S

    monkeypatch.setattr(S, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(P, "_path", lambda: tmp_path / "prefs.json")
    monkeypatch.setenv("LAKE_AGENT_PROVIDER", "openai")
    monkeypatch.setenv("LAKE_AGENT_OPENAI_CHAT", "gpt-4o-mini")
    # 키는 저장값으로 준다 — 환경변수로 주면 '주입 환경'이라 게이트가 면제된다(아래에서 따로 잰다).
    monkeypatch.setattr(C._secrets, "get",
                        lambda f, *a: "sk-test-1234" if f == "openaiApiKey" else "")
    monkeypatch.setattr(C._secrets, "env_overrides", lambda: {})

    ok, why = C.llm_ready()
    assert not ok and "확인" in why, (ok, why)      # 값은 다 있는데 아직 확인 전

    # ① 인증 확인만 하면 아직이다 — 모델이 남았다.
    C._mark(C._AUTH_KEY, C._auth_signature())
    assert C.auth_ok() and not C.models_ok()
    assert not C.llm_ready()[0], "인증만 되고도 켜졌다"

    # ② 모델 확인까지 — 그래도 **아직 안 켠다.** 켜는 것은 사용자의 결정이다.
    C.mark_verified()
    assert C.models_ok() and not C.verified(), "확인이 곧 활성화가 되면 안 된다"
    assert not C.llm_ready()[0]

    # ③ '이 설정 사용' — 여기서 켜진다.
    assert C.activate()["ok"]
    assert C.llm_ready()[0], "세 단계를 다 밟았는데 안 켜졌다"

    # ★ 모델을 바꾸면 **그 조합은 확인된 적이 없다** — 다시 잠근다.
    monkeypatch.setenv("LAKE_AGENT_OPENAI_CHAT", "gpt-4o")
    assert not C.llm_ready()[0], "조합이 바뀌었는데 활성 상태가 따라왔다"
    assert C.auth_ok(), "인증은 그대로여야 한다 — 바뀐 것은 모델이다"
    assert not C.activate()["ok"], "모델 확인 없이 활성화가 됐다"


def test_env_injected_settings_skip_the_gate(monkeypatch, tmp_path):
    """채점/사내 환경은 `AOAI_*` 를 주입하고 **설정 화면을 아무도 안 연다.**

    거기에 게이트를 걸면 정상 경로가 죽는다 — 면제가 이 규칙의 일부다.
    """
    import app.agent.config as C
    import app.infra.prefs as P
    import app.infra.settings as S

    monkeypatch.setattr(S, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(P, "_path", lambda: tmp_path / "prefs.json")
    monkeypatch.setenv("LAKE_AGENT_PROVIDER", "openai")
    monkeypatch.setenv("LAKE_AGENT_OPENAI_CHAT", "gpt-4o-mini")
    monkeypatch.setattr(C._secrets, "get",
                        lambda f, *a: "sk-injected" if f == "openaiApiKey" else "")
    monkeypatch.setattr(C._secrets, "env_overrides", lambda: {"openaiApiKey": "OPENAI_API_KEY"})
    assert C.llm_ready()[0], "환경변수 주입 환경까지 잠그면 안 된다"


def test_compat_sends_bearer_and_the_same_extra_headers_everywhere(monkeypatch):
    """호환 provider 의 인증은 **세 호출에 똑같이** 걸려야 한다.

    실측(스텁 서버가 받은 헤더를 찍어 확인): `Authorization: Bearer <key>` 는 세 곳 다
    나가는데 **추가 헤더는 임베딩에만 빠져 있었다.** 게이트웨이가 X-Auth 같은 헤더로 팀을
    가르는 환경이면 **채팅은 되는데 임베딩만 401/403** 이 나고, 그 증상은 '모델 권한 문제'로
    읽힌다 — 실제 원인은 인증 헤더 누락이다.

    ★ SDK 내부 구조를 들여다보지 않고 **실제로 한 번씩 쏴서 받은 헤더**를 본다.
      내부 필드는 버전마다 바뀌지만 "무엇이 서버에 도착했나"는 안 바뀐다.
    """
    import http.server
    import json as _json
    import socket
    import threading

    import app.agent.config as C

    seen = []

    class H(http.server.BaseHTTPRequestHandler):
        def _j(self, obj):
            b = _json.dumps(obj).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(b)))
            self.end_headers()
            self.wfile.write(b)

        def _note(self):
            seen.append((self.path, {k.lower(): v for k, v in self.headers.items()}))

        def do_GET(self):
            self._note()
            self._j({"object": "list", "data": [{"id": "m1", "object": "model"}]})

        def do_POST(self):
            self._note()
            self.rfile.read(int(self.headers.get("Content-Length") or 0))
            if self.path.endswith("/embeddings"):
                self._j({"object": "list",
                         "data": [{"object": "embedding", "index": 0, "embedding": [0.1] * 4}]})
            else:
                self._j({"id": "c", "object": "chat.completion",
                         "choices": [{"index": 0, "finish_reason": "stop",
                                      "message": {"role": "assistant", "content": "pong"}}]})

        def log_message(self, *a):
            pass

    sk = socket.socket()
    sk.bind(("127.0.0.1", 0))
    port = sk.getsockname()[1]
    sk.close()
    srv = http.server.HTTPServer(("127.0.0.1", port), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        monkeypatch.setenv("LAKE_AGENT_PROVIDER", "openai_compat")
        monkeypatch.setenv("LAKE_AGENT_COMPAT_BASE", f"http://127.0.0.1:{port}/v1")
        monkeypatch.setenv("LAKE_AGENT_COMPAT_KEY", "k-123456")
        monkeypatch.setenv("LAKE_AGENT_COMPAT_CHAT", "m1")
        monkeypatch.setenv("LAKE_AGENT_COMPAT_EMBED", "m1")
        monkeypatch.setenv("LAKE_AGENT_COMPAT_HEADERS", '{"X-Auth": "team-token"}')

        C.list_models(timeout=5)
        C.get_llm(temperature=0, max_tokens=3).invoke("hi")
        C.get_embeddings().embed_query("hi")
    finally:
        srv.shutdown()

    paths = {p for p, _ in seen}
    assert any(p.endswith("/models") for p in paths), paths
    assert any(p.endswith("/chat/completions") for p in paths), paths
    assert any(p.endswith("/embeddings") for p in paths), paths
    for path, h in seen:
        assert h.get("authorization") == "Bearer k-123456", (path, h.get("authorization"))
        assert h.get("x-auth") == "team-token", f"{path} 에 추가 헤더가 안 실렸다"


def test_embeddings_do_not_reach_out_to_tiktoken(monkeypatch):
    """임베딩이 **외부 인코딩 파일을 받으러 나가지 않는다**.

    실사용 사고: "임베딩 모델만 자꾸 SSL verify failed, max retries."
    원인은 SSL 설정도 임베딩 서버도 아니었다 — langchain 의 `OpenAIEmbeddings` 가 기본값에서
    보내기 전에 **tiktoken 으로 토큰을 세고**, 그 tiktoken 이 인코딩 파일을 처음 쓸 때
    `openaipublic.blob.core.windows.net` 에서 requests 로 내려받는다. 사내망의 TLS
    가로채기에 걸리면 `SSLError ... Max retries exceeded`. 채팅은 토큰을 안 세니 멀쩡해서,
    **임베딩만** 죽는 것처럼 보인다.

    우리는 색인 전에 이미 1200자로 자르므로(retrieval/chunk.py) 그 쪼개기가 필요 없다 —
    얻는 것 없이 외부 네트워크 의존만 늘린다.

    tiktoken 을 **막아 두고** 임베딩이 되는지로 잰다. 되면 그 경로를 안 타는 것이다.
    """
    import http.server
    import json as _json
    import socket
    import threading

    import tiktoken

    import app.agent.config as C

    class H(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length") or 0))
            b = _json.dumps({"object": "list",
                             "data": [{"object": "embedding", "index": 0,
                                       "embedding": [0.1] * 8}]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(b)))
            self.end_headers()
            self.wfile.write(b)

        def log_message(self, *a):
            pass

    sk = socket.socket()
    sk.bind(("127.0.0.1", 0))
    port = sk.getsockname()[1]
    sk.close()
    srv = http.server.HTTPServer(("127.0.0.1", port), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    def _boom(*a, **k):
        raise RuntimeError("tiktoken 이 외부에서 인코딩을 받으려 했다")

    monkeypatch.setattr(tiktoken, "encoding_for_model", _boom)
    monkeypatch.setattr(tiktoken, "get_encoding", _boom)
    monkeypatch.setenv("LAKE_AGENT_PROVIDER", "openai_compat")
    monkeypatch.setenv("LAKE_AGENT_COMPAT_BASE", f"http://127.0.0.1:{port}/v1")
    monkeypatch.setenv("LAKE_AGENT_COMPAT_KEY", "k")
    monkeypatch.setenv("LAKE_AGENT_COMPAT_EMBED", "m1")
    try:
        assert len(C.get_embeddings().embed_query("hi")) == 8
    finally:
        srv.shutdown()


def test_embedding_connection_can_be_split_from_chat(monkeypatch):
    import app.agent.config as C

    monkeypatch.setenv("LAKE_AGENT_PROVIDER", "openai_compat")
    monkeypatch.setenv("LAKE_AGENT_COMPAT_BASE", "http://127.0.0.1:18080/v1")
    monkeypatch.setenv("LAKE_AGENT_COMPAT_KEY", "chat-key")
    monkeypatch.setenv("LAKE_AGENT_COMPAT_CHAT", "ltm-qwen3.6-35b-a3b")
    monkeypatch.setenv("LAKE_AGENT_COMPAT_EMBED", "BAAI/bge-m3")
    monkeypatch.setenv("LAKE_AGENT_EMBED_PROVIDER", "openai_compat")
    monkeypatch.setenv("LAKE_AGENT_EMBED_BASE", "http://127.0.0.1:18081/v1")
    monkeypatch.setenv("LAKE_AGENT_EMBED_KEY", "embed-key")

    chat = C.chat_definition()
    embedding = C.embedding_definition()
    assert chat.base_url == "http://127.0.0.1:18080/v1"
    assert chat.api_key == "chat-key"
    assert embedding.base_url == "http://127.0.0.1:18081/v1"
    assert embedding.api_key == "embed-key"
    assert embedding.model == "BAAI/bge-m3"


def test_simple_chat_connection_can_be_split_from_complex_chat(monkeypatch):
    import app.agent.config as C

    monkeypatch.setenv("LAKE_AGENT_PROVIDER", "openai_compat")
    monkeypatch.setenv("LAKE_AGENT_COMPAT_BASE", "http://192.168.55.173:18080/v1")
    monkeypatch.setenv("LAKE_AGENT_COMPAT_KEY", "complex-key")
    monkeypatch.setenv("LAKE_AGENT_COMPAT_CHAT", "ltm-qwen3.6-35b-a3b")
    monkeypatch.setenv("LAKE_AGENT_COMPAT_CHAT_SIMPLE", "Qwen3.5-4B-4bit")
    monkeypatch.setenv("LAKE_AGENT_SIMPLE_BASE", "http://192.168.55.173:18083/v1")
    monkeypatch.setenv("LAKE_AGENT_SIMPLE_KEY", "simple-key")
    monkeypatch.setenv("LAKE_AGENT_SIMPLE_HEADERS", '{"X-Role":"simple"}')

    complex_chat = C.chat_definition("complex")
    simple_chat = C.chat_definition("simple")
    assert complex_chat.base_url == "http://192.168.55.173:18080/v1"
    assert complex_chat.api_key == "complex-key"
    assert simple_chat.base_url == "http://192.168.55.173:18083/v1"
    assert simple_chat.api_key == "simple-key"
    assert simple_chat.model == "Qwen3.5-4B-4bit"
    assert simple_chat.headers == {"X-Role": "simple"}


def test_qwen_complex_structured_contract_never_changes_semantic_endpoint(monkeypatch):
    """Wire-format capability cannot silently move semantic work to the projection model."""
    import app.agent.config as C

    monkeypatch.setenv("LAKE_AGENT_PROVIDER", "openai_compat")
    monkeypatch.setenv("LAKE_AGENT_SKIP_VERIFY", "1")
    monkeypatch.setenv("LAKE_AGENT_COMPAT_BASE", "http://complex:18080/v1")
    monkeypatch.setenv("LAKE_AGENT_COMPAT_KEY", "complex-key")
    monkeypatch.setenv("LAKE_AGENT_COMPAT_CHAT", "ltm-qwen3.6-35b-a3b")
    monkeypatch.setenv("LAKE_AGENT_COMPAT_CHAT_SIMPLE", "Qwen3.5-4B-4bit")
    monkeypatch.setenv("LAKE_AGENT_SIMPLE_BASE", "http://simple:18083/v1")
    monkeypatch.setenv("LAKE_AGENT_SIMPLE_KEY", "simple-key")
    captured = {}

    class Adapter:
        def chat(self, definition, parameters):
            captured.update(definition=definition, parameters=parameters)
            return object()

    monkeypatch.setattr(C, "_provider_adapter", lambda _provider: Adapter())
    C.get_llm(tier="complex", profile="reasoning", output_contract="structured")

    definition = captured["definition"]
    assert definition.model == "ltm-qwen3.6-35b-a3b"
    assert definition.base_url == "http://complex:18080/v1"
    assert definition.api_key == "complex-key"
    assert captured["parameters"]["max_tokens"] == 4096


def test_qwen_complex_typed_projection_delegates_to_split_simple_endpoint(monkeypatch):
    import app.agent.config as C

    monkeypatch.setenv("LAKE_AGENT_PROVIDER", "openai_compat")
    monkeypatch.setenv("LAKE_AGENT_SKIP_VERIFY", "1")
    monkeypatch.setenv("LAKE_AGENT_COMPAT_BASE", "http://complex:18080/v1")
    monkeypatch.setenv("LAKE_AGENT_COMPAT_KEY", "complex-key")
    monkeypatch.setenv("LAKE_AGENT_COMPAT_CHAT", "ltm-qwen3.6-35b-a3b")
    monkeypatch.setenv("LAKE_AGENT_COMPAT_CHAT_SIMPLE", "Qwen3.5-4B-4bit")
    monkeypatch.setenv("LAKE_AGENT_SIMPLE_BASE", "http://simple:18083/v1")
    monkeypatch.setenv("LAKE_AGENT_SIMPLE_KEY", "simple-key")
    captured = {}

    class Adapter:
        def chat(self, definition, parameters):
            captured.update(definition=definition, parameters=parameters)
            return object()

    monkeypatch.setattr(C, "_provider_adapter", lambda _provider: Adapter())
    C.get_llm(tier="complex", profile="fast_structured",
              output_contract="typed_projection")

    definition = captured["definition"]
    assert definition.model == "Qwen3.5-4B-4bit"
    assert definition.base_url == "http://simple:18083/v1"
    assert definition.api_key == "simple-key"
    assert captured["parameters"]["max_tokens"] == 3072


def test_local_qwen_4b_is_projection_only_and_lightweight_falls_back_to_complex(monkeypatch):
    import app.agent.config as C

    monkeypatch.setenv("LAKE_AGENT_PROVIDER", "openai_compat")
    monkeypatch.setenv("LAKE_AGENT_COMPAT_BASE", "http://complex:18080/v1")
    monkeypatch.setenv("LAKE_AGENT_COMPAT_CHAT", "ltm-qwen3.6-35b-a3b")
    monkeypatch.setenv("LAKE_AGENT_COMPAT_CHAT_SIMPLE", "Qwen3.5-4B-4bit")
    monkeypatch.setenv("LAKE_AGENT_SIMPLE_BASE", "http://simple:18083/v1")

    assert C.execution_tier("projection") == "simple"
    assert C.execution_tier("lightweight_semantic") == "complex"
    assert C.execution_tier("deep_semantic") == "complex"


def test_cloud_mixed_keeps_gpt4o_mini_for_qualified_lightweight_semantics(monkeypatch):
    import app.agent.config as C

    monkeypatch.setenv("LAKE_AGENT_PROVIDER", "openai")
    monkeypatch.setenv("LAKE_AGENT_OPENAI_CHAT", "gpt-4o")
    monkeypatch.setenv("LAKE_AGENT_OPENAI_CHAT_SIMPLE", "gpt-4o-mini")

    assert C.execution_tier("projection") == "simple"
    assert C.execution_tier("lightweight_semantic") == "simple"
    assert C.execution_tier("deep_semantic") == "complex"


def test_aoai_arbitrary_deployment_alias_uses_explicit_simple_profile(monkeypatch):
    row = {
        "id": "cfg", "provider": "aoai",
        "chatModel": "corporate-main-slot",
        "chatModelSimple": "corporate-fast-slot",
        "chatModelProfile": "openai-gpt4o",
        "chatModelSimpleProfile": "openai-gpt4o-mini",
        "apiVersion": "2024-10-21",
    }
    monkeypatch.setattr(C, "_profile", lambda _config_id="": row)
    monkeypatch.setattr(C, "_secret", lambda field, _config_id="": {
        "aoaiEndpoint": "https://aoai.example",
        "aoaiApiKey": "secret",
    }.get(field, ""))

    assert C.chat_definition("complex", config_id="cfg").model_profile == "openai-gpt4o"
    assert C.chat_definition("simple", config_id="cfg").model_profile == "openai-gpt4o-mini"
    assert C.execution_tier("lightweight_semantic", config_id="cfg") == "simple"


def test_same_model_alias_on_different_endpoint_cannot_inherit_complex_profile(monkeypatch):
    row = {
        "id": "cfg", "provider": "openai_compat",
        "chatModel": "shared-deployment-alias",
        "chatModelSimple": "shared-deployment-alias",
        "chatModelProfile": "openai-gpt4o-mini",
        "chatModelSimpleProfile": "",
    }
    monkeypatch.setattr(C, "_profile", lambda _config_id="": row)
    monkeypatch.setattr(C, "_secret", lambda field, _config_id="": {
        "compatBaseUrl": "http://complex:18080/v1",
        "compatApiKey": "complex-key",
        "simpleBaseUrl": "http://simple:18083/v1",
        "simpleApiKey": "simple-key",
    }.get(field, ""))

    complex_definition = C.chat_definition("complex", config_id="cfg")
    simple_definition = C.chat_definition("simple", config_id="cfg")
    assert complex_definition.model == simple_definition.model
    assert complex_definition.base_url != simple_definition.base_url
    assert complex_definition.model_profile == "openai-gpt4o-mini"
    assert simple_definition.model_profile == ""
    assert C.execution_tier("lightweight_semantic", config_id="cfg") == "complex"


def test_legacy_single_lane_inherits_existing_complex_profile(monkeypatch):
    row = {
        "id": "cfg", "provider": "openai_compat",
        "chatModel": "shared-deployment-alias", "chatModelSimple": "",
        "chatModelProfile": "openai-gpt4o-mini",
    }
    monkeypatch.setattr(C, "_profile", lambda _config_id="": row)
    monkeypatch.setattr(C, "_secret", lambda field, _config_id="": {
        "compatBaseUrl": "http://shared:18080/v1",
        "compatApiKey": "key",
    }.get(field, ""))

    assert C.chat_definition("simple", config_id="cfg").model_profile == \
        "openai-gpt4o-mini"


def test_unqualified_simple_profile_cannot_take_semantic_work(monkeypatch):
    import app.agent.config as C

    monkeypatch.setenv("LAKE_AGENT_PROVIDER", "openai_compat")
    monkeypatch.setenv("LAKE_AGENT_COMPAT_BASE", "http://complex:18080/v1")
    monkeypatch.setenv("LAKE_AGENT_COMPAT_CHAT", "vendor-large")
    monkeypatch.setenv("LAKE_AGENT_COMPAT_CHAT_SIMPLE", "vendor-small")
    monkeypatch.setenv("LAKE_AGENT_SIMPLE_BASE", "http://simple:18083/v1")

    assert C.execution_tier("lightweight_semantic") == "complex"


def test_runtime_native_strict_structured_call_stays_on_semantic_endpoint(monkeypatch):
    import app.agent.config as C
    from app.agent import capabilities

    monkeypatch.setenv("LAKE_AGENT_PROVIDER", "openai_compat")
    monkeypatch.setenv("LAKE_AGENT_SKIP_VERIFY", "1")
    monkeypatch.setenv("LAKE_AGENT_COMPAT_BASE", "http://complex:18080/v1")
    monkeypatch.setenv("LAKE_AGENT_COMPAT_KEY", "complex-key")
    monkeypatch.setenv("LAKE_AGENT_COMPAT_CHAT", "ltm-qwen3.6-35b-a3b")
    monkeypatch.setenv("LAKE_AGENT_COMPAT_CHAT_SIMPLE", "Qwen3.5-4B-4bit")
    monkeypatch.setenv("LAKE_AGENT_SIMPLE_BASE", "http://simple:18083/v1")
    monkeypatch.setattr(capabilities, "get", lambda tier="complex", config_id="": {
        "checked": {"json_schema": True}})
    captured = {}

    class Adapter:
        def chat(self, definition, parameters):
            captured.update(definition=definition, parameters=parameters)
            return object()

    monkeypatch.setattr(C, "_provider_adapter", lambda _provider: Adapter())
    C.get_llm(tier="complex", profile="reasoning", output_contract="structured")

    assert captured["definition"].model == "ltm-qwen3.6-35b-a3b"
    assert captured["definition"].base_url == "http://complex:18080/v1"


def test_qwen_complex_semantic_memo_stays_on_large_endpoint(monkeypatch):
    import app.agent.config as C

    monkeypatch.setenv("LAKE_AGENT_PROVIDER", "openai_compat")
    monkeypatch.setenv("LAKE_AGENT_SKIP_VERIFY", "1")
    monkeypatch.setenv("LAKE_AGENT_COMPAT_BASE", "http://complex:18080/v1")
    monkeypatch.setenv("LAKE_AGENT_COMPAT_KEY", "complex-key")
    monkeypatch.setenv("LAKE_AGENT_COMPAT_CHAT", "ltm-qwen3.6-35b-a3b")
    monkeypatch.setenv("LAKE_AGENT_COMPAT_CHAT_SIMPLE", "Qwen3.5-4B-4bit")
    monkeypatch.setenv("LAKE_AGENT_SIMPLE_BASE", "http://simple:18083/v1")
    monkeypatch.setenv("LAKE_AGENT_SIMPLE_KEY", "simple-key")
    captured = {}

    class Adapter:
        def chat(self, definition, parameters):
            captured.update(definition=definition, parameters=parameters)
            return object()

    monkeypatch.setattr(C, "_provider_adapter", lambda _provider: Adapter())
    C.get_llm(tier="complex", profile="reasoning", output_contract="semantic_memo")

    definition = captured["definition"]
    assert definition.model == "ltm-qwen3.6-35b-a3b"
    assert definition.base_url == "http://complex:18080/v1"
    assert definition.api_key == "complex-key"
    # Non-separated reasoning is disabled, while the original semantic profile selects
    # the bounded memo contract row.
    assert captured["parameters"]["max_tokens"] == 2048
    assert captured["parameters"]["temperature"] == 0.2
    assert captured["parameters"]["extra_body"]["chat_template_kwargs"] == {
        "enable_thinking": False}


def test_qwen_complex_free_text_stays_on_large_endpoint(monkeypatch):
    import app.agent.config as C

    monkeypatch.setenv("LAKE_AGENT_PROVIDER", "openai_compat")
    monkeypatch.setenv("LAKE_AGENT_SKIP_VERIFY", "1")
    monkeypatch.setenv("LAKE_AGENT_COMPAT_BASE", "http://complex:18080/v1")
    monkeypatch.setenv("LAKE_AGENT_COMPAT_KEY", "complex-key")
    monkeypatch.setenv("LAKE_AGENT_COMPAT_CHAT", "ltm-qwen3.6-35b-a3b")
    monkeypatch.setenv("LAKE_AGENT_COMPAT_CHAT_SIMPLE", "Qwen3.5-4B-4bit")
    monkeypatch.setenv("LAKE_AGENT_SIMPLE_BASE", "http://simple:18083/v1")
    captured = {}

    class Adapter:
        def chat(self, definition, parameters):
            captured["definition"] = definition
            return object()

    monkeypatch.setattr(C, "_provider_adapter", lambda _provider: Adapter())
    C.get_llm(tier="complex", profile="balanced")

    assert captured["definition"].model == "ltm-qwen3.6-35b-a3b"
    assert captured["definition"].base_url == "http://complex:18080/v1"


def test_split_simple_model_catalog_uses_its_own_endpoint(monkeypatch):
    import app.agent.config as C

    monkeypatch.setattr(C, "chat_definition", lambda tier="complex", **kwargs:
                        C.ModelDefinition("openai_compat", "simple-configured", "http://simple/v1")
                        if tier == "simple" else
                        C.ModelDefinition("openai_compat", "complex", "http://complex/v1"))

    class Model:
        def __init__(self, model_id):
            self.id = model_id

    class Client:
        def __init__(self, *args, **kwargs):
            assert kwargs["base_url"] == "http://simple/v1"
            self.models = self

        def list(self):
            return [Model("simple-discovered")]

    monkeypatch.setattr("openai.OpenAI", Client)
    result = C._with_split_simple_models(
        {"chat": ["complex"], "simple": [], "embed": [], "error": ""}, 1)
    assert result["simple"] == ["simple-configured", "simple-discovered"]


def test_split_embedding_model_catalog_uses_its_own_endpoint(monkeypatch):
    import app.agent.config as C

    monkeypatch.setattr(C, "chat_definition", lambda **kwargs:
                        C.ModelDefinition("openai_compat", "chat", "http://chat/v1"))
    monkeypatch.setattr(C, "embedding_definition", lambda *args, **kwargs:
                        C.ModelDefinition("openai_compat", "BAAI/bge-m3", "http://embed/v1"))

    class Model:
        def __init__(self, model_id):
            self.id = model_id

    class Client:
        def __init__(self, *args, **kwargs):
            assert kwargs["base_url"] == "http://embed/v1"
            self.models = self

        def list(self):
            return [Model("BAAI/bge-m3"), Model("irrelevant-chat-model")]

    monkeypatch.setattr("openai.OpenAI", Client)
    result = C._with_split_embedding_models({"chat": ["chat"], "embed": [], "error": ""}, 1)
    assert result["embed"] == ["BAAI/bge-m3"]
    assert result["total"] == 2


def test_split_embedding_catalog_keeps_configured_model_when_models_api_is_absent(monkeypatch):
    import app.agent.config as C

    monkeypatch.setattr(C, "chat_definition", lambda **kwargs:
                        C.ModelDefinition("openai_compat", "chat", "http://chat/v1"))
    monkeypatch.setattr(C, "embedding_definition", lambda *args, **kwargs:
                        C.ModelDefinition("openai_compat", "BAAI/bge-m3", "http://tei/v1"))

    class Client:
        def __init__(self, *args, **kwargs):
            self.models = self

        def list(self):
            raise RuntimeError("404 models")

    monkeypatch.setattr("openai.OpenAI", Client)
    result = C._with_split_embedding_models({"chat": ["chat"], "embed": [], "error": ""}, 1)
    assert result["embed"] == ["BAAI/bge-m3"]
    assert result["warnings"] and not result["error"]
