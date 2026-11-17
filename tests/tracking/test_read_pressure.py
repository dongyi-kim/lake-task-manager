"""Polling a run must not reconstruct the collector's duplicated working graph."""
import json

import pytest

from app.tracking.presentation import present_comments
from app.tracking import storage
from test_comment_rendering import saved_run
from test_name_presentation import with_names


def test_poll_skips_query_and_analysis_graph_but_preserves_rich_comments_and_names(tmp_path, monkeypatch):
    m, provider = saved_run(tmp_path, body="[~alice] ready",
                            rendered_body="<p>Already rendered</p>")
    identity = with_names(m)
    try:
        identity.remember_display_name("alice", "Alice Example")
        owner, scope, snapshot, checkpoint = m.store.read("saved")
        snapshot["events"][0].update(actorId="alice", actorName="alice")
        snapshot["issues"][0].update(assigneeId="alice", assigneeName="alice")
        # Search pages, their flattened issues, and analyzed records all contain
        # overlapping evidence. Polling needs neither of these working copies.
        marker = "collector-only-evidence-" + "x" * 4096
        rows = [{"key": f"P-{index}", "payload": marker} for index in range(250)]
        checkpoint["queries"] = {"hydrate": {"pages": [{"issues": rows}], "issues": rows}}
        checkpoint["analyzed"] = {row["key"]: row for row in rows}
        m.store.save("saved", snapshot, checkpoint)
        expected = present_comments(snapshot, m.client, checkpoint)

        decoded = []
        loads = json.loads

        def observe_decode(payload, *args, **kwargs):
            decoded.append(payload)
            return loads(payload, *args, **kwargs)

        monkeypatch.setattr(storage.json, "loads", observe_decode)
        actual = m.get("saved")

        assert actual == expected
        assert actual["events"][0]["bodyHtml"] == "<p>Already rendered</p>"
        assert actual["events"][0]["actorName"] == "Alice Example"
        assert actual["issues"][0]["assigneeName"] == "Alice Example"
        assert decoded and all("collector-only-evidence-" not in payload for payload in decoded)
        assert provider.calls == []

        # Presentation reads never trim the durable checkpoint used to resume.
        assert m.store.read("saved") == (owner, scope, snapshot, checkpoint)
    finally:
        identity.cache.close()


def test_checkpoint_projection_leaves_full_reads_and_missing_runs_unchanged(tmp_path):
    m, _ = saved_run(tmp_path, body="ready")
    original = m.store.read("saved")

    projected = m.store.read("saved", checkpoint_fields=("issues", "sources"))

    assert projected[:3] == original[:3]
    assert projected[3] == {field: original[3][field] for field in ("issues", "sources")}
    assert m.store.read("saved") == original
    with pytest.raises(KeyError):
        m.store.read("missing", checkpoint_fields=("issues", "sources"))


def test_checkpoint_projection_preserves_missing_null_and_boolean_fields(tmp_path):
    m, _ = saved_run(tmp_path, body="ready")
    _, _, snapshot, _ = m.store.read("saved")
    checkpoint = {"issues": {}, "empty": None, "complete": True, "pending": False}
    m.store.save("saved", snapshot, checkpoint)

    projected = m.store.read("saved", checkpoint_fields=iter([*checkpoint, "sources", "sources"]))

    assert projected[3] == checkpoint
    assert projected[3]["complete"] is True
    assert projected[3]["pending"] is False
    assert m.get("saved")["events"][0]["bodyText"] == "ready"
