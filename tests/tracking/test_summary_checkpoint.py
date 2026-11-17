"""Summary publication cannot replace a collector's durable resume graph."""

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import threading

import pytest

import app.tracking.manager as manager_module
from tracking.test_backend_manager import Provider, manager


REQUEST = {"startDate": "2026-11-10", "endDate": "2026-11-12"}
SUMMARY = {"items": [], "usage": {"totalTokens": 7}}


def test_summary_publication_cannot_rewind_concurrent_checkpoint_progress(tmp_path, monkeypatch):
    provider = Provider()
    provider.fail = True
    engine = manager(tmp_path, provider)
    run = engine.create(REQUEST, background=False)
    _, _, snapshot, checkpoint = engine.store.read(run["id"])
    progressed = deepcopy(checkpoint)
    progressed["queries"]["resumed-page"] = {"nextStartAt": 25, "complete": False}
    snapshot["status"] = "running"
    summary_read, release_summary, writer_ordered = threading.Event(), threading.Event(), threading.Event()
    original_lock, project = engine.store.lock, manager_module.project_activity

    class ObservedLock:
        def __enter__(self):
            if threading.current_thread().name == "checkpoint-writer":
                if original_lock.acquire(blocking=False):
                    return self
                # This publication will follow the atomic summary update.
                writer_ordered.set()
            original_lock.acquire()
            return self

        def __exit__(self, *_):
            original_lock.release()

    def pause_projection(snapshot):
        summary_read.set()
        assert release_summary.wait(3)
        return project(snapshot)

    def publish_progress():
        engine.store.save(run["id"], snapshot, progressed)
        # An unlocked summary read permits progress to land before its write.
        writer_ordered.set()

    monkeypatch.setattr(engine.store, "lock", ObservedLock())
    monkeypatch.setattr(manager_module, "project_activity", pause_projection)
    with ThreadPoolExecutor(max_workers=1) as executor:
        summary = executor.submit(engine.record_summary, run["id"], SUMMARY)
        writer = threading.Thread(target=publish_progress, name="checkpoint-writer")
        try:
            assert summary_read.wait(2)
            writer.start()
            assert writer_ordered.wait(2)
        finally:
            release_summary.set()
        assert summary.result(3) == SUMMARY
        writer.join(3)
        assert not writer.is_alive()
    _, _, final, final_checkpoint = engine.store.read(run["id"])
    assert final_checkpoint == progressed
    assert final["status"] == "running"


@pytest.mark.parametrize("identity", ["owner", "scope", "missing"])
def test_summary_update_preserves_owner_scope_and_missing_run_semantics(tmp_path, identity):
    engine = manager(tmp_path, Provider())
    run = engine.create(REQUEST, background=False)
    before = engine.store.read(run["id"])
    if identity == "owner":
        engine.client.current_user_for_authorization = lambda: {"id": "other"}
    elif identity == "scope":
        engine.client.s.jira_base = "other-server"
    with pytest.raises(KeyError if identity == "missing" else PermissionError):
        engine.record_summary("not-a-run" if identity == "missing" else run["id"], SUMMARY)
    assert engine.store.read(run["id"]) == before
