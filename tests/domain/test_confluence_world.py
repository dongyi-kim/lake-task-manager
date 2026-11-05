"""Confluence UI fixtures are stable data shared by mock and local transports."""

from datetime import date, timedelta
from html.parser import HTMLParser

from app.mock.confluence_world import (
    AUTHOR, DOCUMENT_IDS, GUIDE_TITLE, PAGINATION_CHILD_IDS, build_confluence_documents,
)


def test_rebuilding_world_keeps_ids_and_hierarchy_without_shared_mutable_rows():
    today = date(2026, 11, 5)
    original = build_confluence_documents(today)
    rebuilt = build_confluence_documents(today)
    tomorrow = build_confluence_documents(today + timedelta(days=1))
    assert original == rebuilt
    assert [(p["id"], p["parentId"], p["ancestors"]) for p in original] == [
        (p["id"], p["parentId"], p["ancestors"]) for p in tomorrow]
    assert all(p["id"].isdigit() for p in original)
    assert len({p["id"] for p in original}) == len(original)
    rebuilt[1]["ancestors"][0]["title"] = "changed"
    assert original[1]["ancestors"][0]["title"] == GUIDE_TITLE
    assert build_confluence_documents(today)[1]["ancestors"][0]["title"] == GUIDE_TITLE


def test_nested_and_paginated_documents_reference_existing_same_space_parents():
    rows = build_confluence_documents(date(2026, 11, 5))
    by_id = {row["id"]: row for row in rows}
    for row in rows:
        ancestors = row["ancestors"]
        assert [a["id"] for a in ancestors] == list(dict.fromkeys(a["id"] for a in ancestors))
        assert row["id"] not in [a["id"] for a in ancestors]
        if row["parentId"]:
            assert ancestors[-1]["id"] == row["parentId"]
            assert all(by_id[a["id"]]["space"] == row["space"] for a in ancestors)
        else:
            assert ancestors == []
    assert [p["id"] for p in rows if p["parentId"] == DOCUMENT_IDS["pagination"]] == list(PAGINATION_CHILD_IDS)
    assert len(PAGINATION_CHILD_IDS) > 50
    assert [a["id"] for a in by_id[DOCUMENT_IDS["rollback"]]["ancestors"]] == [
        DOCUMENT_IDS["guide"], DOCUMENT_IDS["releases"]]
    assert {row["space"] for row in rows if not row["parentId"]} == {"DL", "PMO"}


def test_rich_document_exercises_headings_tables_code_links_and_long_content():
    class Elements(HTMLParser):
        def __init__(self):
            super().__init__()
            self.tags, self.links = [], []
        def handle_starttag(self, tag, attrs):
            self.tags.append(tag)
            if tag == "a":
                self.links.append(dict(attrs).get("href"))
    guide = next(row for row in build_confluence_documents(date(2026, 11, 5))
                 if row["id"] == DOCUMENT_IDS["guide"])
    parser = Elements()
    parser.feed(guide["body"])
    assert {"h2", "h3", "h4", "h5", "h6", "table", "th", "pre", "code", "blockquote", "ul", "ol"} <= set(parser.tags)
    assert guide["body"].count("<h3>확인 항목</h3>") == 2
    assert len(guide["body"]) > 2000
    assert any(DOCUMENT_IDS["rollback"] in href for href in parser.links)


def test_shared_world_includes_fixture_records_under_fixture_author():
    from app.mock.world import get_world
    world = get_world()
    rows = [row for row in world.confluence[AUTHOR] if row.get("id") in DOCUMENT_IDS.values()]
    assert {row["id"] for row in rows} == set(DOCUMENT_IDS.values())
    assert all(row["author"] == AUTHOR for row in rows)
