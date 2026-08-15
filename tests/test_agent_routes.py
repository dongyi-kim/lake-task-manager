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


def test_legacy_settings_put_does_not_silently_change_the_active_config(client, monkeypatch):
    """★ prefs 를 **스텁하지 않는다.**

    처음엔 `prefs.save` 를 가로채 인자만 확인했는데, 정작 `prefs` 는 `_DEFAULTS` 에 없는 키를
    조용히 버리고 있었다 — 화면은 저장됐다고 하고 값은 사라지는 상태를 테스트가 통과시켰다.
    가로챈 것을 검사하면 가로챈 것만 검사하게 된다. 여기서는 **다시 읽어서** 확인한다.
    """
    from app.infra import prefs
    monkeypatch.delenv("LAKE_AGENT_PROVIDER", raising=False)
    r = client.put("/api/agent/settings",
                   json={"provider": "openai", "chatModel": "gpt-4o", "embedModel": "text-embedding-3-large"})
    assert r.status_code == 200
    saved = prefs.load()
    assert saved["agentProvider"] == "openai"
    assert saved["agentOpenaiChat"] == "gpt-4o"          # aoai 자리에 들어가면 안 된다
    assert not saved.get("agentAoaiChat")
    # 레거시 고정 탭 저장은 가져오기 후보로만 남는다. 저장·편집이 활성 연결을 바꾸면 안 된다.
    from app.agent import config
    assert config.provider() != "openai"
    assert client.get("/api/agent/status").json()["legacyCandidate"]["chatModel"] == "gpt-4o"


def test_named_configs_allow_multiple_profiles_of_the_same_provider(client):
    a = client.post("/api/agent/configs", json={"name": "개인 OpenAI", "provider": "openai"})
    b = client.post("/api/agent/configs", json={"name": "팀 OpenAI", "provider": "openai"})
    assert a.status_code == 200 and b.status_code == 200
    rows = client.get("/api/agent/status").json()["configs"]
    assert [(x["name"], x["provider"]) for x in rows] == [
        ("개인 OpenAI", "openai"), ("팀 OpenAI", "openai")]


def test_editing_candidate_does_not_change_active_profile(client):
    first = client.post("/api/agent/configs", json={"name": "현재", "provider": "fake"}).json()["config"]
    client.post(f"/api/agent/configs/{first['id']}/probe/auth")
    client.post(f"/api/agent/configs/{first['id']}/probe")
    assert client.post(f"/api/agent/configs/{first['id']}/activate").json()["ok"]

    second = client.post("/api/agent/configs", json={"name": "후보", "provider": "fake"}).json()["config"]
    client.put(f"/api/agent/configs/{second['id']}", json={"chatModel": "fake-chat"})
    status = client.get("/api/agent/status").json()
    assert status["activeConfigId"] == first["id"]
    assert next(x for x in status["configs"] if x["id"] == second["id"])["active"] is False


def test_profile_model_listing_uses_requested_config(client, monkeypatch):
    row = client.post("/api/agent/configs", json={"name": "선택한 후보", "provider": "openai"}).json()["config"]
    from app.agent import config as C
    seen = {}
    def scoped(*, config_id="", **_):
        seen["id"] = config_id
        return {"chat": ["right-model"], "embed": [], "error": ""}
    monkeypatch.setattr(C, "list_models", scoped)
    result = client.get(f"/api/agent/configs/{row['id']}/models").json()
    assert seen["id"] == row["id"] and result["chat"] == ["right-model"]


def test_named_profile_secrets_are_isolated_and_never_returned_raw(client):
    a = client.post("/api/agent/configs", json={"name": "A", "provider": "openai"}).json()["config"]
    b = client.post("/api/agent/configs", json={"name": "B", "provider": "openai"}).json()["config"]
    client.put(f"/api/agent/configs/{a['id']}", json={"secrets": {"openaiApiKey": "sk-a-secret-1111"}})
    client.put(f"/api/agent/configs/{b['id']}", json={"secrets": {"openaiApiKey": "sk-b-secret-2222"}})
    body = client.get("/api/agent/status").text
    assert "sk-a-secret" not in body and "sk-b-secret" not in body
    rows = {x["name"]: x for x in client.get("/api/agent/status").json()["configs"]}
    assert "1111" in rows["A"]["secrets"]["openaiApiKey"]
    assert "2222" in rows["B"]["secrets"]["openaiApiKey"]


