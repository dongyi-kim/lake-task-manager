"""Repository-owned Editor cases for a provider-free MAXIS component benchmark.

This adapter reuses the native Editor inputs, checks and human review declarations.
It is NOT the end-to-end Editor battery: retrieval, graph routing, deterministic
shortcuts and final UI rendering are outside this component's measurement boundary.
No caller-supplied prompt, source, configuration or live mock store is accepted.
The application DEV synthetic-only boundary is deliberately untouched.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date
from functools import lru_cache
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

from app.agent.maxis.context_guard import (
    ContextGuard, ContextPolicy, _EMAIL, _JIRA_MENTION, _USER_ID,
    assert_safe_outbound_text,
)
from app.agent.maxis.contracts import EditorDraftV1
from app.mock.world import World
from tools import agent_compose_eval as native
from tools.agent_eval_contracts import editor_renderer_contract_flaws
from tools.agent_eval_protocol import battery_manifest_sha256, load_protocol


ROOT = Path(__file__).resolve().parents[1]
CASE_IDS = ("CMP2", "CMP3", "CMP6", "CMP8", "CMP9")
COMPONENT_BATTERY_VERSION = "2.0.0"
FIXTURE_VERSION = "ltm-editor-component-synthetic.v2"
FIXTURE_DATE = date(2026, 9, 14)
# Public, reproducible salt for repository fixtures ONLY; not a production secret.
_SYNTHETIC_PSEUDONYM_KEY = hashlib.sha256(
    b"ltm-editor-component-repository-synthetic-person-v1"
).digest()
_TICKET_KEYS = ("DL-9090", "DL-9092", "DL-9093", "DL-9094", "DL-9095")
_DOCUMENT_TITLE = "[설계] 리니지 뷰어 1차"
_SOURCE_FILES = (
    "tools/maxis_editor_eval_cases.py",
    "tools/agent_compose_eval.py",
    "tools/agent_eval_contracts.py",
    "tools/agent_eval_review_specs.py",
    "tools/agent_eval_protocol.py",
    "app/agent/evaluation_protocol.json",
    "app/agent/maxis/contracts.py",
    "app/agent/maxis/context_guard.py",
    "app/mock/world.py",
)


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                      allow_nan=False)


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


# Retain the exact native input and checker at import time. The immutable JSON
# snapshot prevents a caller mutating native.CASES or a returned CaseSpec from
# silently changing the source input within an evaluation process.
_NATIVE_CASES = MappingProxyType({
    row[0]: (row[1], _json(row[2]), row[3])
    for row in native.CASES if row[0] in CASE_IDS
})
_NATIVE_REVIEW_JSON = _json({
    "suite_elements": native.SUITE_REVIEW_ELEMENTS,
    "cases": {case_id: native.CASE_REVIEW_SPECS[case_id] for case_id in CASE_IDS},
})
_SOURCE_BATTERY_VERSION = native.BATTERY_VERSION


def _source_hashes() -> dict[str, str]:
    return {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
            for name in _SOURCE_FILES}


_IMPORTED_SOURCE_HASHES = MappingProxyType(_source_hashes())


def _fixture_world() -> World:
    """Run only pure fixture builders, never World.__init__ or cached live world.

    The normal constructor reads the user's configured plan/people/settings and
    generates unrelated random rows. These two fixture builders use only the
    explicit date, project and in-memory collections initialized here.
    """
    world = World.__new__(World)
    world.today = FIXTURE_DATE
    world.project = "DL"
    world.issues = {}
    world.attachments = {}
    world.confluence = {}
    world._build_dataset_fixtures()
    world._build_dataset_docs()
    return world


def _source(source_id: str, *, kind: str, title: str, content: Any,
            url: str = "") -> dict[str, Any]:
    text = content if isinstance(content, str) else _json(content)
    if len(text) > 1500:
        raise ValueError("synthetic fixture source exceeds EvidenceSourceV1 limit")
    return {
        "source_id": source_id,
        "kind": kind,
        "canonical_id": f"ltm:synthetic:editor:v1:{source_id}",
        "title": title,
        "scope": "DL",
        "url": url,
        "content": text,
        "content_sha256": _sha(text),
    }


def _evidence(world: World) -> list[dict[str, Any]]:
    # Three complete, deliberately selected projections leave room for the native
    # request/seed, EditorDraft schema and provenance in the 6144-byte input budget.
    # They are not a claim that the production ticket history was fully retrieved.
    parent = world.issues["DL-9090"]
    current = {field: parent[field] for field in (
        "key", "type", "summary", "statusName", "assignee", "description",
    )}
    last_comment = parent["comments"][-1]
    current["latestComment"] = {"text": last_comment["text"],
                                "date": last_comment["created"].isoformat()}
    rows = [_source(
        "fixture.ticket.DL-9090", kind="jira", title=parent["summary"],
        url="https://jira.corp.example/browse/DL-9090", content=current,
    )]
    fields = ("key", "type", "summary", "statusName", "assignee", "parentKey", "description")
    family = [{field: world.issues[key].get(field) for field in fields}
              for key in _TICKET_KEYS if key != "DL-9090"]
    rows.append(_source("fixture.ticket.lineage-family", kind="jira",
                        title="리니지 자식·연결 티켓 현재 필드", content=family))
    documents = [row for pages in world.confluence.values() for row in pages
                 if row["title"] == _DOCUMENT_TITLE and row["space"] == "DL"]
    if len(documents) != 1:
        raise ValueError("synthetic fixture document identity is not unique")
    document = documents[0]
    rows.append(_source(
        "fixture.document.lineage", kind="confluence", title=_DOCUMENT_TITLE,
        url=world._conf_url(_DOCUMENT_TITLE), content=document["body"],
    ))
    return rows


@lru_cache(maxsize=1)
def _case_snapshots() -> tuple[Mapping[str, str], Mapping[str, str]]:
    """Freeze the whole suite, including its source-file identity, once per process."""
    if set(_NATIVE_CASES) != set(CASE_IDS):
        raise ValueError("native editor benchmark allowlist is incomplete")
    hashes = _source_hashes()
    if hashes != dict(_IMPORTED_SOURCE_HASHES):
        raise ValueError("synthetic Editor benchmark source changed after import")
    evidence = _evidence(_fixture_world())
    reviews = json.loads(_NATIVE_REVIEW_JSON)
    protocol = load_protocol()
    snapshots = {}
    for case_id in CASE_IDS:
        description, input_json, checker = _NATIVE_CASES[case_id]
        inputs = json.loads(input_json)
        schema = EditorDraftV1.model_json_schema()
        schema["properties"]["mode"] = {"type": "string", "const": inputs["kind"]}
        review = {"suite_elements": reviews["suite_elements"],
                  "case_spec": reviews["cases"][case_id]}
        spec = {
            "case_id": case_id,
            "role_id": "editor_author",
            "source_suite": "editor",
            "source_battery_version": _SOURCE_BATTERY_VERSION,
            "component_battery_version": COMPONENT_BATTERY_VERSION,
            "comparable_to_source_suite": False,
            "measurement_boundary": "remote-editor-component-with-fixed-evidence",
            "description": description,
            "input": inputs,
            "instruction": inputs["prompt"],
            "seed_html": inputs.get("seed_html", ""),
            "payload_schema": schema,
            "evidence": evidence,
            "expected": reviews["cases"][case_id],
            "review": review,
            "checker": {
                "source": "tools.agent_compose_eval.CASES",
                "case_id": case_id,
                "result_contract": "LTM compose result: ok/html/note/references",
                "renderer": "tools.agent_eval_contracts.editor_renderer_contract_flaws",
                "semantic_correctness_assessed": False,
                "manifest_sha256": battery_manifest_sha256(
                    [(case_id, description, inputs, checker)], review,
                    checker_dependencies=native.EDITOR_CHECKER_DEPENDENCIES,
                ),
            },
            "provenance": {
                "synthetic_only": True,
                "fixture_version": FIXTURE_VERSION,
                "fixture_date": FIXTURE_DATE.isoformat(),
                "fixture_builder": "World._build_dataset_fixtures + World._build_dataset_docs",
                "ticket_allowlist": list(_TICKET_KEYS),
                "fixture_projection": "parent fields/latest comment, child/linked-ticket fields, design document",
                "production_retrieval_coverage": "not assessed",
                "protocol_version": protocol["protocolVersion"],
                "rubric_version": protocol["rubricVersion"],
                "source_file_sha256": hashes,
            },
        }
        spec["provenance"]["case_sha256"] = _sha(_json(spec))
        snapshots[case_id] = _json(spec)
    return MappingProxyType(snapshots), MappingProxyType(hashes)


def _snapshot(case_id: str) -> str:
    if not isinstance(case_id, str) or case_id not in CASE_IDS:
        raise ValueError("case_id is outside the synthetic Editor allowlist")
    cases, hashes = _case_snapshots()
    if _source_hashes() != dict(hashes):
        raise ValueError("synthetic Editor benchmark source changed during execution")
    return cases[case_id]


def load_case(case_id: str) -> dict[str, Any]:
    """Return a detached JSON-compatible CaseSpec for one exact allowlisted ID."""
    return json.loads(_snapshot(case_id))


def load_cases() -> tuple[dict[str, Any], ...]:
    """Return the five source cases in fixed order; no prompt/file overrides exist."""
    return tuple(load_case(case_id) for case_id in CASE_IDS)


def validate_case_spec(spec: Mapping[str, Any]) -> None:
    """Reject modified input/evidence/schema/checker metadata, even with forged hashes."""
    if not isinstance(spec, Mapping):
        raise ValueError("CaseSpec must be a JSON object")
    try:
        canonical = _snapshot(spec.get("case_id"))
        provided = _json(dict(spec))
    except (TypeError, KeyError) as exc:
        raise ValueError("CaseSpec is not canonical synthetic benchmark data") from exc
    if provided != canonical:
        raise ValueError("CaseSpec differs from the repository-owned synthetic snapshot")


def project_case(spec: Mapping[str, Any]) -> dict[str, Any]:
    """Return guarded wire fields and a strictly LOCAL opaque-person rendering map.

    Use only ``evidence``, ``input`` and ``instruction`` when building outbound
    EvidencePack values. ``person_map`` restores known fixture mentions locally
    after response validation; it contains raw fixture IDs and must NEVER be
    serialized to Dify. Original CaseSpec/seed/rubric remain unchanged for review.
    This function rejects arbitrary input before applying the synthetic salt.
    """
    validate_case_spec(spec)
    guard = ContextGuard(ContextPolicy(allowed_scopes={}), _SYNTHETIC_PSEUDONYM_KEY)
    content_fields = {
        "input": spec["input"], "instruction": spec["instruction"],
        "evidence": spec["evidence"],
    }
    serialized = _json(content_fields)
    identities = {match.group(0) for pattern in (_EMAIL, _USER_ID)
                  for match in pattern.finditer(serialized)}
    identities.update(match.group(1) for match in _JIRA_MENTION.finditer(serialized))
    ordered = tuple(sorted(identities))

    def project(value: Any) -> Any:
        if isinstance(value, str):
            clean = guard._pseudonymize(value, ordered)
            assert_safe_outbound_text(clean, "synthetic_editor_projection")
            return clean
        if isinstance(value, list):
            return [project(row) for row in value]
        if isinstance(value, dict):
            return {key: project(item) for key, item in value.items()}
        return value

    projected = project(content_fields)
    for row in projected["evidence"]:
        row["content_sha256"] = _sha(row["content"])
    # Validate the serialized WIRE fields independently; this catches a future
    # source shape accidentally putting an identifier into a dictionary key.
    assert_safe_outbound_text(_json(projected), "synthetic_editor_wire")
    projected["person_map"] = {
        guard._identity_marker(identity): identity for identity in ordered
    }
    return projected


def check_output(case_id: str, result: Mapping[str, Any]) -> dict[str, Any]:
    """Reuse native minimum checks over a compose-shaped result, not human quality.

    The caller must label whether it passes raw Role HTML or the final LTM-rendered
    result. Raw mention tokens can legitimately fail the final renderer contract;
    those failures must not be silently reclassified as semantic model failures.
    """
    _snapshot(case_id)
    if not isinstance(result, Mapping):
        raise ValueError("output must use the compose result object contract")
    normalized = dict(result)
    try:
        source_pass = bool(_NATIVE_CASES[case_id][2](normalized))
    except (KeyError, TypeError, ValueError):
        source_pass = False
    flaws = editor_renderer_contract_flaws(normalized)
    return {"sourceCasePass": source_pass, "rendererFlaws": flaws,
            "automaticPass": source_pass and not flaws,
            "semanticCorrectnessAssessed": False}


__all__ = ["CASE_IDS", "COMPONENT_BATTERY_VERSION", "FIXTURE_VERSION", "load_case",
           "load_cases", "validate_case_spec", "project_case", "check_output"]
