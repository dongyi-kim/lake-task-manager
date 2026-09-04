"""Strict parsing and deterministic validation of Dify-generated proposals."""

from __future__ import annotations

import html
import json
import re
from collections.abc import Mapping
from decimal import Decimal, InvalidOperation
from typing import Any, TypeVar
from urllib.parse import urlsplit, urlunsplit

from pydantic import BaseModel, ValidationError

from app.agent.maxis.context_guard import (
    ContextGuardError,
    assert_safe_outbound_text,
    forbidden_authority_key,
)
from app.agent.maxis.contracts import EvidencePackV1, RemoteRoleResultV1


T = TypeVar("T", bound=BaseModel)
_URL = re.compile(r"https?://[^\s<>\"']+", re.I)
_NUMERIC_CLAIM = re.compile(
    r"(?<![A-Za-z0-9_.-])(-?\d+(?:\.\d+)?)\s*(%|건|명|일|시간|SP|점|회|개)(?![A-Za-z])",
    re.I,
)
_ISSUE_IDENTIFIER = re.compile(r"(?<![A-Z0-9])([A-Z][A-Z0-9]{1,15}-[1-9]\d*)(?![A-Z0-9])")
# Every free-text payload value is an assertion unless it is one of these structural
# or provenance controls.  An allow-list of narrative names is unsafe: a new Role field
# such as ``goal`` or ``our_context`` would otherwise bypass claim coverage until this
# module happened to be updated.
_PAYLOAD_CONTROL_FIELDS = frozenset({
    "contract", "role_id", "mode", "tier", "issue_type", "type", "kind",
    "finding_kind", "intent", "module", "playbook", "answer_depth", "field",
    "relation", "state", "status", "structure", "priority", "duedate",
    "source_id", "source_ids", "metric_id", "metric_ids", "id", "task_id",
    "depends_on", "anchor_key", "target_selector_id", "parent_ref", "parent",
    "epic", "epic_name", "key", "keys", "assignee", "components", "labels",
    "ref", "url", "target_id", "record_id", "meeting_assignment_ref",
    "outcome_refs", "outcome_contract_id", "user",
})
_GROUND_TOKEN = re.compile(r"[0-9A-Za-z가-힣]{2,}")
_DANGEROUS = (
    ("script", re.compile(r"<\s*/?\s*script\b", re.I)),
    ("active_content", re.compile(r"<\s*(?:iframe|object|embed|base|meta)\b", re.I)),
    ("event_handler", re.compile(r"\bon[a-z]{3,}\s*=", re.I)),
    ("javascript_url", re.compile(r"(?:javascript|vbscript)\s*:", re.I)),
    ("html_data_url", re.compile(r"data\s*:\s*text/html", re.I)),
    ("srcdoc", re.compile(r"\bsrcdoc\s*=", re.I)),
)
class ResponseGuardError(ValueError):
    def __init__(self, code: str, path: str = ""):
        self.code = code
        self.path = path
        message = code if not path else f"{code} ({path})"
        super().__init__(message)


def _strict_json_object(value: str) -> dict[str, Any]:
    """Parse one object while rejecting duplicate keys and non-finite numbers."""
    def object_pairs(pairs):
        result = {}
        for key, item in pairs:
            if key in result:
                raise ValueError("duplicate JSON object key")
            result[key] = item
        return result

    def invalid_constant(_value):
        raise ValueError("non-finite JSON number")

    decoded = json.loads(
        value,
        object_pairs_hook=object_pairs,
        parse_constant=invalid_constant,
    )
    if not isinstance(decoded, dict):
        raise TypeError("object required")
    return decoded


