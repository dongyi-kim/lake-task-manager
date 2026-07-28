# -*- coding: utf-8 -*-
"""개발자용 진단 기능(dev tools) — 게이팅 + 안전한 스키마 덤프."""
from app.infra import devtools as dt


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


def test_enabled_open_for_registered():
    """지금은 config 무관 — 등록된(DEV_TOOLS) 기능은 항상 열려 있고, 미등록은 닫힘.
    노출 제어는 나중에 역할 훅(enabled 의 role 인자)으로 붙인다."""
    assert dt.enabled(None, "bitbucket_probe")     # 등록됨 → 열림
    assert dt.enabled(None, "sso_status")
    assert not dt.enabled(None, "존재하지않는기능")  # 미등록 → 닫힘
    assert dt.any_enabled(None)


def test_dev_routes_open_now():
    """지금은 dev 라우트가 항상 열려 있다(config 무관).
    역할 구분이 생기면 devtools.enabled() 한 곳에서 가리면 된다."""
    from fastapi.testclient import TestClient
    import app.main
    c = TestClient(app.main.app)
    assert c.get("/api/dev/tools").status_code == 200
    assert c.get("/api/dev/sso").status_code == 200
    assert c.get("/api/health").status_code == 200
