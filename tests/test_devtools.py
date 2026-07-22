# -*- coding: utf-8 -*-
"""개발자용 진단 기능(dev tools) — 게이팅 + 안전한 스키마 덤프."""
from app import devtools as dt


def test_schema_masks_values_keeps_structure():
    """값은 마스킹(타입/길이)하고 키 구조는 보존 — 화면에 찍어도 사내 데이터가 안 샌다."""
    sample = {"code": {"count": 3, "isLastPage": True, "values": [
        {"repository": {"slug": "my-repo", "project": {"key": "DATA"}},
         "file": "src/main/App.java",
         "hitContexts": [[{"line": 12, "text": "secret token abcdef ghijk lmnop qrstuv"}]]}]}}
    sk = dt.schema_of(sample)
    flat = dt.key_tree(sk)
    joined = "\n".join(flat)
    # 구조(키)는 있다
    assert any("repository.slug" in k for k in flat)
    assert any("file" in k for k in flat)
    assert any("hitContexts" in k and "line" in k for k in flat) or \
        any("hitContexts" in k and "text" in k for k in flat)
    # 긴 값은 앞 24자 힌트만 남고 뒷부분은 잘린다(전체 노출 방지)
    assert "qrstuv" not in joined                # 뒤쪽은 안 보임
    assert "str(38)" in joined                   # 길이는 알려주되 값 전체는 아님
    assert "int" in joined and "bool" in joined


def test_list_shows_count_and_merged_keys():
    sk = dt.schema_of({"values": [{"a": 1}, {"a": 2, "b": 3}]})
    flat = "\n".join(dt.key_tree(sk))
    assert "[2개]" in flat                      # 개수 표시
    assert "a" in flat and "b" in flat          # 항목마다 다른 키도 병합돼 보임


def test_enabled_flag():
    class S:
        dev_tools = {"bitbucket_probe"}
    assert dt.enabled(S, "bitbucket_probe")
    assert not dt.enabled(S, "other")
    class S2:
        dev_tools = set()
    assert not dt.any_enabled(S2)


def test_dev_routes_gated_by_flag(monkeypatch):
    """dev_tools 가 비면 /api/dev/* 라우트 자체가 없고, 켜면 붙는다.

    config 값에 의존하지 않게 env(LAKE_DEV_TOOLS)로 강제한다.
    """
    import importlib
    from fastapi.testclient import TestClient

    # 꺼짐 — env 로 빈 목록 강제
    monkeypatch.setenv("LAKE_DEV_TOOLS", "__none__")
    import app.settings
    importlib.reload(app.settings)
    import app.main
    importlib.reload(app.main)
    c = TestClient(app.main.app)
    assert c.get("/api/dev/tools").status_code == 404
    assert c.get("/api/health").status_code == 200

    # 켜짐 — env 로 활성화하면 라우트가 생긴다
    monkeypatch.setenv("LAKE_DEV_TOOLS", "bitbucket_probe")
    importlib.reload(app.settings)
    importlib.reload(app.main)
    c2 = TestClient(app.main.app)
    assert c2.get("/api/dev/tools").status_code == 200

    # 원상복구 — 다른 테스트에 영향 없게 기본으로 리로드
    monkeypatch.delenv("LAKE_DEV_TOOLS", raising=False)
    importlib.reload(app.settings)
    importlib.reload(app.main)
