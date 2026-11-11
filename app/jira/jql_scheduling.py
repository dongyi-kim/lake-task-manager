"""Shared scheduling for provider pages and context-preserving query fan-out."""

from collections import deque
from concurrent.futures import CancelledError, FIRST_COMPLETED, ThreadPoolExecutor, wait
from contextlib import contextmanager
from contextvars import ContextVar, copy_context
import threading


_CANCELLED = ContextVar("jql_scheduler_cancelled", default=None)


def _is_cancelled(signal):
    return bool(signal and (signal.is_set() if hasattr(signal, "is_set") else signal()))


class BoundedQueryScheduler:
    """Share one instance per provider/client; hold slots only around physical pages.

    Query fan-out never holds a page slot, so a task can safely call another batched helper.
    FIFO page admission gives waiting interactive requests a turn between exhaustive pages.
    """

    def __init__(self, capacity=4):
        if type(capacity) is not int or not 1 <= capacity <= 8:
            raise ValueError("query concurrency must be an integer between 1 and 8")
        self.capacity = capacity
        self._condition = threading.Condition()
        self._waiting = deque()
        self._active = 0
        self._local = threading.local()

    @contextmanager
    def page_scope(self, *, parallel_capable=True, cancelled=None):
        signal = cancelled if cancelled is not None else _CANCELLED.get()
        if _is_cancelled(signal):
            raise CancelledError("Jira query cancelled before page dispatch")
        if getattr(self._local, "depth", 0):
            # Provider wrappers can nest on the same thread, but copied worker contexts
            # cannot inherit this admission and accidentally bypass the shared limit.
            self._local.depth += 1
            try:
                yield
            finally:
                self._local.depth -= 1
            return
        limit = self.capacity if parallel_capable else 1
        ticket = object()
        with self._condition:
            self._waiting.append(ticket)
            self._condition.notify_all()
            try:
                while True:
                    if _is_cancelled(signal):
                        raise CancelledError("Jira query cancelled while waiting for a page")
                    if self._waiting[0] is ticket and self._active < limit:
                        self._waiting.popleft()
                        self._active += 1
                        self._condition.notify_all()
                        break
                    self._condition.wait(timeout=0.05 if signal is not None else None)
            except BaseException:
                self._waiting.remove(ticket)
                self._condition.notify_all()
                raise
        self._local.depth = 1
        try:
            yield
        finally:
            self._local.depth = 0
            with self._condition:
                self._active -= 1
                self._condition.notify_all()

    def run_bounded(self, items, fn, *, parallel_capable=True, cancelled=None):
        """Yield task results with copied request policies and cooperative cancellation.

        Close the iterator to cancel queued/future pages. A page already in transport is
        allowed to finish; cancellation is checked again before its next physical page.
        """
        items = iter(items)
        inherited = _CANCELLED.get()
        stopped = threading.Event()
        def interrupted():
            return stopped.is_set() or _is_cancelled(cancelled) or _is_cancelled(inherited)
        def invoke(item):
            if interrupted():
                raise CancelledError("Jira query cancelled before task dispatch")
            token = _CANCELLED.set(interrupted)
            try:
                return fn(item)
            finally:
                _CANCELLED.reset(token)
        if not parallel_capable:
            for item in items:
                yield invoke(item)
            return
        pool = ThreadPoolExecutor(max_workers=self.capacity, thread_name_prefix="jql")
        pending = set()
        def submit_next():
            try:
                item = next(items)
            except StopIteration:
                return
            pending.add(pool.submit(copy_context().run, invoke, item))
        try:
            for _ in range(self.capacity):
                submit_next()
            while pending:
                done, _remaining = wait(pending, return_when=FIRST_COMPLETED)
                for future in done:
                    pending.remove(future)
                    result = future.result()
                    submit_next()
                    yield result
        finally:
            stopped.set()
            for future in pending:
                future.cancel()
            pool.shutdown(wait=True, cancel_futures=True)
