"""Shared completeness and measurement contract for deterministic LLM bypasses.

A typed fast path is allowed only when every caller-declared authority check is true.
The helper deliberately does not infer completeness from prose: each owning workflow
must name the structured contract and the exact checks that prove its data complete.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from app.agent.workflow.state import AgentState, note


TYPED_FAST_PATH_CONTRACT = "typed-fast-path.v1"


@dataclass(frozen=True)
class TypedFastPathDecision:
    """Immutable result of one structured fast-path completeness check."""

    path_id: str
    authority: str
    complete: bool
    missing: tuple[str, ...]
    saved_calls: int

    def as_dict(self) -> dict:
        return {
            "contract": TYPED_FAST_PATH_CONTRACT,
            "id": self.path_id,
            "complete": self.complete,
            "authority": self.authority,
            "savedCalls": self.saved_calls,
            "missing": list(self.missing),
        }


def evaluate_typed_fast_path(
    path_id: str,
    *,
    authority: str,
    checks: Mapping[str, bool],
    saved_calls: int = 1,
) -> TypedFastPathDecision:
    """Evaluate named typed checks and fail closed when any check is false.

    ``saved_calls`` is the number skipped only when the fast path is actually complete.
    An incomplete decision always records zero savings so fallback attempts cannot inflate
    efficiency measurements.
    """

    path_id = str(path_id or "").strip()
    authority = str(authority or "").strip()
    normalized = {str(name or "").strip(): value is True
                  for name, value in dict(checks or {}).items()}
    if not path_id:
        raise ValueError("typed fast path id is required")
    if not authority:
        raise ValueError("typed fast path authority is required")
    if not normalized or any(not name for name in normalized):
        raise ValueError("typed fast path requires named completeness checks")
    if not isinstance(saved_calls, int) or isinstance(saved_calls, bool) or saved_calls < 1:
        raise ValueError("typed fast path saved_calls must be a positive integer")
    missing = tuple(sorted(name for name, passed in normalized.items() if not passed))
    complete = not missing
    return TypedFastPathDecision(
        path_id=path_id,
        authority=authority,
        complete=complete,
        missing=missing,
        saved_calls=saved_calls if complete else 0,
    )


def typed_fast_path_note(
    state: AgentState,
    node: str,
    text: str,
    decision: TypedFastPathDecision,
) -> list[dict]:
    """Return one normal trace row with a machine-readable fast-path sidecar."""

    row = note(state, node, text)[0]
    row["fastPath"] = decision.as_dict()
    return [row]
