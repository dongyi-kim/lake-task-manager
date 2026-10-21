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
                   {identity.casefold(): {"display"} for identity in identities})


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


@pytest.mark.parametrize("role", ["knowledge_curator", "portfolio_analyst", "result_integrator"])
def test_readonly_role_restores_only_this_calls_verified_person(monkeypatch, role):
    from app.agent.maxis import backend
    calls = install_directory(monkeypatch, [{"name": "alice.fixture", "displayName": "Alice"}])
    payload = {"summary": f"담당 {marker('alice.fixture')} 확인 필요"}
    evidence = {"sources": [{"content": marker("alice.fixture")}]}
    restored = backend._rehydrate_readonly_role_people(role, payload, evidence,
        ("alice.fixture",), {"alice.fixture": {"id"}})
    assert restored == {"summary": "담당 [~alice.fixture] 확인 필요"}
    assert len(calls) == 1 and marker("alice.fixture") in payload["summary"]
    with pytest.raises(backend.MaxisRuntimeError, match="사용자 참조"):
        backend._rehydrate_readonly_role_people(role, payload, {},
            ("alice.fixture",), {"alice.fixture": {"id"}})
    assert len(calls) == 1  # Invented/out-of-input markers never reach directory lookup.


@pytest.mark.parametrize("role", ["work_architect", "auditor", "request_architect", "query_specialist", "editor_author"])
def test_readonly_restore_does_not_expand_other_role_authority(monkeypatch, role):
    from app.agent.maxis import backend
    calls = install_directory(monkeypatch, [])
    payload = {"assignee": marker("alice.fixture")}
    assert backend._rehydrate_readonly_role_people(role, payload, payload,
        ("alice.fixture",), {"alice.fixture": {"id"}}) is payload
    assert not calls


@pytest.mark.parametrize("role", ["knowledge_curator", "portfolio_analyst", "result_integrator"])
def test_readonly_role_gathers_local_identity_types_without_serializing_map(monkeypatch, role):
    from app.agent.maxis import backend, person_references
    from app.agent.tools import _ctx
    state = {"thread_id": "fixture-thread", "correlation_id": "fixture-correlation"}
    payload = {"evidencePack": {"guarded": True}}
    seen = {}
    cfg = object()
    monkeypatch.setattr(_ctx, "settings", lambda: cfg)
    monkeypatch.setattr(backend, "resolve_role", lambda *_: SimpleNamespace(route="maxis"))
    monkeypatch.setattr(backend, "role_payload", lambda *_a, **_k: payload)
    monkeypatch.setattr(backend, "_known_identity_values", lambda current, settings:
                        ("alice.fixture",) if current is state and settings is cfg else ())
    monkeypatch.setattr(person_references, "identity_types", lambda *_: {"alice.fixture": {"id"}})
    def invoke(_role, request, *_args, **kwargs):
        seen.update(kwargs)
        assert request is payload and 'alice.fixture' not in str(request)
        return {"output": {"summary": "typed result"}, "metadata": {}}
    monkeypatch.setattr(backend, "invoke", invoke)
    assert backend.invoke_role(role, state, [], {}, output_kind="structured") == {"summary": "typed result"}
    assert seen["person_reference_values"] == ("alice.fixture",)
    assert seen["person_reference_types"] == {"alice.fixture": {"id"}}


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
        resolve(payload, payload, ["alice.fixture"], marker, {"alice.fixture": {kind}})


def test_identity_type_inventory_preserves_every_local_constraint(monkeypatch):
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
        "roster.fixture": {"id"}, "caller.fixture": {"id"},
        "alice.fixture": {"id", "display"}, "bob.fixture": {"id"},
        "김샘": {"display"}, "정한울": {"display"},
        "a@example.test": {"email"}, "mentioned.fixture": {"id"},
    }


def test_same_plaintext_id_and_other_users_name_cannot_share_a_marker(monkeypatch):
    from app.infra import settings
    monkeypatch.setattr(settings, "load_people", lambda: {})
    users = [{"name": "alice.fixture", "displayName": "Alice Fixture"},
             {"name": "bob.fixture", "displayName": "alice.fixture"}]
    install_directory(monkeypatch, users)
    kinds = identity_types({"people": users}, SimpleNamespace())
    payload = {"text": marker("alice.fixture")}
    with pytest.raises(ValueError, match="directory_unknown_or_ambiguous"):
        resolve(payload, payload, ["alice.fixture"], marker, kinds)


def test_same_plaintext_id_and_display_are_safe_only_for_one_user(monkeypatch):
    install_directory(monkeypatch, [{"name": "alice.fixture", "displayName": "alice.fixture"}])
    payload = {"text": marker("alice.fixture")}
    assert resolve(payload, payload, ["alice.fixture"], marker,
                   {"alice.fixture": {"id", "display"}}) == {
                       marker("alice.fixture"): "[~alice.fixture]"}


def test_deep_and_late_identity_constraints_are_not_silently_omitted(monkeypatch):
    from app.infra import settings
    monkeypatch.setattr(settings, "load_people", lambda: {})
    state = {"query_results": [{"result": {"tickets": [
        {"who": "alice.fixture", "details": {"level2": {"level3": {
            "assigneeId": "alice.fixture"}}}},
        *[{} for _ in range(201)],
        {"emailAddress": "alice.fixture"},
    ]}}]}
    assert identity_types(state, SimpleNamespace())["alice.fixture"] == {
        "id", "display", "email"}


@pytest.mark.parametrize("limit", ["_MAX_IDENTITY_DEPTH", "_MAX_IDENTITY_VALUES"])
def test_inventory_limit_fails_closed_instead_of_using_partial_types(monkeypatch, limit):
    from app.agent.maxis import person_references
    from app.infra import settings
    monkeypatch.setattr(settings, "load_people", lambda: {})
    monkeypatch.setattr(person_references, limit, 1)
    with pytest.raises(ValueError, match="inventory_incomplete"):
        identity_types({"who": "alice.fixture", "nested": {"assigneeId": "alice.fixture"}},
                       SimpleNamespace())
