"""Read-only MAXIS status evidence from bounded synthetic audit ledgers."""
from __future__ import annotations

import io
import json
import socket
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.agent.maxis import audit, profiles
from app.infra import settings

WORKFLOW_ID = "11111111-1111-4111-8111-111111111111"
UNAVAILABLE = {"state": "unavailable", "observations": []}


@pytest.fixture(autouse=True)
def ledger(monkeypatch, tmp_path):
    def forbidden(*args, **kwargs):
        raise AssertionError("private profile/key/socket access forbidden")

    for target, name in (
        (socket.socket, "connect"), (socket.socket, "connect_ex"),
        (socket, "create_connection"), (profiles.prefs, "load"),
        (profiles.prefs, "save_strict"), (profiles.credentials, "get_for"),
        (profiles.credentials, "resolved_for"), (profiles.credentials, "save_for"),
    ):
        monkeypatch.setattr(target, name, forbidden)
    monkeypatch.setattr(settings, "CACHE_DIR", tmp_path)
    for name in ("LAKE_MAXIS_PROFILE", *profiles.CONFIG_ENV_NAMES.values(),
                 *profiles.WORKFLOW_VERSION_ENV_NAMES.values(),
                 *profiles.ALLOWED_HOSTS_ENV_NAMES.values(),
                 *profiles.credentials.ENV_NAMES.values()):
        monkeypatch.delenv(name, raising=False)
    return tmp_path / "maxis_audit.jsonl"


