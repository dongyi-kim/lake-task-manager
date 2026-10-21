"""Closed remote fixtures rebind time/manifest before the actual outbound guard."""

import hashlib
import json
from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from app.agent.maxis import backend
from app.agent.maxis.context_guard import validate_pack_safety
from app.agent.maxis.contracts import EvidencePackV1
from tools import maxis_role_eval_cases as offline
from tools import maxis_role_remote_cases as remote
from tools.agent_eval_review_specs import review_specs


NOW = datetime(2026, 10, 3, 12, 3, 4, tzinfo=timezone.utc)
MANIFEST = "123456789abcdef0" * 4


def _build(case_id="role-result", **kwargs):
    return remote.build_remote_fixture(case_id, observed_at=kwargs.get("observed_at", NOW),
                                       manifest_sha256=kwargs.get("manifest_sha256", MANIFEST))


@pytest.mark.parametrize("binding", [None, "", "a" * 63, "A" * 64, "g" * 64, "0" * 65, 0, True])
def test_manifest_must_be_canonical_digest_before_builder(binding):
    with patch.object(offline, "_build_controlled_fixture", side_effect=AssertionError("builder ran")):
        with pytest.raises(ValueError, match="SHA-256"):
            _build(manifest_sha256=binding)


@pytest.mark.parametrize("observed", [None, "2026-10-03", NOW.replace(tzinfo=None),
                                     NOW.astimezone(timezone(timedelta(hours=9)))])
def test_time_requires_explicit_aware_utc_before_builder(observed):
    with patch.object(offline, "_build_controlled_fixture", side_effect=AssertionError("builder ran")):
        with pytest.raises(ValueError, match="aware UTC"):
            _build(observed_at=observed)


def test_remote_closed_cases_reject_content_and_detach_metadata():
    original = remote.load_remote_cases()
    assert len(original) == 11
    assert remote.BATTERY_VERSION == "3.0.0"
    changed = remote.load_remote_cases()
    changed[0]["role_id"] = "action_executor"
    changed[0]["specialized_review"][0]["expected"]["private"] = "caller input"
    assert remote.load_remote_cases() == original
    for supplied in (changed[0], "private text", "config/jira.yml", "", None):
        with pytest.raises(ValueError, match="repository-owned"):
            _build(supplied)
    with pytest.raises(TypeError):
        remote.build_remote_fixture("role-result", observed_at=NOW, manifest_sha256=MANIFEST,
                                    state={"request_text": "injected"})


def test_every_case_uses_current_attestations_and_real_bounded_schema():
    original_clock, original_salt = backend.datetime, backend._PSEUDONYM_KEY
    with patch.object(backend, "role_payload", wraps=backend.role_payload) as actual:
        for case in remote.load_remote_cases():
            built = _build(case["id"])
            payload = built["payload"]
            pack = EvidencePackV1.model_validate_json(json.dumps(payload["evidencePack"]))
            validate_pack_safety(pack)
            assert payload["roleId"] == case["role_id"]
            assert pack.knowledge_manifest_sha256 == MANIFEST
            assert pack.requested_at == NOW
            assert payload["runtime"]["profile"] == "synthetic-component-fixture"
            assert pack.payload_schema_json == offline.build_fixture_payload(case["id"])[
                "payload"]["evidencePack"]["payload_schema_json"]
            expected_time = NOW - timedelta(hours=1) if case["id"] == "role-stale" else NOW
            for source in pack.sources:
                if source.kind == "jira":
                    assert source.retrieved_at == expected_time
            for context in pack.role_context:
                assert context.observed_at == expected_time
            for source in built["state"].get("query_results", []):
                proof = source["_remoteEvidence"]
                assert datetime.fromisoformat(proof["retrievedAt"]) == expected_time
                assert proof["contract"] == "ltm-evidence-attestation.v1"
            assert pack.freshness == case["expected"]["freshness"]
            assert pack.partial == case["expected"]["partial"]
            assert set(case["expected"]["flags"]) <= set(pack.flags)
            wire = json.dumps(payload, ensure_ascii=False)
            assert "user.fixture" not in wire and "fixture@example.invalid" not in wire
            assert "specialized_review" not in wire and "majorWhen" not in wire
            assert "case_metadata_sha256" not in wire and "benchmark_refs" not in wire
            assert "content_paths_any" not in wire and "safety_boundary" not in wire
    assert actual.call_count == 22  # 11 remote builds + 11 original schema comparisons.
    assert backend.datetime is original_clock and backend._PSEUDONYM_KEY == original_salt


def test_builder_does_not_read_real_identity_settings_credentials_or_network():
    from app.infra import settings
    from app.agent.tools import _ctx
    from app.agent.maxis import credentials, profiles

    fail = AssertionError("live dependency used by fixture builder")
    with ExitStack() as stack:
        for obj, names in ((settings, ("get_settings", "load_people")),
                           (_ctx, ("client",)), (profiles, ("active", "resolve")),
                           (credentials, ("load_for", "get_for", "resolved_for", "environment_overrides")),
                           (backend, ("invoke_role",))):
            for name in names:
                stack.enter_context(patch.object(obj, name, side_effect=fail))
        stack.enter_context(patch("socket.socket.connect", side_effect=fail))
        for case in remote.load_remote_cases():
            _build(case["id"])


