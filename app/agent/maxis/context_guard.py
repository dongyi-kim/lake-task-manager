"""Fail-closed construction of bounded, pseudonymous MAXIS context packs."""

from __future__ import annotations

import hashlib
import hmac
import json
import math
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from types import MappingProxyType
from itertools import islice
from typing import Any, Iterable, Mapping
from urllib.parse import parse_qsl, urlsplit

from pydantic import ValidationError

from app.agent.maxis.contracts import (
    canonical_payload_schema,
    EvidenceFactV1,
    EvidenceMetricV1,
    EvidencePackV1,
    EvidenceScopeV1,
    EvidenceSourceV1,
    RoleContextV1,
)


_SOURCE_KINDS = frozenset({"jira", "confluence", "bitbucket", "knowledge", "ltm"})
_EMAIL = re.compile(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b")
_USER_ID = re.compile(
    r"(?i)(?<![A-Za-z0-9_])(?:sk[a-z]{1,8}|user|emp)\.[a-z0-9_-]{3,}"
    r"(?![A-Za-z0-9_-])"
)
_JIRA_MENTION = re.compile(r"\[~([^\]\s]{1,128})\]")
_SENSITIVE_PATTERNS = (
    ("authorization", re.compile(r"(?i)\bauthorization\s*:\s*(?:bearer|basic)\s+\S+")),
    ("bearer_token", re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{12,}")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b")),
    ("openai_key", re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{12,}\b")),
    ("aws_key", re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b")),
    ("github_token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b")),
    ("slack_token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{12,}\b")),
    ("dify_key", re.compile(r"\bapp-[A-Za-z0-9_-]{20,}\b")),
    ("private_key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    (
        "secret_assignment",
        re.compile(
            r"(?i)\b(?:api[_-]?key|password|passwd|client[_-]?secret|access[_-]?token|"
            r"refresh[_-]?token|session(?:id)?|cookie)\s*[:=]\s*[\"']?[^\s\"']{8,}"
        ),
    ),
)
_FORBIDDEN_CONTEXT_KEYS = frozenset({
    "approvaltoken",
    "commenttoken",
    "executionreceipt",
    "mutationreceipt",
    "questionreceiptprojection",
    "apikey",
    "accesstoken",
    "refreshtoken",
    "authorization",
    "clientsecret",
    "cookie",
    "credential",
    "credentials",
    "password",
    "passwd",
    "secret",
    "session",
    "sessionid",
    "token",
})


def forbidden_authority_key(value: object) -> bool:
    """Recognize secret/capability keys across snake_case, kebab-case and camelCase."""
    raw = str(value or "")
    camel_split = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", raw)
    parts = tuple(
        part for part in re.split(r"[^a-z0-9]+", camel_split.casefold()) if part
    )
    normalized = "".join(parts)
    if normalized in _FORBIDDEN_CONTEXT_KEYS:
        return True
    if any(part in {
        "authorization", "bearer", "cookie", "credential", "credentials",
        "password", "passwd", "secret", "session", "sessionid", "token",
    } for part in parts):
        return True
    if any(marker in normalized for marker in (
        "accesstoken", "refreshtoken", "clientsecret", "apikey", "privatekey",
    )):
        return True
    return False


class ContextGuardError(ValueError):
    def __init__(self, code: str, path: str = ""):
        self.code = code
        self.path = path
        message = code if not path else f"{code} ({path})"
        super().__init__(message)


def assert_safe_outbound_text(value: str, path: str = "outbound") -> None:
    """Reject secrets and identities that should have been pseudonymized upstream."""

    if _EMAIL.search(value) or _USER_ID.search(value) or _JIRA_MENTION.search(value):
        raise ContextGuardError("unpseudonymized_identity", path)
    for label, pattern in _SENSITIVE_PATTERNS:
        if pattern.search(value):
            raise ContextGuardError(f"sensitive_{label}", path)


