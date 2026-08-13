"""통합 검색 — mock(jira820 additive: DL + JIRA820) 로 3소스 fan-out 검증."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.domain import search   # noqa: E402
from app.infra.cache import Cache                  # noqa: E402
from app.jira.jira_client import JiraClient       # noqa: E402
from app.infra.settings import get_settings        # noqa: E402


def _client():
    return JiraClient(get_settings(), Cache(":memory:"))


def test_search_fans_out_three_sources():
    r = search.search_all(_client(), get_settings(), "런북", scope="all", limit=6)
    assert set(("jira", "confluence", "bitbucket")) <= set(r)
    assert "items" in r["jira"] and "items" in r["confluence"] and "items" in r["bitbucket"]


def test_jira_multiproject_scoped():
    s = get_settings()
    r = search.search_all(_client(), s, "the", scope="scoped", limit=15)
    projs = {i["project"] for i in r["jira"]["items"]}
    # 스코프 프로젝트(config)에 포함된 것만 (DL / JIRA820 등)
    assert projs <= set(s.search_jira_projects)
    for it in r["jira"]["items"]:
        assert it["type"] == "jira" and it["key"] and "url" in it


def test_confluence_search_returns_pages():
    # dev corpus의 "런북" 결과는 search.confluence.spaces 밖에도 존재한다. scope="all"도
    # config 밖으로 넓히지 않는 것이 계약이므로, 허용 space 안에 있는 "가이드"로 검증한다.
    r = search.search_all(_client(), get_settings(), "가이드", scope="all", limit=6)
    items = r["confluence"]["items"]
    assert items, "허용 Confluence space의 검색 결과가 있어야(가이드)"
    assert {it["space"] for it in items} <= set(get_settings().search_confluence_spaces)
    for it in items:
        assert it["type"] == "confluence" and it["title"]
        assert it["url"].startswith("/spaces/") or "/spaces/" in it["url"]


def test_bitbucket_off_by_default():
    """Bitbucket 연동은 기본 꺼짐 — 켜지 않으면 검색에 아예 안 낀다(빈 칸)."""
    s = get_settings()
    assert s.bitbucket_enabled is False
    r = search.search_all(_client(), s, "ETL", scope="scoped", limit=5)
    assert r["bitbucket"]["items"] == []


def test_bitbucket_when_enabled_is_mock():
    """켜면 mock 결과가 나온다(연동 예정)."""
    s = get_settings()
    s.set_bitbucket_enabled(True)
    try:
        r = search.search_all(_client(), s, "ETL", scope="scoped", limit=5)
        bb = r["bitbucket"]
        assert bb.get("mock") is True
        assert bb["items"] and all(i.get("mock") and i["repo"] for i in bb["items"])
    finally:
        s.set_bitbucket_enabled(False)


def test_empty_query_returns_empty():
    r = search.search_all(_client(), get_settings(), "   ", limit=5)
    assert r["jira"]["items"] == [] and r["confluence"]["items"] == [] and r["bitbucket"]["items"] == []


def test_endpoint_ok():
    from fastapi.testclient import TestClient

    from app.main import app
    j = TestClient(app).get("/api/search", params={"q": "런북", "scope": "all"}).json()
    assert "jira" in j and "confluence" in j and "bitbucket" in j


def test_user_badge_endpoint_returns_full_display_name_and_username():
    """공통 사람 호버는 Jira 전체 표시명과 username을 받으며 메일은 노출하지 않는다."""
    from fastapi.testclient import TestClient

    from app.main import app
    c = TestClient(app)
    users = c.get("/api/mention/users", params={"q": "skcc."}).json()
    assert users
    uid = users[0]["id"]
    r = c.get("/api/mention/user/" + uid)
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == uid
    assert body["displayName"]
    assert body["username"] == uid
    assert "mail" not in body


def test_user_badge_endpoint_does_not_guess_unknown_people(monkeypatch):
    from fastapi.testclient import TestClient

    import app.main as main
    monkeypatch.setattr(main, "_client", type("MissingUserClient", (), {
        "user_badge": lambda self, uid: None,
    })())
    r = TestClient(main.app).get("/api/mention/user/not.a.real.user")
    assert r.status_code == 404


def test_browse_route_serves_spa():
    """/browse/{key} — Jira 와 같은 URL 로 티켓 단독 페이지를 연다.

    서버는 SPA 진입점만 돌려주고(서버 렌더링 없음), 어떤 티켓인지는 프론트가
    경로에서 읽는다. 정적 마운트("/")보다 먼저 선언돼야 마운트에 먹히지 않는다.
    """
    from fastapi.testclient import TestClient

    from app.main import app
    c = TestClient(app)
    r = c.get("/browse/DL-9018")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    # 하위 경로에서도 상대 자산이 루트 기준으로 풀려야 한다
    assert '<base href="/">' in r.text


def _keys(q):
    from fastapi.testclient import TestClient

    from app.main import app
    r = TestClient(app).get("/api/search", params={"q": q}).json()
    return [(x["key"], x.get("exact", False)) for x in r["jira"]["items"]]


def test_exact_ticket_key_comes_first():
    """티켓 키를 그대로 치면 그 티켓이 맨 위.

    text~ 검색만으로는 본문에 그 키가 언급된 다른 티켓이 위에 올 수 있다
    (예: DL-9001 이 코멘트에 적힌 DL-9007). 정확히 그 티켓을 찾는 게 의도다.
    """
    ks = _keys("DL-9001")
    assert ks and ks[0] == ("DL-9001", True)


def test_bare_number_resolves_to_project_key():
    """번호만 쳐도 검색 대상 프로젝트 키를 붙여 찾는다."""
    ks = _keys("9001")
    assert ks and ks[0] == ("DL-9001", True)


def test_key_match_is_case_insensitive():
    ks = _keys("dl-9001")
    assert ks and ks[0] == ("DL-9001", True)


def test_exact_match_is_not_duplicated():
    ks = _keys("DL-9001")
    assert [k for k, _ in ks].count("DL-9001") == 1


def test_unknown_key_does_not_break_search():
    """없는 키를 쳐도 조용히 넘어간다(500 금지)."""
    assert _keys("DL-99999") == []


def test_confluence_401_gives_actionable_message():
    """Confluence 는 도메인이 달라 Jira 세션만으로는 401 이 난다.

    원문 메시지('세션 만료 가능')는 Jira 가 끊긴 것처럼 읽혀 오해를 부른다 →
    Confluence 전용 안내로 바꾸고 needLogin 플래그를 준다.
    """
    from app.auth.base import SessionExpired
    from app.domain.search import _search_confluence
    from app.infra.settings import get_settings

    class _P:
        def get_json(self, *a, **k):
            raise SessionExpired("HTTP 401 on /rest/api/search — 세션 만료 가능. login 재실행.")

    class _C:
        provider = _P()

        def _conf_get_json(self, url, params=None):
            # 무음 갱신 불가(dev provider) → 실제 클라이언트와 같이 SessionExpired 를 그대로 올린다.
            return self.provider.get_json(url, params=params)

    s = get_settings()
    out = _search_confluence(_C(), s, "테스트", "scoped", 5)
    assert out["items"] == [] and out.get("needLogin") is True
    assert "로그인 필요" in out["error"] and "세션 만료" not in out["error"]


def test_confluence_result_has_path():
    """Confluence 검색 결과에 문서 경로(스페이스 + 상위폴더)가 담긴다.

    UI 가 breadcrumb(직계부모 ‹ … ‹ 스페이스)로 그린다. 경로는 [스페이스 … 직계부모] 순.
    """
    from app.domain.search import _search_confluence
    from app.infra.settings import get_settings
    from app.jira.jira_client import JiraClient
    from app.infra.cache import Cache

    s = get_settings()
    c = JiraClient(s, Cache(":memory:"))
    out = _search_confluence(c, s, "가이드", "all", 20)
    items = out["items"]
    assert items, "Confluence 결과가 없음"
    assert all("path" in it for it in items)
    # 경로 첫 요소는 스페이스 이름(루트), 폴더가 있으면 뒤에 이어진다
    deep = [it for it in items if len(it["path"]) >= 2]
    assert deep, "폴더 계층이 있는 문서가 없음"
    assert all(len(it["path"]) >= 1 for it in items)


def test_excerpt_highlight_is_safe_html():
    """검색 스니펫의 하이라이트를 <mark> 로 살리되 XSS 안전(평문 escape 후 마커만 태그화)."""
    from app.domain.search import _clean_excerpt
    assert _clean_excerpt("앞 @@@hl@@@쿼리@@@endhl@@@ 뒤") == "앞 <mark>쿼리</mark> 뒤"
    # 스크립트 주입은 escape
    out = _clean_excerpt("@@@hl@@@<script>x</script>@@@endhl@@@")
    assert "<script>" not in out and "&lt;script&gt;" in out and "<mark>" in out
    # 잘려서 짝이 안 맞으면 마커 제거(깨진 태그 방지)
    assert "@@@" not in _clean_excerpt("잘린 @@@hl@@@쿼리")
    # 마커 없는 평문도 escape
    assert _clean_excerpt("<b>x</b>") == "&lt;b&gt;x&lt;/b&gt;"


def test_service_user_extraction():
    """SSO 순회 로그인의 인증 판정 — 서비스별 응답에서 사용자 식별자를 뽑는다."""
    from app.auth.sso_session import _extract_user
    assert _extract_user({"name": "hong", "displayName": "홍길동"}) == "hong"   # Jira myself
    assert _extract_user({"username": "hong"}) == "hong"                        # Confluence current
    assert _extract_user({"values": [{"name": "hong", "slug": "hong"}]}) == "hong"  # Bitbucket users
    assert _extract_user({"name": "anonymous"}) is None                         # 익명은 미인증
    assert _extract_user({}) is None


def test_auth_targets_include_configured_services():
    """base 가 설정된 서비스만 SSO 로그인 순회 대상 — Jira 는 항상, 나머지는 base 있을 때."""
    from app.infra.settings import get_settings
    s = get_settings()
    names = [t[0] for t in s.auth_targets]
    assert "Jira" in names
    # confluence_base 가 있으면 Confluence 포함(dev 는 jira820 이 같은 호스트로 서빙)
    if s.confluence_base:
        assert "Confluence" in names
    if not s.bitbucket_base:
        assert "Bitbucket" not in names       # base 없으면 제외(현재 mock)


def test_default_avatar_url_detection():
    """Jira 기본(프로필 없음) 아바타 URL 은 None 처리돼 프론트가 시그니처로 폴백해야 한다.
    커스텀 아바타는 ownerId 를 담으므로 유지한다."""
    from app.jira.jira_client import _is_default_avatar_url
    assert _is_default_avatar_url("") is True
    assert _is_default_avatar_url("https://j/secure/useravatar?avatarId=10122") is True
    assert _is_default_avatar_url("https://j/secure/useravatar?ownerId=jdoe&avatarId=99") is False
    assert _is_default_avatar_url("https://gravatar.com/avatar/abc") is False
