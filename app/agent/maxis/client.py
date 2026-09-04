"""Blocking, stateless Dify application client for MAXIS-managed workflows."""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import ssl
import threading
import time
from collections import OrderedDict, deque
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Literal, Mapping
from urllib.parse import unquote, urlsplit, urlunsplit

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.agent.maxis.context_guard import (
    ContextGuardError,
    assert_safe_outbound_text,
    validate_pack_safety,
)
from app.agent.maxis.errors import (
    MaxisErrorCode,
    MaxisGatewayError,
    classify_http_status,
)
from app.agent.maxis.contracts import (
    canonical_payload_schema,
    EvidenceFactV1,
    EvidencePackV1,
    EvidenceScopeV1,
    EvidenceSourceV1,
    RoleContextV1,
)
from app.agent.maxis.registry import WorkflowBinding
from app.agent.maxis.response_guard import ResponseGuard, ResponseGuardError


_USER_RE = re.compile(r"^ltm_[0-9a-f]{16}$")
_PARAMETER_VARIABLE_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
_PARAMETER_CONTROL_RE = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")
_CONTROL_VALUE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")

# This is the complete, content-bounded Start contract shared by every LTM
# Chatflow. ``query`` and ``user`` are Dify Chat API fields, not Start variables.
REQUIRED_START_VARIABLES = frozenset({
    "context_pack",
    "role_id",
    "correlation_id",
    "output_contract",
    "workflow_version",
    "knowledge_version",
})
_REQUIRED_START_CONTROLS = {
    "context_pack": "paragraph",
    "role_id": "text-input",
    "correlation_id": "text-input",
    "output_contract": "text-input",
    "workflow_version": "text-input",
    "knowledge_version": "text-input",
}
# ContextGuard bounds content-bearing strings to 20k characters.  A deployed Dify
# paragraph must at least accommodate that contract; ``null``/missing max_length is
# treated as unbounded.  Short controls must accommodate our 128-character wire IDs.
_MIN_START_CAPACITY = {"context_pack": 20_000, **{
    key: 128 for key in REQUIRED_START_VARIABLES if key != "context_pack"
}}


class _StrictResult(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class DifyUsage(_StrictResult):
    prompt_tokens: int = Field(default=0, ge=0)
    completion_tokens: int = Field(default=0, ge=0)
    total_tokens: int = Field(default=0, ge=0)


class DifyChatResponse(_StrictResult):
    answer: str
    message_id: str
    conversation_id: str
    task_id: str = ""
    usage: DifyUsage
    latency_ms: int = Field(ge=0)


class DifyProbeResult(_StrictResult):
    ok: bool
    status_code: int | None = None
    latency_ms: int = Field(ge=0)
    parameter_keys: tuple[str, ...] = ()
    error_code: str = ""


class DifyContractProbeResult(_StrictResult):
    """Metadata-only result of an end-to-end synthetic Chatflow canary."""

    ok: bool
    status_code: int | None = None
    latency_ms: int = Field(ge=0)
    role_id: str = ""
    contract_verified: bool = False
    version_verified: bool = False
    citation_verified: bool = False
    knowledge_retrieval_verified: bool = False
    error_code: str = ""


_PROBE_ROLE_BY_WORKFLOW = {
    "agent_core": "knowledge_curator",
    "editor_author": "editor_author",
    "pmo_brief": "portfolio_analyst",
}
_PROBE_PAYLOAD_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "state": {"type": "string", "const": "ready"},
    },
    "required": ["state"],
}


