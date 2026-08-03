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


# ── PAT 확인(pat_probe) ──────────────────────────────────────────────────────
# 실제 사내 인스턴스에서 PAT 발급이 404 로 막혔는데, 응답이 Jira 의 'Oops, You've found a
# dead link' **HTML 안내 페이지**였다. 한 경로만 찌르고 상태코드만 보면 '기능이 없다'와
# '우리가 엉뚱한 데를 쳤다'가 구분되지 않는다 — 아래는 그 구분이 유지되는지 본다.

def _pat_client(responses):
    """provider 를 갈아끼운 TestClient — responses[path] = dict(200) 또는 (status, body)."""
    from fastapi.testclient import TestClient
    from app.auth.base import UpstreamError
    import app.main

    class _Stub:
        def get_json(self, url, params=None, priority=0, quiet=False):
            path = url.split("8080", 1)[-1] if "8080" in url else url
            for p, r in responses.items():
                if path.endswith(p):
                    if isinstance(r, tuple):
                        raise UpstreamError(r[0], p, r[1])
                    return r
            raise UpstreamError(404, path, "not stubbed")

    # provider 는 lazy property(세터 없음) — 내부 슬롯을 직접 갈아끼운다.
    cl = app.main._client
    real, built = cl._provider, cl._provider_built
    cl._provider, cl._provider_built = _Stub(), True

    def restore():
        cl._provider, cl._provider_built = real, built

    return TestClient(app.main.app), restore


_DEAD_LINK = "<!DOCTYPE html><html><head><title>Oops</title></head><body>" \
             "<h1>Oops, You've found a dead link.</h1></body></html>"


def test_pat_dead_link_page_is_reported_as_feature_absent():
    """전부 HTML 404 + sanity 200 → '이 인스턴스에 PAT 가 없다'로 결론나야 한다."""
    c, restore = _pat_client({
        "/rest/api/2/myself": {"name": "me"},
        "/rest/pat/latest/tokens": (404, _DEAD_LINK),
        "/rest/pat/1.0/tokens": (404, _DEAD_LINK),
        "/secure/ViewPersonalAccessTokens.jspa": (404, _DEAD_LINK),
    })
    try:
        d = c.get("/api/dev/pat/jira").json()
        assert d["supported"] is False and d["restPath"] is None
        assert d["sanity"]["ok"] is True                     # 세션·주소는 멀쩡하다
        rest = d["rest"]["/rest/pat/latest/tokens"]
        assert rest["status"] == 404 and rest["body"] == "HTML(오류 페이지)"
        assert "자원 없음" in rest["meaning"]                 # HTML 404 = 자원 미등록
        assert "PAT 가 열려 있지 않습니다" in d["verdict"]
        # 발급도 시도조차 하지 않는다 — 없는 자원에 POST 를 쏴 봐야 같은 404 만 돌아온다
        p = c.post("/api/dev/pat/jira", json={}).json()
        assert p["issued"] is False and p["status"] == 404
        assert "시도하지 않았습니다" in p["detail"]
    finally:
        restore()


def test_pat_ui_alive_but_rest_blocked():
    """화면은 200 인데 REST 만 404 → '화면은 있고 REST 만 막혔다'로 갈라야 한다."""
    c, restore = _pat_client({
        "/rest/api/2/myself": {"name": "me"},
        "/rest/pat/latest/tokens": (404, _DEAD_LINK),
        "/rest/pat/1.0/tokens": (404, _DEAD_LINK),
        "/secure/ViewPersonalAccessTokens.jspa": {"html": "ok"},
    })
    try:
        d = c.get("/api/dev/pat/jira").json()
        assert d["supported"] is False
        assert "REST 는 막혀" in d["verdict"]
    finally:
        restore()


def test_pat_session_dead_is_not_blamed_on_pat():
    """sanity 부터 401 이면 PAT 얘기를 하면 안 된다 — 로그인/주소 문제로 안내한다."""
    c, restore = _pat_client({
        "/rest/api/2/myself": (401, '{"message":"세션 없음"}'),
        "/rest/pat/latest/tokens": (401, '{"message":"세션 없음"}'),
        "/rest/pat/1.0/tokens": (401, '{"message":"세션 없음"}'),
        "/secure/ViewPersonalAccessTokens.jspa": (401, '{"message":"세션 없음"}'),
    })
    try:
        d = c.get("/api/dev/pat/jira").json()
        assert d["supported"] is False
        assert "로그인/주소" in d["verdict"]
        assert "인증·권한" in d["rest"]["/rest/pat/latest/tokens"]["meaning"]
    finally:
        restore()


def test_pat_available_lists_tokens_without_values():
    """열려 있으면 supported + 토큰 목록(값은 애초에 안 옴)."""
    c, restore = _pat_client({
        "/rest/api/2/myself": {"name": "me"},
        "/rest/pat/latest/tokens": [{"id": "1", "name": "t1", "expiringAt": "2026-09-01"}],
    })
    try:
        d = c.get("/api/dev/pat/jira").json()
        assert d["supported"] is True and d["restPath"] == "/rest/pat/latest/tokens"
        assert d["count"] == 1 and d["tokens"][0]["name"] == "t1"
        assert "rawToken" not in str(d)
        assert "사용 가능" in d["verdict"]
    finally:
        restore()
