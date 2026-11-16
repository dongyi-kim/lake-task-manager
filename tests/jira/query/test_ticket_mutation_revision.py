"""Successful Jira writes notify idle UI clients independently of cache refreshes."""
from contextlib import closing
from types import SimpleNamespace

import pytest

from app.infra.cache import Cache
from app.jira.cache_policy import JqlCachePolicy, MutationEvent


def policy(cache, env="mock", base="https://jira.example", project="DL"):
    return JqlCachePolicy(SimpleNamespace(cache=cache, env=env, JQL_CACHE_VERSION=1, s=SimpleNamespace(
        jira_base=base, project_key=project, epic_link_field_id="epic", sp_field_id="sp")))


def test_successful_mutation_batch_publishes_once_and_failed_attempt_does_not():
    with closing(Cache(":memory:")) as cache:
        changes = policy(cache)
        initial = changes.ticket_revision()
        with pytest.raises(RuntimeError):
            with changes.mutation_batch_scope():
                raise RuntimeError("write rejected before success")
        assert changes.ticket_revision() == initial
        with pytest.raises(RuntimeError):
            with changes.mutation_batch_scope():
                changes.record_mutation(MutationEvent("transition", "DL-1", changed_fields=("status",)))
                changes.record_mutation(MutationEvent("comment", "DL-1", changed_fields=("comment",)))
                assert changes.ticket_revision() == initial
                raise RuntimeError("later batch item failed; prior successes remain")
        assert changes.ticket_revision() == initial.rsplit(":", 1)[0] + ":1"


def test_refresh_and_jql_generation_changes_do_not_publish_ticket_writes():
    with closing(Cache(":memory:")) as cache:
        changes = policy(cache)
        initial = changes.ticket_revision()
        changes.advance_generation()
        changes.apply_mutation_events(())
        changes.record_mutation(MutationEvent("refresh", "DL-1"))
        assert changes.ticket_revision() == initial


def test_ticket_revision_survives_restart_and_is_scoped_to_environment_and_jira(tmp_path):
    path = tmp_path / "cache.sqlite"
    with closing(Cache(path)) as cache:
        changes = policy(cache)
        changes.record_mutation(MutationEvent("fields", "DL-1", changed_fields=("summary",)))
        saved = changes.ticket_revision()
    with closing(Cache(path)) as cache:
        assert policy(cache).ticket_revision() == saved
        revisions = {saved, policy(cache, env="prod").ticket_revision(),
                     policy(cache, base="https://other.example").ticket_revision(),
                     policy(cache, project="OTHER").ticket_revision()}
        assert len(revisions) == 4
        assert all(token.endswith(":0") for token in revisions - {saved})
