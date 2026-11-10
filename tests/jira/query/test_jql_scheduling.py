"""Shared provider scheduling bounds pages across requests and yields between pages."""

from concurrent.futures import CancelledError, ThreadPoolExecutor
from contextvars import ContextVar
import threading

import pytest

from app.jira import jql_scheduling


@pytest.mark.parametrize("capacity", [0, 9, True, 2.5])
def test_capacity_rejects_values_outside_supported_range(capacity):
    with pytest.raises(ValueError):
        jql_scheduling.BoundedQueryScheduler(capacity)


@pytest.mark.parametrize("capacity,expected", [(2, 2), (None, 4), (8, 8)])
def test_shared_page_capacity_applies_across_concurrent_requests(capacity, expected):
    scheduler = (jql_scheduling.BoundedQueryScheduler() if capacity is None
                 else jql_scheduling.BoundedQueryScheduler(capacity))
    release = threading.Event()
    active, peak, lock = 0, 0, threading.Lock()
    def page(_):
        nonlocal active, peak
        with scheduler.page_scope():
            with lock:
                active += 1
                peak = max(peak, active)
            assert release.wait(3)
            with lock:
                active -= 1
        return True
    with ThreadPoolExecutor(2) as callers:
        first = callers.submit(lambda: list(scheduler.run_bounded(range(expected + 1), page)))
        second = callers.submit(lambda: list(scheduler.run_bounded(range(expected + 1), page)))
        try:
            with scheduler._condition:
                assert scheduler._condition.wait_for(lambda: len(scheduler._waiting) >= 2, timeout=3)
        finally:
            release.set()
        assert first.result() == second.result() == [True] * (expected + 1)
    assert peak == expected


def test_serial_page_admission_is_shared_across_requests():
    scheduler = jql_scheduling.BoundedQueryScheduler()
    release = threading.Event()
    active, peak, lock = 0, 0, threading.Lock()
    def page():
        nonlocal active, peak
        with scheduler.page_scope(parallel_capable=False):
            with lock:
                active += 1
                peak = max(peak, active)
            assert release.wait(3)
            with lock:
                active -= 1
    with ThreadPoolExecutor(3) as pool:
        futures = [pool.submit(page) for _ in range(3)]
        try:
            with scheduler._condition:
                assert scheduler._condition.wait_for(lambda: len(scheduler._waiting) == 2, timeout=3)
        finally:
            release.set()
        for future in futures:
            future.result()
    assert peak == 1


def test_serial_provider_and_copied_request_contexts():
    scheduler = jql_scheduling.BoundedQueryScheduler()
    context = ContextVar("scheduler_test", default="missing")
    token = context.set("request")
    caller = threading.get_ident()
    try:
        serial = list(scheduler.run_bounded([1, 2], lambda _: threading.get_ident(), parallel_capable=False))
        assert serial == [caller, caller]
        parallel = list(scheduler.run_bounded([1, 2], lambda value: (value, context.get())))
        assert sorted(parallel) == [(1, "request"), (2, "request")]
    finally:
        context.reset(token)


def test_waiting_pages_get_capacity_before_current_query_reacquires_it():
    scheduler = jql_scheduling.BoundedQueryScheduler(1)
    queued = threading.Event()
    order = []
    def waiting():
        queued.set()
        with scheduler.page_scope():
            order.append("other-request")
    with ThreadPoolExecutor(1) as pool:
        with scheduler.page_scope():
            future = pool.submit(waiting)
            assert queued.wait(3)
            # Inspect the condition-protected queue to establish that contention actually
            # exists; the asserted behavior is the resulting public acquisition order.
            with scheduler._condition:
                assert scheduler._condition.wait_for(lambda: bool(scheduler._waiting), timeout=3)
        with scheduler.page_scope():
            order.append("next-page")
        future.result()
    assert order == ["other-request", "next-page"]


def test_cancellation_removes_waiter_and_never_dispatches_its_page():
    scheduler = jql_scheduling.BoundedQueryScheduler(1)
    cancelled, queued = threading.Event(), threading.Event()
    dispatched = []
    def wait():
        queued.set()
        with scheduler.page_scope(cancelled=cancelled):
            dispatched.append(True)
    with ThreadPoolExecutor(1) as pool:
        with scheduler.page_scope():
            future = pool.submit(wait)
            assert queued.wait(3)
            cancelled.set()
            with pytest.raises(CancelledError):
                future.result(timeout=3)
        with scheduler.page_scope():
            dispatched.append("next")
    assert dispatched == ["next"]


def test_nested_page_wrappers_and_fanout_do_not_deadlock():
    scheduler = jql_scheduling.BoundedQueryScheduler(1)
    def page(value):
        with scheduler.page_scope():
            with scheduler.page_scope():
                return value
    def outer(value):
        return list(scheduler.run_bounded([value], page))
    assert list(scheduler.run_bounded([1], outer)) == [[1]]


def test_cancellation_between_pages_prevents_following_transport():
    scheduler = jql_scheduling.BoundedQueryScheduler(1)
    cancelled = threading.Event()
    dispatched = []
    def query(_):
        with scheduler.page_scope():
            dispatched.append(1)
        cancelled.set()
        with scheduler.page_scope():
            dispatched.append(2)
    with pytest.raises(CancelledError):
        list(scheduler.run_bounded([1], query, cancelled=cancelled))
    assert dispatched == [1]
