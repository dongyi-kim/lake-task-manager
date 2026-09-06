"""Canonical identifiers shared by the MAXIS profile and wire boundaries."""

from __future__ import annotations

import re


_PUBLISHED_WORKFLOW_UUID = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
    re.I,
)


def canonical_published_workflow_id(
    value: object, *, allow_empty: bool = False,
) -> str:
    """Return one lower-case canonical Dify published-workflow UUID."""

    candidate = str(value or "").strip()
    if not candidate and allow_empty:
        return ""
    if not _PUBLISHED_WORKFLOW_UUID.fullmatch(candidate):
        raise ValueError(
            "Dify published workflow version must be a canonical UUID."
        )
    return candidate.lower()


__all__ = ["canonical_published_workflow_id"]
