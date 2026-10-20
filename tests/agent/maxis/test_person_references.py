"""No unverified display name may become a mention on the return path."""
from types import SimpleNamespace

import pytest

from app.agent.maxis.backend import (
    _person_reference_marker as marker,
    _rehydrate_editor_person_references as restore,
)
from app.agent.maxis.person_references import result_person_references as resolve, identity_types


def result_person_references(value, evidence, identities, pseudonymize):
    """Tests default to display inputs; explicit ID/email tests use resolve."""
    return resolve(value, evidence, identities, pseudonymize,
                   {identity.casefold(): "display" for identity in identities})


def install_directory(monkeypatch, users, canonical=None):
    from app.agent import references
    from app.agent.tools import _ctx
    calls = []

    def lookup(path, params):
        calls.append((path, params))
        return users

    def resolve(refs):
        uid = canonical or refs[0]["user_id"]
        return {"ok": True, "references": [{
            "userId": uid, "mention": f"[~{uid}]", "resolved": True,
        }]}

    monkeypatch.setattr(_ctx, "client", lambda: SimpleNamespace(
        provider=SimpleNamespace(get_json=lookup)))
    monkeypatch.setattr(references, "resolve_references", resolve)
    return calls


@pytest.mark.parametrize("original", ["alice.fixture", "김샘"])
def test_result_restores_id_and_display_name_to_same_verified_mention(monkeypatch, original):
    calls = install_directory(monkeypatch, [
        {"name": "alice.fixture", "displayName": "김샘 SKCC"}])
    payload = {"text": f"담당 {marker(original)} 확인 필요"}
    evidence = {"sources": [{"content": marker(original)}]}
    mapping = result_person_references(payload, evidence, [original], marker)
    assert restore(payload, mapping) == {"text": "담당 [~alice.fixture] 확인 필요"}
    assert calls == [("/rest/api/2/user/search",
                      {"username": original, "maxResults": 100})]
    assert payload["text"].endswith("확인 필요") and marker(original) in payload["text"]
    assert evidence == {"sources": [{"content": marker(original)}]}


@pytest.mark.parametrize("users", [
    [],
    [{"name": "alice.fixture", "displayName": "김샘"},
     {"name": "bob.fixture", "displayName": "김샘"}],
    [{"name": "alice.fixture", "displayName": "김샘 SKCC"},
     {"name": "bob.fixture", "displayName": "김샘 Partner"}],
    [{"name": "alice.fixture", "displayName": "김샘님"}],
    [{"name": "alice.fixture", "displayName": "김샘"}] * 100,
    {"users": []},
])
def test_missing_fuzzy_ambiguous_and_truncated_directory_never_pick_first(monkeypatch, users):
    install_directory(monkeypatch, users)
    payload = {"text": marker("김샘")}
    with pytest.raises(ValueError, match="person_directory"):
        result_person_references(payload, payload, ["김샘"], marker)


def test_canonical_resolver_must_confirm_same_identity(monkeypatch):
    install_directory(monkeypatch, [{"name": "alice.fixture"}], canonical="other.fixture")
    payload = {"text": marker("alice.fixture")}
    with pytest.raises(ValueError, match="canonical_identity"):
        result_person_references(payload, payload, ["alice.fixture"], marker)


@pytest.mark.parametrize("evidence,identities", [
    ({}, ["alice.fixture"]),
    ({"text": marker("alice.fixture")}, []),
])
def test_unknown_marker_has_no_directory_access(monkeypatch, evidence, identities):
    calls = install_directory(monkeypatch, [])
    with pytest.raises(ValueError, match="person_reference"):
        result_person_references(
            {"text": marker("alice.fixture")}, evidence, identities, marker)
    assert not calls


def test_no_markers_does_not_lookup_directory(monkeypatch):
    calls = install_directory(monkeypatch, [])
    assert result_person_references({"text": "조회 결과"}, {}, [], marker) == {}
    assert not calls


def test_casefold_aliases_are_one_identity_but_digest_collision_is_not(monkeypatch):
    calls = install_directory(monkeypatch, [{"name": "alice.fixture"}])
    payload = {"text": marker("ALICE.FIXTURE")}
    assert result_person_references(
        payload, payload, ["alice.fixture", "ALICE.FIXTURE"], marker,
    ) == {marker("alice.fixture"): "[~alice.fixture]"}
    assert len(calls) == 1
    with pytest.raises(ValueError, match="unknown_or_ambiguous"):
        result_person_references(
            payload, payload, ["alice.fixture", "bob.fixture"],
            lambda _: marker("alice.fixture"),
        )
    assert len(calls) == 1


@pytest.mark.parametrize("kind", ["id", "email"])
def test_typed_identity_cannot_resolve_through_someone_elses_display_name(monkeypatch, kind):
    install_directory(monkeypatch, [
        {"name": "bob.fixture", "displayName": "alice.fixture Partner",
         "emailAddress": "bob@example.test"}])
    payload = {"text": marker("alice.fixture")}
    with pytest.raises(ValueError, match="directory_unknown"):
        resolve(payload, payload, ["alice.fixture"], marker, {"alice.fixture": kind})


def test_identity_type_inventory_preserves_strongest_local_constraint(monkeypatch):
    from app.infra import settings
    monkeypatch.setattr(settings, "load_people", lambda: {"team": ["roster.fixture"]})
    kinds = identity_types({
        "user_id": "caller.fixture",
        "query_results": [{"result": {
            "assignee": "ALICE.FIXTURE", "assigneeId": "alice.fixture",
            "author": {"name": "bob.fixture", "displayName": "김샘"},
            "who": "정한울", "emailAddress": "a@example.test",
        }}],
        "ticket_progress": "담당 [~mentioned.fixture]",
    }, SimpleNamespace(jira_user="caller.fixture", managers=[]))
    assert kinds == {
        "roster.fixture": "id", "caller.fixture": "id", "alice.fixture": "id",
        "bob.fixture": "id", "김샘": "display", "정한울": "display",
        "a@example.test": "email", "mentioned.fixture": "id",
    }
