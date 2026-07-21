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
