"""Complete hierarchy enumeration uses content metadata, never the search index."""
from types import SimpleNamespace

import pytest

from app.auth.base import UpstreamUnavailable
from app.infra.cache import Cache
from app.jira import confluence_service as service


@pytest.fixture
def client():
    cache = Cache(":memory:")
    yield SimpleNamespace(env="mock", s=SimpleNamespace(confluence_base="https://docs.test/wiki"), cache=cache)
    cache.close()


def row(cid):
    return {"id": str(cid), "title": f"Page {cid}", "space": {"key": "DL"}, "ancestors": []}


def test_tree_follows_upstream_offsets_through_empty_and_short_batches_and_reuses_cache(client):
    calls = []
    # CQL contains none of these pages. The content API has >100 visible pages,
    # a permission-filtered empty batch and a server-imposed page cap of 25.
    expected = [row(i) for i in range(205)]
    def get(url, params):
        assert url == "https://docs.test/wiki/rest/api/space/DL/content/page"
        assert params["depth"] == "all" and params["expand"] == "space,ancestors"
        assert params["limit"] == 100
        start = params["start"]
        calls.append(start)
        rows = [] if start == 25 else expected[(start if start < 25 else start - 25):start]
        if start == 0:
            rows = expected[:25]
        next_start = start + 25 if start < 225 else None
        return {"results": rows, "start": start, "size": len(rows), "limit": 25,
                "_links": {"next": f"/rest/api/space/DL/content/page?start={next_start}&limit=25"} if next_start else {}}
    client._conf_get_json = get
    for _ in range(2):
        pages, offset = [], 0
        while offset is not None:
            data = service.tree_pages(client, "DL", start=offset)
            pages.extend(data["results"])
            offset = data["nextStart"]
        assert pages == expected
    assert calls == list(range(0, 226, 25))
    service.invalidate(client, ["204"])
    service.tree_pages(client, "DL")
    assert len(calls) == 11


@pytest.mark.parametrize("broken", [
    {"results": [row(1)], "_links": {"next": "?start=0"}},
    {"results": [row(1)], "_links": {"next": "?cursor=opaque"}},
    {"results": [{"id": "1", "title": "Missing ancestors", "space": {"key": "DL"}}]},
    {"results": [{**row(1), "space": {"key": "OTHER"}}]},
    {"results": [], "complete": False},
])
def test_incomplete_tree_batches_are_not_cached_as_success(client, broken):
    responses = iter([broken, {"results": [row(2)]}])
    client._conf_get_json = lambda *a, **k: next(responses)
    with pytest.raises(UpstreamUnavailable):
        service.tree_pages(client, "DL")
    assert service.tree_pages(client, "DL")["results"] == [row(2)]
