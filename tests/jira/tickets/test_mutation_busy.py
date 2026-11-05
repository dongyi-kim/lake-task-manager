"""Local overload preserves mutation evidence without claiming a transport outage."""
from types import SimpleNamespace

import pytest

from app.auth.base import MutationOutcomeUnknown, UpstreamBusy
from app.infra.cache import Cache
from app.jira.jira_client import JiraClient
from app.jira.mutation_recovery import ABSENT, FOUND, MutationReceiptStore, Reconciliation


def _busy(*_args, **_kwargs):
    raise UpstreamBusy("local queue full")


@pytest.fixture
def receipts():
    cache = Cache(":memory:")
    yield MutationReceiptStore(cache, "test")
    cache.close()


def _execute(store, write, reconcile, **kwargs):
    return store.execute(kind="test-write", mutation_id="busy-test-id", payload={"key": "DL-1"},
                         write=write, reconcile=reconcile, after_success=lambda _: None, **kwargs)


def test_busy_before_first_write_keeps_receipt_and_retries_after_proven_absence(receipts):
    with pytest.raises(MutationOutcomeUnknown) as error:
        _execute(receipts, _busy, lambda _: Reconciliation(ABSENT))
    assert error.value.upstream_failed is False
    assert receipts.receipt("test-write", "busy-test-id")["state"] == "pending"
    assert not receipts._inflight
    receipts.ABSENT_RETRY_GRACE_SECONDS = 0
    assert _execute(receipts, lambda: {"id": "new"}, lambda _: Reconciliation(ABSENT)) == {"id": "new"}


def test_busy_after_earlier_step_does_not_repeat_committed_write(receipts):
    remote_writes = []

    def multi_step_write():
        remote_writes.append({"id": "committed"})
        _busy()  # A later read/write was not admitted; the first step already committed.

    with pytest.raises(MutationOutcomeUnknown) as error:
        _execute(receipts, multi_step_write, lambda _: Reconciliation(FOUND, remote_writes[0]))
    assert error.value.upstream_failed is False
    assert receipts.receipt("test-write", "busy-test-id")["state"] == "pending"
    assert _execute(receipts, multi_step_write,
                    lambda _: Reconciliation(FOUND, remote_writes[0])) == {"id": "committed"}
    assert len(remote_writes) == 1


@pytest.mark.parametrize("blocked_stage", ["reconcile", "identity"])
def test_busy_during_recovery_preserves_receipt_without_opening_circuit(receipts, blocked_stage):
    with pytest.raises(MutationOutcomeUnknown):
        _execute(receipts, _busy, lambda _: Reconciliation(ABSENT))
    receipts.ABSENT_RETRY_GRACE_SECONDS = 0
    calls = []
    with pytest.raises(MutationOutcomeUnknown) as error:
        _execute(receipts, lambda: calls.append("write"),
                 _busy if blocked_stage == "reconcile" else lambda _: Reconciliation(ABSENT),
                 before_retry=_busy if blocked_stage == "identity" else None)
    assert error.value.upstream_failed is False
    assert receipts.receipt("test-write", "busy-test-id")["state"] == "pending"
    assert calls == []


@pytest.mark.parametrize("method", ["current_user", "current_user_for_authorization"])
def test_identity_overload_does_not_publish_empty_identity_or_change_auth_state(method):
    cache = Cache(":memory:")
    client = JiraClient(SimpleNamespace(jira_env="prod", jira_base="https://jira.test", project_key="DL"), cache)
    client._provider = SimpleNamespace(get_json=_busy)
    client._provider_built = True
    client.needs_login = lambda: False
    try:
        with pytest.raises(UpstreamBusy):
            cache.refresh("identity-assembly", 60, getattr(client, method))
        assert cache.get("identity-assembly") is None
        assert cache.get("myself:prod") is None
        assert not client.upstream_down()
        assert not getattr(client, "_session_dead", False)
    finally:
        cache.close()
