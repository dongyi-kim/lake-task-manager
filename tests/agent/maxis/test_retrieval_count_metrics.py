"""Scoped zero-hit counts become evidence, never a bypass of source attestation."""
import copy
import json

import pytest

from app.agent.maxis import backend


def source(kind="confluence", count=0):
    field, query = ("documents", "canonicalCql") if kind == "confluence" else ("tickets", "canonicalJql")
    return {"source_id": "src_test", "kind": kind, "canonical_id": "ltm-query:test",
            "scope": "DL", "partial": False,
            "content": json.dumps({field: [{"key": f"DL-{n + 1}"} for n in range(count)],
                                   query: "scoped query", "hasMore": False})}


@pytest.mark.parametrize("kind", ["jira", "confluence"])
@pytest.mark.parametrize("count", [0, 1, 3])
def test_count_is_exact_returned_rows_not_upstream_global_total(kind, count):
    row = source(kind, count)
    value = json.loads(row["content"])
    value.update(total=999, scopePartitioned=True)
    row["content"] = json.dumps(value)
    before = copy.deepcopy(row)
    metrics = backend._retrieval_count_metrics([row])
    assert row == before and len(metrics) == 1
    assert metrics[0]["value"] == count and metrics[0]["unit"] == "건"
    assert metrics[0]["source_ids"] == [row["source_id"]]
    assert "DL 범위" in metrics[0]["label"]
    assert "반환" in metrics[0]["label"]


@pytest.mark.parametrize("field,value", [
    ("hasMore", True), ("hasMore", None), ("error", "upstream failed"),
    ("errors", ["failed"]), ("truncated", True), ("partial", True),
    ("ok", False), ("status", "failed"), ("canonicalCql", ""),
    ("documents", None), ("documents", [None]),
])
def test_incomplete_or_failed_result_never_proves_zero(field, value):
    row = source()
    result = json.loads(row["content"])
    result[field] = value
    row["content"] = json.dumps(result)
    assert backend._retrieval_count_metrics([row]) == []


@pytest.mark.parametrize("field,value", [
    ("partial", True), ("partial", None), ("truncated", True), ("kind", "ltm"),
    ("kind", "knowledge"), ("content", "not JSON"),
])
def test_unusable_source_never_gets_count(field, value):
    row = source()
    row[field] = value
    assert backend._retrieval_count_metrics([row]) == []


def test_two_scopes_do_not_reuse_metric_identity_or_global_counts():
    a, b = source(count=1), source(count=0)
    b.update(source_id="src_other", scope="PMO")
    rows = backend._retrieval_count_metrics([a, b])
    assert [row["value"] for row in rows] == [1, 0]
    assert len({row["metric_id"] for row in rows}) == 2
    assert rows[1]["source_ids"] == ["src_other"]


def test_zero_count_passes_both_numeric_guards_only_with_linked_metric(context_guard, now):
    from app.agent.maxis import ResponseGuard, ResponseGuardError
    row = source("jira")
    row.update(title="검색 결과", url="", retrieved_at=now)
    schema = {"type": "object", "properties": {"summary": {"type": "string"}},
              "required": ["summary"], "additionalProperties": False}
    pack = context_guard.build(request_id="zero-count", use_case="work_architect",
        query="검색 결과 요약", payload_schema=schema, user_identity="tester",
        requested_at=now, sources=[row], metrics=backend._retrieval_count_metrics([row]),
        knowledge_manifest_sha256="a" * 64)
    text = "해당 검색의 반환 항목 수는 0건이다."
    answer = {"contract": "remote-role-result.v1", "role_id": "work_architect",
        "status": "ok", "payload": {"summary": text},
        "claims": [{"claim_id": "claim-1", "kind": "inference", "text": text,
                    "citation_ids": ["cite-1"]}],
        "citations": [{"citation_id": "cite-1", "source_id": "src_test"}],
        "payload_claims": [{"path": "/summary", "claim_ids": ["claim-1"]}],
        "missing_inputs": [], "warnings": [], "workflow_version": "wf-test",
        "knowledge_version": "kb-test", "knowledge_manifest_sha256": "a" * 64,
        "knowledge_proof": "LTM_RELEASE_SENTINEL_V1=test"}
    validate = lambda evidence, output: ResponseGuard().validate_remote_role(
        json.dumps(output, ensure_ascii=False), expected_role_id="work_architect",
        payload_model=schema, evidence=evidence, expected_knowledge_manifest_sha256="a" * 64)
    assert validate(pack, answer)[1] == {"summary": text}
    with pytest.raises(ResponseGuardError):
        validate(pack.model_copy(update={"metrics": ()}), answer)
    wrong = copy.deepcopy(answer)
    wrong["payload"]["summary"] = wrong["claims"][0]["text"] = text.replace("0건", "2건")
    with pytest.raises(ResponseGuardError):
        validate(pack, wrong)
