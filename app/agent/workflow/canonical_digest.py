"""Canonical, non-secret SHA-256 bindings shared by typed workflow authorities."""

from __future__ import annotations

import hashlib
import json


def digest_value(value) -> str:
    """Hash one exact JSON value using the workflow's stable wire serialization.

    This is an integrity and staleness binding, not a signature or factual authority.
    Non-JSON values and NaN/Infinity fail closed at the caller boundary.
    """
    wire = json.dumps(
        value if value is not None else {},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256(wire.encode("utf-8")).hexdigest()


__all__ = ["digest_value"]