def _synthetic_contract_probe_pack(
    binding: WorkflowBinding,
    knowledge_manifest_sha256: str,
) -> tuple[EvidencePackV1, str]:
    """Build a fresh canary containing no tenant data, identity, or capability."""

    preferred_role = _PROBE_ROLE_BY_WORKFLOW.get(binding.key, "")
    role_id = preferred_role if preferred_role in binding.roles else (
        binding.roles[0] if binding.roles else ""
    )
    if not role_id:
        raise MaxisGatewayError(MaxisErrorCode.CONFIGURATION)

    now = datetime.now(timezone.utc)
    nonce = hashlib.sha256(
        f"{binding.key}:{time.time_ns()}".encode("utf-8")
    ).hexdigest()[:12]
    request_id = f"probe_{binding.key}_{nonce}"
    source_id = f"probe_source_{nonce}"
    source_content = "The supplied synthetic activation canary has state ready."
    source = EvidenceSourceV1(
        source_id=source_id,
        kind="ltm",
        canonical_id=f"ltm:maxis-probe:{binding.key}",
        title="Synthetic MAXIS activation canary",
        content=source_content,
        retrieved_at=now,
        content_sha256=hashlib.sha256(source_content.encode("utf-8")).hexdigest(),
    )
    context_json = json.dumps(
        {
            "expected_state": "ready",
            "probe_kind": "synthetic_contract",
            "workflow": binding.key,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    role_context = RoleContextV1(
        context_id=f"probe_context_{nonce}",
        name="activation_probe",
        content_json=context_json,
        observed_at=now,
        content_sha256=hashlib.sha256(context_json.encode("utf-8")).hexdigest(),
    )
    query = (
        "Read only the supplied synthetic evidence and return its ready state "
        "as one cited fact."
    )
    payload_schema_json, payload_schema_sha256 = canonical_payload_schema(
        _PROBE_PAYLOAD_SCHEMA
    )
    pack = EvidencePackV1(
        request_id=request_id,
        use_case=role_id,
        query=query,
        pseudonymous_user=f"ltm_{nonce}0000",
        requested_at=now,
        scope=EvidenceScopeV1(),
        sources=(source,),
        facts=(EvidenceFactV1(
            fact_id=f"probe_fact_{nonce}",
            text=source_content,
            source_ids=(source_id,),
            observed_at=now,
        ),),
        role_context=(role_context,),
        freshness="fresh",
        partial=False,
        knowledge_manifest_sha256=knowledge_manifest_sha256,
        payload_schema_json=payload_schema_json,
        payload_schema_sha256=payload_schema_sha256,
    )
    return pack, role_id


def canonical_api_base(value: str, *, require_https: bool) -> str:
    raw = str(value or "").strip()
    if not raw:
        raise MaxisGatewayError(MaxisErrorCode.CONFIGURATION)
    try:
        parsed = urlsplit(raw)
        port = parsed.port
    except ValueError as exc:
        raise MaxisGatewayError(MaxisErrorCode.CONFIGURATION) from exc
    scheme = parsed.scheme.casefold()
    if scheme not in {"http", "https"} or not parsed.hostname:
        raise MaxisGatewayError(MaxisErrorCode.CONFIGURATION)
    if require_https and scheme != "https":
        raise MaxisGatewayError(MaxisErrorCode.POLICY)
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise MaxisGatewayError(MaxisErrorCode.POLICY)
    host = parsed.hostname.casefold()
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    if port is not None:
        host += f":{port}"
    path = (parsed.path or "").rstrip("/")
    if any(part == ".." for part in unquote(path).split("/")):
        raise MaxisGatewayError(MaxisErrorCode.POLICY)
    if path.endswith("/chat-messages") or path.endswith("/parameters"):
        raise MaxisGatewayError(MaxisErrorCode.CONFIGURATION)
    if not path.endswith("/v1"):
        path = path + "/v1" if path else "/v1"
    return urlunsplit((scheme, host, path, "", ""))


@dataclass(frozen=True, slots=True)
class DifyClientConfig:
    profile: str
    stage: Literal["dev", "prod", "test"]
    base_url: str
    profile_fingerprint: str = ""
    require_https: bool = True
    verify_tls: bool | str | ssl.SSLContext = True
    connect_timeout_seconds: float = 3.0
    read_timeout_seconds: float = 30.0
    total_timeout_seconds: float = 35.0
    max_response_bytes: int = 1024 * 1024

    def __post_init__(self):
        if not str(self.profile or "").strip():
            raise MaxisGatewayError(MaxisErrorCode.CONFIGURATION)
        fingerprint = str(self.profile_fingerprint or "").strip()
        if fingerprint and not re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9._:-]{7,127}", fingerprint
        ):
            raise MaxisGatewayError(MaxisErrorCode.CONFIGURATION)
        if self.stage not in {"dev", "prod", "test"}:
            raise MaxisGatewayError(MaxisErrorCode.CONFIGURATION)
        if self.stage == "prod" and not self.require_https:
            raise MaxisGatewayError(MaxisErrorCode.POLICY)
        if self.stage == "prod" and self.verify_tls is False:
            raise MaxisGatewayError(MaxisErrorCode.POLICY)
        if not 0 < self.connect_timeout_seconds <= 3.0:
            raise MaxisGatewayError(MaxisErrorCode.CONFIGURATION)
        if not 0 < self.read_timeout_seconds <= 30.0:
            raise MaxisGatewayError(MaxisErrorCode.CONFIGURATION)
        if not 0 < self.total_timeout_seconds <= 35.0:
            raise MaxisGatewayError(MaxisErrorCode.CONFIGURATION)
        if self.total_timeout_seconds < max(
            self.connect_timeout_seconds, self.read_timeout_seconds
        ):
            raise MaxisGatewayError(MaxisErrorCode.CONFIGURATION)
        if not 1024 <= self.max_response_bytes <= 4 * 1024 * 1024:
            raise MaxisGatewayError(MaxisErrorCode.CONFIGURATION)
        canonical = canonical_api_base(self.base_url, require_https=self.require_https)
        object.__setattr__(self, "profile", str(self.profile).strip())
        object.__setattr__(self, "profile_fingerprint", fingerprint)
        object.__setattr__(self, "base_url", canonical)

    @property
    def timeout(self) -> httpx.Timeout:
        return httpx.Timeout(
            timeout=self.total_timeout_seconds,
            connect=self.connect_timeout_seconds,
            read=self.read_timeout_seconds,
            write=self.total_timeout_seconds,
            pool=self.connect_timeout_seconds,
        )


