"""통합 검색 — mock(jira820 additive: DL + JIRA820) 로 3소스 fan-out 검증."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app import search                       # noqa: E402
from app.cache import Cache                  # noqa: E402
from app.jira_client import JiraClient       # noqa: E402
from app.settings import get_settings        # noqa: E402


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
    r = search.search_all(_client(), get_settings(), "런북", scope="all", limit=6)
    items = r["confluence"]["items"]
    assert items, "confluence 검색 결과가 있어야(런북)"
    for it in items:
        assert it["type"] == "confluence" and it["title"]
        assert it["url"].startswith("/spaces/") or "/spaces/" in it["url"]


def test_bitbucket_is_mock():
    r = search.search_all(_client(), get_settings(), "ETL", scope="scoped", limit=5)
    bb = r["bitbucket"]
    assert bb.get("mock") is True
    assert bb["items"] and all(i.get("mock") and i["repo"] for i in bb["items"])


def test_empty_query_returns_empty():
    r = search.search_all(_client(), get_settings(), "   ", limit=5)
    assert r["jira"]["items"] == [] and r["confluence"]["items"] == [] and r["bitbucket"]["items"] == []


def test_endpoint_ok():
    from fastapi.testclient import TestClient

    from app.main import app
    j = TestClient(app).get("/api/search", params={"q": "런북", "scope": "all"}).json()
    assert "jira" in j and "confluence" in j and "bitbucket" in j


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
    from app.search import _search_confluence
    from app.settings import get_settings

    class _P:
        def get_json(self, *a, **k):
            raise SessionExpired("HTTP 401 on /rest/api/search — 세션 만료 가능. login 재실행.")

    class _C:
        provider = _P()

    s = get_settings()
    out = _search_confluence(_C(), s, "테스트", "scoped", 5)
    assert out["items"] == [] and out.get("needLogin") is True
    assert "로그인 필요" in out["error"] and "세션 만료" not in out["error"]
