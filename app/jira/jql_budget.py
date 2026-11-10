"""Request-wide accounting for unique physical JQL, independent of pages/retries."""

from contextlib import contextmanager
from contextvars import ContextVar, copy_context
from functools import wraps
from inspect import isgeneratorfunction, signature
import threading

from app.jira.jql import JqlUnsupported, tokenize


_BUDGET = ContextVar("jql_physical_budget", default=None)


def current_budget():
    return _BUDGET.get()


def budgeted_query(fn):
    """Share nested request budgets; keep streaming state in an isolated context."""
    parameters = signature(fn)
    def limit(args, kwargs):
        bound = parameters.bind_partial(*args, **kwargs)
        bound.apply_defaults()
        value = bound.arguments.get("max_parts", 64)
        validate_max_parts(value)
        return value
    if isgeneratorfunction(fn):
        @wraps(fn)
        def iterator(*args, **kwargs):
            max_parts = limit(args, kwargs)
            context = copy_context()
            # This context belongs exclusively to the iterator. No token survives a yield
            # in its caller's context, and all other policy/collector variables are copied.
            context.run(_BUDGET.set, current_budget() or PhysicalQueryBudget(max_parts))
            return _ContextIterator(context, context.run(fn, *args, **kwargs))
        return iterator
    @wraps(fn)
    def call(*args, **kwargs):
        with physical_query_scope(limit(args, kwargs)):
            return fn(*args, **kwargs)
    return call


class _ContextIterator:
    def __init__(self, context, iterator):
        self._context = context
        self._iterator = iterator

    def __iter__(self):
        return self

    def __next__(self):
        return self._context.run(next, self._iterator)

    def send(self, value):
        return self._context.run(self._iterator.send, value)

    def throw(self, *args):
        return self._context.run(self._iterator.throw, *args)

    def close(self):
        return self._context.run(self._iterator.close)


class PhysicalQueryBudgetExceeded(ValueError):
    """A complete result would require a query beyond the request's allowance."""


def validate_max_parts(value):
    if type(value) is not int or not 1 <= value <= 64:
        raise ValueError("max_parts must be an integer between 1 and 64")


def query_identity(query):
    """Ignore token spacing while preserving literals, case and query semantics."""
    try:
        return tuple((token.kind, token.value) for token in tokenize(str(query or "").strip()))
    except JqlUnsupported:
        # Unsupported legacy syntax may still be sent verbatim. Never collapse its values.
        return (("RAW", str(query or "")),)


class PhysicalQueryBudget:
    def __init__(self, max_parts):
        validate_max_parts(max_parts)
        self.max_parts = max_parts
        self._queries = set()
        self._lock = threading.Lock()

    def claim(self, query):
        identity = query_identity(query)
        with self._lock:
            if identity in self._queries:
                return False
            if len(self._queries) >= self.max_parts:
                raise PhysicalQueryBudgetExceeded(
                    f"JQL physical-query budget ({self.max_parts}) exceeded; result is incomplete")
            self._queries.add(identity)
            return True

    def diagnostics(self):
        with self._lock:
            return {"maxParts": self.max_parts, "physicalQueryCount": len(self._queries)}

    @property
    def remaining(self):
        with self._lock:
            return self.max_parts - len(self._queries)

    def can_fit(self, queries):
        identities = {query_identity(query) for query in queries}
        with self._lock:
            return len(self._queries | identities) <= self.max_parts


@contextmanager
def physical_query_scope(max_parts=64):
    """Copied worker contexts share the same lock and cannot reset their parent's budget."""
    validate_max_parts(max_parts)
    existing = _BUDGET.get()
    if existing is not None:
        yield existing
        return
    budget = PhysicalQueryBudget(max_parts)
    token = _BUDGET.set(budget)
    try:
        yield budget
    finally:
        _BUDGET.reset(token)


def claim_physical_query(query):
    """Claim immediately before transport; duplicate pages/retries consume no extra part."""
    budget = _BUDGET.get()
    return budget.claim(query) if budget is not None else False
