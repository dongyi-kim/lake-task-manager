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
