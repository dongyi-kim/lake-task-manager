"""Offline Role fixtures exercise production projection without private inputs."""

import json
from unittest.mock import patch

import pytest

from app.agent.maxis import backend
from app.agent.maxis.context_guard import MAX_CONTEXT_PACK_UTF8_BYTES
from app.agent.maxis.contracts import EvidencePackV1, canonical_payload_schema
from app.agent.workflow.role_manifest import ROLE_SPECS, remote_execution_allowed
from tools import maxis_role_eval_cases as fixtures


def test_detached_cases_cover_every_remote_role_and_reject_supplied_content():
    cases = fixtures.load_cases()
    assert {row["role_id"] for row in cases} == {
        role for role in ROLE_SPECS if remote_execution_allowed(role)
    }
    assert len({row["id"] for row in cases}) == len(cases)
    assert fixtures.BATTERY_VERSION
    for row in cases:
        assert row["benchmark_refs"] and row["expected"]
    original = fixtures.load_cases()
    cases[0]["expected"]["private"] = "private input"
    cases[0]["role_id"] = "action_executor"
    assert fixtures.load_cases() == original
    for supplied in (cases[0], "private internal text", "config/jira.yml", "", None):
        with pytest.raises(ValueError, match="repository-owned"):
            fixtures.build_fixture_payload(supplied)
    with pytest.raises(TypeError):
        fixtures.build_fixture_payload(original[0]["id"], state={"private": "text"})


def test_all_payloads_are_reproducible_bounded_and_restore_patches():
    original_clock, original_salt = backend.datetime, backend._PSEUDONYM_KEY
    for case in fixtures.load_cases():
        first = fixtures.build_fixture_payload(case["id"])
        assert first == fixtures.build_fixture_payload(case["id"])
        payload = first["payload"]
        pack = EvidencePackV1.model_validate_json(json.dumps(payload["evidencePack"]))
        wire = json.dumps(pack.model_dump(mode="json"), ensure_ascii=False,
                          sort_keys=True, separators=(",", ":"))
        assert len(wire.encode("utf-8")) <= MAX_CONTEXT_PACK_UTF8_BYTES
        assert payload["roleId"] == case["role_id"]
        assert payload["runtime"]["profile"] == "offline-fixture"
        assert "user.fixture" not in wire
        assert "fixture@example.invalid" not in wire
        assert pack.payload_schema()["properties"]
        assert pack.freshness == case["expected"]["freshness"]
        assert pack.partial == case["expected"]["partial"]
        assert set(case["expected"]["flags"]) <= set(pack.flags)
        if case["id"] not in {"role-overflow", "role-missing"}:
            assert set(case["expected"]["required_context_keys"]) <= {
                row.name for row in pack.role_context
            }
    assert backend.datetime is original_clock
    assert backend._PSEUDONYM_KEY == original_salt


def test_fixture_path_does_not_read_live_settings_roster_credentials_or_network():
    from app.infra import settings
    from app.agent.tools import _ctx
    from app.agent.maxis import credentials, profiles

    fail = AssertionError("live dependency used by offline fixture")
    with patch.object(settings, "get_settings", side_effect=fail), \
            patch.object(settings, "load_people", side_effect=fail), \
            patch.object(_ctx, "client", side_effect=fail), \
            patch.object(profiles, "active", side_effect=fail), \
            patch("socket.socket.connect", side_effect=fail), \
            patch.object(backend, "invoke_role", side_effect=fail):
        # Credential entry points are forbidden regardless of current storage backend.
        from contextlib import ExitStack
        with ExitStack() as stack:
            for name in ("load_for", "get_for", "resolved_for", "environment_overrides"):
                stack.enter_context(patch.object(credentials, name, side_effect=fail))
            for case in fixtures.load_cases():
                fixtures.build_fixture_payload(case["id"])


def test_guard_receives_actual_sources_and_attestations():
    built = fixtures.build_fixture_payload("role-knowledge")
    row = built["state"]["query_results"][0]
    assert row["_remoteEvidence"]["contract"] == "ltm-evidence-attestation.v1"
    assert row["_remoteEvidence"]["provenance"]["tracked"] is True
    assert built["payload"]["evidencePack"]["sources"][0]["kind"] == "jira"
    overflow = fixtures.build_fixture_payload("role-overflow")["payload"]["evidencePack"]
    assert overflow["partial"]
    assert "source_truncated" in overflow["flags"]
    assert any(row["truncated"] for row in overflow["sources"])
    assert "interpretation" not in {row["name"] for row in overflow["role_context"]}
    stale = fixtures.build_fixture_payload("role-stale")["payload"]["evidencePack"]
    assert stale["freshness"] == "stale"
    assert "partial_source" in stale["flags"]
    missing = fixtures.build_fixture_payload("role-missing")["payload"]["evidencePack"]
    assert missing["sources"] == [] and missing["freshness"] == "empty"


def test_dev_gate_still_replaces_arbitrary_request_and_context():
    from app.agent.tools import _ctx
    from types import SimpleNamespace

    with patch.object(_ctx, "settings", return_value=SimpleNamespace()), \
            patch.object(backend, "_known_identity_values", side_effect=AssertionError):
        payload = backend.role_payload(
            "result_integrator", {
                "runtime_profile": "dev", "knowledge_manifest_sha256": "0" * 64,
                "request_text": "private pasted request", "situation": "private findings",
            }, [], {}, output_kind="text",
        )
    pack = payload["evidencePack"]
    assert pack["role_context"] == []
    assert pack["sources"][0]["source_id"] == "synthetic_dev_fixture"
    assert pack["flags"] == ["synthetic_only"]
    assert "private" not in json.dumps(pack)


def test_payload_uses_real_wire_contracts():
    from app.agent.workflow.contracts import role_output_schema
    from app.agent.maxis.contracts import EditorDraftV1

    for case_id, role in (("role-query", "query_specialist"),
                          ("role-knowledge", "knowledge_curator")):
        actual = fixtures.build_fixture_payload(case_id)["payload"]["evidencePack"]
        assert actual["payload_schema_json"] == canonical_payload_schema(
            role_output_schema(role))[0]
    editor = EditorDraftV1.model_json_schema()
    editor["properties"]["mode"] = {"type": "string", "const": "description"}
    assert fixtures.build_fixture_payload("role-editor")["payload"]["evidencePack"][
        "payload_schema_json"] == canonical_payload_schema(editor)[0]
