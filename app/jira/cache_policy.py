"""JQL cache namespace and mutation invalidation policy.

This service owns cache dependency decisions.  Jira transport and response
projection remain in ``JiraClient``; keeping this policy separate makes writes
and searches share one explicit invalidation contract.
"""

import hashlib
import threading
from contextlib import contextmanager
from dataclasses import dataclass


@dataclass(frozen=True)
class MutationEvent:
    """One successful Jira write and the cache dependencies it can affect."""

    kind: str
    key: str = ""
    changed_fields: tuple[str, ...] = ()
    parent_key: str | None = None
    epic_key: str | None = None
    related_keys: tuple[str, ...] = ()


def changed_predicate_fields(events, epic_link_field_id=None):
    """Return JQL predicate fields whose membership a mutation can change."""
    events = tuple(events or ())
    fields = {
        str(field).strip().lower()
        for event in events
        for field in (event.changed_fields or ())
        if str(field).strip()
    }
    if events:
        fields.add("updated")
    if "components" in fields:
        fields.add("component")
    if "issuetype" in fields:
        fields.add("type")
    if "duedate" in fields:
        fields.add("due")
    if fields & {"resolution", "resolutiondate"}:
        fields.add("resolved")
    if "status" in fields:
        fields.add("statuscategory")
    if "key" in fields:
        fields.add("issue")
    epic_field = str(epic_link_field_id or "").strip().lower()
    if epic_field and epic_field in fields:
        fields.update({"epic link", "epic"})
    if fields & {"summary", "description", "comment"}:
        fields.add("text")
    if any(event.kind in {"create", "create_epic"} for event in events):
        fields.update({
            "*", "key", "project", "issuetype", "status", "statuscategory",
            "created", "updated", "reporter", "resolution",
        })
    return fields


class JqlCachePolicy:
    """Own JQL generations, reverse indexes, and successful-write invalidation."""

    def __init__(self, owner):
        self.owner = owner
        self._mutation_batch = threading.local()

    @property
    def cache(self):
        return self.owner.cache

    def epoch_namespace(self):
        return f"jql:{self.owner.env}"

    def leaf_epoch_namespace(self):
        return f"jqlleaf:{self.owner.env}"

    def generation(self):
        return self.cache.epoch(self.epoch_namespace())

    def leaf_generation(self):
        return self.cache.epoch(self.leaf_epoch_namespace())

    def user_context(self):
        user = self.owner.current_user() or {}
        identity = str(user.get("id") or "anonymous")
        return hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16]

    def advance_generation(self):
        self.cache.bump_epoch(self.leaf_epoch_namespace())
        return self.cache.bump_epoch(self.epoch_namespace())

    def index_prefix(self, kind, leaf_generation, value):
        return (
            f"jqlidx{kind}:v{self.owner.JQL_CACHE_VERSION}:{self.owner.env}:"
            f"e{leaf_generation}:u{self.user_context()}:"
            f"{str(value or '').lower()}:"
        )

    def affected_leaf_keys(self, events):
        events = tuple(events or ())
        generation = self.leaf_generation()
        keys = set()
        for event in events:
            if not event.key or (event.kind != "delete" and event.changed_fields):
                continue
            prefix = self.index_prefix("issue", generation, event.key)
            keys.update(
                value
                for value in self.cache.entries_by_prefix(prefix).values()
                if isinstance(value, str)
            )

        fields = changed_predicate_fields(
            [event for event in events if event.kind != "delete"],
            getattr(self.owner.s, "epic_link_field_id", None),
        )
        for field in fields:
            prefix = self.index_prefix("field", generation, field)
            keys.update(
                value
                for value in self.cache.entries_by_prefix(prefix).values()
                if isinstance(value, str)
            )
        return keys

    def apply_mutation_events(self, events):
        events = tuple(events or ())
        if not events:
            return events

        fields = {
            str(field).lower()
            for event in events
            for field in (event.changed_fields or ())
        }
        kinds = {event.kind for event in events}
        structural = bool(kinds & {
            "create", "create_epic", "delete", "transition", "assignee", "epic_link",
        }) or bool(fields & {
            "summary", "issuetype", "status", "assignee", "reporter", "components",
            "labels", "duedate", "resolution", "resolutiondate", "priority", "parent",
            str(self.owner.s.epic_link_field_id or "").lower(),
            str(self.owner.s.sp_field_id or "").lower(),
        })
        people = bool(kinds & {
            "create", "delete", "transition", "assignee", "epic_link",
        }) or bool(fields & {
            "assignee", "components", "status", "resolution", "resolutiondate", "timespent",
        })

        if structural:
            for prefix in ("mt:", "mytasks:", "search:", "epic_cand:", "epic_options:"):
                self.cache.invalidate(prefix)
        if people:
            for prefix in ("workload:", "workload_bucket:", "activity:"):
                self.cache.invalidate(prefix)
        if structural:
            for prefix in ("wbs_build:", "vit_build:", "vit_bases:", "vit_list:"):
                self.cache.invalidate(prefix)

        self.cache.invalidate_keys(self.affected_leaf_keys(events))
        self.cache.bump_epoch(self.epoch_namespace())
        return events

    def record_mutation(self, event: MutationEvent):
        pending = getattr(self._mutation_batch, "events", None)
        if pending is not None:
            pending.append(event)
        else:
            self.apply_mutation_events((event,))
        return event

    @contextmanager
    def mutation_batch_scope(self):
        if getattr(self._mutation_batch, "events", None) is not None:
            yield
            return
        self._mutation_batch.events = []
        try:
            yield
        finally:
            events = tuple(self._mutation_batch.events)
            del self._mutation_batch.events
            self.apply_mutation_events(events)