def _walk(value: Any, path: str = "$"):
    if isinstance(value, dict):
        for key, item in value.items():
            yield from _walk(item, f"{path}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            yield from _walk(item, f"{path}[{index}]")
    elif isinstance(value, str):
        yield path, value


def _collect_named_ids(value: Any, names: frozenset[str]) -> set[str]:
    found: set[str] = set()
    if isinstance(value, dict):
        for key, item in value.items():
            if key in names:
                if isinstance(item, str):
                    found.add(item)
                elif isinstance(item, (list, tuple)):
                    found.update(str(entry) for entry in item if isinstance(entry, str))
            found.update(_collect_named_ids(item, names))
    elif isinstance(value, (list, tuple)):
        for item in value:
            found.update(_collect_named_ids(item, names))
    return found


def _authority_path(value: Any, path: str = "$") -> str:
    if isinstance(value, dict):
        for key, item in value.items():
            if forbidden_authority_key(key):
                return f"{path}.{key}"
            found = _authority_path(item, f"{path}.{key}")
            if found:
                return found
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            found = _authority_path(item, f"{path}[{index}]")
            if found:
                return found
    return ""


def _canonical_url(value: str) -> str:
    candidate = value.rstrip(".,);]}")
    try:
        parsed = urlsplit(candidate)
        port = parsed.port
    except ValueError as exc:
        raise ResponseGuardError("invalid_url") from exc
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ResponseGuardError("invalid_url")
    if parsed.username or parsed.password or parsed.fragment:
        raise ResponseGuardError("unsafe_url")
    host = parsed.hostname.casefold()
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    if port is not None:
        host += f":{port}"
    return urlunsplit((parsed.scheme.casefold(), host, parsed.path or "", parsed.query, ""))


def _number_token(number: str, unit: str) -> str:
    try:
        normalized = format(Decimal(number).normalize(), "f")
    except InvalidOperation:
        normalized = number
    if "." in normalized:
        normalized = normalized.rstrip("0").rstrip(".")
    return normalized + unit.casefold()


def _pointer_segment(value: object) -> str:
    return str(value).replace("~", "~0").replace("/", "~1")


def _payload_control_field(field: str) -> bool:
    return (
        field in _PAYLOAD_CONTROL_FIELDS
        or field.endswith("_id")
        or field.endswith("_ids")
        or field.endswith("_ref")
        or field.endswith("_refs")
    )


def _payload_assertions(value: Any, path: str = "", field: str = ""):
    """Yield non-empty narrative strings and their canonical JSON Pointers."""
    if isinstance(value, dict):
        for key, item in value.items():
            segment = _pointer_segment(key)
            yield from _payload_assertions(item, f"{path}/{segment}", str(key))
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            yield from _payload_assertions(item, f"{path}/{index}", field)
    elif (
        isinstance(value, str)
        and not _payload_control_field(field)
        and value.strip()
    ):
        yield path, value


def _resolve_pointer(value: Any, pointer: str) -> Any:
    current = value
    for raw in pointer[1:].split("/"):
        segment = raw.replace("~1", "/").replace("~0", "~")
        if isinstance(current, dict):
            if segment not in current:
                raise KeyError(segment)
            current = current[segment]
        elif isinstance(current, (list, tuple)):
            if not re.fullmatch(r"0|[1-9]\d*", segment):
                raise KeyError(segment)
            index = int(segment)
            if index >= len(current):
                raise KeyError(segment)
            current = current[index]
        else:
            raise KeyError(segment)
    return current


def _normalized_assertion(value: str) -> str:
    # Editor payloads are HTML while claims are prose. Compare visible text so formatting
    # cannot bypass coverage and workflows do not have to repeat presentation markup.
    visible = html.unescape(re.sub(r"<[^>]+>", " ", str(value or "")))
    return " ".join(visible.split())


def _ground_tokens(value: str) -> set[str]:
    """Return bounded lexical tokens for deterministic qualitative grounding."""
    visible = _normalized_assertion(value).casefold()
    return set(_GROUND_TOKEN.findall(visible))


class ResponseGuard:
    """Parse exact JSON and validate only against server-owned evidence."""

    def validate(
        self,
        answer: str,
        model: type[T],
        evidence: EvidencePackV1 | None = None,
    ) -> T:
        if not isinstance(answer, str) or not answer.strip():
            raise ResponseGuardError("empty_response")
        if len(answer.encode("utf-8")) > 1024 * 1024:
            raise ResponseGuardError("response_too_large")
        try:
            _strict_json_object(answer)
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            raise ResponseGuardError("invalid_json") from exc
        try:
            # JSON mode preserves strict scalar validation while allowing JSON arrays to
            # materialize immutable tuple fields.
            result = model.model_validate_json(answer, strict=True)
        except ValidationError as exc:
            raise ResponseGuardError("contract_validation_failed") from exc
        self._validate_model(result, evidence)
        return result

    def validate_remote_role(
        self,
        answer: str,
        *,
        expected_role_id: str,
        payload_model: type[T] | Mapping[str, Any] | None = None,
        evidence: EvidencePackV1 | None = None,
        expected_knowledge_manifest_sha256: str | None = None,
    ) -> tuple[RemoteRoleResultV1, T | dict[str, Any] | None]:
        remote = self.validate(answer, RemoteRoleResultV1, evidence)
        if remote.role_id != expected_role_id:
            raise ResponseGuardError("role_mismatch", "role_id")
        if (
            expected_knowledge_manifest_sha256 is not None
            and remote.knowledge_manifest_sha256
            != str(expected_knowledge_manifest_sha256).strip().lower()
        ):
            raise ResponseGuardError(
                "knowledge_manifest_mismatch", "knowledge_manifest_sha256"
            )
        # `needs_input` and `blocked` are control outcomes, not partial Role results.
        # Never validate/apply a half-built payload under the normal Role schema.
        if remote.status != "ok":
            if remote.payload:
                raise ResponseGuardError("non_ok_payload_must_be_empty", "payload")
            return remote, None
        payload: T | dict[str, Any] | None = None
        if isinstance(payload_model, Mapping):
            try:
                from jsonschema.validators import validator_for

                schema = dict(payload_model)
                validator_type = validator_for(schema)
                validator_type.check_schema(schema)
                validator_type(schema).validate(remote.payload)
            except Exception as exc:
                # jsonschema exceptions include the rejected instance; never copy them into
                # a cross-boundary error or audit log.
                raise ResponseGuardError(
                    "payload_contract_validation_failed", "payload"
                ) from exc
            payload = dict(remote.payload)
            self._validate_data(payload, evidence)
        elif payload_model is not None:
            try:
                raw = json.dumps(remote.payload, ensure_ascii=False, separators=(",", ":"))
                payload = payload_model.model_validate_json(raw, strict=True)
            except (TypeError, ValidationError) as exc:
                raise ResponseGuardError("payload_contract_validation_failed", "payload") from exc
            self._validate_model(payload, evidence)
        return remote, payload

    def _validate_claim_citations(
        self,
        remote: RemoteRoleResultV1,
        evidence: EvidencePackV1 | None,
    ) -> None:
        """Bind factual assertions to the *cited* rows, not the whole pack.

        A global allow-list rejects invented values in arbitrary payload fields, but it is
        not sufficient citation validation: a true value from source A must not be
        presented as if source B supported it.  This pass follows each claim's citations
        and builds a claim-local assertion allow-list.
        """
        if evidence is None:
            return
        if remote.status == "ok" and remote.payload and evidence.sources and not remote.claims:
            raise ResponseGuardError("payload_claims_required", "claims")

        sources = {row.source_id: row for row in evidence.sources}
        citations = {row.citation_id: row for row in remote.citations}
        facts = tuple(evidence.facts)
        metrics = tuple(evidence.metrics)

        for index, claim in enumerate(remote.claims):
            if claim.kind not in {"fact", "inference", "recommendation"}:
                continue
            cited_source_ids = {
                citations[citation_id].source_id
                for citation_id in claim.citation_ids
                if citation_id in citations
            }
            cited_sources = [
                sources[source_id]
                for source_id in cited_source_ids
                if source_id in sources
            ]
            supporting_text = [
                text
                for source in cited_sources
                for text in (source.canonical_id, source.title, source.url, source.content)
                if text
            ]
            supporting_text.extend(
                fact.text
                for fact in facts
                if cited_source_ids.intersection(fact.source_ids)
            )

            allowed_numbers: set[str] = set()
            allowed_urls: set[str] = set()
            allowed_identifiers: set[str] = set()
            for text in supporting_text:
                allowed_numbers.update(
                    _number_token(number, unit)
                    for number, unit in _NUMERIC_CLAIM.findall(text)
                )
                allowed_urls.update(_canonical_url(url) for url in _URL.findall(text))
                allowed_identifiers.update(_ISSUE_IDENTIFIER.findall(text.upper()))
            for metric in metrics:
                if cited_source_ids.intersection(metric.source_ids):
                    allowed_numbers.add(_number_token(str(metric.value), metric.unit))
            allowed_urls.update(
                _canonical_url(source.url) for source in cited_sources if source.url
            )
            allowed_identifiers.update(
                identifier
                for source in cited_sources
                for identifier in _ISSUE_IDENTIFIER.findall(source.canonical_id.upper())
            )

            path = f"claims[{index}].text"
            for number, unit in _NUMERIC_CLAIM.findall(claim.text):
                if _number_token(number, unit) not in allowed_numbers:
                    raise ResponseGuardError("citation_assertion_mismatch", path)
            for raw_url in _URL.findall(claim.text):
                if _canonical_url(raw_url) not in allowed_urls:
                    raise ResponseGuardError("citation_assertion_mismatch", path)
            for identifier in _ISSUE_IDENTIFIER.findall(claim.text.upper()):
                if identifier not in allowed_identifiers:
                    raise ResponseGuardError("citation_assertion_mismatch", path)

            # Numbers, URLs and Jira keys alone do not ground a qualitative statement.
            # A factual claim must also reuse the cited source's actual language.  This
            # intentionally favours extractive facts at the remote trust boundary;
            # interpretations belong in an ``inference`` claim and recommendations keep
            # their separate typed kind.
            if claim.kind == "fact":
                claim_tokens = _ground_tokens(claim.text)
                support_tokens = set().union(*(
                    _ground_tokens(text) for text in supporting_text
                )) if supporting_text else set()
                overlap = claim_tokens & support_tokens
                required = max(1, (len(claim_tokens) * 7 + 9) // 10)
                if not claim_tokens or len(overlap) < required:
                    raise ResponseGuardError("citation_assertion_mismatch", path)

    def _validate_payload_claim_coverage(
        self,
        remote: RemoteRoleResultV1,
        evidence: EvidencePackV1 | None,
    ) -> None:
        """Require every narrative payload field to name a cited, matching claim."""
        if evidence is None or remote.status != "ok" or not remote.payload:
            return

        required = dict(_payload_assertions(remote.payload))
        if not required:
            return
        bindings = {row.path: row for row in remote.payload_claims}
        missing = sorted(set(required) - set(bindings))
        if missing:
            raise ResponseGuardError("payload_claim_coverage_missing", missing[0])

        claims = {row.claim_id: row for row in remote.claims}
        for pointer, binding in bindings.items():
            try:
                pointed = _resolve_pointer(remote.payload, pointer)
            except (KeyError, TypeError, ValueError) as exc:
                raise ResponseGuardError("payload_claim_path_invalid", pointer) from exc
            if not isinstance(pointed, str) or not pointed.strip():
                raise ResponseGuardError("payload_claim_path_not_text", pointer)
            linked = [claims[claim_id] for claim_id in binding.claim_ids]
            if any(
                claim.kind in {"fact", "inference", "recommendation"}
                and not claim.citation_ids
                for claim in linked
            ):
                raise ResponseGuardError("payload_claim_citation_required", pointer)
            assertion = _normalized_assertion(pointed)
            support = _normalized_assertion(" ".join(claim.text for claim in linked))
            # Exact equality supports long fields split across ordered claims.  Containment
            # lets one cited claim cover several concise fields in the same typed finding.
            if assertion != support and assertion not in support:
                raise ResponseGuardError("payload_claim_text_mismatch", pointer)

    def _validate_model(
        self,
        result: BaseModel,
        evidence: EvidencePackV1 | None,
    ) -> None:
        self._validate_data(result.model_dump(mode="json"), evidence)
        if isinstance(result, RemoteRoleResultV1):
            self._validate_claim_citations(result, evidence)
            self._validate_payload_claim_coverage(result, evidence)

    def _validate_data(
        self,
        data: dict[str, Any],
        evidence: EvidencePackV1 | None,
    ) -> None:
        authority = _authority_path(data)
        if authority:
            raise ResponseGuardError("forbidden_authority_field", authority)
        for path, text in _walk(data):
            for label, pattern in _DANGEROUS:
                if pattern.search(text):
                    raise ResponseGuardError(f"unsafe_{label}", path)
            try:
                assert_safe_outbound_text(text, path)
            except ContextGuardError as exc:
                raise ResponseGuardError("sensitive_or_identity_output", path) from exc

        source_refs = _collect_named_ids(data, frozenset({"source_id", "source_ids"}))
        metric_refs = _collect_named_ids(data, frozenset({"metric_id", "metric_ids"}))
        # Definition fields in the evidence pack itself are never passed here. For output
        # contracts, every such id is a provenance claim and must resolve server-side.
        if evidence is None:
            if source_refs or metric_refs:
                raise ResponseGuardError("evidence_required")
            allowed_urls: set[str] = set()
            allowed_numbers: set[str] = set()
        else:
            known_sources = {row.source_id for row in evidence.sources}
            unknown_sources = source_refs - known_sources
            if unknown_sources:
                raise ResponseGuardError("unknown_source_reference")
            known_metrics = {row.metric_id for row in evidence.metrics}
            unknown_metrics = metric_refs - known_metrics
            if unknown_metrics:
                raise ResponseGuardError("unknown_metric_reference")
            allowed_urls = {
                _canonical_url(row.url) for row in evidence.sources if row.url
            }
            allowed_numbers = set()
            evidence_strings = [evidence.query]
            evidence_strings.extend(row.content for row in evidence.sources)
            evidence_strings.extend(row.text for row in evidence.facts)
            for text in evidence_strings:
                allowed_numbers.update(
                    _number_token(number, unit)
                    for number, unit in _NUMERIC_CLAIM.findall(text)
                )
            for metric in evidence.metrics:
                allowed_numbers.add(_number_token(str(metric.value), metric.unit))

        for path, text in _walk(data):
            for raw_url in _URL.findall(text):
                canonical = _canonical_url(raw_url)
                if canonical not in allowed_urls:
                    raise ResponseGuardError("unverified_url", path)
            for number, unit in _NUMERIC_CLAIM.findall(text):
                if _number_token(number, unit) not in allowed_numbers:
                    raise ResponseGuardError("unverified_numeric_claim", path)


__all__ = ["ResponseGuard", "ResponseGuardError"]