def _string_values(value: Any, path: str = "evidence_pack"):
    if isinstance(value, str):
        yield path, value
    elif isinstance(value, Mapping):
        for key, item in value.items():
            yield from _string_values(item, f"{path}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            yield from _string_values(item, f"{path}[{index}]")


def validate_pack_safety(pack: EvidencePackV1, *, max_total_chars: int = 20_000) -> None:
    """Second-line validation for callers that did not retain the originating guard."""

    dumped = pack.model_dump(mode="json")
    for path, value in _string_values(dumped):
        assert_safe_outbound_text(value, path)
    try:
        canonical_schema, schema_digest = canonical_payload_schema(
            pack.payload_schema_json
        )
    except (TypeError, ValueError) as exc:
        raise ContextGuardError("invalid_payload_schema", "payload_schema") from exc
    if canonical_schema != pack.payload_schema_json:
        raise ContextGuardError("noncanonical_payload_schema", "payload_schema_json")
    if schema_digest != pack.payload_schema_sha256:
        raise ContextGuardError("payload_schema_digest_mismatch", "payload_schema_sha256")
    schema = pack.payload_schema()

    def validate_schema_keys(value: Any, path: str = "payload_schema") -> None:
        if isinstance(value, Mapping):
            for key, item in value.items():
                if forbidden_authority_key(key):
                    raise ContextGuardError("forbidden_authority_field", f"{path}.{key}")
                validate_schema_keys(item, f"{path}.{key}")
        elif isinstance(value, list):
            for index, item in enumerate(value):
                validate_schema_keys(item, f"{path}[{index}]")

    validate_schema_keys(schema)
    if _text_chars(dumped) > max_total_chars:
        raise ContextGuardError("context_too_large", "evidence_pack")


def _canonical_authority(value: str, *, host_only: bool) -> str:
    """Return an exact lower-case ``host[:port]`` without wildcard semantics."""

    raw = value.strip()
    if not raw or any(ord(character) <= 32 for character in raw):
        raise ValueError("host is required")
    if host_only:
        if "://" in raw or any(marker in raw for marker in ("/", "?", "#")):
            raise ValueError("host allowlist entries cannot contain URL components")
        candidate = "//" + raw
    else:
        candidate = raw
    try:
        parsed = urlsplit(candidate)
        port = parsed.port
    except ValueError as exc:
        raise ValueError("invalid host") from exc
    if not host_only and parsed.scheme not in {"http", "https"}:
        raise ValueError("invalid URL scheme")
    if not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("invalid host")
    host = parsed.hostname.casefold().rstrip(".")
    if not host or "*" in host or "%" in host:
        raise ValueError("wildcard and zone hosts are not allowed")
    if ":" not in host:
        try:
            host = host.encode("idna").decode("ascii")
        except UnicodeError as exc:
            raise ValueError("invalid international host") from exc
    else:
        host = f"[{host}]"
    return host + (f":{port}" if port is not None else "")


@dataclass(frozen=True, slots=True)
class ContextPolicy:
    allowed_scopes: Mapping[str, Iterable[str]]
    allowed_source_kinds: frozenset[str] = _SOURCE_KINDS
    allowed_source_hosts: Mapping[str, Iterable[str]] = field(default_factory=dict)
    allowed_context_keys: frozenset[str] = frozenset()
    max_sources: int = 10
    max_chars_per_source: int = 1500
    max_total_chars: int = 20_000
    freshness_seconds: int = 900
    allow_http_urls: bool = False

    def __post_init__(self):
        scopes: dict[str, frozenset[str]] = {}
        for raw_kind, raw_values in dict(self.allowed_scopes or {}).items():
            kind = str(raw_kind or "").strip().casefold()
            if kind not in _SOURCE_KINDS - {"knowledge", "ltm"}:
                raise ValueError(f"unsupported scope kind: {kind or '<empty>'}")
            if isinstance(raw_values, (str, bytes)):
                raise ValueError(f"scope values must be a collection: {kind}")
            values = frozenset(str(value).strip() for value in raw_values if str(value).strip())
            scopes[kind] = values
        if isinstance(self.allowed_source_kinds, (str, bytes)):
            raise ValueError("allowed_source_kinds must be a collection")
        kinds = frozenset(str(value).strip().casefold() for value in self.allowed_source_kinds)
        if not kinds or not kinds.issubset(_SOURCE_KINDS):
            raise ValueError("allowed_source_kinds contains an unsupported kind")
        hosts: dict[str, frozenset[str]] = {}
        for raw_kind, raw_values in dict(self.allowed_source_hosts or {}).items():
            kind = str(raw_kind or "").strip().casefold()
            if kind not in kinds:
                raise ValueError(f"unsupported host kind: {kind or '<empty>'}")
            if isinstance(raw_values, (str, bytes)):
                raise ValueError(f"host values must be a collection: {kind}")
            values = frozenset(
                _canonical_authority(str(value or "").strip(), host_only=True)
                for value in raw_values
                if str(value or "").strip()
            )
            hosts[kind] = values
        if isinstance(self.allowed_context_keys, (str, bytes)):
            raise ValueError("allowed_context_keys must be a collection")
        context_keys = frozenset(
            str(value or "").strip() for value in self.allowed_context_keys
            if str(value or "").strip()
        )
        if any(not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", key) for key in context_keys):
            raise ValueError("allowed_context_keys contains an invalid key")
        numeric_values = (
            self.max_sources,
            self.max_chars_per_source,
            self.max_total_chars,
            self.freshness_seconds,
        )
        if any(isinstance(value, bool) or not isinstance(value, int) for value in numeric_values):
            raise ValueError("context policy limits must be integers")
        if not isinstance(self.allow_http_urls, bool):
            raise ValueError("allow_http_urls must be boolean")
        if not 1 <= self.max_sources <= 10:
            raise ValueError("max_sources must be between 1 and 10")
        if not 1 <= self.max_chars_per_source <= 1500:
            raise ValueError("max_chars_per_source must be between 1 and 1500")
        if not 1000 <= self.max_total_chars <= 20_000:
            raise ValueError("max_total_chars must be between 1000 and 20000")
        if not 1 <= self.freshness_seconds <= 86_400:
            raise ValueError("freshness_seconds must be between 1 and 86400")
        object.__setattr__(self, "allowed_scopes", MappingProxyType(scopes))
        object.__setattr__(self, "allowed_source_kinds", kinds)
        object.__setattr__(self, "allowed_source_hosts", MappingProxyType(hosts))
        object.__setattr__(self, "allowed_context_keys", context_keys)


def _datetime(value: Any, path: str) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ContextGuardError("invalid_datetime", path) from exc
    else:
        raise ContextGuardError("invalid_datetime", path)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ContextGuardError("timezone_required", path)
    return parsed


def _required_text(row: Mapping[str, Any], name: str, path: str, *, max_length: int) -> str:
    value = row.get(name)
    if not isinstance(value, str) or not value.strip():
        raise ContextGuardError("required_text", f"{path}.{name}")
    clean = value.strip()
    if len(clean) > max_length:
        raise ContextGuardError("text_too_long", f"{path}.{name}")
    return clean


def _optional_text(row: Mapping[str, Any], name: str, path: str, *, max_length: int) -> str:
    value = row.get(name, "")
    if not isinstance(value, str):
        raise ContextGuardError("invalid_text", f"{path}.{name}")
    clean = value.strip()
    if len(clean) > max_length:
        raise ContextGuardError("text_too_long", f"{path}.{name}")
    return clean


def _text_chars(value: Any) -> int:
    if isinstance(value, str):
        return len(value)
    if isinstance(value, datetime):
        return len(value.isoformat())
    if isinstance(value, Mapping):
        return sum(len(str(key)) + _text_chars(item) for key, item in value.items())
    if isinstance(value, (list, tuple, set, frozenset)):
        return sum(_text_chars(item) for item in value)
    return 0


class ContextGuard:
    def __init__(self, policy: ContextPolicy, pseudonym_key: bytes):
        if not isinstance(pseudonym_key, bytes) or len(pseudonym_key) < 16:
            raise ValueError("pseudonym_key must contain at least 16 bytes")
        self.policy = policy
        self._pseudonym_key = bytes(pseudonym_key)

    def pseudonym(self, value: str) -> str:
        raw = str(value or "").strip()
        if not raw:
            raise ContextGuardError("identity_required", "user_identity")
        digest = hmac.new(self._pseudonym_key, raw.casefold().encode("utf-8"), "sha256")
        return "ltm_" + digest.hexdigest()[:16]

    def _identity_marker(self, value: str) -> str:
        digest = hmac.new(self._pseudonym_key, value.casefold().encode("utf-8"), "sha256")
        return "[[person:" + digest.hexdigest()[:12] + "]]"

    def _pseudonymize(self, value: str, identities: tuple[str, ...]) -> str:
        # Jira usernames are not restricted to the company's historical ``sk*.id``
        # pattern.  Treat every canonical Jira mention as an identity token before
        # looking for known roster values, including opaque usernames such as
        # ``[~alice.smith]``.
        text = _JIRA_MENTION.sub(
            lambda match: self._identity_marker(match.group(1)), value
        )
        for identity in sorted(identities, key=len, reverse=True):
            if identity:
                text = re.sub(re.escape(identity), self._identity_marker(identity), text, flags=re.I)
        text = _EMAIL.sub(lambda match: self._identity_marker(match.group(0)), text)
        text = _USER_ID.sub(lambda match: self._identity_marker(match.group(0)), text)
        return text

    def _sanitize_json_value(
        self,
        value: Any,
        identities: tuple[str, ...],
        path: str,
        *,
        depth: int = 0,
    ) -> Any:
        if depth > 8:
            raise ContextGuardError("context_depth_limit", path)
        if value is None or isinstance(value, bool):
            return value
        if isinstance(value, int):
            return value
        if isinstance(value, float):
            if not math.isfinite(value):
                raise ContextGuardError("invalid_number", path)
            return value
        if isinstance(value, str):
            clean = self._pseudonymize(value, identities)
            self._check_sensitive(clean, path)
            return clean
        if isinstance(value, (list, tuple)):
            if len(value) > 100:
                raise ContextGuardError("context_collection_limit", path)
            return [
                self._sanitize_json_value(item, identities, f"{path}[{index}]", depth=depth + 1)
                for index, item in enumerate(value)
            ]
        if isinstance(value, Mapping):
            if len(value) > 100:
                raise ContextGuardError("context_collection_limit", path)
            out: dict[str, Any] = {}
            for raw_key, item in value.items():
                if not isinstance(raw_key, str) or not raw_key:
                    raise ContextGuardError("invalid_context_key", path)
                if forbidden_authority_key(raw_key):
                    raise ContextGuardError("forbidden_authority_field", f"{path}.{raw_key}")
                out[raw_key] = self._sanitize_json_value(
                    item, identities, f"{path}.{raw_key}", depth=depth + 1
                )
            return out
        raise ContextGuardError("non_json_context_value", path)

    def _sanitize_source_content(
        self,
        value: str,
        identities: tuple[str, ...],
        path: str,
    ) -> str:
        """Recursively inspect JSON-shaped connector output before transmission."""
        clean = self._pseudonymize(value, identities)
        try:
            decoded = json.loads(clean)
        except (json.JSONDecodeError, TypeError, ValueError):
            self._check_sensitive(clean, path)
            return clean
        if not isinstance(decoded, (dict, list)):
            self._check_sensitive(clean, path)
            return clean
        sanitized = self._sanitize_json_value(decoded, identities, path)
        clean = json.dumps(
            sanitized,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        self._check_sensitive(clean, path)
        return clean

    @staticmethod
    def _check_sensitive(value: str, path: str) -> None:
        assert_safe_outbound_text(value, path)

    def _validate_scope(self, kind: str, scope: str, path: str) -> None:
        if kind in {"knowledge", "ltm"}:
            return
        allowed = self.policy.allowed_scopes.get(kind, frozenset())
        if not allowed:
            raise ContextGuardError("scope_not_configured", path)
        if scope not in allowed:
            raise ContextGuardError("scope_not_allowed", path)

    def _validate_url(self, kind: str, url: str, path: str) -> None:
        if not url:
            return
        try:
            parsed = urlsplit(url)
            authority = _canonical_authority(url, host_only=False)
        except (TypeError, ValueError) as exc:
            raise ContextGuardError("invalid_url", path) from exc
        if parsed.fragment:
            raise ContextGuardError("unsafe_url", path)
        if parsed.scheme == "http" and not self.policy.allow_http_urls:
            raise ContextGuardError("https_required", path)
        allowed_hosts = self.policy.allowed_source_hosts.get(kind, frozenset())
        if not allowed_hosts:
            raise ContextGuardError("source_host_not_configured", path)
        if authority not in allowed_hosts:
            raise ContextGuardError("source_host_not_allowed", path)
        self._check_sensitive(url, path)
        secret_query_names = {
            "access_token", "api_key", "apikey", "key", "password", "secret",
            "signature", "sig", "token",
        }
        if any(name.casefold() in secret_query_names for name, _ in parse_qsl(parsed.query)):
            raise ContextGuardError("sensitive_url_query", path)

    def build(
        self,
        *,
        request_id: str,
        use_case: str,
        query: str,
        payload_schema: Mapping[str, Any],
        user_identity: str,
        sources: Iterable[Mapping[str, Any]],
        facts: Iterable[Mapping[str, Any]] = (),
        metrics: Iterable[Mapping[str, Any]] = (),
        role_context: Iterable[Mapping[str, Any]] = (),
        identity_values: Iterable[str] = (),
        additional_flags: Iterable[str] = (),
        requested_at: datetime | None = None,
        knowledge_manifest_sha256: str | None = None,
    ) -> EvidencePackV1:
        now = requested_at or datetime.now(timezone.utc)
        now = _datetime(now, "requested_at")
        try:
            payload_schema_json, payload_schema_sha256 = canonical_payload_schema(
                payload_schema
            )
        except (TypeError, ValueError) as exc:
            raise ContextGuardError("invalid_payload_schema", "payload_schema") from exc
        if isinstance(identity_values, (str, bytes)):
            raise ContextGuardError("identity_collection_required", "identity_values")
        bounded_identities = list(islice(iter(identity_values), 201))
        if len(bounded_identities) > 200:
            raise ContextGuardError("identity_limit", "identity_values")
        identities = tuple(
            dict.fromkeys(
                value.strip()
                for value in (
                    str(item or "") for item in (*bounded_identities, user_identity)
                )
                if value.strip()
            )
        )
        if not isinstance(query, str) or not query.strip():
            raise ContextGuardError("query_required", "query")
        clean_query = self._pseudonymize(query.strip(), identities)
        if len(clean_query) > 4000:
            raise ContextGuardError("text_too_long", "query")
        self._check_sensitive(clean_query, "query")

        selected_plus_one = list(islice(iter(sources), self.policy.max_sources + 1))
        selected = selected_plus_one[: self.policy.max_sources]
        if isinstance(additional_flags, (str, bytes)):
            raise ContextGuardError("flag_collection_required", "additional_flags")
        requested_flags = list(islice(iter(additional_flags), 17))
        if len(requested_flags) > 16 or any(
            not isinstance(value, str)
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]*", value)
            for value in requested_flags
        ):
            raise ContextGuardError("invalid_flag", "additional_flags")
        flags: list[str] = list(dict.fromkeys(requested_flags))
        if len(selected_plus_one) > len(selected):
            flags.append("source_limit")

        prepared_sources: list[dict[str, Any]] = []
        stale_count = 0
        for index, raw in enumerate(selected):
            path = f"sources[{index}]"
            if not isinstance(raw, Mapping):
                raise ContextGuardError("invalid_source", path)
            kind = _required_text(raw, "kind", path, max_length=32).casefold()
            if kind not in self.policy.allowed_source_kinds:
                raise ContextGuardError("source_kind_not_allowed", f"{path}.kind")
            scope = _optional_text(raw, "scope", path, max_length=160)
            self._validate_scope(kind, scope, f"{path}.scope")
            retrieved_at = _datetime(raw.get("retrieved_at"), f"{path}.retrieved_at")
            if retrieved_at > now and (retrieved_at - now).total_seconds() > 60:
                raise ContextGuardError("future_retrieval_time", f"{path}.retrieved_at")
            is_stale = (now - retrieved_at).total_seconds() > self.policy.freshness_seconds
            stale_count += int(is_stale)
            updated_at = raw.get("updated_at")
            if updated_at is not None:
                updated_at = _datetime(updated_at, f"{path}.updated_at")
            title = self._pseudonymize(
                _required_text(raw, "title", path, max_length=240), identities
            )
            content = raw.get("content", "")
            if not isinstance(content, str):
                raise ContextGuardError("invalid_text", f"{path}.content")
            content = self._sanitize_source_content(
                content.strip(), identities, f"{path}.content"
            )
            self._check_sensitive(title, f"{path}.title")
            truncated = len(content) > self.policy.max_chars_per_source
            if truncated:
                content = content[: self.policy.max_chars_per_source]
            url = _optional_text(raw, "url", path, max_length=2048)
            self._validate_url(kind, url, f"{path}.url")
            partial = raw.get("partial", False)
            if not isinstance(partial, bool):
                raise ContextGuardError("invalid_boolean", f"{path}.partial")
            prepared_sources.append({
                "source_id": _required_text(raw, "source_id", path, max_length=128),
                "kind": kind,
                "canonical_id": _required_text(raw, "canonical_id", path, max_length=240),
                "title": title,
                "scope": scope,
                "url": url,
                "content": content,
                "retrieved_at": retrieved_at,
                "updated_at": updated_at,
                "partial": partial,
                "truncated": truncated,
            })
            if partial and "partial_source" not in flags:
                flags.append("partial_source")
            if truncated and "source_truncated" not in flags:
                flags.append("source_truncated")
        if stale_count:
            flags.append("stale_source")

        selected_context_plus_one = list(islice(iter(role_context), 9))
        selected_context = selected_context_plus_one[:8]
        if len(selected_context_plus_one) > len(selected_context):
            flags.append("role_context_limit")
        prepared_context: list[dict[str, Any]] = []
        stale_context_count = 0
        for index, raw in enumerate(selected_context):
            path = f"role_context[{index}]"
            if not isinstance(raw, Mapping):
                raise ContextGuardError("invalid_role_context", path)
            name = _required_text(raw, "name", path, max_length=64)
            if name not in self.policy.allowed_context_keys:
                raise ContextGuardError("context_key_not_allowed", f"{path}.name")
            if "value" not in raw:
                raise ContextGuardError("context_value_required", f"{path}.value")
            safe_value = self._sanitize_json_value(
                raw["value"], identities, f"{path}.value"
            )
            content_json = json.dumps(
                safe_value,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
            if len(content_json) > 4000:
                raise ContextGuardError("role_context_too_large", f"{path}.value")
            observed_at = _datetime(raw.get("observed_at"), f"{path}.observed_at")
            if observed_at > now and (observed_at - now).total_seconds() > 60:
                raise ContextGuardError("future_observation_time", f"{path}.observed_at")
            is_stale_context = (
                now - observed_at
            ).total_seconds() > self.policy.freshness_seconds
            stale_context_count += int(is_stale_context)
            partial_context = raw.get("partial", False)
            if not isinstance(partial_context, bool):
                raise ContextGuardError("invalid_boolean", f"{path}.partial")
            prepared_context.append({
                "context_id": _required_text(raw, "context_id", path, max_length=128),
                "name": name,
                "content_json": content_json,
                "observed_at": observed_at,
                "content_sha256": hashlib.sha256(content_json.encode("utf-8")).hexdigest(),
                "partial": partial_context,
            })
            if partial_context and "partial_role_context" not in flags:
                flags.append("partial_role_context")
        known_source_ids = {row["source_id"] for row in prepared_sources}
        prepared_facts: list[dict[str, Any]] = []
        for index, raw in enumerate(facts):
            if index >= 40:
                raise ContextGuardError("fact_limit", "facts")
            path = f"facts[{index}]"
            if not isinstance(raw, Mapping):
                raise ContextGuardError("invalid_fact", path)
            refs = raw.get("source_ids")
            if not isinstance(refs, (list, tuple)) or not refs:
                raise ContextGuardError("source_ids_required", f"{path}.source_ids")
            if any(not isinstance(value, str) for value in refs):
                raise ContextGuardError("invalid_source_id", f"{path}.source_ids")
            source_ids = tuple(value.strip() for value in refs if value.strip())
            if not source_ids or not set(source_ids).issubset(known_source_ids):
                if "fact_source_omitted" not in flags:
                    flags.append("fact_source_omitted")
                continue
            text = self._pseudonymize(
                _required_text(raw, "text", path, max_length=1000), identities
            )
            self._check_sensitive(text, f"{path}.text")
            observed_at = _datetime(raw.get("observed_at"), f"{path}.observed_at")
            if observed_at > now and (observed_at - now).total_seconds() > 60:
                raise ContextGuardError("future_observation_time", f"{path}.observed_at")
            prepared_facts.append({
                "fact_id": _required_text(raw, "fact_id", path, max_length=128),
                "text": text,
                "source_ids": source_ids,
                "observed_at": observed_at,
            })
        prepared_metrics: list[dict[str, Any]] = []
        for index, raw in enumerate(metrics):
            if index >= 40:
                raise ContextGuardError("metric_limit", "metrics")
            path = f"metrics[{index}]"
            if not isinstance(raw, Mapping):
                raise ContextGuardError("invalid_metric", path)
            refs = raw.get("source_ids")
            if not isinstance(refs, (list, tuple)) or not refs:
                raise ContextGuardError("source_ids_required", f"{path}.source_ids")
            if any(not isinstance(value, str) for value in refs):
                raise ContextGuardError("invalid_source_id", f"{path}.source_ids")
            source_ids = tuple(value.strip() for value in refs if value.strip())
            if not source_ids or not set(source_ids).issubset(known_source_ids):
                if "metric_source_omitted" not in flags:
                    flags.append("metric_source_omitted")
                continue
            value = raw.get("value")
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ContextGuardError("invalid_metric_value", f"{path}.value")
            label = self._pseudonymize(
                _required_text(raw, "label", path, max_length=160), identities
            )
            self._check_sensitive(label, f"{path}.label")
            prepared_metrics.append({
                "metric_id": _required_text(raw, "metric_id", path, max_length=128),
                "label": label,
                "value": value,
                "unit": _optional_text(raw, "unit", path, max_length=32),
                "formula_version": _required_text(
                    raw, "formula_version", path, max_length=80
                ),
                "source_ids": source_ids,
            })
        scope = {
            "projects": tuple(sorted(self.policy.allowed_scopes.get("jira", ()))),
            "spaces": tuple(sorted(self.policy.allowed_scopes.get("confluence", ()))),
            "repositories": tuple(sorted(self.policy.allowed_scopes.get("bitbucket", ()))),
        }
        skeleton = {
            "contract": "evidence-pack.v1",
            "request_id": request_id,
            "use_case": use_case,
            "query": clean_query,
            "pseudonymous_user": self.pseudonym(user_identity),
            "requested_at": now,
            "scope": scope,
            "sources": [{**row, "content": "", "content_sha256": "0" * 64}
                        for row in prepared_sources],
            "facts": prepared_facts,
            "metrics": prepared_metrics,
            "role_context": prepared_context,
            "knowledge_manifest_sha256": knowledge_manifest_sha256,
            "payload_schema_json": payload_schema_json,
            "payload_schema_sha256": payload_schema_sha256,
        }
        # ``RoleContextV1`` bounds each value, but eight individually valid 4k values
        # can still exceed the EvidencePack-wide 20k budget before source content is
        # considered.  Keep the upstream priority order and progressively redact/drop
        # only the lowest-priority context rows until metadata fits.  Never slice JSON:
        # a typed omission remains valid, is integrity-hashed, and makes the pack
        # explicitly partial so freshness-sensitive drafting Roles fail closed.
        budget_omitted_context_rows: set[int] = set()
        while _text_chars(skeleton) > self.policy.max_total_chars and prepared_context:
            row = prepared_context[-1]
            row_marker = id(row)
            if row_marker not in budget_omitted_context_rows:
                omitted_json = json.dumps(
                    {"omitted": True, "originalChars": len(row["content_json"])},
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                row["content_json"] = omitted_json
                row["content_sha256"] = hashlib.sha256(
                    omitted_json.encode("utf-8")
                ).hexdigest()
                row["partial"] = True
                budget_omitted_context_rows.add(row_marker)
                if "partial_role_context" not in flags:
                    flags.append("partial_role_context")
            else:
                prepared_context.pop()
                budget_omitted_context_rows.discard(row_marker)
                if "role_context_limit" not in flags:
                    flags.append("role_context_limit")
            skeleton["role_context"] = prepared_context
        remaining = self.policy.max_total_chars - _text_chars(skeleton)
        if remaining < 0:
            raise ContextGuardError("context_too_large", "metadata")
        for row in prepared_sources:
            content = row["content"]
            if len(content) > remaining:
                row["content"] = content[:remaining]
                row["truncated"] = True
                if "total_truncated" not in flags:
                    flags.append("total_truncated")
                if "source_truncated" not in flags:
                    flags.append("source_truncated")
            remaining -= len(row["content"])
            row["content_sha256"] = hashlib.sha256(
                row["content"].encode("utf-8")
            ).hexdigest()

        # Context rows may have been removed by the aggregate budget reducer above.
        # Recompute the stale count so freshness cannot be skewed by omitted rows.
        stale_context_count = sum(
            int((now - row["observed_at"]).total_seconds()
                > self.policy.freshness_seconds)
            for row in prepared_context
        )
        if stale_context_count and "stale_role_context" not in flags:
            flags.append("stale_role_context")
        observation_count = len(prepared_sources) + len(prepared_context)
        stale_observation_count = stale_count + stale_context_count
        if not observation_count:
            freshness = "empty"
        elif stale_observation_count == 0:
            freshness = "fresh"
        elif stale_observation_count == observation_count:
            freshness = "stale"
        else:
            freshness = "mixed"
        partial_flags = {
            "source_limit", "source_truncated", "total_truncated", "partial_source",
            "fact_source_omitted", "metric_source_omitted",
            "role_context_limit", "partial_role_context",
        }
        partial = bool(set(flags) & partial_flags)
        try:
            pack = EvidencePackV1(
                request_id=request_id,
                use_case=use_case,
                query=clean_query,
                pseudonymous_user=self.pseudonym(user_identity),
                requested_at=now,
                scope=EvidenceScopeV1(**scope),
                sources=tuple(EvidenceSourceV1(**row) for row in prepared_sources),
                facts=tuple(EvidenceFactV1(**row) for row in prepared_facts),
                metrics=tuple(EvidenceMetricV1(**row) for row in prepared_metrics),
                role_context=tuple(RoleContextV1(**row) for row in prepared_context),
                freshness=freshness,
                partial=partial,
                flags=tuple(flags),
                knowledge_manifest_sha256=knowledge_manifest_sha256,
                payload_schema_json=payload_schema_json,
                payload_schema_sha256=payload_schema_sha256,
            )
        except ValidationError as exc:
            raise ContextGuardError("contract_validation_failed", "evidence_pack") from exc
        validate_pack_safety(pack, max_total_chars=self.policy.max_total_chars)
        return pack


__all__ = [
    "ContextGuard",
    "ContextGuardError",
    "ContextPolicy",
    "assert_safe_outbound_text",
    "forbidden_authority_key",
    "validate_pack_safety",
]
