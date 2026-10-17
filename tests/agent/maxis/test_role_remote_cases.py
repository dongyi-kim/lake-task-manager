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
    assert remote.BATTERY_VERSION == "2.0.0"
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


def test_offline_projections_pin_explicit_work_intent_revision_and_other_roles():
    # Work's runtime projection intentionally adds citeable intent and lossless text
    # references. Its old digest was c0da8ce977201243e1365aa607c19720191c7a65621c1078d8e76e2419f38c2f;
    # this pins the new projection, not a rewrite of historical raw evaluation data.
    # All other fixtures remain identical to the pre-adapter-refactor baseline.
    # Freeze the pre-policy-retrieval runtime for this fixture-input regression;
    # actual candidate projections are exercised by the tests above and runtime tests.
    from app.agent.maxis.retrieval_query import enrich_role_query

    def baseline_query(query, role_id, schema):
        return enrich_role_query(query, role_id, schema) if role_id == "editor_author" else query
    expected = {
        "role-request": "6bdb057ce99fcee97e1f7368dbad5b899850261f536729a1e8c9a5097b7dd809",
        "role-query": "89aec58d6306dddb42ff197b8076a3be62f3367f8fa98ef518b2a77fbba8bfa3",
        "role-knowledge": "a183fa5abd1960b4531040ced6941209d9c2cefe7bec21efc40d22c2196fd9e3",
        "role-work": "f6159f40b07330ffb436b21e9963823f9b7337d7a171a69ff179b10c4868921e",
        "role-auditor": "8dcb5612eb5d8b03125cf7941b79ef302d9471714e5dcfda4bd1372add4ceb4f",
        "role-result": "c10afa5157a3f34402141c84af0e623a30b17be5a3fa5bb85f74cf92abba80e6",
        "role-portfolio": "f585f21c92ba86f8021ad5145e98875fbae9f1c92352b1fed8ef57f71554b2c2",
        "role-editor": "58cd9be5cfa20159e85e79367a559b9b5fce7b5d88cd4673ef5613cd4d9175f0",
        "role-overflow": "aadfdcafc9dbcd61916b594528ee751b7a7c5cbe6e846ea8bf71ec3ffeaa4940",
        "role-stale": "71adf31275c9adb478c0535b976c961e663708762e560787f966ee77e1869b5f",
        "role-missing": "26ecbd6472a5d7ec5d55b4abee7ae04b474e6a79f6ce96900da73b4d55940d8f",
    }
    for case_id, digest in expected.items():
        with patch("app.agent.maxis.retrieval_query.enrich_role_query", baseline_query):
            encoded = json.dumps(offline.build_fixture_payload(case_id), ensure_ascii=False,
                                 sort_keys=True, separators=(",", ":"))
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
