"""agent/config — provider 4-way 해석 + fake 경로가 키 없이 도는지.

이 테스트는 **키 없이** 돌아야 한다. 개발 PC 에서는 사내 AOAI 가 Private Endpoint 로 막혀
있어(403) 실 호출을 전제로 하면 CI 도 로컬도 아무것도 검증하지 못한다.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
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


def test_status_never_leaks_secrets(clean_env, monkeypatch):
    monkeypatch.setattr(S, "load", lambda: {"aoaiApiKey": "sk-super-secret-1234",
                                            "aoaiEndpoint": "https://x.example"})
    st = C.status()
    blob = repr(st)
    assert "sk-super-secret-1234" not in blob
    assert "1234" in st["secrets"]["aoaiApiKey"]             # 끝 4자만 힌트로
    assert "super-secret" not in st["secrets"]["aoaiApiKey"]
    assert st["secrets"]["aoaiEndpoint"] == "https://x.example"   # 비밀 아님 → 그대로


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
    """Planner 의 의도 분류가 fake 로도 굴러가야 그래프 분기를 테스트할 수 있다."""
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


def test_role_tiers_assigned_to_shallow_judgment_roles(clean_env):
    """의도 분류·결정적 실행만 simple — 조사·초안·검토·작문은 기본 모델을 유지한다."""
    from app.agent.workflow.agents.assigner import Assigner
    from app.agent.workflow.agents.historian import Historian
    from app.agent.workflow.agents.operator import Operator
    from app.agent.workflow.agents.planner import Planner
    from app.agent.workflow.agents.refiner import Refiner
    from app.agent.workflow.agents.responder import Responder
    from app.agent.workflow.agents.reviewer import Reviewer
    assert Planner.tier == "simple" and Operator.tier == "simple"
    for cls in (Historian, Refiner, Assigner, Reviewer, Responder):
        assert cls.tier == "complex", cls.__name__


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
    from app.agent.workflow.agents.planner import SCHEMA
    from app.agent.workflow.prompts import persona
    expect = {"epic_create", "task_create", "bug_report", "subtask_bulk", "find_people",
              "find_tickets", "knowledge", "history", "workload", "assign_fit"}
    assert expect <= set(PLAYBOOKS), set(PLAYBOOKS)
    for k in expect:
        assert "플로우" in PLAYBOOKS[k] and "주의" in PLAYBOOKS[k], k
    # Planner enum 과 자산이 어긋나면 조용히 주입이 빠진다 — 함께 묶어 검증
    assert expect <= set(SCHEMA["properties"]["playbook"]["enum"])
    p = persona({"playbook": "subtask_bulk"})
    assert "Standard playbook" in p and "재질문 금지" in p
    assert "Standard playbook" not in persona({})

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



def test_compat_base_url_gets_the_v1_path_when_the_user_typed_only_the_host(monkeypatch):
    """호환 엔드포인트에 경로가 없으면 `/v1` 을 붙인다.

    OpenAI SDK 는 base_url 뒤에 `/models`·`/chat/completions` 를 **상대로** 붙인다.
    사용자가 호스트만 넣으면 `{host}/models` 를 부르고, 호환 서버(vLLM·Ollama·LM Studio·
    TGI)는 거기에 아무것도 없어 404 다 — 화면에는 "조회 실패"로만 보인다.
    이미 경로가 있으면 손대지 않는다(`/openai/v1` 같은 배치도 있다).
    """
    import app.agent.config as C
    monkeypatch.setenv("LAKE_AGENT_COMPAT_BASE", "https://llm.example")
    assert C.compat_base() == "https://llm.example/v1"
    monkeypatch.setenv("LAKE_AGENT_COMPAT_BASE", "https://llm.example/")
    assert C.compat_base() == "https://llm.example/v1"
    for typed in ("https://llm.example/v1", "https://llm.example/openai/v1",
                  "https://llm.example/api/v1"):
        monkeypatch.setenv("LAKE_AGENT_COMPAT_BASE", typed)
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
