"""Fixed synthetic inputs for OFFLINE MAXIS Role payload measurements.

This is a component fixture adapter, not a workflow, retrieval, model-quality or UI
benchmark. Source benchmark references describe the scenario mapping; their old
inputs/checkers and the runtime DEV fixed-fixture gate are unchanged. Only exact
repository case IDs are accepted. No prompt, state, file or configuration override
exists. Run in an isolated offline process: scoped mock patches are process-wide
while the synchronous builder executes. No client or gateway is invoked.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from types import MappingProxyType, SimpleNamespace
from unittest.mock import patch

from app.agent.maxis import backend


BATTERY_VERSION = "2.0.0"  # missing-evidence case now declares its absent required inputs
FIXTURE_VERSION = "ltm-maxis-role-offline.v1"
_NOW = datetime(2026, 9, 17, 0, 0, tzinfo=timezone.utc)
# Public deterministic keys belong ONLY to these fixed offline fixtures.
_KEY = hashlib.sha256(b"ltm-maxis-role-offline-synthetic-v1").digest()
_BUG = "리니지 뷰어에서 2홉 이상 펼치면 화면이 빈다. 크롬에서 재현되고 기대는 그래프가 그려지는 것. 버그로 올려줘."
_HISTORY = "fdc.fdc_trace_summary_ic 데이터에 대해 히스토리 정리"
_SCENARIOS = (
    ("role-request", "request_architect", "bug-request-routing", "conversation", "5.0.0", "S2-버그", ()),
    ("role-query", "query_specialist", "history-query-planning", "conversation", "5.0.0", "S3-이력", ("request_plan", "keywords")),
    ("role-knowledge", "knowledge_curator", "curated-internal-history", "conversation", "5.0.0", "S3-이력", ("situation", "evidence")),
    ("role-work", "work_architect", "bug-draft", "create", "6.0.0", "BUG2", ("request_plan", "situation", "evidence")),
    ("role-auditor", "auditor", "bug-draft-audit", "create", "6.0.0", "BUG2", ("draft", "evidence")),
    ("role-result", "result_integrator", "history-response", "conversation", "5.0.0", "S3-이력", ("situation", "evidence", "knowledge_brief")),
    ("role-portfolio", "portfolio_analyst", "ticket-progress", "conversation", "5.0.0", "S6-진척", ("intent", "mentioned_keys", "ticket_progress")),
    ("role-editor", "editor_author", "existing-description", "editor", "4.0.0", "CMP2", ("ticket_key", "kind", "seed_html")),
    ("role-overflow", "result_integrator", "context-budget-overflow", "conversation", "5.0.0", "S8-복합근거품질", ("situation", "evidence", "knowledge_brief", "interpretation")),
    ("role-stale", "auditor", "stale-partial-evidence", "create", "6.0.0", "BUG2", ("draft", "evidence")),
    ("role-missing", "result_integrator", "missing-evidence", "conversation", "5.0.0", "S3-이력", ("situation", "evidence", "knowledge_brief")),
)
_SOURCE_FILES = {
    "conversation": "tools/agent_lang_ab.py", "create": "tools/agent_create_suite.py",
    "editor": "tools/agent_compose_eval.py",
}


def _json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _case_specs() -> dict[str, str]:
    snapshots = {}
    for case_id, role, scenario, suite, version, source_id, required in _SCENARIOS:
        flags = {"role-overflow": ["source_truncated", "partial_role_context"],
                 "role-stale": ["stale_source", "partial_source", "stale_role_context"]}
        spec = {
            "id": case_id, "role_id": role, "scenario": scenario,
            "battery_version": BATTERY_VERSION, "fixture_version": FIXTURE_VERSION,
            "measurement_boundary": "offline-role-payload-component",
            "synthetic_only": True, "comparable_to_source_suite": False,
            "benchmark_refs": [{"suite": suite, "battery_version": version,
                                "case_id": source_id, "source": _SOURCE_FILES[suite]}],
            "expected": {
                "required_context_keys": list(required), "max_pack_utf8_bytes": 6144,
                "freshness": {"role-stale": "stale", "role-missing": "empty"}.get(case_id, "fresh"),
                "partial": case_id in {"role-overflow", "role-stale"},
                "flags": flags.get(case_id, []), "semantic_quality_assessed": False,
                "no_remote_calls": True,
            },
        }
        spec["case_metadata_sha256"] = hashlib.sha256(_json(spec).encode("utf-8")).hexdigest()
        snapshots[case_id] = _json(spec)
    return snapshots


_CASES = MappingProxyType(_case_specs())


def load_cases() -> list[dict]:
    """Return detached metadata copies; modifications never change fixture inputs."""
    return [json.loads(value) for value in _CASES.values()]


class _FixedClock(datetime):
    @classmethod
    def now(cls, tz=None):
        return _NOW.astimezone(tz) if tz else _NOW.replace(tzinfo=None)


def _schema(role: str) -> tuple[dict, str]:
    """Use the schema the real Role sends, including any actual budget failure."""
    if role == "result_integrator":
        return backend._TEXT_PAYLOAD_SCHEMA, "text"
    if role in {"query_specialist", "knowledge_curator"}:
        from app.agent.workflow.contracts import role_output_schema
        return role_output_schema(role), "structured"
    if role == "editor_author":
        from app.agent.maxis.contracts import EditorDraftV1
        schema = EditorDraftV1.model_json_schema()
        schema["properties"]["mode"] = {"type": "string", "const": "description"}
        return schema, "structured"
    if role == "work_architect":
        from app.agent.workflow.work_architect.contracts import CREATE_SCHEMA
        return CREATE_SCHEMA, "structured"
    from importlib import import_module
    # These canonical Role modules expose the exact schema used by schema().
    return import_module(f"app.agent.workflow.agents.{role}").SCHEMA, "structured"


def _state(spec: dict) -> dict:
    role, case_id = spec["role_id"], spec["id"]
    state = {
        "thread_id": "offline-role-fixture", "correlation_id": case_id,
        "correlation_started_at": _NOW.isoformat(), "user_id": "user.fixture",
        "runtime_engine": "offline", "runtime_profile": "offline-fixture",
        "knowledge_manifest_sha256": hashlib.sha256(FIXTURE_VERSION.encode()).hexdigest(),
        "request_text": _HISTORY,
    }
    plan = {"goal": _HISTORY, "tasks": [{"id": "history", "kind": "query",
            "instruction": _HISTORY, "depends_on": [], "write_intent": False}]}
    evidence = [{"key": "DL-9044", "fact": "배치 주기를 2시간에서 30분으로 변경"}]
    situation = "합성 조회에서 배치 주기 변경 이력을 확인함."
    if role in {"request_architect", "work_architect", "auditor"}:
        state["request_text"] = _BUG
        state["intent"] = "plan_work"
        plan = {"goal": _BUG, "tasks": [{"id": "bug", "kind": "ticket",
                "instruction": _BUG, "depends_on": [], "write_intent": True}]}
        evidence = [{"key": "DL-9090", "fact": "리니지 뷰어 부모 업무, 진행 중"}]
        situation = "사용자가 Chrome의 2홉 빈 화면과 기대 결과를 제시함."
    if role == "query_specialist":
        state.update(request_plan=plan, keywords=["fdc.fdc_trace_summary_ic"])
    if role in {"knowledge_curator", "work_architect", "result_integrator"}:
        state.update(situation=situation, evidence=evidence)
    if role == "work_architect":
        state["request_plan"] = plan
    if role == "auditor":
        state.update(evidence=evidence, draft={"items": [{"type": "Bug",
            "summary": "리니지 뷰어 2홉 빈 화면", "description":
            "<h3>재현</h3><p>Chrome에서 2홉 펼침</p><h3>기대</h3><p>그래프 표시</p>"}]})
    if role == "result_integrator":
        state["knowledge_brief"] = {"our_context": "배치 주기 30분", "open_questions": []}
    if role == "portfolio_analyst":
        state.update(request_text="DL-9090 지금 어디까지 진행됐어?", intent="progress",
                     mentioned_keys=["DL-9090"], ticket_progress={"key": "DL-9090",
                     "doneSP": 5, "totalSP": 10, "progressPct": 50})
    if role == "editor_author":
        state.update(request_text="이 티켓 본문을 배경·작업 범위·완료 조건 갖춰 다시 써줘",
                     ticket_key="DL-9095", kind="description",
                     seed_html="<p>리니지 뷰어 모니터링 적용. 기존 수치 30분 보존.</p>")
    if case_id == "role-overflow":
        # Late required interpretation deliberately competes with earlier context.
        # Expected keys remain the requested contract, even when production drops them.
        for key in ("result", "review", "draft", "change_plan", "questions",
                    "situation", "evidence", "knowledge_brief", "interpretation"):
            state[key] = {"fixture_text": "합성 근거 설명 " * 120}
    if case_id == "role-stale":
        state["correlation_started_at"] = (_NOW - timedelta(hours=1)).isoformat()
    if case_id == "role-missing":
        for key in ("situation", "evidence", "knowledge_brief"):
            state.pop(key, None)
        state["request_text"] = ""
    return state


def _attach_sources(state: dict, spec: dict) -> None:
    role, case_id = spec["role_id"], spec["id"]
    if role in {"request_architect", "query_specialist"} or case_id == "role-missing":
        return
    key = "DL-9044" if role in {"knowledge_curator", "result_integrator"} else "DL-9090"
    if role == "editor_author":
        key = "DL-9095"
    row = {"key": key, "summary": "합성 이력·진척 확인",
           "assignee": "user.fixture", "reporter": "fixture@example.invalid",
           "statusCategory": "indeterminate", "description":
           ("배치 주기를 2시간에서 30분으로 변경" if key == "DL-9044"
            else "리니지 뷰어 2홉·모니터링 작업"), "doneSP": 5, "totalSP": 10}
    state["query_results"] = []
    stale = case_id == "role-stale"
    for index in range(4 if case_id == "role-overflow" else 1):
        result = {"issues": [dict(row)], "total": 1, "complete": not stale}
        if case_id == "role-overflow":
            result["issues"][0]["description"] = "예산 초과 합성 자료 " * 600
        query_id = f"fixture-{index}"
        proof = backend.attest_query_result(
            query_id, "jira", result, scopes=("DL",), partial=stale,
            retrieved_at=_NOW - timedelta(hours=1) if stale else _NOW,
            provenance={"tracked": True, "read_count": 1, "served_stale": stale,
                        "upstream_observed": False},
        )
        state["query_results"].append({"id": query_id, "source": "jira",
                                       "result": result, "_remoteEvidence": proof})


def build_fixture_payload(case_id: str) -> dict:
    """Build actual guarded Role payload for an exact fixed case, without I/O.

    Guard/schema failures propagate so a runner can report them as observations.
    Returned state is synthetic LOCAL evidence, never an outbound request body.
    """
    if type(case_id) is not str or case_id not in _CASES:
        raise ValueError("case_id must select repository-owned offline fixture data")
    spec = json.loads(_CASES[case_id])
    from app.agent.tools import _ctx
    settings = SimpleNamespace(
        search_jira_projects=("DL",), search_confluence_spaces=("DL",),
        search_bitbucket_projects=(), jira_env="mock", jira_base="https://jira.example.invalid",
        confluence_base="https://wiki.example.invalid", jira_user="user.fixture", managers=(),
    )
    with patch.object(_ctx, "settings", return_value=settings), \
            patch.object(backend, "_known_identity_values", return_value=(
                "user.fixture", "fixture@example.invalid")), \
            patch.object(backend, "datetime", _FixedClock), \
            patch.object(backend, "_PSEUDONYM_KEY", _KEY), \
            patch.object(backend, "_EVIDENCE_ATTESTATION_KEY", _KEY):
        state = _state(spec)
        _attach_sources(state, spec)
        schema, output_kind = _schema(spec["role_id"])
        payload = backend.role_payload(spec["role_id"], state, [], schema,
                                       output_kind=output_kind)
    return {"case": spec, "state": state, "payload": payload}


__all__ = ["BATTERY_VERSION", "FIXTURE_VERSION", "load_cases", "build_fixture_payload"]
