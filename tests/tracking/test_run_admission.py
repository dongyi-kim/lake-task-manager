"""Collector admission bounds live graphs without losing durable resume checkpoints."""
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from app.auth.base import UpstreamBusy
from app.infra.resource_pressure import ResourceBusy, resource_pressure
from tracking.test_backend_manager import Provider, manager


REQUEST = {"startDate": "2026-11-10", "endDate": "2026-11-12"}


class BlockingProvider(Provider):
    def __init__(self):
        super().__init__()
        self.entered, self.release = threading.Event(), threading.Event()

    def get_json(self, path, params=None):
        if path.endswith("/search"):
            self.entered.set()
            assert self.release.wait(5), "collector was not released"
        return super().get_json(path, params)


def test_burst_rejects_before_insert_and_cancel_retains_capacity_until_worker_exits(tmp_path):
    provider = BlockingProvider()
    engine = manager(tmp_path, provider)
    run = engine.create(REQUEST)
    assert provider.entered.wait(5)
    try:
        for _ in range(20):
            with pytest.raises(UpstreamBusy):
                engine.create(REQUEST)
        assert [item["id"] for item in engine.list()] == [run["id"]]
        assert len(engine.workers) == len(engine.controls) == 1
        # Repeated resume of this same worker observes it without another graph.
        assert engine.resume(run["id"])["id"] == run["id"]
        assert engine.cancel(run["id"])["status"] == "cancelled"
        with pytest.raises(UpstreamBusy):
            engine.create(REQUEST, background=False)
    finally:
        provider.release.set()
        final = engine.wait(run["id"], 5)
    assert final["status"] == "cancelled"
    assert engine.workers == engine.controls == {}
    # A completed cancellation does not leave an ever-growing control registry.
    engine.cancel(run["id"])
    assert engine.controls == {}
    assert engine.create(REQUEST, background=False)["status"] == "complete"
    assert engine.workers == engine.controls == {}


def test_busy_resume_preserves_checkpoint_without_loading_its_full_graph(tmp_path, monkeypatch):
    provider = BlockingProvider()
    provider.release.set()
    provider.fail = True
    engine = manager(tmp_path, provider)
    paused = engine.create(REQUEST, background=False)
    assert paused["status"] == "paused"
    before = engine.store.read(paused["id"])
    provider.fail = False
    provider.release.clear()
    provider.entered.clear()
    live = engine.create(REQUEST)
    assert provider.entered.wait(5)
    reads = []
    original_read = engine.store.read

    def record_read(run_id, *, checkpoint_fields=None):
        if run_id == paused["id"]:
            reads.append(checkpoint_fields)
        return original_read(run_id, checkpoint_fields=checkpoint_fields)

    monkeypatch.setattr(engine.store, "read", record_read)
    try:
        with pytest.raises(UpstreamBusy):
            engine.resume(paused["id"], background=False)
        assert reads and None not in reads
        assert original_read(paused["id"]) == before
    finally:
        provider.release.set()
        engine.wait(live["id"], 5)
    resumed = engine.resume(paused["id"], background=False)
    assert resumed["status"] == "complete"
    assert resumed["asOf"] == paused["asOf"]
    assert resumed["coverage"]["issuesTotal"] == resumed["coverage"]["issuesComplete"] == 1


def test_worker_setup_failure_pauses_durable_run_and_releases_synchronous_capacity(tmp_path):
    engine = manager(tmp_path, Provider())

    def fail_plan():
        raise RuntimeError("plan unavailable")

    engine.plan_loader = fail_plan
    run = engine.create(REQUEST, background=False)
    assert run["status"] == "paused"
    assert run["coverage"]["complete"] is False
    assert engine.workers == engine.controls == {}
    engine.plan_loader = lambda: {"wbs": []}
    assert engine.resume(run["id"], background=False)["status"] == "complete"


def test_thread_start_failure_releases_capacity_and_saved_run_remains_resumable(tmp_path, monkeypatch):
    engine = manager(tmp_path, Provider())

    def fail_start(_worker):
        raise RuntimeError("thread unavailable")

    with monkeypatch.context() as patch:
        patch.setattr(threading.Thread, "start", fail_start)
        with pytest.raises(RuntimeError, match="thread unavailable"):
            engine.create(REQUEST)
    assert engine.workers == engine.controls == {}
    run = engine.list()[0]
    assert run["status"] in {"queued", "paused"}
    assert engine.resume(run["id"], background=False)["status"] == "complete"


