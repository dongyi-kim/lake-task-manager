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
    schema = {"type": "object",
              "properties": {"intent": {"type": "string", "enum": ["search", "create", "update"]},
                             "confident": {"type": "boolean"},
                             "n": {"type": "integer"}}}
    out = C.get_llm().with_structured_output(schema).invoke("티켓 만들어줘")
    assert out["intent"] in ("search", "create", "update")
    assert isinstance(out["confident"], bool)
    assert isinstance(out["n"], int)


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
