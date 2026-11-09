"""Evaluation-only authored facts, independent of Jira/product parsers."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class Event:
    identity: str
    issue_key: str
    stream: str
    kind: str
    occurred_at: datetime
    recorded_at: datetime
    payload: dict[str, Any]
    sources: tuple[str, ...] = ()


def stamp(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Evaluation timestamps must have an explicit timezone")
    return value.isoformat()
