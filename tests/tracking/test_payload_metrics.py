"""Byte accounting must not allocate a second copy of a large source page."""
from datetime import datetime
import tracemalloc
from types import SimpleNamespace

import pytest

from app.jira.jql_scheduling import BoundedQueryScheduler
from app.tracking import storage
from app.tracking.source import TrackingSource


@pytest.mark.parametrize("value", [None, [], {"body": '한글 😀\n"\\\t', "empty": None},
    {"when": datetime(2026, 11, 17), "values": [True, False, 1, 1.5]},
    {None: "null key", True: "bool key", 2: "integer", 1.25: "float"},
    ("", {}, (), float("nan"), float("inf"), float("-inf")),
    {"crossing": "x" * 16383 + '\\"\n한🙂' + "x" * 16384}])
def test_encoded_size_matches_stored_utf8_json(value):
    assert storage.encoded_size(value) == len(storage.encode(value).encode("utf-8"))


def test_single_enormous_body_accounting_has_bounded_scratch():
    payload = {"body": "한🙂" * (2 * 1024 * 1024)}
    expected = len(storage.encode(payload).encode("utf-8"))
    tracemalloc.start()
    try:
        actual = storage.encoded_size(payload)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert actual == expected
    assert peak < 1024 * 1024, f"One body allocated {peak} accounting bytes"


@pytest.mark.parametrize("kind", ["list", "dict", "invalid_key"])
def test_encoded_size_rejects_unserializable_containers_like_storage(kind):
    if kind == "list":
        value = []
        value.append(value)
        expected = ValueError
    elif kind == "dict":
        value = {}
        value["self"] = value
        expected = ValueError
    else:
        value, expected = {(1, 2): "invalid key"}, TypeError
    with pytest.raises(expected):
        storage.encode(value)
    with pytest.raises(expected):
        storage.encoded_size(value)


def test_encoded_size_permits_shared_containers_without_treating_them_as_cycles():
    shared = ["한🙂"]
    value = [shared, shared]
    assert storage.encoded_size(value) == len(storage.encode(value).encode("utf-8"))


def test_large_page_accounting_uses_less_than_one_extra_page():
    # Allocate the source before tracing: the test measures accounting overhead.
    payload = {"issues": [{"key": f"P-{i}", "body": f"{i}:" + "한" * 32768}
                          for i in range(64)]}
    expected = len(storage.encode(payload).encode("utf-8"))
    provider = SimpleNamespace(supports_parallel=False, get_json=lambda *_a, **_k: payload)
    client = SimpleNamespace(provider=provider, _query_scheduler=BoundedQueryScheduler())
    metrics = {"restRequests": 0, "requestBytes": 0, "responseBytes": 0}
    source = TrackingSource(client, None, "large", metrics, lambda: None, lambda: None)

    tracemalloc.start()
    try:
        result = source.read("/rest/api/2/search", {"maxResults": 100})
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    assert result is payload
    assert metrics["responseBytes"] == expected
    assert metrics["restRequests"] == 1
    assert peak < expected // 2, f"Accounting allocated {peak} bytes for {expected}-byte page"