@dataclass(frozen=True, slots=True)
class CircuitSnapshot:
    state: Literal["closed", "open", "half_open"]
    recent_failures: int
    retry_after_seconds: int


class CircuitBreaker:
    """Three relevant failures in five minutes open calls for two minutes."""

    def __init__(
        self,
        *,
        failure_threshold: int = 3,
        failure_window_seconds: float = 300.0,
        open_seconds: float = 120.0,
        clock: Callable[[], float] = time.monotonic,
    ):
        if failure_threshold < 1 or failure_window_seconds <= 0 or open_seconds <= 0:
            raise ValueError("invalid circuit breaker policy")
        self.failure_threshold = failure_threshold
        self.failure_window_seconds = failure_window_seconds
        self.open_seconds = open_seconds
        self._clock = clock
        self._failures: deque[float] = deque()
        self._opened_until = 0.0
        self._half_open_in_flight = False
        self._lock = threading.Lock()

    def _purge(self, now: float) -> None:
        cutoff = now - self.failure_window_seconds
        while self._failures and self._failures[0] < cutoff:
            self._failures.popleft()

    def before_call(self) -> None:
        with self._lock:
            now = self._clock()
            self._purge(now)
            if self._opened_until > now:
                raise MaxisGatewayError(
                    MaxisErrorCode.CIRCUIT_OPEN, retryable=True
                )
            if self._opened_until:
                if self._half_open_in_flight:
                    raise MaxisGatewayError(
                        MaxisErrorCode.CIRCUIT_OPEN, retryable=True
                    )
                self._half_open_in_flight = True

    def record_success(self) -> None:
        with self._lock:
            self._failures.clear()
            self._opened_until = 0.0
            self._half_open_in_flight = False

    def record_failure(self) -> None:
        with self._lock:
            now = self._clock()
            self._purge(now)
            if self._half_open_in_flight:
                self._opened_until = now + self.open_seconds
                self._half_open_in_flight = False
                return
            self._failures.append(now)
            if len(self._failures) >= self.failure_threshold:
                self._opened_until = now + self.open_seconds

    def snapshot(self) -> CircuitSnapshot:
        with self._lock:
            now = self._clock()
            self._purge(now)
            if self._opened_until > now:
                state = "open"
                retry_after = max(1, int(self._opened_until - now + 0.999))
            elif self._opened_until:
                state = "half_open"
                retry_after = 0
            else:
                state = "closed"
                retry_after = 0
            return CircuitSnapshot(state, len(self._failures), retry_after)