def row(**changes):
    values = dict(
        correlation_id="private-correlation", event="remote_role.invoke",
        status="succeeded", profile="dev", stage="dev", route_mode="shadow",
        workflow_key="agent_core", role_id="work_architect",
        workflow_version=WORKFLOW_ID, knowledge_version="ltm-knowledge-2026.09",
        occurred_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    values.update(changes)
    return audit.make_audit_event(**values).model_dump(mode="json")


def write_rows(path, rows):
    path.write_text("".join(json.dumps(item) + "\n" for item in rows), encoding="utf-8")


def read():
    return audit.read_last_remote_observations()


def test_missing_ledger_does_not_imply_local_execution(ledger):
    assert read() == {"state": "none_recorded", "observations": []}
    assert not ledger.exists()


@pytest.mark.parametrize("route,owner", [("shadow", "local"), ("maxis", "unknown")])
def test_history_preserves_unknowns_versions_and_output_ownership(ledger, route, owner):
    write_rows(ledger, [row(route_mode=route)])
    before = ledger.read_bytes()
    result = read()
    item = result["observations"][0]
    assert result["state"] == "observed"
    assert item["routeMode"] == route and item["outputOwner"] == owner
    assert item["transportStatus"] is None and item["resultStatus"] is None
    assert item["currentBindingMatch"] == "unknown"
    assert item["knowledgeVersion"] == "ltm-knowledge-2026.09"
    assert item["workflowVersion"] == WORKFLOW_ID
    assert set(item) == {
        "profile", "workflowKey", "roleId", "observedAt", "routeMode",
        "outputOwner", "workflowVersion", "knowledgeVersion", "currentBindingMatch",
        "auditStatus", "transportStatus", "resultStatus", "httpStatus", "workflowRunId",
    }
    assert "private-correlation" not in json.dumps(result)
    assert ledger.read_bytes() == before


def test_http_success_does_not_hide_blocked_result(ledger):
    telemetry = audit.RemoteRequestTelemetryV1(
        transport_status="succeeded", http_status=200, result_status="blocked",
    )
    write_rows(ledger, [row(status="blocked", error_code="policy", remote_request=telemetry)])
    item = read()["observations"][0]
    assert item["transportStatus"] == "succeeded" and item["resultStatus"] == "blocked"
    assert item["workflowRunId"] is None and item["httpStatus"] == 200
    assert item["auditStatus"] == "blocked"


@pytest.mark.parametrize("field,value", [
    ("role_id", "private_role"), ("workflow_key", "private_workflow"),
    ("profile", "private_profile"), ("workflow_version", "app-" + "X" * 30),
    ("knowledge_version", "app-" + "X" * 30),
    ("knowledge_version", "https://private.invalid/docs"),
])
def test_malicious_legacy_metadata_is_unavailable(ledger, field, value):
    record = row()
    record[field] = value
    write_rows(ledger, [record])
    assert read() == UNAVAILABLE


@pytest.mark.parametrize("data", [
    b"bad json\n", b"\xff\n", b"null\n", b"[]\n", b'{"occurred_at":false}\n',
])
def test_malformed_ledger_is_unavailable(ledger, data):
    ledger.write_bytes(data)
    assert read() == UNAVAILABLE


def test_unreadable_ledger_does_not_expose_exception(ledger, monkeypatch):
    write_rows(ledger, [row()])
    def denied(*args, **kwargs):
        raise PermissionError("private address")
    monkeypatch.setattr(Path, "open", denied)
    assert read() == UNAVAILABLE


def test_latest_rotated_observation_keeps_its_historical_versions(ledger):
    newer = row(occurred_at=datetime(2026, 2, 1, tzinfo=timezone.utc),
                knowledge_version="ltm-knowledge-2025.01")
    write_rows(ledger.with_name(ledger.name + ".1"), [newer])
    write_rows(ledger, [row(), row()])
    assert len(read()["observations"]) == 1
    assert read()["observations"][0]["knowledgeVersion"] == "ltm-knowledge-2025.01"


@pytest.mark.parametrize("version", ["private.invalid", "127.0.0.1", "legacy-version"])
def test_noncanonical_knowledge_label_remains_unknown(ledger, version):
    write_rows(ledger, [row(knowledge_version=version)])
    assert read()["observations"][0]["knowledgeVersion"] is None


@pytest.mark.parametrize("value", [
    "0001-01-01T00:00:00+14:00", "9999-12-31T23:59:59-14:00",
])
def test_extreme_dates_are_unavailable(ledger, value):
    record = row()
    record["occurred_at"] = value
    write_rows(ledger, [record])
    assert read() == UNAVAILABLE


@pytest.mark.parametrize("telemetry,transport,result", [
    ({}, None, None), ({"http_status": 200}, None, None),
    ({"workflow_run_id": WORKFLOW_ID}, None, None),
    ({"transport_status": "succeeded"}, "succeeded", None),
    ({"result_status": "blocked"}, None, "blocked"),
    ({"transport_status": "not_attempted", "result_status": "not_evaluated"},
     "not_attempted", "not_evaluated"),
])
def test_partial_legacy_telemetry_preserves_presence(ledger, telemetry, transport, result):
    record = row()
    record["remote_request"] = telemetry
    write_rows(ledger, [record])
    item = read()["observations"][0]
    assert item["transportStatus"] == transport and item["resultStatus"] == result
    assert item["httpStatus"] == telemetry.get("http_status")
    assert item["workflowRunId"] == telemetry.get("workflow_run_id")


@pytest.mark.parametrize("field,value", [
    ("workflow_run_id", "private.invalid"), ("http_status", True),
    ("transport_status", "private prompt"), ("result_status", "app-secret"),
    ("workflow_run_id", "11111111-1111-6111-8111-111111111111"),
])
def test_bad_telemetry_is_unavailable(ledger, field, value):
    record = row()
    record["remote_request"] = {field: value}
    write_rows(ledger, [record])
    assert read() == UNAVAILABLE


@pytest.mark.parametrize("delta,accepted", [(0, True), (1, False)])
def test_actual_read_boundary_after_growth(ledger, monkeypatch, delta, accepted):
    encoded = json.dumps(row()).encode("utf-8") + b"\n"
    limit = len(encoded) + 13
    ledger.write_bytes(encoded)
    original_open = Path.open
    def growing_open(path, mode="r", *args, **kwargs):
        if path == ledger and mode == "rb":
            with original_open(path, "wb") as writer:
                writer.write(b" " * (13 + delta) + encoded)
        return original_open(path, mode, *args, **kwargs)
    monkeypatch.setattr(Path, "open", growing_open)
    if accepted:
        assert len(audit._read_rows(ledger, max_read_bytes=limit)) == 1
    else:
        with pytest.raises(ValueError):
            audit._read_rows(ledger, max_read_bytes=limit)


@pytest.mark.parametrize("mode,count", [
    ("empty", 0), ("space", None), ("blank_lf", None), ("lf", 1), ("crlf", 1),
    ("no_lf", 1), ("double_lf", None), ("cr_separator", None), ("vertical_separator", None),
])
def test_lf_iteration_semantics_unchanged(ledger, mode, count):
    encoded = json.dumps(row()).encode("utf-8")
    data = {
        "empty": b"", "space": b" \t", "blank_lf": b"\n", "lf": encoded + b"\n",
        "crlf": encoded + b"\r\n", "no_lf": encoded, "double_lf": encoded + b"\n\n",
        "cr_separator": encoded + b"\r" + encoded,
        "vertical_separator": encoded + b"\v" + encoded,
    }[mode]
    ledger.write_bytes(data)
    if count is None:
        with pytest.raises(ValueError):
            audit._read_rows(ledger, max_read_bytes=4096)
    else:
        assert len(audit._read_rows(ledger, max_read_bytes=4096)) == count


@pytest.mark.parametrize("extra", [0, 1])
def test_stream_read_is_bounded_and_closed(ledger, monkeypatch, extra):
    payload = json.dumps(row()).encode("utf-8") + b"\n"
    ledger.write_bytes(payload)
    calls = []
    class CheckedStream(io.BytesIO):
        def read(self, size=-1):
            calls.append(size)
            assert size == len(payload) + 1
            return super().read(size)

        def __iter__(self):
            raise AssertionError("disk stream must not be iterated")
    stream = CheckedStream(payload + b" " * extra)
    monkeypatch.setattr(Path, "open", lambda *args, **kwargs: stream)
    if extra:
        with pytest.raises(ValueError):
            audit._read_rows(ledger, max_read_bytes=len(payload))
    else:
        assert len(audit._read_rows(ledger, max_read_bytes=len(payload))) == 1
    assert calls == [len(payload) + 1] and stream.closed


def test_oversize_is_rejected_before_validation(ledger, monkeypatch):
    ledger.write_bytes(b"x")
    monkeypatch.setattr(Path, "open", lambda *a, **k: io.BytesIO(b"x" * 1025))
    def forbidden(*args, **kwargs):
        raise AssertionError("oversize input reached JSON validation")
    monkeypatch.setattr(audit.AuditEventV1, "model_validate_json", forbidden)
    with pytest.raises(ValueError):
        audit._read_rows(ledger, max_read_bytes=1024)


@pytest.mark.parametrize("grows_after_stat", [False, True])
def test_status_read_limit_is_enforced(ledger, monkeypatch, grows_after_stat):
    encoded = json.dumps(row()).encode("utf-8") + b"\n"
    oversized = b" " * (2 * 1024 * 1024) + encoded
    ledger.write_bytes(encoded if grows_after_stat else oversized)
    original_open = Path.open
    def growing_open(path, mode="r", *args, **kwargs):
        if path == ledger and mode == "rb":
            with original_open(path, "wb") as writer:
                writer.write(oversized)
        return original_open(path, mode, *args, **kwargs)
    if grows_after_stat:
        monkeypatch.setattr(Path, "open", growing_open)
    assert read() == UNAVAILABLE


def test_status_addition_is_read_only(ledger, monkeypatch):
    for name, replacement in {
        "_public_profile": lambda name: {"name": name},
        "active_profile_name": lambda: "", "active_fingerprint": lambda: "",
        "routes": lambda: {}, "kill_switch_enabled": lambda: False,
    }.items():
        monkeypatch.setattr(profiles, name, replacement)
    monkeypatch.setattr(profiles.credentials, "environment_overrides", lambda: {})
    assert profiles.status()["lastRemoteObservations"] == read()
