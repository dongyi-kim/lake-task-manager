"""Stale reads remain available while background refresh work stays bounded."""

from concurrent.futures import ThreadPoolExecutor
import threading

from app.infra.cache import Cache
from app.infra.settings import get_settings
from app.jira.jira_client import JiraClient
import app.jira.jira_client as client_module
from app.infra.resource_pressure import ResourceBusy, resource_pressure


def test_refresh_backlog_is_bounded_and_overflow_can_retry():
    client = JiraClient(get_settings(), Cache(":memory:"))
    release, started = threading.Event(), threading.Event()
    completed = []

    def producer(key):
        started.set()
        assert release.wait(3)
        completed.append(key)
        return {"fresh": key}

    try:
        for index in range(20):
            key = f"swr:{index}"
            client.cache.set(key, {"stale": key}, -1)
            assert client._swr(key, lambda key=key: producer(key)) == {"stale": key}
        assert started.wait(1)
        release.set()
        client._bg_pool.shutdown(wait=True)
        assert len(completed) == 8
        assert client.cache.get_stale("swr:19") == {"stale": "swr:19"}
        # The rejected key was not left marked in flight and can be scheduled later.
        client._bg_pool = ThreadPoolExecutor(max_workers=1)
        assert client._swr("swr:19", lambda: {"retried": True}) == {"stale": "swr:19"}
        client._bg_pool.shutdown(wait=True)
        assert client.cache.get("swr:19") == {"retried": True}
    finally:
        release.set()
        if getattr(client, "_bg_pool", None):
            client._bg_pool.shutdown(wait=True)
        client.cache.close()


def test_overlapping_lazy_initialization_creates_only_one_executor(monkeypatch):
    client = JiraClient(get_settings(), Cache(":memory:"))
    entered, release, duplicate = threading.Event(), threading.Event(), threading.Event()
    pools = []

    def factory(**kwargs):
        pool = ThreadPoolExecutor(**kwargs)
        pools.append(pool)
        if len(pools) == 1:
            entered.set()
            assert release.wait(3)
        else:
            duplicate.set()
        return pool

    monkeypatch.setattr(client_module, "ThreadPoolExecutor", factory)
    first = threading.Thread(target=client._ensure_bg)
    second = threading.Thread(target=client._ensure_bg)
    try:
        first.start()
        assert entered.wait(1)
        second.start()
        duplicated = duplicate.wait(.2)
    finally:
        release.set()
        first.join(2)
        second.join(2)
        for pool in pools:
            pool.shutdown(wait=True)
        client.cache.close()
    assert not duplicated
    assert len(pools) == 1


def test_submit_failure_keeps_stale_read_and_releases_refresh_slot(monkeypatch):
    client = JiraClient(get_settings(), Cache(":memory:"))
    client.cache.set("swr:retry", {"stale": True}, -1)
    client._ensure_bg()
    submit = client._bg_pool.submit

    def reject(*args, **kwargs):
        raise RuntimeError("executor unavailable")

    try:
        monkeypatch.setattr(client._bg_pool, "submit", reject)
        assert client._swr("swr:retry", lambda: {"fresh": True}) == {"stale": True}
        monkeypatch.setattr(client._bg_pool, "submit", submit)
        assert client._swr("swr:retry", lambda: {"fresh": True}) == {"stale": True}
        client._bg_pool.shutdown(wait=True)
        assert client.cache.get("swr:retry") == {"fresh": True}
    finally:
        client._bg_pool.shutdown(wait=True)
        client.cache.close()


def test_memory_pressure_skips_refresh_without_losing_stale_data(monkeypatch):
    client = JiraClient(get_settings(), Cache(":memory:"))
    calls = []
    def busy():
        raise ResourceBusy("memory pressure")
    monkeypatch.setattr(resource_pressure, "check", busy)
    try:
        client.cache.set("swr:pressure", {"stale": True}, -1)
        assert client._swr("swr:pressure", lambda: calls.append(1) or {"fresh": True}) == {"stale": True}
        client._bg_pool.shutdown(wait=True)
        assert calls == []
        assert client.cache.get_stale("swr:pressure") == {"stale": True}
        assert not client._bg_inflight
        monkeypatch.setattr(resource_pressure, "check", lambda: None)
        client._bg_pool = ThreadPoolExecutor(max_workers=1)
        assert client._swr("swr:pressure", lambda: calls.append(1) or {"fresh": True}) == {"stale": True}
        client._bg_pool.shutdown(wait=True)
        assert calls == [1]
        assert client.cache.get("swr:pressure") == {"fresh": True}
    finally:
        client._bg_pool.shutdown(wait=True)
        client.cache.close()


def test_reprime_shares_bounded_background_admission_and_is_best_effort(monkeypatch):
    client = JiraClient(get_settings(), Cache(":memory:"))
    entered, release = threading.Event(), threading.Event()
    reads = []
    def get_issue(key):
        entered.set()
        assert release.wait(3)
        reads.append(key)
    monkeypatch.setattr(client, "get_issue", get_issue)
    monkeypatch.setattr(client, "_get_issue_view", lambda key: None)
    monkeypatch.setattr(client, "_issue_comments", lambda key: None)
    try:
        client._reprime("DL-1", comments=True)
        assert entered.wait(1)
        for _ in range(50):
            client._reprime("DL-1", comments=True)
        for i in range(2, 21):
            client._reprime(f"DL-{i}", comments=True)
        release.set()
        client._bg_pool.shutdown(wait=True)
        assert reads == [f"DL-{i}" for i in range(1, 9)]
        # Submission failure after a successful Jira mutation must not turn the
        # write into an apparent error, nor leave an occupied refresh lease.
        client._reprime("DL-21", comments=True)
        assert not client._bg_inflight
    finally:
        release.set()
        client._bg_pool.shutdown(wait=True)
        client.cache.close()
