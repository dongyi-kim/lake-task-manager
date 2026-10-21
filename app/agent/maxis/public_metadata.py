"""Fail-closed checks for non-secret MAXIS deployment metadata.

Public version, label, and environment fields are intentionally persisted and
returned by status APIs.  A value that *looks* like a credential must therefore be
rejected even when its field name is harmless.  Error messages never echo the value.
"""

from __future__ import annotations

import re


_CREDENTIAL_PATTERNS = (
    # Dify application API keys are issued with an ``app-`` prefix.  Keep a minimum
    # token length so ordinary labels such as ``app-dev`` remain valid metadata.
    re.compile(r"(?<![A-Za-z0-9_-])app-[A-Za-z0-9_-]{20,}(?![A-Za-z0-9_-])", re.I),
    re.compile(r"(?<![A-Za-z0-9_-])sk-(?:proj-)?[A-Za-z0-9_-]{20,}(?![A-Za-z0-9_-])", re.I),
    re.compile(r"(?:^|\s)Bearer\s+[A-Za-z0-9._~+/=-]{8,}(?:$|\s)", re.I),
    # JWTs are three base64url segments.  The minimum segment lengths avoid
    # classifying dotted semantic versions as tokens.
    re.compile(
        r"(?<![A-Za-z0-9_-])[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\."
        r"[A-Za-z0-9_-]{10,}(?![A-Za-z0-9_-])"
    ),
)


def credential_shaped_public_text(value: object) -> bool:
    """Return whether a public metadata value resembles bearer credential material."""

    text = str(value or "").strip()
    return bool(text and any(pattern.search(text) for pattern in _CREDENTIAL_PATTERNS))


def require_safe_public_text(value: object, label: str) -> str:
    """Return stripped text or reject credential-shaped material without echoing it."""

    text = str(value or "").strip()
    if credential_shaped_public_text(text):
        raise ValueError(f"{label} cannot contain credential-shaped material.")
    return text


__all__ = ["credential_shaped_public_text", "require_safe_public_text"]
