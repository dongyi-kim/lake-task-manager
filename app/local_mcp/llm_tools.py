"""Local named LLM connections; no Jira authentication, secret reads, or arbitrary files."""

import json
from contextlib import nullcontext
from typing import Annotated, Literal
from urllib.parse import parse_qsl, unquote, urlsplit, urlunsplit

from pydantic import Field, SecretStr

from app.agent import connection_settings as connections, secrets
from app.agent.diagnostics import diagnostic_sanitizer
from app.local_mcp.schemas.llm import ConnectionSecrets, LlmConfigChanges, Provider
from app.services.ticket_commands import CommandError

LLM_TOOLS = frozenset({"get_llm_settings", "create_llm_config", "update_llm_config",
                      "delete_llm_config", "list_llm_models", "check_llm_connection", "activate_llm_config"})


def _sensitive_values(config_id=""):
    hidden = set()
    for field in secrets.SECRET_FIELDS:
        raw = secrets.get_for(config_id, field) if config_id else ""
        for text in (raw, secrets.get(field)):
            if text:
                hidden.add(text)
                if field.endswith("Headers"):
                    try:
                        hidden.update(v for v in json.loads(text).values() if isinstance(v, str) and v)
                    except (ValueError, AttributeError):
                        pass
    for field in set(ConnectionSecrets.model_fields) - set(secrets.SECRET_FIELDS):
        for text in (secrets.get_for(config_id, field) if config_id else "", secrets.get(field)):
            try:
                url = urlsplit(text)
                if url.username or url.password or url.query or url.fragment:
                    hidden.update(v for v in (text, url.username, url.password, unquote(url.username or ""),
                                             unquote(url.password or ""), url.fragment) if v)
                    hidden.update(v for _key, v in parse_qsl(url.query) if v)
            except ValueError:
                if text:
                    hidden.add(text)

    return hidden


def _redact(value, hidden):
    def clean(item):
        if isinstance(item, str):
            for text in sorted(hidden, key=len, reverse=True):
                item = item.replace(text, "[redacted]")
            return item
        if isinstance(item, dict):
            return {clean(key): clean(val) for key, val in item.items()}
        if isinstance(item, list):
            return [clean(val) for val in item]
        return item
    return clean(value)


def _public(row):
    # Consume only the UI's masked view; never re-export even its four-character key hints.
    values = row.get("secrets") or {}
    public = {}
    for key in ConnectionSecrets.model_fields:
        value = values.get(key, "")
        if key in secrets.SECRET_FIELDS:
            public[key] = "configured" if value else ""
        else:
            try:
                url = urlsplit(value)
                public[key] = urlunsplit((url.scheme, url.netloc.rsplit("@", 1)[-1], url.path, "", ""))
            except ValueError:
                public[key] = "[invalid URL]"
    return _bounded({**row, "secrets": public})


def _diagnostics(value, hidden):
    if isinstance(value, dict):
        return {key: _redact(item, hidden) if key in {"error", "errors", "warnings", "sample", "message", "detail"}
                else _diagnostics(item, hidden) for key, item in value.items()}
    if isinstance(value, list):
        return [_diagnostics(item, hidden) for item in value]
    return value


def _encoded(value):
    return json.dumps(value, ensure_ascii=False).encode("utf-8")


def _bounded(value):
    if len(_encoded(value)) > 6144:
        raise CommandError("단일 LLM 설정/모델 항목이 응답 한도를 넘습니다. LTM 설정 UI에서 확인하거나 값을 줄여 주세요.", 413)
    return value


def _page(items, start, limit, convert=lambda item: item):
    rows = []
    for item in items[start:start + limit]:
        value = _bounded(convert(item))
        if rows and len(_encoded([*rows, value])) > 6144:
            break
        rows.append(value)
    following = start + len(rows)
    return rows, {"startAt": start, "returned": len(rows), "total": len(items),
                  "hasMore": following < len(items), "nextStartAt": following if following < len(items) else None}


def _preview(value):
    raw = _encoded(value)
    return value if len(raw) <= 1024 else {"truncated": True, "originalBytes": len(raw),
                                          "preview": raw[:512].decode("utf-8", errors="ignore")}


def _validate_connection(values):
    # Validate inside the tool: SDK/Pydantic validation diagnostics may echo the original input.
    for key, value in values.items():
        if not value:
            continue
        if key.endswith("Headers"):
            try:
                parsed = json.loads(value)
                valid = isinstance(parsed, dict) and all(isinstance(v, str) for v in parsed.values())
            except ValueError:
                valid = False
            if not valid:
                raise ValueError("Headers must be a JSON object of strings.")
        elif key not in secrets.SECRET_FIELDS:
            try:
                url = urlsplit(value.strip())
                valid = (url.scheme in ("http", "https") and url.hostname and not url.username
                         and not url.password and not url.query and not url.fragment)
            except ValueError:
                valid = False
            if not valid:
                raise ValueError("Use an HTTP(S) base URL without credentials, query, or fragment.")


def _run(fn, *args, config_id="", diagnostic=False):
    captured = _sensitive_values(config_id) if diagnostic else set()
    try:
        with diagnostic_sanitizer(lambda text: _redact(text, captured | _sensitive_values(config_id))) \
                if diagnostic else nullcontext():
            result = fn(*args)
        return _diagnostics(result, captured | _sensitive_values(config_id)) if diagnostic else result
    except (KeyError, ValueError) as exc:
        raise CommandError(_redact(str(exc), captured | _sensitive_values(config_id)), 400) from None
    except Exception:
        raise CommandError("LLM 연결 설정 요청을 완료하지 못했습니다.", 500) from None


