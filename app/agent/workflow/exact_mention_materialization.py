"""Current-turn authority for bounded exact Jira mention materialization.

This module owns *read context* only.  It never emits or amends mutation targets.  The
human-authored key set is bound to one QueryPlan execution and to exact projected ticket
details by a process-local signature, so stale materialized state cannot make an incomplete
acquisition look complete.
"""

from __future__ import annotations

import hashlib
import hmac
import math
import os
import re
from html import unescape
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, model_validator

from app.agent.workflow.canonical_digest import digest_value
from app.agent.workflow.contracts import ContinuationContract, QueryPlan
from app.agent.workflow.state import last_user_text, request_text


EXACT_MENTION_ARTIFACT = "exact-mention-materialization.v1"
EXACT_MENTION_AUTHORITY = "query-runner.exact-human-read.v1"
MAX_EXACT_MENTION_KEYS = 8

_KEY_RE = re.compile(
    r"(?<![A-Za-z0-9-])([A-Za-z][A-Za-z0-9]*-\d+)(?![A-Za-z0-9-])")
_DIGEST_RE = r"^[0-9a-f]{64}$"
_SIGNING_KEY = os.urandom(32)


def _ordered_keys(value: str) -> tuple[str, ...]:
    seen: set[str] = set()
    rows: list[str] = []
    for match in _KEY_RE.finditer(str(value or "")):
        key = match.group(1).upper()
        if key not in seen:
            seen.add(key)
            rows.append(key)
    return tuple(rows)


