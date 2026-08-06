"""`/api/agent/*` — 게이팅 · 설정 · 승인 흐름.

라우트가 지켜야 할 것은 둘이다: **비밀이 화면으로 새지 않는 것**, 그리고 **승인 없이는
아무것도 안 만들어지는 것**. 나머지는 그래프 테스트가 본다.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JIRA_ENV", "mock")

pytest.importorskip("langgraph", reason="requirements-agent.txt 미설치")

from fastapi.testclient import TestClient     # noqa: E402


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("LAKE_AGENT_PROVIDER", "fake")
    import app.infra.settings as S
    monkeypatch.setattr(S, "CACHE_DIR", tmp_path)
    from app.agent import approval
    from app.agent.workflow import graph
    approval.clear()
    graph.reset()
    from app.main import app as fastapi_app
    yield TestClient(fastapi_app)
    approval.clear()
    graph.reset()


def test_routes_are_registered_when_installed(client):
    assert client.get("/api/agent/status").status_code == 200


def test_frontend_is_told_the_agent_is_available(client):
    """버튼이 없을 때 사용자가 '설치가 빠진 건지 고장인지'를 알아야 한다."""
    p = client.get("/api/prefs").json()
    assert p["agentEnabled"] is True
    assert p["agentReason"] == ""


def test_status_never_returns_a_raw_key(client, monkeypatch):
    from app.agent import secrets as S
    monkeypatch.setattr(S, "load", lambda: {"openaiApiKey": "sk-live-XYZ-987654"})
    body = client.get("/api/agent/status").text
    assert "sk-live-XYZ-987654" not in body
    assert "9876" not in body or "XYZ" not in body      # 끝 4자 힌트 외에는 남지 않는다


def test_settings_put_stores_the_model_in_the_right_slot(client, monkeypatch):
    saved = {}
    from app.infra import prefs
    monkeypatch.setattr(prefs, "save", lambda patch: saved.update(patch) or saved)
    r = client.put("/api/agent/settings",
                   json={"provider": "openai", "chatModel": "gpt-4o", "embedModel": "text-embedding-3-large"})
    assert r.status_code == 200
    assert saved["agentProvider"] == "openai"
    assert saved["agentOpenaiChat"] == "gpt-4o"          # aoai 자리에 들어가면 안 된다
    assert "agentAoaiChat" not in saved


def test_settings_put_does_not_echo_the_secret_back(client, monkeypatch):
    from app.agent import secrets as S
    monkeypatch.setattr(S, "save", lambda patch: patch)
    r = client.put("/api/agent/settings", json={"secrets": {"openaiApiKey": "sk-secret-4321"}})
    assert "sk-secret-4321" not in r.text


def test_probe_reports_where_it_broke(client):
    r = client.post("/api/agent/probe").json()
    assert r["provider"] == "fake" and r["ok"] is True
    assert r["embeddings"]["dim"] == 256


def test_index_stats_are_visible(client):
    r = client.get("/api/agent/index").json()
    assert "static" in r and "dynamic" in r


def test_chat_rejects_an_empty_message(client):
    assert client.post("/api/agent/chat", json={"text": "  "}).status_code == 400


def test_chat_answers_and_keeps_a_thread(client):
    r = client.post("/api/agent/chat", json={"text": "데이터 카탈로그 관련 이력 알려줘"}).json()
    assert r["thread_id"] and r["reply"]
    again = client.post("/api/agent/chat",
                        json={"text": "더 자세히", "threadId": r["thread_id"]}).json()
    assert again["thread_id"] == r["thread_id"]


def test_stream_emits_progress_events(client):
    """조사에 십수 초가 걸린다 — 빈 화면을 보여 주면 사용자는 멈춘 줄 안다."""
    with client.stream("POST", "/api/agent/chat/stream",
                       json={"text": "실시간 수집 관련 이력"}) as r:
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/event-stream")
        kinds = [line for line in r.iter_lines() if line.startswith("data: ")]
    assert kinds and any('"type": "final"' in k or '"type":"final"' in k for k in kinds)


def test_approve_with_a_bogus_token_is_refused(client):
    r = client.post("/api/agent/approve", json={"threadId": "t1", "token": "made-up"}).json()
    assert r["ok"] is False


def test_cancel_is_idempotent_enough(client):
    assert client.post("/api/agent/cancel", json={"threadId": "t1", "token": "x"}).json()["ok"] is True


def test_snapshot_of_an_unknown_thread_does_not_explode(client):
    assert client.get("/api/agent/snapshot/없는대화").status_code == 200
