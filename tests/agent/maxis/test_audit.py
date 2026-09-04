from __future__ import annotations

import inspect
import json
import os
import stat
from concurrent.futures import ThreadPoolExecutor

import pytest
from pydantic import ValidationError

from app.agent.maxis import (
    AuditEventV1,
    audit_log_fields,
    make_audit_event,
    read_audit_summary,
    record_audit_event,
)


def test_audit_helper_has_no_raw_content_parameter():
    parameters = set(inspect.signature(make_audit_event).parameters)
    assert not parameters.intersection({
        "prompt", "query", "answer", "content", "payload", "api_key", "token", "secret",
    })


def test_audit_event_serializes_only_bounded_metadata():
    event = make_audit_event(
        correlation_id="req-001",
        event="dify.invoke",
        status="succeeded",
        profile="dev",
        stage="dev",
        route_mode="shadow",
        workflow_key="agent_core",
        workflow_version="wf-1",
        knowledge_version="kb-1",
        role_id="work_architect",
        latency_ms=120,
        input_bytes=500,
        output_bytes=200,
        source_count=2,
        citation_count=2,
        request_sha256="a" * 64,
    )
    row = audit_log_fields(event)
    assert row["latency_ms"] == 120
    assert row["request_sha256"] == "a" * 64
    assert not set(row).intersection({"query", "answer", "payload", "api_key"})


def test_failed_audit_event_requires_safe_error_code():
    with pytest.raises(ValidationError):
        AuditEventV1(
            event_id="a" * 32,
            occurred_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc),
            correlation_id="req-001",
            event="dify.invoke",
            status="failed",
            profile="dev",
            stage="dev",
            route_mode="maxis",
            workflow_key="agent_core",
        )


def _event(index: int, *, status="succeeded", error_code=""):
    return make_audit_event(
        correlation_id=f"req-{index}",
        event="dify.invoke",
        status=status,
        profile="dev",
        stage="dev",
        route_mode="shadow",
        workflow_key="agent_core",
        workflow_version="wf-1",
        knowledge_version="kb-1",
        role_id="work_architect",
        error_code=error_code,
    )


def test_audit_ledger_appends_thread_safely_and_reads_aggregate_only(tmp_path):
    path = tmp_path / "maxis_audit.jsonl"
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(
            lambda index: record_audit_event(
                _event(index), path=path, max_rows=100, max_bytes=100_000
            ),
            range(12),
        ))
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 12
    assert all(json.loads(line)["contract"] == "maxis-audit-event.v1" for line in lines)
    summary = read_audit_summary(path=path)
    assert summary.total_rows == 12
    assert summary.active_rows == 12
    assert summary.rotated_rows == 0
    assert [(row.key, row.count) for row in summary.status_counts] == [
        ("succeeded", 12)
    ]
    assert not hasattr(summary, "events")
    if os.name != "nt":
        assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_audit_ledger_rotates_by_row_limit_and_includes_single_backup(tmp_path):
    path = tmp_path / "maxis_audit.jsonl"
    for index in range(3):
        record_audit_event(_event(index), path=path, max_rows=2, max_bytes=100_000)
    assert len(path.read_text(encoding="utf-8").splitlines()) == 1
    assert len((tmp_path / "maxis_audit.jsonl.1").read_text(
        encoding="utf-8"
    ).splitlines()) == 2
    summary = read_audit_summary(path=path)
    assert (summary.total_rows, summary.active_rows, summary.rotated_rows) == (3, 1, 2)


def test_audit_writer_rejects_secret_even_in_metadata_field_without_echo(tmp_path):
    secret = "app-0123456789abcdefghijklmnop"
    event = _event(1).model_copy(update={"workflow_version": secret})
    with pytest.raises(ValueError) as caught:
        record_audit_event(event, path=tmp_path / "maxis_audit.jsonl")
    assert "unsafe audit metadata" in str(caught.value)
    assert secret not in str(caught.value)
    assert not (tmp_path / "maxis_audit.jsonl").exists()


def test_default_audit_path_resolves_settings_cache_dir_at_call_time(tmp_path, monkeypatch):
    from app.infra import settings

    first = tmp_path / "first"
    second = tmp_path / "second"
    monkeypatch.setattr(settings, "CACHE_DIR", first)
    record_audit_event(_event(1))
    assert (first / "maxis_audit.jsonl").is_file()
    monkeypatch.setattr(settings, "CACHE_DIR", second)
    record_audit_event(_event(2))
    assert (second / "maxis_audit.jsonl").is_file()
    assert read_audit_summary().total_rows == 1
