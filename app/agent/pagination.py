"""Provider-neutral pagination completeness and row deduplication.

Callers own provider invocation and provider-specific metadata.  This module owns the
cross-provider invariants that make a collected snapshot safe to call complete: pages must
advance, exact pages must not repeat, rows are unique, and an advertised total must match the
unique row count exactly.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Iterable


_IDENTITY_FIELDS = ("key", "id", "url", "accountId", "username")


def row_identity(row: Any) -> tuple[str, str]:
    """Return a stable, provider-neutral identity for one paginated row."""
    if isinstance(row, dict):
        for name in _IDENTITY_FIELDS:
            value = row.get(name)
            if value not in (None, ""):
                return name, str(value)
        return "row", json.dumps(row, ensure_ascii=False, sort_keys=True, default=str)
    return "value", json.dumps(row, ensure_ascii=False, sort_keys=True, default=str)


@dataclass
class PaginationAccumulator:
    """Collect unique rows while enforcing bounded, observable page progress."""

    max_pages: int = 200
    rows: list[Any] = field(default_factory=list)
    pages: int = 0
    duplicates_dropped: int = 0
    incomplete_reason: str = ""
    cursor: str = ""
    _row_identities: set[tuple[str, str]] = field(default_factory=set, repr=False)
    _page_signatures: set[tuple[tuple[str, str], ...]] = field(
        default_factory=set, repr=False
    )
    _seen_cursors: set[str] = field(default_factory=lambda: {""}, repr=False)

    def add_page(self, rows: Iterable[Any] | None) -> bool:
        """Add a page and return false when its exact non-empty signature repeats."""
        bucket = list(rows or [])
        self.pages += 1
        signature = tuple(row_identity(row) for row in bucket)
        if signature and signature in self._page_signatures:
            self.incomplete_reason = "repeated_page"
            return False
        if signature:
            self._page_signatures.add(signature)
        for row in bucket:
            identity = row_identity(row)
            if identity in self._row_identities:
                self.duplicates_dropped += 1
                continue
            self._row_identities.add(identity)
            self.rows.append(row)
        return True

    def advance(self, *, has_more: bool, next_cursor: Any, total: Any = None) -> bool:
        """Validate page progress, update the cursor, and report whether to fetch again."""
        if self.incomplete_reason:
            return False
        if not has_more:
            if isinstance(total, int) and total != len(self.rows):
                self.incomplete_reason = (
                    "returned_below_total" if len(self.rows) < total else "returned_above_total"
                )
            return False
        cursor = str(next_cursor or "")
        if not cursor:
            self.incomplete_reason = "missing_next_cursor"
            return False
        if cursor in self._seen_cursors:
            self.incomplete_reason = "cursor_cycle"
            return False
        if self.pages >= max(1, int(self.max_pages)):
            self.incomplete_reason = "page_limit"
            return False
        self._seen_cursors.add(cursor)
        self.cursor = cursor
        return True

    def metadata(self) -> dict[str, Any]:
        """Return the common bounded diagnostics consumed by all pagination callers."""
        out: dict[str, Any] = {
            "returned": len(self.rows),
            "pages": self.pages,
            "complete": not bool(self.incomplete_reason),
        }
        if self.duplicates_dropped:
            out["duplicatesDropped"] = self.duplicates_dropped
        if self.incomplete_reason:
            out.update(incomplete=True, incompleteReason=self.incomplete_reason, complete=False)
        return out
