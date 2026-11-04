"""Optional per-request sanitization before diagnostic text is shortened."""

from contextlib import contextmanager
from contextvars import ContextVar

_SANITIZER = ContextVar("agent_diagnostic_sanitizer", default=None)


@contextmanager
def diagnostic_sanitizer(sanitize):
    token = _SANITIZER.set(sanitize)
    try:
        yield
    finally:
        _SANITIZER.reset(token)


def diagnostic_text(value, limit, *, suffix=""):
    text = str(value)
    sanitize = _SANITIZER.get()
    if sanitize is not None:
        text = sanitize(text)
    return text[:limit] + (suffix if len(text) > limit else "")