class _StrictFrozen(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class ExactMentionRequestV1(_StrictFrozen):
    contract: Literal["exact-mention-request.v1"] = "exact-mention-request.v1"
    authority: Literal["current-human", "validated-continuation"]
    source_digest: str = Field(pattern=_DIGEST_RE)
    keys: tuple[Annotated[str, Field(pattern=r"^[A-Z][A-Z0-9]*-\d+$")], ...] = Field(
        min_length=1, max_length=MAX_EXACT_MENTION_KEYS)

    @model_validator(mode="after")
    def unique_keys(self):
        if len(self.keys) != len(set(self.keys)):
            raise ValueError("exact mention keys must be unique")
        return self


class ExactMentionOutcomeV1(_StrictFrozen):
    key: str = Field(pattern=r"^[A-Z][A-Z0-9]*-\d+$")
    status: Literal["success", "error"]
    returned_key: str = Field(default="", max_length=32)
    detail_digest: str = Field(default="", max_length=64)
    error_kind: Literal[
        "", "not_found", "permission", "provider_error", "wrong_key", "partial",
        "duplicate", "comments_incomplete", "invalid_detail",
    ] = ""

    @model_validator(mode="after")
    def exact_terminal_shape(self):
        if self.status == "success":
            if (self.returned_key != self.key
                    or not re.fullmatch(_DIGEST_RE, self.detail_digest)
                    or self.error_kind):
                raise ValueError("success must bind the exact returned key and detail digest")
        elif self.returned_key or self.detail_digest or not self.error_kind:
            raise ValueError("error must carry only a bounded error kind")
        return self


class _ExactTicketDetailV1(BaseModel):
    """Minimal provider-neutral detail shape shared by receipt issuer and consumer."""

    model_config = ConfigDict(extra="allow", strict=True)
    key: str = Field(pattern=r"^[A-Z][A-Z0-9]*-\d+$", max_length=32)
    summary: str = Field(min_length=1, max_length=1000)
    status: str = Field(min_length=1, max_length=240)
    comments: list[dict] = Field(max_length=8)
    error: None = None
    comments_error: None = None

    @model_validator(mode="after")
    def nonblank_canonical_scalars(self):
        if not self.summary.strip() or not self.status.strip():
            raise ValueError("summary and status must be nonblank strings")
        return self


_DETAIL_ADAPTER = TypeAdapter(_ExactTicketDetailV1)


def validate_and_digest_exact_ticket_detail(
    value,
    expected_key: str,
) -> tuple[dict, str] | None:
    """Strictly validate one exact detail and prove finite canonical JSON serialization."""
    try:
        model = _DETAIL_ADAPTER.validate_python(value, strict=True)
        if model.key != str(expected_key or "").strip().upper():
            return None
        canonical = model.model_dump(mode="python")
        return canonical, digest_value(canonical)
    except (TypeError, ValueError):
        return None


def exact_ticket_detail_prompt_complete(value, *, comments_proven: bool) -> bool:
    """Prove the bounded QueryRunner ticket projection loses no supported observation."""
    if not isinstance(value, dict):
        return False
    if validate_and_digest_exact_ticket_detail(value, str(value.get("key") or "")) is None:
        return False
    supported = {
        "key", "type", "issuetype", "status", "summary", "title", "done",
        "parentKey", "epicKey", "assignee", "priority", "duedate", "sp",
        "created", "updated", "resolution", "url", "self", "components", "labels",
        "description", "comments", "error", "comments_error",
    }
    if any(key not in supported and raw not in (None, "", [], {})
           for key, raw in value.items()):
        return False

    def plain(raw) -> str:
        return re.sub(
            r"\s+", " ", re.sub(r"<[^>]+>", " ", unescape(str(raw or ""))),
        ).strip()

    limits = {
        "type": 80, "issuetype": 80, "status": 100, "summary": 240,
        "title": 240, "parentKey": 40, "epicKey": 40, "assignee": 100,
        "priority": 80, "duedate": 40, "created": 40, "updated": 40,
        "resolution": 80, "url": 500, "self": 500, "description": 360,
    }
    for field, limit in limits.items():
        raw = value.get(field)
        if raw in (None, ""):
            continue
        if type(raw) is not str or len(plain(raw)) > limit:
            return False
    if "done" in value and type(value.get("done")) is not bool:
        return False
    if value.get("sp") not in (None, ""):
        sp = value.get("sp")
        if type(sp) in (int, float):
            if not math.isfinite(float(sp)):
                return False
        elif type(sp) is not str or len(plain(sp)) > 40:
            return False
    for field in ("components", "labels"):
        raw = value.get(field)
        if raw in (None, []):
            continue
        if (not isinstance(raw, list) or len(raw) > 8
                or any(type(item) is not str or len(plain(item)) > 80 for item in raw)):
            return False
    if comments_proven:
        return True
    comments = value.get("comments") or []
    if not isinstance(comments, list) or len(comments) > 2:
        return False
    for row in comments:
        if not isinstance(row, dict):
            return False
        for field, limit in (("author", 100), ("created", 40), ("body", 180)):
            raw = row.get(field)
            if raw not in (None, "") and (type(raw) is not str or len(plain(raw)) > limit):
                return False
    return True


class ExactMentionReceiptV1(_StrictFrozen):
    contract: Literal["exact-mention-receipt.v1"] = "exact-mention-receipt.v1"
    authority: Literal["query-runner.exact-human-read.v1"] = EXACT_MENTION_AUTHORITY
    attempt_digest: str = Field(pattern=_DIGEST_RE)
    thread_digest: str = Field(pattern=_DIGEST_RE)
    request_digest: str = Field(pattern=_DIGEST_RE)
    query_plan_digest: str = Field(pattern=_DIGEST_RE)
    comment_evidence_digest: str = Field(pattern=_DIGEST_RE)
    requested: tuple[Annotated[str, Field(pattern=r"^[A-Z][A-Z0-9]*-\d+$")], ...] = Field(
        min_length=1, max_length=MAX_EXACT_MENTION_KEYS)
    outcomes: tuple[ExactMentionOutcomeV1, ...] = Field(
        min_length=1, max_length=MAX_EXACT_MENTION_KEYS)
    complete: bool
    signature: str = Field(pattern=_DIGEST_RE)

    @model_validator(mode="after")
    def exact_coverage(self):
        if len(self.requested) != len(set(self.requested)):
            raise ValueError("requested keys must be unique")
        outcome_keys = tuple(row.key for row in self.outcomes)
        if outcome_keys != self.requested or len(outcome_keys) != len(set(outcome_keys)):
            raise ValueError("outcomes must cover requested keys exactly and in order")
        if self.complete is not all(row.status == "success" for row in self.outcomes):
            raise ValueError("complete must equal terminal success coverage")
        return self


_RECEIPT_ADAPTER = TypeAdapter(ExactMentionReceiptV1)


def exact_mention_request(state) -> ExactMentionRequestV1 | None:
    """Return the only code-owned read-key authority for this turn.

    Explicit keys in the latest HumanMessage always win.  A keyless continuation may reuse
    all exact read keys from the validated frozen human root.  Its mutation ``target_keys``
    are a separate authority and must never narrow or expand this context-only set.  Model-
    authored ``mentioned_keys`` and RequestPlan prose are intentionally ignored.
    """
    current = last_user_text(state)
    keys = _ordered_keys(current)
    authority: Literal["current-human", "validated-continuation"] = "current-human"
    source = current
    if not keys:
        if state.get("turn_continuation") is not True:
            return None
        try:
            contract = ContinuationContract.model_validate(
                state.get("continuation_contract"), strict=True)
        except Exception:
            return None
        root = request_text(state)
        if not root or contract.root_request != root:
            return None
        # ``target_keys`` is mutation authority and must not narrow/expand this read-only
        # context set.  Validating the contract proves only that this is the frozen root.
        keys = _ordered_keys(root)
        authority = "validated-continuation"
        source = root
    if not keys or len(keys) > MAX_EXACT_MENTION_KEYS:
        return None
    try:
        return ExactMentionRequestV1(
            authority=authority,
            source_digest=digest_value({"authority": authority, "text": source, "keys": keys}),
            keys=keys,
        )
    except Exception:
        return None


def exact_where_key(where: str, source: str) -> str:
    """Parse one positive singleton key predicate; reject every broader expression."""
    field = r"(?:issueKey|key)" if source == "comments" else r"key"
    value = str(where or "").strip()
    # Peel harmless wrapping parentheses only when they wrap the whole expression.
    while value.startswith("(") and value.endswith(")"):
        depth = 0
        wraps = True
        for index, char in enumerate(value):
            depth += char == "("
            depth -= char == ")"
            if depth == 0 and index != len(value) - 1:
                wraps = False
                break
        if not wraps:
            break
        value = value[1:-1].strip()
    match = re.fullmatch(
        rf"(?i){field}\s*=\s*['\"]?([A-Z][A-Z0-9]*-\d+)['\"]?", value)
    return match.group(1).upper() if match else ""


def normalize_exact_key_echo(state, query: dict) -> bool:
    """Remove a lexical key echo only when exact human and structural identities agree."""
    source = str(query.get("source") or "")
    if source not in {"jira", "comments"}:
        return False
    authority = exact_mention_request(state)
    if authority is None:
        return False
    structural = exact_where_key(query.get("where") or "", source)
    lexical = _ordered_keys(query.get("query") or "")
    leftover = _KEY_RE.sub("", str(query.get("query") or ""))
    if (not structural or lexical != (structural,)
            or re.sub(r"[\s,;/]+", "", leftover)
            or structural not in authority.keys):
        return False
    query["query"] = ""
    return True


def exact_mention_plan_keys(request: ExactMentionRequestV1, query_plan) -> tuple[str, ...] | None:
    """Bind the human read set to exact singleton Jira/comment QuerySpecs."""
    try:
        plan = QueryPlan.model_validate(query_plan, strict=True)
    except Exception:
        return None
    seen: set[str] = set()
    seen_reads: set[tuple[str, str]] = set()
    ordered: list[str] = []
    for row in plan.queries:
        if row.source not in {"jira", "comments"}:
            continue
        key = exact_where_key(row.where, row.source)
        if not key or str(row.query or "").strip():
            return None
        identity = (row.source, key)
        if identity in seen_reads:
            return None
        seen_reads.add(identity)
        if key not in seen:
            seen.add(key)
            ordered.append(key)
    if seen != set(request.keys):
        return None
    return tuple(key for key in request.keys if key in seen)


def exact_comment_all_keys(query_plan) -> frozenset[str]:
    try:
        plan = QueryPlan.model_validate(query_plan, strict=True)
    except Exception:
        return frozenset()
    return frozenset(
        key for row in plan.queries
        if row.source == "comments" and row.completeness == "all"
        for key in [exact_where_key(row.where, "comments")] if key and not row.query.strip()
    )


def exact_comment_evidence_digest(query_plan, query_results) -> str:
    """Bind every planned all-comment compact row and coverage ledger to the receipt."""
    try:
        plan = QueryPlan.model_validate(query_plan, strict=True)
        specs = [(row.id, exact_where_key(row.where, "comments"))
                 for row in plan.queries
                 if row.source == "comments" and row.completeness == "all"]
        if (any(not key for _identity, key in specs)
                or any(row.query.strip() for row in plan.queries
                       if row.source == "comments" and row.completeness == "all")):
            return ""
        if len(specs) != len({identity for identity, _key in specs}):
            return ""
        result_rows = [row for row in (query_results or []) if isinstance(row, dict)]
        canonical = []
        for identity, key in specs:
            matches = [row for row in result_rows
                       if str(row.get("id") or "") == identity
                       and row.get("source") == "comments"
                       and isinstance(row.get("result"), dict)]
            if len(matches) != 1:
                return ""
            result = matches[0]["result"]
            # Bind only the evidence projection used by the completeness gate.  QueryRunner
            # may attach exact ticket details to this row after receipt issuance; those have
            # their own per-key digest and must not make a comments-only plan self-stale.
            comments = []
            for raw in result.get("comments") or []:
                if not isinstance(raw, dict):
                    return ""
                comments.append({field: raw.get(field) for field in (
                    "id", "ticketKey", "ticketSummary", "author", "date", "snippet",
                    "bodyTruncated",
                ) if field in raw})
            evidence = {field: result.get(field) for field in (
                "returned", "complete", "incomplete", "incompleteReason", "error",
                "contextTruncated", "candidateCoverage", "commentCoverage",
            ) if field in result}
            evidence["comments"] = comments
            canonical.append({"id": identity, "key": key, "result": evidence})
        return digest_value({"contract": "exact-comment-evidence.v1", "rows": canonical})
    except (TypeError, ValueError):
        return ""


def _unsigned(receipt: ExactMentionReceiptV1 | dict) -> dict:
    value = receipt.model_dump(mode="json") if isinstance(receipt, ExactMentionReceiptV1) \
        else dict(receipt)
    value.pop("signature", None)
    return value


def _signature(value: dict) -> str:
    return hmac.new(_SIGNING_KEY, digest_value(value).encode("ascii"), hashlib.sha256).hexdigest()


def issue_exact_mention_receipt(
    request: ExactMentionRequestV1 | None,
    query_plan,
    outcomes,
    *,
    thread_id: str,
    attempt_id: str = "",
    query_results=(),
) -> dict | None:
    """Mint a signed receipt only for an exact current read plan/outcome ledger."""
    thread = str(thread_id or "").strip()
    attempt = str(attempt_id or "").strip()
    if (request is None or not thread or not attempt
            or exact_mention_plan_keys(request, query_plan) is None):
        return None
    try:
        terminal = tuple(
            row if isinstance(row, ExactMentionOutcomeV1)
            else ExactMentionOutcomeV1.model_validate(row, strict=True)
            for row in outcomes
        )
        comment_digest = exact_comment_evidence_digest(query_plan, query_results)
        if not comment_digest:
            return None
        unsigned = {
            "contract": "exact-mention-receipt.v1",
            "authority": EXACT_MENTION_AUTHORITY,
            "attempt_digest": digest_value({"attempt_id": attempt}),
            "thread_digest": digest_value({"thread_id": thread}),
            "request_digest": digest_value(request.model_dump(mode="json")),
            "query_plan_digest": digest_value(query_plan),
            "comment_evidence_digest": comment_digest,
            "requested": request.keys,
            "outcomes": terminal,
            "complete": all(row.status == "success" for row in terminal),
        }
        receipt = ExactMentionReceiptV1(
            **unsigned, signature=_signature({
                **unsigned,
                "requested": list(request.keys),
                "outcomes": [row.model_dump(mode="json") for row in terminal],
            }),
        )
        return receipt.model_dump(mode="python")
    except Exception:
        return None


def _normalize_wire(value) -> dict | None:
    if not isinstance(value, dict):
        return None
    row = dict(value)
    if isinstance(row.get("requested"), list):
        row["requested"] = tuple(row["requested"])
    if isinstance(row.get("outcomes"), list):
        row["outcomes"] = tuple(row["outcomes"])
    return row


def parse_exact_mention_receipt(value, state) -> ExactMentionReceiptV1 | None:
    """Validate signature plus current thread, human request and exact QueryPlan binding."""
    try:
        wire = _normalize_wire(value)
        receipt = _RECEIPT_ADAPTER.validate_python(wire, strict=True)
        supplied = receipt.signature
        if not hmac.compare_digest(supplied, _signature(_unsigned(receipt))):
            return None
        request = exact_mention_request(state)
        if request is None or exact_mention_plan_keys(request, state.get("query_plan")) is None:
            return None
        if receipt.requested != request.keys:
            return None
        if receipt.thread_digest != digest_value({"thread_id": str(state.get("thread_id") or "").strip()}):
            return None
        if receipt.attempt_digest != digest_value({
                "attempt_id": str(state.get("turn_attempt_id") or "").strip()}):
            return None
        if receipt.request_digest != digest_value(request.model_dump(mode="json")):
            return None
        if receipt.query_plan_digest != digest_value(state.get("query_plan")):
            return None
        if receipt.comment_evidence_digest != exact_comment_evidence_digest(
                state.get("query_plan"), state.get("query_results")):
            return None
        return receipt
    except Exception:
        return None


def verified_exact_mention_details(state) -> dict[str, dict]:
    """Return only receipt-bound current detail projections, never merged legacy state."""
    artifact = (state.get("query_artifacts") or {}).get(EXACT_MENTION_ARTIFACT)
    if not isinstance(artifact, dict):
        return {}
    receipt = parse_exact_mention_receipt(artifact.get("receipt"), state)
    details = artifact.get("details")
    if receipt is None or not receipt.complete or not isinstance(details, list):
        return {}
    by_key: dict[str, dict] = {}
    for row in details:
        if not isinstance(row, dict):
            return {}
        key = str(row.get("key") or "").strip().upper()
        if key in by_key:
            return {}
        by_key[key] = row
    try:
        expected = {row.key: row.detail_digest for row in receipt.outcomes}
        if set(by_key) != set(expected):
            return {}
        for key in expected:
            validated = validate_and_digest_exact_ticket_detail(by_key[key], key)
            if validated is None or validated[1] != expected[key]:
                return {}
    except Exception:
        return {}
    return by_key


def verified_exact_mention_keys(state) -> set[str]:
    return set(verified_exact_mention_details(state))


__all__ = [
    "EXACT_MENTION_ARTIFACT", "EXACT_MENTION_AUTHORITY", "ExactMentionOutcomeV1",
    "ExactMentionReceiptV1", "ExactMentionRequestV1", "exact_comment_all_keys",
    "exact_comment_evidence_digest",
    "exact_mention_plan_keys", "exact_mention_request", "exact_where_key",
    "issue_exact_mention_receipt",
    "normalize_exact_key_echo", "parse_exact_mention_receipt",
    "exact_ticket_detail_prompt_complete",
    "validate_and_digest_exact_ticket_detail",
    "verified_exact_mention_details", "verified_exact_mention_keys",
]