def test_specialized_reviews_registered_canonically_and_detached():
    common, cases = review_specs("maxis-role-component")
    assert set(cases) == {case["id"] for case in remote.load_remote_cases()}
    ids = set()
    for row in common + [element for spec in cases.values() for element in spec["elements"]]:
        assert set(row) == {"id", "dimension", "question", "majorWhen", "evidenceSources", "expected"}
        assert row["dimension"] in {"request_fulfillment", "factual_grounding", "contract_actionability",
                                    "safety_uncertainty", "readability_rendering"}
        assert row["id"] not in ids
        ids.add(row["id"])
        assert all(row.values())
    cases["role-request"]["elements"][0]["expected"]["private"] = True
    assert "private" not in review_specs("maxis-role-component")[1]["role-request"]["elements"][0]["expected"]
    outcomes = next(row for row in common if row["id"] == "role_component_outcome_contract")
    outcomes["expected"]["caseOutcomes"]["role-request"]["kind"] = "injected"
    fresh, _ = review_specs("maxis-role-component")
    assert next(row for row in fresh if row["id"] == outcomes["id"])[
        "expected"]["caseOutcomes"]["role-request"]["kind"] == "payload"


def test_offline_projections_pin_work_and_query_intent_revisions():
    # Work's runtime projection intentionally adds citeable intent and lossless text
    # references. Its old digest was c0da8ce977201243e1365aa607c19720191c7a65621c1078d8e76e2419f38c2f;
    # this pins the new projection, not a rewrite of historical raw evaluation data.
    # Query adds only its backend-owned task row. Editor's policy suffix now uses
    # the public corpus term "본문" instead of "Description"; that query suffix
    # is the only change from its old 58cd9be5... input identity.
    # Battery 3.0.0 records Auditor's fresh intent alongside stale Jira evidence.
    # The full fixture digest includes that changed case metadata; old raw results
    # remain untouched. This is an input identity assertion, not a quality score.
    # Freeze the pre-policy-retrieval runtime for this fixture-input regression;
    # actual candidate projections are exercised by the tests above and runtime tests.
    from app.agent.maxis.retrieval_query import enrich_role_query

    def baseline_query(query, role_id, schema):
        return enrich_role_query(query, role_id, schema) if role_id == "editor_author" else query
    expected = {
        "role-request": "16fc3fb96f983c42b07e506db0c938a07fcc1532881f9aad802d8fa305c1aa3d",
        "role-query": "146c98179d35fae44a88e74f72a7c8c2a51bb1e8765f873679ae076a35dd27cc",
        "role-knowledge": "45dead47937f4a4aaf2f40c7790f67136b8da02aa4ea025b54e1b65ba75f6f7d",
        "role-work": "6f48b987f363d5b4ecf61580d7b3b04230f75a1814cb0c9297267c944d1277a6",
        "role-auditor": "b5ee2d46e5b389e98c1899e9a21b2d7b5cebcc8b27bcf9d0c995bb759fd67444",
        "role-result": "5acb2dc413402495bd91a364ab97a45600ebf8872de975f3fa7c779f83d2df6d",
        "role-portfolio": "98d9bfbcf22760b1bbc39d8424678b7ab1a7e4093b5d9f787b6e4df1ccf93c61",
        "role-editor": "54dad6e763bd7296dee698a93da049917e68a63e118ea92a1e661ac2d6fa478a",
        # The same oversized fixture is projected under the current 32 KiB
        # budget. It must remain partial, not become a normal full-evidence case.
        "role-overflow": "a34d1590557ee40774997d5dd18ae9bacf8cb866d7085c74c0223ed3b047d0b1",
        "role-stale": "866c2480194272d96af922ee46c37df2462769f012dedbf40abcb692447e9599",
        "role-missing": "35782f82f63f7d7d5ae620c698abe533cd4afcf1a46e0efa268cbfbb4c1c92a7",
    }
    for case_id, digest in expected.items():
        with patch("app.agent.maxis.retrieval_query.enrich_role_query", baseline_query):
            fixture = offline.build_fixture_payload(case_id)
            encoded = json.dumps(fixture, ensure_ascii=False,
                                 sort_keys=True, separators=(",", ":"))
        if case_id == "role-overflow":
            pack = fixture["payload"]["evidencePack"]
            assert pack["partial"] is True
            assert {"total_truncated", "source_truncated"} <= set(pack["flags"])
        assert hashlib.sha256(encoded.encode()).hexdigest() == digest


@pytest.mark.parametrize("case_id", [case["id"] for case in remote.load_remote_cases()])
def test_candidate_only_changes_policy_query_not_sources_context_or_fixture(case_id):
    from app.agent.maxis.retrieval_query import enrich_role_query

    def baseline_query(query, role_id, schema):
        return enrich_role_query(query, role_id, schema) if role_id == "editor_author" else query

    with patch("app.agent.maxis.retrieval_query.enrich_role_query", baseline_query):
        baseline = _build(case_id)
    candidate = _build(case_id)
    before = baseline["payload"]["evidencePack"]["query"]
    pack = candidate["payload"]["evidencePack"]
    if candidate["case"]["role_id"] in {
        "query_specialist", "work_architect", "auditor", "portfolio_analyst",
    }:
        assert pack["query"].startswith(before + "\n\nPolicy search terms: ")
    else:
        assert pack["query"] == before
    pack["query"] = before
    assert candidate == baseline