def test_restart_keeps_unfinished_query_checkpoint_and_resume_collects_it(tmp_path):
    provider = Provider()
    provider.fail = True
    engine = manager(tmp_path, provider)
    run = engine.create(REQUEST, background=False)
    _, _, snapshot, checkpoint = engine.store.read(run["id"])
    snapshot["status"] = "running"
    engine.store.save(run["id"], snapshot, checkpoint)
    restarted = manager(tmp_path, provider)
    _, _, recovered, recovered_cp = restarted.store.read(run["id"])
    assert recovered["status"] == "paused"
    assert recovered_cp == checkpoint
    provider.fail = False
    final = restarted.resume(run["id"], background=False)
    assert final["status"] == "complete"
    assert final["summary"]["created"] == final["summary"]["comments"] == 1
    assert restarted.workers == restarted.controls == {}


def test_synchronous_collector_also_reserves_capacity_and_releases_after_completion(tmp_path):
    provider = BlockingProvider()
    engine = manager(tmp_path, provider)
    with ThreadPoolExecutor(max_workers=1) as executor:
        collecting = executor.submit(engine.create, REQUEST, background=False)
        assert provider.entered.wait(5)
        try:
            with pytest.raises(UpstreamBusy):
                engine.create(REQUEST)
            assert len(engine.list()) == 1
        finally:
            provider.release.set()
        assert collecting.result(5)["status"] == "complete"
    assert engine.workers == engine.controls == {}
    assert engine.create(REQUEST, background=False)["status"] == "complete"


def test_resume_does_not_restart_run_that_completed_after_status_probe(tmp_path, monkeypatch):
    provider = Provider()
    engine = manager(tmp_path, provider)
    complete = engine.create(REQUEST, background=False)
    calls = len(provider.calls)
    original_owned = engine._owned

    def stale_status(run_id, *, checkpoint_fields=None):
        snapshot, cp = original_owned(run_id, checkpoint_fields=checkpoint_fields)
        if checkpoint_fields == ():
            snapshot["status"] = "running"
        return snapshot, cp

    monkeypatch.setattr(engine, "_owned", stale_status)
    resumed = engine.resume(complete["id"], background=False)
    assert resumed["status"] == "complete"
    assert resumed["metrics"]["retries"] == complete["metrics"]["retries"]
    assert len(provider.calls) == calls
    assert engine.workers == engine.controls == {}


def test_memory_pressure_rejects_new_runs_and_resume_before_checkpoint_mutation(tmp_path, monkeypatch):
    provider = Provider()
    provider.fail = True
    engine = manager(tmp_path, provider)
    paused = engine.create(REQUEST, background=False)
    before = engine.store.read(paused["id"])
    calls = len(provider.calls)

    def pressure():
        raise ResourceBusy("simulated memory pressure")

    monkeypatch.setattr(resource_pressure, "check", pressure)
    with pytest.raises(ResourceBusy):
        engine.create(REQUEST)
    with pytest.raises(ResourceBusy):
        engine.resume(paused["id"])
    assert engine.store.read(paused["id"]) == before
    assert len(engine.list()) == 1
    assert len(provider.calls) == calls
    assert engine.workers == engine.controls == {}


def test_memory_pressure_pauses_between_comment_pages_and_resume_keeps_completed_page(tmp_path, monkeypatch):
    busy = False

    def pressure():
        if busy:
            raise ResourceBusy("simulated memory pressure")

    monkeypatch.setattr(resource_pressure, "check", pressure)

    class PagedProvider(Provider):
        offsets = []

        def get_json(self, path, params=None):
            nonlocal busy
            result = super().get_json(path, params)
            if path.endswith("/comment"):
                offset = params["startAt"]
                self.offsets.append(offset)
                result.update(total=2, startAt=offset)
                result["comments"][0]["id"] = str(offset + 1)
                if offset == 0:
                    busy = True
            return result

    provider = PagedProvider()
    engine = manager(tmp_path, provider)
    paused = engine.create(REQUEST, background=False)
    assert paused["status"] == "paused" and paused["coverage"]["complete"] is False
    assert paused["errors"][0]["reason"] == "ResourceBusy"
    _, _, _, checkpoint = engine.store.read(paused["id"])
    comments = checkpoint["sources"]["P-1"]
    assert [row["id"] for row in comments["comments"]] == ["1"]
    assert comments["commentsComplete"] is False
    assert provider.offsets == [0]
    busy = False
    resumed = engine.resume(paused["id"], background=False)
    assert resumed["status"] == "complete" and resumed["coverage"]["missing"] == []
    assert resumed["summary"]["created"] == 1
    assert resumed["summary"]["comments"] == 2
    assert provider.offsets == [0, 1]
    assert engine.workers == engine.controls == {}
