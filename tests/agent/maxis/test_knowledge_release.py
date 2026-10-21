"""Synthetic release -> exact retrieval -> revocation; no remote services."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import socket

import pytest

from app.agent.maxis.knowledge_release import (
    KnowledgeAccessContext, KnowledgeReleaseError, admit_chunk, build_candidate,
    cache_identity, historical_snapshot, lookup_glossary, plan_blue_green, whole_heading_sections,
)

NOW = datetime(2026, 10, 17, 12, tzinfo=timezone.utc)
DATASET_A = "11111111-1111-4111-8111-111111111111"
DATASET_B = "22222222-2222-4222-8222-222222222222"


def _glossary():
    return {
        "schemaVersion": "1.0.0", "glossaryId": "example", "version": "1.0.0",
        "classification": "internal", "asOf": "2026-10-17",
        "sources": [{"id": "upstream", "repository": "https://example.invalid/no-fetch",
                     "revision": "a" * 40, "path": "../never-resolve.md", "section": "Terms",
                     "observedAt": "2026-10-17", "classification": "internal"}],
        "terms": [{"id": name, "name": name, "aliases": ["Engine"], "scope": scope,
                   "definition": f"Complete {name} definition.", "status": "documented",
                   "sourceRefs": ["upstream"], "notEquivalentTo": [], "caveats": ["Not a task status."]}
                  for name, scope in (("query-engine", "Query"), ("task-engine", "Tasks"))],
    }


def _write_release(root: Path, *, release="kb-1", dataset=DATASET_A, patch=None, bodies=None):
    bodies = bodies or {"policy": "# Rules\n\nApproval required.",
                        "design": "# System\n\nContext.\n\n## Module\n\nFull module definition.",
                        "glossary": json.dumps(_glossary())}
    sources = []
    for kind, text in bodies.items():
        path = f"{release}-{kind}.txt"
        raw = text.encode("utf-8")
        (root / path).write_bytes(raw)
        source = {
            "id": kind, "corpus": kind, "path": path, "revision": "rev-1",
            "sha256": hashlib.sha256(raw).hexdigest(), "owner": "example-owner",
            "classification": "internal", "audiences": ["pilot"],
            "allowed_roles": ["research_analyst"], "reviewed_for_version": release,
            "reviewed_at": (NOW - timedelta(days=1)).isoformat(),
            "reviewer_digest": "d" * 64, "valid_until": (NOW + timedelta(days=20)).isoformat(),
        }
        if patch:
            source.update(patch)
        sources.append(source)
    spec = {"contract": "ltm-knowledge-candidate.v1", "release_id": release,
            "dataset_id": dataset, "sources": sources}
    config = root / f"{release}.json"
    config.write_text(json.dumps(spec), encoding="utf-8")
    return config


def _candidate(root, **kwargs):
    return build_candidate(_write_release(root, **kwargs), root, now=NOW)


def _access(candidate, **changes):
    context = KnowledgeAccessContext(
        candidate.sha256, "f" * 64, "auth-1", "pilot", "research_analyst",
        frozenset(row.id for row in candidate.spec.sources), frozenset(),
        tuple((row.id, row.revision, row.sha256) for row in candidate.spec.sources),
        NOW - timedelta(seconds=1), NOW + timedelta(minutes=1),
    )
    return replace(context, **changes)


def test_actual_release_services_preserve_complete_content_and_provenance(tmp_path, monkeypatch):
    def forbidden(*_args, **_kwargs):
        raise AssertionError("no network")
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    config = _write_release(tmp_path)
    candidate = build_candidate(config, tmp_path, now=NOW)
    repeated = build_candidate(config, tmp_path, now=NOW)
    assert candidate.sha256 == repeated.sha256
    assert candidate.metadata()["corpora"] == {"policy": 1, "glossary": 1, "design": 1}
    assert len(candidate.chunks) == 5
    for chunk in candidate.chunks:
        assert hashlib.sha256(chunk.text.encode()).hexdigest() == chunk.sha256
        assert admit_chunk(candidate, chunk_id=chunk.id, text=chunk.text,
                           access=_access(candidate), now=NOW) == chunk
    result = lookup_glossary(candidate, "glossary", "ENGINE", access=_access(candidate), now=NOW)
    assert result["status"] == "ambiguous" and result["totalCandidates"] == 2
    scoped = lookup_glossary(candidate, "glossary", "Engine", scope="Query",
                             access=_access(candidate), now=NOW)
    assert scoped["status"] == "matched"
    assert scoped["candidates"][0]["sources"][0]["revision"] == "a" * 40
    assert "Complete query-engine definition." not in repr(candidate)
    assert "no-fetch" not in json.dumps(candidate.metadata())


@pytest.mark.parametrize(("patch", "code"), [
    ({"release_sha256": "0" * 64}, "release_not_current"),
    ({"scope_digest": ""}, "access_context_invalid"),
    ({"expires_at": NOW}, "access_context_expired"),
    ({"checked_at": NOW + timedelta(seconds=1)}, "access_context_expired"),
    ({"expires_at": NOW + timedelta(minutes=10)}, "access_context_expired"),
    ({"allowed_source_ids": frozenset()}, "source_not_authorized"),
    ({"revoked_source_ids": frozenset({"glossary"})}, "source_not_authorized"),
    ({"role_id": "people_advisor"}, "source_not_authorized"),
    ({"audience": "outsider"}, "source_not_authorized"),
    ({"source_revisions": ()}, "source_revision_not_current"),
])
def test_lookup_requires_current_release_grants_and_revision(tmp_path, patch, code):
    candidate = _candidate(tmp_path)
    with pytest.raises(KnowledgeReleaseError, match=f"^{code}$"):
        lookup_glossary(candidate, "glossary", "Engine", access=_access(candidate, **patch), now=NOW)


def test_revocation_or_revision_change_invalidates_cache_and_cached_chunk(tmp_path):
    candidate = _candidate(tmp_path)
    chunk = next(row for row in candidate.chunks if row.source_id == "policy")
    access = _access(candidate)
    revoked = replace(access, revoked_source_ids=frozenset({"policy"}), authorization_revision="auth-2")
    changed = replace(access, source_revisions=(("policy", "rev-2", "a" * 64),))
    assert len({cache_identity(candidate, row) for row in (access, revoked, changed)}) == 3
    for context, code in ((revoked, "source_not_authorized"), (changed, "source_revision_not_current")):
        with pytest.raises(KnowledgeReleaseError, match=code):
            admit_chunk(candidate, chunk_id=chunk.id, text=chunk.text, access=context, now=NOW)
    with pytest.raises(KnowledgeReleaseError, match="chunk_content_mismatch"):
        admit_chunk(candidate, chunk_id=chunk.id, text=chunk.text + "forged", access=access, now=NOW)


def test_whole_sections_preserve_heading_hierarchy_and_long_fenced_code(tmp_path):
    text = "# Parent\n\n## Definition\n\nComplete definition.\n\n````md\n```py\n# not a heading\n```\n````\n\n### Caveat\n\nKeep caveat."
    sections = whole_heading_sections(text)
    assert [row[0] for row in sections] == ["Parent", "Parent / Definition", "Parent / Definition / Caveat"]
    assert "# not a heading" in sections[1][1]
    config = _write_release(tmp_path, bodies={"policy": "# Definition\n" + "가" * 3000})
    with pytest.raises(KnowledgeReleaseError, match="whole_section_exceeds_budget"):
        build_candidate(config, tmp_path, now=NOW)


@pytest.mark.parametrize(("patch", "code"), [
    ({"path": "../private.md"}, "source_path_invalid"),
    ({"path": "C:/private.md"}, "source_path_invalid"),
    ({"sha256": "a" * 64}, "source_digest_mismatch"),
    ({"reviewed_for_version": "other"}, "release_spec_invalid"),
    ({"allowed_roles": ["invented"]}, "release_spec_invalid"),
    ({"reviewed_at": (NOW - timedelta(days=91)).isoformat()}, "source_review_not_current"),
    ({"valid_until": NOW.isoformat()}, "source_review_not_current"),
])
def test_invalid_source_fails_before_candidate_admission(tmp_path, patch, code):
    with pytest.raises(KnowledgeReleaseError, match=f"^{code}$"):
        _candidate(tmp_path, patch=patch, bodies={"policy": "# Rules\n\nApproval required."})


def test_no_duplicate_json_keys_or_public_reclassification(tmp_path):
    config = tmp_path / "invalid.json"
    config.write_text('{"contract": "private", "contract": "private"}', encoding="utf-8")
    with pytest.raises(KnowledgeReleaseError, match="^release_spec_invalid$"):
        build_candidate(config, tmp_path, now=NOW)
    with pytest.raises(KnowledgeReleaseError, match="source_classification_mismatch"):
        _candidate(tmp_path, patch={"classification": "public"})


@pytest.mark.parametrize("mutation", ["duplicate_id", "missing_reference", "public_internal", "self_equivalence"])
def test_glossary_release_rejects_broken_provenance(tmp_path, mutation):
    glossary = _glossary()
    if mutation == "duplicate_id":
        glossary["terms"][1]["id"] = glossary["terms"][0]["id"]
    elif mutation == "missing_reference":
        glossary["terms"][0]["sourceRefs"] = ["absent"]
    elif mutation == "public_internal":
        glossary["classification"] = "public"
    else:
        glossary["terms"][0]["notEquivalentTo"] = [glossary["terms"][0]["id"]]
    with pytest.raises(KnowledgeReleaseError, match="source_content_invalid"):
        _candidate(tmp_path, bodies={"glossary": json.dumps(glossary)})


def test_blue_green_review_is_non_destructive_and_content_free(tmp_path):
    old = _candidate(tmp_path)
    new = _candidate(tmp_path, release="kb-2", dataset=DATASET_B)
    before = {path.name: path.read_bytes() for path in tmp_path.iterdir()}
    plan = plan_blue_green(old, new, now=NOW)
    assert plan["dryRun"] is True and plan["activationAllowed"] is False
    assert plan["blockers"] == []
    assert "Complete" not in json.dumps(plan) and "kb-1-policy.txt" not in json.dumps(plan)
    assert before == {path.name: path.read_bytes() for path in tmp_path.iterdir()}
    assert plan_blue_green(old, old, now=NOW)["blockers"] == ["new_dataset_required", "new_release_id_required"]


def test_expired_baseline_can_be_replaced_but_cannot_be_used_for_runtime(tmp_path):
    config = _write_release(tmp_path, patch={
        "reviewed_at": (NOW - timedelta(days=110)).isoformat(),
        "valid_until": (NOW - timedelta(days=20)).isoformat(),
    })
    with pytest.raises(KnowledgeReleaseError, match="source_review_not_current"):
        build_candidate(config, tmp_path, now=NOW)
    baseline = historical_snapshot(config, tmp_path, now=NOW)
    fresh = _candidate(tmp_path, release="kb-2", dataset=DATASET_B)
    assert plan_blue_green(baseline, fresh, now=NOW)["blockers"] == []
    assert not hasattr(baseline, "chunks") and not hasattr(baseline, "glossaries")
    with pytest.raises(KnowledgeReleaseError, match="runtime_candidate_required"):
        admit_chunk(baseline, chunk_id="unused", text="", access=_access(fresh), now=NOW)
    with pytest.raises(KnowledgeReleaseError, match="runtime_candidate_required"):
        lookup_glossary(baseline, "glossary", "Engine", access=_access(fresh), now=NOW)
    with pytest.raises(KnowledgeReleaseError, match="runtime_candidate_required"):
        plan_blue_green(fresh, baseline, now=NOW)


@pytest.mark.parametrize("field", ["id", "revision", "owner", "audiences"])
def test_credential_shaped_source_metadata_is_rejected_without_echo(tmp_path, field):
    secret = "app-" + "a" * 24
    value = [secret] if field == "audiences" else secret
    with pytest.raises(KnowledgeReleaseError, match="^release_spec_invalid$") as caught:
        _candidate(tmp_path, patch={field: value}, bodies={"policy": "# Rules\n\nApproval required."})
    assert secret not in str(caught.value)


def test_credential_shaped_release_label_is_rejected_without_echo(tmp_path):
    secret = "app-" + "a" * 24
    with pytest.raises(KnowledgeReleaseError, match="^release_spec_invalid$"):
        _candidate(tmp_path, release=secret)


def test_dry_run_cli_executes_production_services_and_never_echoes_paths(tmp_path, capsys, monkeypatch):
    path = Path(__file__).resolve().parents[3] / "deploy/maxis-dify/plan_knowledge_release.py"
    spec = importlib.util.spec_from_file_location("knowledge_release_cli", path)
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    # Keep the real build implementation, with a deterministic clock for this fixture.
    actual = cli.build_candidate
    monkeypatch.setattr(cli, "build_candidate", lambda *args: actual(*args, now=NOW))
    actual_history, actual_plan = cli.historical_snapshot, cli.plan_blue_green
    monkeypatch.setattr(cli, "historical_snapshot", lambda *args: actual_history(*args, now=NOW))
    monkeypatch.setattr(cli, "plan_blue_green", lambda *args: actual_plan(*args, now=NOW))
    old = _write_release(tmp_path, patch={"reviewed_at": (NOW - timedelta(days=100)).isoformat()})
    new = _write_release(tmp_path, release="kb-2", dataset=DATASET_B)
    assert cli.main(["--current", str(old), "--candidate", str(new), "--source-root", str(tmp_path)]) == 0
    assert json.loads(capsys.readouterr().out)["activationAllowed"] is False
    assert cli.main(["--private-path-secret"]) == 2
    assert "private-path-secret" not in capsys.readouterr().out