_SHARED_BREAKER_LIMIT = 256
_SHARED_BREAKERS_LOCK = threading.Lock()
_SHARED_BREAKERS: OrderedDict[tuple[str, str, str], CircuitBreaker] = OrderedDict()


def _shared_circuit_breaker(
    *,
    profile_fingerprint: str,
    workflow_key: str,
    api_key: str,
    clock: Callable[[], float],
) -> CircuitBreaker:
    """Return a bounded process-wide breaker without retaining credential material."""

    credential_fingerprint = hashlib.sha256(
        str(api_key or "").strip().encode("utf-8")
    ).hexdigest()
    key = (profile_fingerprint, workflow_key, credential_fingerprint)
    with _SHARED_BREAKERS_LOCK:
        existing = _SHARED_BREAKERS.get(key)
        if existing is not None:
            _SHARED_BREAKERS.move_to_end(key)
            return existing
        breaker = CircuitBreaker(clock=clock)
        _SHARED_BREAKERS[key] = breaker
        while len(_SHARED_BREAKERS) > _SHARED_BREAKER_LIMIT:
            _SHARED_BREAKERS.popitem(last=False)
        return breaker


def _jsonable(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Mapping):
        out: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise MaxisGatewayError(MaxisErrorCode.BAD_REQUEST)
            out[key] = _jsonable(item)
        return out
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    raise MaxisGatewayError(MaxisErrorCode.BAD_REQUEST)


def _strict_json_loads(value: str | bytes) -> Any:
    def object_pairs(pairs):
        result = {}
        for key, item in pairs:
            if key in result:
                raise ValueError("duplicate JSON object key")
            result[key] = item
        return result

    def invalid_constant(_value):
        raise ValueError("non-finite JSON number")

    return json.loads(
        value,
        object_pairs_hook=object_pairs,
        parse_constant=invalid_constant,
    )


_INPUT_KEYS = REQUIRED_START_VARIABLES


