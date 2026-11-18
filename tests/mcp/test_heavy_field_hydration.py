"""Heavy Jira fields are hydrated and cached independently, without silent truncation."""
from copy import deepcopy

import pytest

from app.jira.body_search import search_body_page
from test_two_phase_body_collection import BodySource


def source_with_comments(env, total=3):
    source = BodySource(env, total=total)
    for row in source.rows.values():
        row["fields"]["comment"] = {"startAt": 0, "maxResults": 100, "total": 25,
            "comments": [{"id": str(i), "body": row["key"] + ":comment:" + str(i)} for i in range(25)]}
    return source


def page(env, fields="summary,updated,description,comment", **kwargs):
    return search_body_page(env.client, "project = DL", fields=fields, max_results=10, **kwargs)


def test_both_cold_fields_share_one_hydration_and_warm_selection_reuses_discovery(build_env):
    env = build_env()
    source = source_with_comments(env)
    first = page(env)
    assert len(source.calls) == 2
    assert set(source.calls[0]["params"]["fields"].split(",")) == {"summary", "updated"}
    assert set(source.calls[1]["params"]["fields"].split(",")) >= {"description", "comment"}
    for row in first["issues"]:
        assert row["fields"]["comment"] == source.rows[row["key"]]["fields"]["comment"]
        assert len(row["fields"]["comment"]["comments"]) == 25
    before = len(source.calls)
    assert page(env, "summary,updated,comment")["issues"][0]["fields"]["comment"]
    assert page(env, "summary,updated,description")["issues"][0]["fields"]["description"]
    assert len(source.calls) == before


def test_description_hit_fetches_only_missing_comments(build_env):
    env = build_env()
    source = source_with_comments(env)
    page(env, "summary,updated,description")
    before = len(source.calls)
    result = page(env)
    assert len(source.calls) == before + 1
    fields = set(source.calls[-1]["params"]["fields"].split(","))
    assert "comment" in fields and "description" not in fields
    assert all(row["fields"]["description"] and row["fields"]["comment"] for row in result["issues"])


def test_mixed_ticket_coverage_fetches_exact_missing_field_groups(build_env):
    from app.jira.body_search import ticket_field_context
    import time
    env = build_env()
    source = source_with_comments(env)
    context = ticket_field_context(env.client)
    context.caches["description"].store([source.rows["DL-0001"]], time.time())
    context.caches["comment"].store([source.rows["DL-0002"]], time.time())
    result = page(env)
    assert len(result["issues"]) == 3 and len(source.calls) == 4
    groups = {tuple(call["matchedKeys"]): set(call["params"]["fields"].split(","))
              for call in source.calls[1:]}
    assert groups[("DL-0001",)] & {"description", "comment"} == {"comment"}
    assert groups[("DL-0002",)] & {"description", "comment"} == {"description"}
    assert groups[("DL-0003",)] & {"description", "comment"} == {"description", "comment"}


def test_metadata_only_read_never_loads_any_heavy_payload(build_env, monkeypatch):
    env = build_env()
    source = source_with_comments(env)
    page(env)
    before = len(source.calls)
    original = env.cache.get_with_metadata
    def load(key, **kwargs):
        assert not key.startswith(("ticketbody:", "ticketcomment:"))
        return original(key, **kwargs)
    monkeypatch.setattr(env.cache, "get_with_metadata", load)
    result = page(env, "summary,updated")
    assert len(source.calls) == before
    assert all(set(row["fields"]) == {"summary", "updated"} for row in result["issues"])


@pytest.mark.parametrize("invalid", [None, "", {}, {"comments": None}, {"comments": "bad"}])
def test_invalid_comments_are_not_trusted_empty_or_warm_cached(build_env, invalid):
    env = build_env()
    source = source_with_comments(env, total=1)
    source.rows["DL-0001"]["fields"]["comment"] = deepcopy(invalid)
    first = page(env, "summary,updated,comment")
    assert first["issues"][0]["fields"].get("comment") == invalid
    before = len(source.calls)
    source.rows["DL-0001"]["fields"]["comment"] = []
    second = page(env, "summary,updated,comment")
    assert len(source.calls) == before + 1
    assert second["issues"][0]["fields"]["comment"] == []


def test_aggregate_description_and_comments_obey_one_output_budget(build_env, monkeypatch):
    import app.jira.body_search as module
    env = build_env()
    source = source_with_comments(env, total=2)
    for row in source.rows.values():
        row["fields"]["description"] = "d" * 2200
        row["fields"]["comment"] = [{"id": "1", "body": "c" * 2200}]
    monkeypatch.setattr(module, "MAX_BODY_RESULT_BYTES", 8000)
    with pytest.raises(ValueError, match="16 MiB"):
        page(env)


def test_warm_combined_budget_rejects_before_loading_second_field_payload(build_env, monkeypatch):
    import app.jira.body_search as module
    env = build_env()
    source = source_with_comments(env, total=2)
    for row in source.rows.values():
        row["fields"]["description"] = "d" * 2200
        row["fields"]["comment"] = [{"id": "1", "body": "c" * 2200}]
    page(env)
    before, loaded = len(source.calls), []
    original = env.cache.get_with_metadata
    def load(key, **kwargs):
        loaded.append(key)
        return original(key, **kwargs)
    monkeypatch.setattr(env.cache, "get_with_metadata", load)
    monkeypatch.setattr(module, "MAX_BODY_RESULT_BYTES", 8000)
    with pytest.raises(ValueError, match="16 MiB"):
        page(env)
    assert len(source.calls) == before
    assert any(key.startswith("ticketcomment:") for key in loaded)
    assert not any(key.startswith("ticketbody:") for key in loaded)