def register_llm(register, runtime):
    def get_llm_settings(start_at: Annotated[int, Field(ge=0)] = 0,
                         max_results: Annotated[int, Field(ge=1, le=10)] = 3) -> dict:
        """Read local LTM named LLM connections, active ID, verification state and environment overrides.
        Keys/headers are write-only and returned only as configured/empty indicators. No Jira login or
        LLM request is required. Environment configuration can override the selected named connection.
        Follow nextStartAt while hasMore. Pages respect an 8 KiB data budget; IDs are never shortened.
        """
        state = _run(connections.read_settings)
        rows, pagination = _page(state["configs"], start_at, max_results, _public)
        return {**state, "configs": rows, **pagination}

    def create_llm_config(name: Annotated[str, Field(min_length=1, max_length=200)], provider: Provider) -> dict:
        """Create a named LTM LLM connection candidate; returns its ID and masked settings.
        Providers: aoai (Azure deployment names), openai, openai_compat, fake (development only).
        Does not connect, activate, provision a key, or change the current conversation's connection.
        Then update fields, check auth and models, and explicitly activate the verified candidate.
        """
        return _public(_run(connections.create_config, name, provider))

    def update_llm_config(config_id: str, changes: LlmConfigChanges) -> dict:
        """Update selected fields of an existing LTM connection using the same storage as the settings UI.
        Omitted fields stay unchanged; empty strings clear stored values. Provider is immutable.
        secrets accepts write-only keys/JSON headers and base URLs, including separate simple/embedding
        endpoints. No network request or activation occurs. Changed credentials/models require new checks.
        """
        patch = changes.model_dump(exclude_none=True)
        if changes.secrets:
            patch["secrets"] = {key: value.get_secret_value() if isinstance(value, SecretStr) else value
                                for key, value in changes.secrets.model_dump(exclude_none=True).items()}
            _validate_connection(patch["secrets"])
        current = connections.get_config(config_id)
        preview = {**current, **{key: value for key, value in patch.items() if key != "secrets"},
                   "secrets": {**current["secrets"], **patch.get("secrets", {})}}
        _public(preview)  # Reject oversized public fields before saving; keys/headers stay write-only.
        return _public(_run(connections.update_config, config_id, patch, config_id=config_id))

    def delete_llm_config(config_id: str) -> dict:
        """Delete a local connection candidate and its stored keys. An active connection cannot be deleted;
        activate another verified config first. Does not revoke provider keys or modify environment values.
        """
        return _run(connections.delete_config, config_id, config_id=config_id)

    def list_llm_models(config_id: str, kind: Literal["chat", "simple", "embed"] = "chat",
                        start_at: Annotated[int, Field(ge=0)] = 0,
                        max_results: Annotated[int, Field(ge=1, le=50)] = 25) -> dict:
        """Fetch available chat/simple/embedding models for one saved connection using the UI's provider API.
        Makes network requests (except fake), but no inference calls or activation. Azure returns deployments.
        Listing errors/warnings appear in diagnostics; free-form model names remain supported.
        Select kind and follow nextStartAt. Pages use an 8 KiB data budget; model IDs are never truncated.
        """
        result = _run(connections.list_models, config_id, config_id=config_id, diagnostic=True)
        models, pagination = _page(result.get(kind) or [], start_at, max_results)
        return {"kind": kind, "models": models, **pagination, "partial": bool(result.get("error")),
                "diagnostics": _preview({key: result[key] for key in ("error", "warnings") if result.get(key)})}

    def check_llm_connection(config_id: str, stage: Literal["auth", "models"] = "auth") -> dict:
        """Explicitly test a saved connection and persist the UI's verification fingerprint; never activate it.
        auth checks credentials via model listing. models performs actual chat, embedding and capability
        probes and can incur provider charges. Use only when the user requested connection verification.
        Returns passed plus redacted diagnostics and current config. Both stages must pass before activation.
        """
        _public(connections.get_config(config_id))
        result = _run(connections.check_connection, config_id, stage, config_id=config_id, diagnostic=True)
        return {"stage": stage, "passed": bool(result.get("ok")), "result": _preview(result),
                "config": _public(connections.get_config(config_id))}

    def activate_llm_config(config_id: str) -> dict:
        """Apply a connection only after auth and model checks match its current settings fingerprint.
        Shares the UI's activation and runtime reset; candidate editing alone never activates it.
        May reset cached agent workflows/indexes. Environment overrides still take precedence.
        """
        _public(connections.get_config(config_id))
        result = _run(connections.activate_config, config_id, config_id=config_id)
        if not result.get("ok"):
            raise CommandError(_redact(result.get("error") or "연결 확인이 필요합니다.", _sensitive_values(config_id)), 400)
        return _public(connections.get_config(config_id))

    for fn in (get_llm_settings, list_llm_models):
        register(fn, read_only=True)
    for fn in (create_llm_config, update_llm_config, delete_llm_config,
               check_llm_connection, activate_llm_config):
        register(fn, read_only=False)