def test_each_legacy_provider_can_be_imported_without_becoming_active(client, monkeypatch):
    from app.infra import prefs
    from app.agent import secrets
    monkeypatch.delenv("LAKE_AGENT_PROVIDER", raising=False)
    prefs.save({"agentProvider": "openai_compat", "agentOpenaiChat": "gpt-4o",
                "agentOpenaiEmbed": "text-embedding-3-small",
                "agentCompatChat": "qwen-old", "agentCompatEmbed": "bge-old"})
    secrets.save({"openaiApiKey": "sk-openai-1111", "compatBaseUrl": "http://old/v1",
                  "compatApiKey": "compat-2222"})
    before = client.get("/api/agent/status").json()
    assert {x["provider"] for x in before["legacyCandidates"]} == {"openai", "openai_compat"}

    imported = client.post("/api/agent/configs/import-legacy",
                           json={"name": "기존 OpenAI", "provider": "openai"}).json()["config"]
    after = client.get("/api/agent/status").json()
    assert imported["provider"] == "openai" and imported["chatModel"] == "gpt-4o"
    assert after["activeConfigId"] == ""
    assert {x["provider"] for x in after["legacyCandidates"]} == {"openai_compat"}


def test_every_agent_pref_key_survives_a_round_trip(monkeypatch, tmp_path):
    """prefs 화이트리스트(_DEFAULTS)에 빠진 키는 조용히 사라진다 — 새 설정을 늘릴 때의 함정.

    ★ CACHE_DIR 을 반드시 옮긴다. 처음엔 빠뜨려서 **개발자의 실제 app_prefs.json 에 썼다** —
      테스트가 사용자 설정을 덮어쓰는 건 실패보다 나쁘다(조용하고, 나중에 발견된다).
    """
    import app.infra.settings as S
    monkeypatch.setattr(S, "CACHE_DIR", tmp_path)
    from app.infra import prefs
    keys = ["agentProvider", "agentApiVersion", "agentAoaiChat", "agentAoaiEmbed",
            "agentOpenaiChat", "agentOpenaiEmbed", "agentCompatChat", "agentCompatEmbed"]
    prefs.save({k: "v-" + k for k in keys})
    got = prefs.load()
    missing = [k for k in keys if got.get(k) != "v-" + k]
    assert not missing, f"prefs 가 삼킨 키: {missing}"


def test_settings_put_does_not_echo_the_secret_back(client, monkeypatch):
    from app.agent import secrets as S
    monkeypatch.setattr(S, "save", lambda patch: patch)
    r = client.put("/api/agent/settings", json={"secrets": {"openaiApiKey": "sk-secret-4321"}})
    assert "sk-secret-4321" not in r.text


def test_probe_reports_where_it_broke(client):
    r = client.post("/api/agent/probe").json()
    assert r["provider"] == "fake" and r["ok"] is True
    assert r["embeddings"]["dim"] == 256


def test_models_endpoint_lists_for_the_active_provider(client):
    """콤보박스 재료. fake 는 자기 모델을, 실패는 200+사유를 준다(목록은 참고이지 제약이 아니다)."""
    r = client.get("/api/agent/models").json()
    assert r["chat"] == ["fake-chat"] and r["embed"] == ["fake-embed"]


def test_models_endpoint_fails_soft(client, monkeypatch):
    """조회가 막혀도 500 이 아니라 빈 목록 + 사유 — 화면은 자유 입력으로 폴백한다."""
    from app.agent import config as C
    monkeypatch.setenv("LAKE_AGENT_PROVIDER", "openai")     # 키 없음 → 조회 실패가 정상
    r = client.get("/api/agent/models")
    assert r.status_code == 200
    body = r.json()
    assert body["chat"] == [] and body["error"]


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
