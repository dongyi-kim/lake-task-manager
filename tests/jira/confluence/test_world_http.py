"""Shared world documents use jira820 REST, with no browser response interception."""

from fastapi.testclient import TestClient

from app.auth.inprocess import InProcessProvider
from app.mock.confluence_world import DOCUMENT_IDS, GUIDE_TITLE, PAGINATION_CHILD_IDS
from app.mock.fakebridge import build_injected_app


def read(http, path, **params):
    response = http.get(path, params=params)
    assert response.status_code == 200, response.text
    return response.json()


def test_shared_documents_use_generic_routes_and_keep_identity_after_rebuild():
    first, second = build_injected_app(), build_injected_app()
    assert type(first.state.confluence).__module__ == "jira820.confluence"
    cid = DOCUMENT_IDS["guide"]
    with TestClient(first) as before, TestClient(second) as rebuilt:
        original = read(before, f"/rest/api/content/{cid}", expand="body.storage,space,version,ancestors")
        response = before.put(f"/rest/api/content/{cid}", json={
            "type": "page", "title": "Edited only in this running world",
            "version": {"number": 2}, "body": original["body"],
        })
        assert response.status_code == 200, response.text
        restored = read(rebuilt, f"/rest/api/content/{cid}", expand="body.storage,space,version,ancestors")
        assert restored["id"] == original["id"] == cid
        assert restored["title"] == original["title"] == GUIDE_TITLE
        assert restored["version"]["number"] == 1
        assert restored["body"]["storage"] == original["body"]["storage"]
        assert restored["ancestors"] == []
        assert read(before, f"/rest/api/content/{cid}", version=1, expand="body.storage")["title"] == GUIDE_TITLE


def test_http_tree_cql_pagination_and_nested_ancestors_use_stable_fixture_ids():
    with TestClient(build_injected_app()) as http:
        cid = DOCUMENT_IDS["pagination"]
        seen, start = [], 0
        while True:
            data = read(http, f"/rest/api/content/{cid}/child/page", start=start, limit=17)
            seen.extend(row["id"] for row in data["results"])
            if not data["_links"].get("next"):
                break
            start += data["size"]
        assert seen == list(PAGINATION_CHILD_IDS)
        assert len(seen) == len(set(seen)) == 55
        nested = read(http, f"/rest/api/content/{DOCUMENT_IDS['rollback']}", expand="ancestors")
        assert [row["id"] for row in nested["ancestors"]] == [DOCUMENT_IDS["guide"], DOCUMENT_IDS["releases"]]
        search = read(http, "/rest/api/content/search",
                      cql=f'space="DL" AND parent={cid} AND title~"운영 문서 55"', expand="ancestors")
        assert [row["id"] for row in search["results"]] == [PAGINATION_CHILD_IDS[-1]]
        roots = read(http, "/rest/api/space/PMO/content/page", depth="root")
        assert DOCUMENT_IDS["pmo"] in [row["id"] for row in roots["results"]]
        assert DOCUMENT_IDS["guide"] not in [row["id"] for row in roots["results"]]


def test_inprocess_and_http_document_sources_share_rendered_body_and_hierarchy():
    provider = InProcessProvider()
    try:
        with TestClient(build_injected_app()) as http:
            for cid in (DOCUMENT_IDS["guide"], DOCUMENT_IDS["rollback"], DOCUMENT_IDS["pmo_child"]):
                params = {"expand": "body.view,space,version,ancestors"}
                path = f"/rest/api/content/{cid}"
                mock = provider.get_json(path, params=params)
                local = read(http, path, **params)
                for key in ("id", "title", "body", "ancestors", "space", "version"):
                    assert mock[key] == local[key], (cid, key)
    finally:
        provider.close()