def _validated_inputs(
    binding: WorkflowBinding, inputs: Mapping[str, Any]
) -> tuple[dict[str, Any], EvidencePackV1]:
    """Require the bounded EvidencePack even when an upstream adapter is compromised.

    Raw Agent state, message history, approval material, and arbitrary Dify variables cannot
    pass this second trust-boundary check.
    """

    if not isinstance(inputs, Mapping):
        raise MaxisGatewayError(MaxisErrorCode.POLICY)
    supplied = set(inputs)
    unknown = supplied - _INPUT_KEYS
    missing = _INPUT_KEYS - supplied
    if unknown or missing:
        raise MaxisGatewayError(MaxisErrorCode.POLICY)
    candidate = inputs.get("context_pack")
    try:
        if isinstance(candidate, EvidencePackV1):
            pack = candidate
        elif isinstance(candidate, Mapping):
            encoded = json.dumps(candidate, ensure_ascii=False, separators=(",", ":"))
            pack = EvidencePackV1.model_validate_json(encoded, strict=True)
        else:
            raise TypeError("EvidencePack object required")
        validate_pack_safety(pack)
        # Materialize the verified schema once more at the final network boundary.  This
        # makes the Dify client fail closed even for a model instance constructed by an
        # unsafe caller that bypassed the normal ContextGuard path.
        pack.payload_schema()
    except (ContextGuardError, TypeError, ValueError, ValidationError) as exc:
        raise MaxisGatewayError(MaxisErrorCode.POLICY) from exc

    # Dify Chatflow Start inputs support a paragraph/text variable reliably across
    # deployments.  Serialize only after strict validation; callers cannot smuggle an
    # arbitrary pre-serialized state blob through this field.
    clean: dict[str, Any] = {
        "context_pack": json.dumps(
            pack.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    }
    for key in _INPUT_KEYS - {"context_pack"}:
        if key not in inputs:
            continue
        value = inputs[key]
        if not isinstance(value, str) or not _CONTROL_VALUE_RE.fullmatch(value):
            raise MaxisGatewayError(MaxisErrorCode.POLICY)
        clean[key] = value
    role_id = clean.get("role_id")
    if role_id not in binding.roles or role_id != pack.use_case:
        raise MaxisGatewayError(MaxisErrorCode.POLICY)
    if clean.get("correlation_id") != pack.request_id:
        raise MaxisGatewayError(MaxisErrorCode.POLICY)
    if clean.get("output_contract") != binding.output_contract:
        raise MaxisGatewayError(MaxisErrorCode.POLICY)
    return clean, pack


def _bounded_int(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0
    return max(0, int(value))


def _start_parameter_keys(raw: Mapping[str, Any]) -> tuple[str, ...]:
    """Extract the Dify Chat app's Start variables from ``user_input_form``.

    Dify represents each input as a one-key control object, for example
    ``{"paragraph": {"variable": "context_pack", ...}}``.  Treating top-level
    response keys as app inputs would make a transport-only probe look healthy, so
    the form structure is deliberately parsed and bounded here.
    """

    form = raw.get("user_input_form")
    if not isinstance(form, list) or len(form) > 64:
        raise TypeError("bounded user_input_form required")
    variables: list[str] = []
    seen: set[str] = set()
    for row in form:
        if not isinstance(row, dict) or len(row) != 1:
            raise TypeError("one input control required")
        control, definition = next(iter(row.items()))
        if (
            not isinstance(control, str)
            or not _PARAMETER_CONTROL_RE.fullmatch(control)
            or not isinstance(definition, dict)
        ):
            raise TypeError("invalid input control")
        variable = definition.get("variable")
        if not isinstance(variable, str) or not _PARAMETER_VARIABLE_RE.fullmatch(variable):
            raise TypeError("invalid input variable")
        if variable in seen:
            raise TypeError("duplicate input variable")
        if variable in REQUIRED_START_VARIABLES:
            if definition.get("required") is not True:
                raise TypeError("required Start variable is optional")
            if control != _REQUIRED_START_CONTROLS[variable]:
                raise TypeError("wrong Start variable control")
            max_length = definition.get("max_length")
            if max_length is not None and (
                isinstance(max_length, bool)
                or not isinstance(max_length, int)
                or max_length < _MIN_START_CAPACITY[variable]
            ):
                raise TypeError("Start variable capacity is too small")
        seen.add(variable)
        variables.append(variable)
    return tuple(sorted(variables))


class DifyClient:
    def __init__(
        self,
        config: DifyClientConfig,
        *,
        client: httpx.Client | None = None,
        breaker: CircuitBreaker | None = None,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.config = config
        self._injected_breaker = breaker
        self.breaker = breaker or CircuitBreaker(clock=clock)
        self._clock = clock
        self._owns_client = client is None
        self._client = client or httpx.Client(
            timeout=config.timeout,
            verify=config.verify_tls,
            follow_redirects=False,
        )

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        self.close()

    @staticmethod
    def _headers(api_key: str, *, json_body: bool) -> dict[str, str]:
        key = str(api_key or "").strip()
        if not key or any(ord(character) <= 32 for character in key):
            raise MaxisGatewayError(MaxisErrorCode.CONFIGURATION)
        headers = {
            "Authorization": f"Bearer {key}",
            "Accept": "application/json",
        }
        if json_body:
            headers["Content-Type"] = "application/json"
        return headers

    def _request_bytes(
        self,
        method: str,
        path: str,
        *,
        api_key: str,
        payload: dict[str, Any] | None,
        response_limit: int,
        breaker: CircuitBreaker | None = None,
    ) -> tuple[bytes, int, int]:
        active_breaker = breaker or self.breaker
        active_breaker.before_call()
        started = self._clock()
        try:
            kwargs: dict[str, Any] = {
                "headers": self._headers(api_key, json_body=payload is not None),
                "timeout": self.config.timeout,
            }
            if payload is not None:
                kwargs["json"] = payload
            with self._client.stream(
                method,
                self.config.base_url + path,
                follow_redirects=False,
                **kwargs,
            ) as response:
                if not 200 <= response.status_code < 300:
                    raise classify_http_status(response.status_code)
                content_length = response.headers.get("content-length", "")
                if content_length.isdecimal() and int(content_length) > response_limit:
                    raise MaxisGatewayError(
                        MaxisErrorCode.RESPONSE_TOO_LARGE, retryable=True
                    )
                chunks: list[bytes] = []
                size = 0
                for chunk in response.iter_bytes():
                    if self._clock() - started > self.config.total_timeout_seconds:
                        raise MaxisGatewayError(
                            MaxisErrorCode.TIMEOUT, retryable=True
                        )
                    size += len(chunk)
                    if size > response_limit:
                        raise MaxisGatewayError(
                            MaxisErrorCode.RESPONSE_TOO_LARGE, retryable=True
                        )
                    chunks.append(chunk)
                if self._clock() - started > self.config.total_timeout_seconds:
                    raise MaxisGatewayError(
                        MaxisErrorCode.TIMEOUT, retryable=True
                    )
                body = b"".join(chunks)
                status = response.status_code
        except MaxisGatewayError as exc:
            if exc.retryable:
                active_breaker.record_failure()
            else:
                active_breaker.record_success()
            raise
        except (httpx.TimeoutException, TimeoutError) as exc:
            active_breaker.record_failure()
            raise MaxisGatewayError(
                MaxisErrorCode.TIMEOUT, retryable=True
            ) from exc
        except httpx.RequestError as exc:
            active_breaker.record_failure()
            raise MaxisGatewayError(
                MaxisErrorCode.NETWORK, retryable=True
            ) from exc
        latency_ms = max(0, int((self._clock() - started) * 1000))
        return body, status, latency_ms

    def _invoke_breaker(
        self, binding: WorkflowBinding, api_key: str
    ) -> CircuitBreaker:
        if self._injected_breaker is not None:
            return self._injected_breaker
        if not self.config.profile_fingerprint:
            return self.breaker
        return _shared_circuit_breaker(
            profile_fingerprint=self.config.profile_fingerprint,
            workflow_key=binding.key,
            api_key=api_key,
            clock=self._clock,
        )

    def invoke(
        self,
        binding: WorkflowBinding,
        *,
        api_key: str,
        query: str,
        user: str,
        inputs: Mapping[str, Any],
        workflow_id: str = "",
    ) -> DifyChatResponse:
        if binding.endpoint != "/chat-messages" or binding.response_mode != "blocking":
            raise MaxisGatewayError(MaxisErrorCode.CONFIGURATION)
        if binding.conversation_mode != "stateless":
            raise MaxisGatewayError(MaxisErrorCode.POLICY)
        clean_query = str(query or "").strip()
        if not clean_query or len(clean_query) > 12_000 or "\x00" in clean_query:
            raise MaxisGatewayError(MaxisErrorCode.BAD_REQUEST)
        try:
            assert_safe_outbound_text(clean_query, "query")
        except ContextGuardError as exc:
            raise MaxisGatewayError(MaxisErrorCode.POLICY) from exc
        clean_inputs, evidence_pack = _validated_inputs(binding, inputs)
        if clean_query != evidence_pack.query:
            raise MaxisGatewayError(MaxisErrorCode.POLICY)
        clean_user = str(user or "").strip()
        if not _USER_RE.fullmatch(clean_user):
            raise MaxisGatewayError(MaxisErrorCode.BAD_REQUEST)
        if clean_user != evidence_pack.pseudonymous_user:
            raise MaxisGatewayError(MaxisErrorCode.POLICY)
        pinned_workflow = str(workflow_id or "").strip()
        if pinned_workflow and (
            len(pinned_workflow) > 120
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]*", pinned_workflow)
        ):
            raise MaxisGatewayError(MaxisErrorCode.CONFIGURATION)
        if self.config.stage == "prod" and not pinned_workflow:
            raise MaxisGatewayError(MaxisErrorCode.POLICY)
        if pinned_workflow and clean_inputs["workflow_version"] != pinned_workflow:
            raise MaxisGatewayError(MaxisErrorCode.POLICY)
        payload = {
            "inputs": _jsonable(clean_inputs),
            "query": clean_query,
            "response_mode": "blocking",
            "user": clean_user,
        }
        if pinned_workflow:
            payload["workflow_id"] = pinned_workflow
        limit = min(binding.max_response_bytes, self.config.max_response_bytes)
        active_breaker = self._invoke_breaker(binding, api_key)
        body, _, latency_ms = self._request_bytes(
            "POST",
            binding.endpoint,
            api_key=api_key,
            payload=payload,
            response_limit=limit,
            breaker=active_breaker,
        )
        try:
            raw = _strict_json_loads(body)
            if not isinstance(raw, dict):
                raise TypeError("object required")
            if raw.get("event") not in {None, "message", "agent_message"}:
                raise TypeError("unexpected event")
            answer = raw.get("answer")
            message_id = raw.get("message_id") or raw.get("id")
            conversation_id = raw.get("conversation_id")
            if not all(isinstance(value, str) and value for value in (
                answer, message_id, conversation_id
            )):
                raise TypeError("required response fields missing")
            task_id = raw.get("task_id") or ""
            if not isinstance(task_id, str):
                raise TypeError("invalid task id")
            metadata = raw.get("metadata") if isinstance(raw.get("metadata"), dict) else {}
            usage = metadata.get("usage") if isinstance(metadata.get("usage"), dict) else {}
            usage_result = DifyUsage(
                prompt_tokens=_bounded_int(usage.get("prompt_tokens")),
                completion_tokens=_bounded_int(usage.get("completion_tokens")),
                total_tokens=_bounded_int(usage.get("total_tokens")),
            )
            result = DifyChatResponse(
                answer=answer,
                message_id=message_id,
                conversation_id=conversation_id,
                task_id=task_id,
                usage=usage_result,
                latency_ms=latency_ms,
            )
            active_breaker.record_success()
            return result
        except (ValueError, TypeError, ValidationError) as exc:
            active_breaker.record_failure()
            raise MaxisGatewayError(
                MaxisErrorCode.INVALID_RESPONSE, retryable=True
            ) from exc

    def probe_contract(
        self,
        binding: WorkflowBinding,
        *,
        api_key: str,
        workflow_version: str,
        knowledge_version: str,
        knowledge_manifest_sha256: str,
        knowledge_sentinel_sha256: str,
    ) -> DifyContractProbeResult:
        """Execute and verify a synthetic role call without returning its content.

        A successful ``/parameters`` response only proves that an app exposes the
        expected fields.  Activation additionally requires this canary to prove that
        the published Chatflow accepts an EvidencePack/RoleContext and returns the
        exact remote envelope, pinned versions, and a citation to supplied evidence.
        """

        started = self._clock()
        role_id = ""
        try:
            if not re.fullmatch(r"[0-9a-f]{64}", knowledge_sentinel_sha256 or ""):
                raise MaxisGatewayError(MaxisErrorCode.CONFIGURATION)
            pack, role_id = _synthetic_contract_probe_pack(
                binding, knowledge_manifest_sha256
            )
            response = self.invoke(
                binding,
                api_key=api_key,
                query=pack.query,
                user=pack.pseudonymous_user,
                inputs={
                    "context_pack": pack,
                    "role_id": role_id,
                    "correlation_id": pack.request_id,
                    "output_contract": binding.output_contract,
                    "workflow_version": workflow_version,
                    "knowledge_version": knowledge_version,
                },
                workflow_id=workflow_version,
            )
            remote, _ = ResponseGuard().validate_remote_role(
                response.answer,
                expected_role_id=role_id,
                payload_model=pack.payload_schema(),
                evidence=pack,
                expected_knowledge_manifest_sha256=knowledge_manifest_sha256,
            )
            latency_ms = response.latency_ms
        except ResponseGuardError as exc:
            return DifyContractProbeResult(
                ok=False,
                status_code=200,
                latency_ms=max(0, int((self._clock() - started) * 1000)),
                role_id=role_id,
                error_code=f"remote_{exc.code}"[:80],
            )
        except MaxisGatewayError as exc:
            return DifyContractProbeResult(
                ok=False,
                status_code=exc.status_code,
                latency_ms=max(0, int((self._clock() - started) * 1000)),
                role_id=role_id,
                error_code=exc.code.value,
            )

        contract_verified = True
        versions_match = (
            remote.workflow_version == workflow_version
            and remote.knowledge_version == knowledge_version
        )
        if not versions_match:
            code = (
                "workflow_version_mismatch"
                if remote.workflow_version != workflow_version
                else "knowledge_version_mismatch"
            )
            return DifyContractProbeResult(
                ok=False,
                status_code=200,
                latency_ms=latency_ms,
                role_id=role_id,
                contract_verified=contract_verified,
                error_code=code,
            )
        if remote.status != "ok":
            return DifyContractProbeResult(
                ok=False,
                status_code=200,
                latency_ms=latency_ms,
                role_id=role_id,
                contract_verified=contract_verified,
                version_verified=True,
                error_code="remote_status_not_ok",
            )

        citation_sources = {
            citation.citation_id: citation.source_id for citation in remote.citations
        }
        expected_source = pack.sources[0].source_id
        citation_verified = any(
            claim.kind == "fact"
            and expected_source in {
                citation_sources.get(citation_id) for citation_id in claim.citation_ids
            }
            for claim in remote.claims
        )
        if not citation_verified:
            return DifyContractProbeResult(
                ok=False,
                status_code=200,
                latency_ms=latency_ms,
                role_id=role_id,
                contract_verified=contract_verified,
                version_verified=True,
                error_code="citation_contract_failed",
            )
        proof_digest = hashlib.sha256(
            remote.knowledge_proof.encode("utf-8")
        ).hexdigest()
        knowledge_retrieval_verified = hmac.compare_digest(
            proof_digest, knowledge_sentinel_sha256
        )
        if not knowledge_retrieval_verified:
            return DifyContractProbeResult(
                ok=False,
                status_code=200,
                latency_ms=latency_ms,
                role_id=role_id,
                contract_verified=contract_verified,
                version_verified=True,
                citation_verified=True,
                error_code="knowledge_retrieval_failed",
            )
        return DifyContractProbeResult(
            ok=True,
            status_code=200,
            latency_ms=latency_ms,
            role_id=role_id,
            contract_verified=contract_verified,
            version_verified=True,
            citation_verified=True,
            knowledge_retrieval_verified=True,
        )

    def probe_parameters(self, *, api_key: str) -> DifyProbeResult:
        started = self._clock()
        status: int | None = None
        try:
            body, status, latency_ms = self._request_bytes(
                "GET",
                "/parameters",
                api_key=api_key,
                payload=None,
                response_limit=min(self.config.max_response_bytes, 256 * 1024),
            )
            raw = _strict_json_loads(body)
            if not isinstance(raw, dict):
                raise TypeError("object required")
            keys = _start_parameter_keys(raw)
            missing = REQUIRED_START_VARIABLES - set(keys)
            unexpected = set(keys) - REQUIRED_START_VARIABLES
            if missing or unexpected:
                # The endpoint is reachable, but the selected Dify app cannot satisfy
                # the LTM transport contract and therefore must not be activated.
                self.breaker.record_success()
                return DifyProbeResult(
                    ok=False,
                    status_code=status,
                    latency_ms=latency_ms,
                    parameter_keys=keys,
                    error_code=(
                        "missing_start_variables" if missing
                        else "unexpected_start_variables"
                    ),
                )
            result = DifyProbeResult(
                ok=True,
                status_code=status,
                latency_ms=latency_ms,
                parameter_keys=keys,
            )
            self.breaker.record_success()
            return result
        except (ValueError, TypeError):
            self.breaker.record_failure()
            elapsed = max(0, int((self._clock() - started) * 1000))
            return DifyProbeResult(
                ok=False,
                status_code=status,
                latency_ms=elapsed,
                error_code=MaxisErrorCode.INVALID_RESPONSE.value,
            )
        except MaxisGatewayError as exc:
            elapsed = max(0, int((self._clock() - started) * 1000))
            return DifyProbeResult(
                ok=False,
                status_code=exc.status_code,
                latency_ms=elapsed,
                error_code=exc.code.value,
            )


__all__ = [
    "CircuitBreaker",
    "CircuitSnapshot",
    "DifyChatResponse",
    "DifyClient",
    "DifyClientConfig",
    "DifyContractProbeResult",
    "DifyProbeResult",
    "DifyUsage",
    "REQUIRED_START_VARIABLES",
    "canonical_api_base",
]
