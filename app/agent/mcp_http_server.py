"""Authenticated, read-only Streamable HTTP MCP endpoint for MAXIS/Dify.

This process is intentionally separate from the desktop LTM web application.
It exposes the same canonical read tools as :mod:`app.agent.mcp_server`, but it
never exposes Jira mutation tools.  A corporate OAuth2 token-introspection
endpoint validates the caller's on-behalf-of access token.  Production refuses
plain HTTP and refuses to start without TLS (or an explicitly declared mTLS
terminating proxy).

The corporate issuer, audience and certificate paths are deployment inputs;
they are not guessed or committed to the repository.  Dify should receive an
end-user OBO token through the closed-network gateway, not a long-lived LTM
write credential.
"""

from __future__ import annotations

import logging
import os
import re
import ssl
import time
from dataclasses import dataclass, field
from typing import Any, Mapping
from urllib.parse import urlparse

import httpx
from mcp.server.auth.provider import AccessToken
from mcp.server.auth.settings import AuthSettings
from mcp.server.transport_security import TransportSecuritySettings

from app.agent.mcp_server import build_server

log = logging.getLogger(__name__)

READ_SCOPE = "ltm:read"
_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}


def _required(env: Mapping[str, str], name: str) -> str:
    value = env.get(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} is required")
    return value


def _is_https_or_loopback(url: str, *, allow_loopback_http: bool) -> bool:
    try:
        parsed = urlparse(url)
        parsed.port  # force validation of malformed/out-of-range ports
    except ValueError:
        return False
    if (not parsed.hostname or parsed.username or parsed.password
            or parsed.query or parsed.fragment):
        return False
    if parsed.scheme == "https":
        return True
    return bool(
        allow_loopback_http
        and parsed.scheme == "http"
        and (parsed.hostname or "").lower() in _LOOPBACK_HOSTS
    )


def _valid_allowed_host(value: str) -> bool:
    """Accept only one exact Host-header authority, never wildcard/URL syntax."""
    if not value or any(ord(ch) < 33 for ch in value) or "*" in value:
        return False
    try:
        parsed = urlparse("//" + value)
        parsed.port  # validate malformed and out-of-range ports
    except ValueError:
        return False
    return bool(
        parsed.hostname
        and not parsed.username
        and not parsed.password
        and not parsed.path
        and not parsed.query
        and not parsed.fragment
    )


def _as_scopes(value: object) -> list[str]:
    if isinstance(value, str):
        return [part for part in value.split() if part]
    if isinstance(value, list):
        return [part for part in value if isinstance(part, str) and part]
    return []


def _audiences(value: object) -> set[str]:
    if isinstance(value, str):
        return {value}
    if isinstance(value, list):
        return {item for item in value if isinstance(item, str)}
    return set()


class OAuthIntrospectionTokenVerifier:
    """Validate opaque/JWT access tokens at an RFC 7662 introspection endpoint."""

    def __init__(
        self,
        *,
        introspection_url: str,
        client_id: str,
        client_secret: str,
        audience: str,
        required_scope: str = READ_SCOPE,
        identity_claim: str = "sub",
        timeout_seconds: float = 5.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._url = introspection_url
        self._client_id = client_id
        self._client_secret = client_secret
        self._audience = audience
        self._required_scope = required_scope
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.:-]{0,63}", identity_claim):
            raise ValueError("identity_claim is invalid")
        self._identity_claim = identity_claim
        self._timeout = timeout_seconds
        self._transport = transport

    async def verify_token(self, token: str) -> AccessToken | None:
        if not token or len(token) > 16_384:
            return None
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(self._timeout),
                transport=self._transport,
            ) as client:
                response = await client.post(
                    self._url,
                    data={"token": token, "token_type_hint": "access_token"},
                    auth=(self._client_id, self._client_secret),
                    headers={"Accept": "application/json"},
                )
            response.raise_for_status()
            body = response.json()
        except (httpx.HTTPError, ValueError, TypeError):
            # Token material and response bodies must never enter logs.
            log.warning("MCP OAuth token introspection failed")
            return None

        if not isinstance(body, dict) or body.get("active") is not True:
            return None
        scopes = _as_scopes(body.get("scope"))
        if self._required_scope not in scopes:
            return None
        if self._audience not in _audiences(body.get("aud")):
            return None

        expires_at = body.get("exp")
        if not isinstance(expires_at, int) or expires_at <= int(time.time()):
            return None
        subject = body.get("sub")
        client_id = body.get("client_id")
        if not isinstance(subject, str) or not subject.strip():
            return None
        delegated_identity = body.get(self._identity_claim)
        if not isinstance(delegated_identity, str) or not delegated_identity.strip():
            return None
        if not isinstance(client_id, str) or not client_id.strip():
            client_id = "obo-client"
        return AccessToken(
            token=token,
            client_id=client_id,
            scopes=scopes,
            expires_at=expires_at,
            resource=self._audience,
            subject=subject,
            claims={
                "token_type": body.get("token_type", "Bearer"),
                "ltm_user_id": delegated_identity.strip(),
            },
        )


@dataclass(frozen=True)
class HttpMcpSettings:
    issuer_url: str
    resource_url: str
    audience: str
    introspection_url: str
    oauth_client_id: str
    oauth_client_secret: str = field(repr=False)
    identity_claim: str
    host: str
    port: int
    allowed_hosts: tuple[str, ...]
    tls_certfile: str | None
    tls_keyfile: str | None
    tls_ca_certs: str | None
    behind_mtls_proxy: bool

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "HttpMcpSettings":
        values = os.environ if env is None else env
        allow_loopback_http = values.get("LTM_MCP_ALLOW_HTTP_LOOPBACK", "") == "1"
        resource_url = _required(values, "LTM_MCP_RESOURCE_URL")
        issuer_url = _required(values, "LTM_MCP_OAUTH_ISSUER_URL")
        introspection_url = _required(values, "LTM_MCP_OAUTH_INTROSPECTION_URL")
        for name, url in (
            ("LTM_MCP_RESOURCE_URL", resource_url),
            ("LTM_MCP_OAUTH_ISSUER_URL", issuer_url),
            ("LTM_MCP_OAUTH_INTROSPECTION_URL", introspection_url),
        ):
            if not _is_https_or_loopback(url, allow_loopback_http=allow_loopback_http):
                raise RuntimeError(f"{name} must use HTTPS (loopback HTTP is development-only)")

        try:
            port = int(values.get("LTM_MCP_PORT", "4460"))
        except ValueError as exc:
            raise RuntimeError("LTM_MCP_PORT must be an integer") from exc
        if not 1 <= port <= 65_535:
            raise RuntimeError("LTM_MCP_PORT must be between 1 and 65535")

        hosts = tuple(
            item.strip()
            for item in values.get("LTM_MCP_ALLOWED_HOSTS", "").split(",")
            if item.strip()
        )
        if not hosts:
            parsed = urlparse(resource_url)
            hosts = tuple(filter(None, (parsed.netloc, parsed.hostname)))
        if any(not _valid_allowed_host(value) for value in hosts):
            raise RuntimeError("LTM_MCP_ALLOWED_HOSTS must contain explicit host names")

        certfile = values.get("LTM_MCP_TLS_CERTFILE", "").strip() or None
        keyfile = values.get("LTM_MCP_TLS_KEYFILE", "").strip() or None
        ca_certs = values.get("LTM_MCP_TLS_CA_CERTS", "").strip() or None
        behind_proxy = values.get("LTM_MCP_BEHIND_MTLS_PROXY", "") == "1"
        identity_claim = values.get("LTM_MCP_OAUTH_IDENTITY_CLAIM", "sub").strip() or "sub"
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.:-]{0,63}", identity_claim):
            raise RuntimeError("LTM_MCP_OAUTH_IDENTITY_CLAIM is invalid")
        bind_host = values.get("LTM_MCP_HOST", "127.0.0.1").strip() or "127.0.0.1"
        is_dev_loopback = urlparse(resource_url).scheme == "http" and allow_loopback_http
        if not is_dev_loopback and not behind_proxy and not (certfile and keyfile and ca_certs):
            raise RuntimeError(
                "production MCP requires mTLS certificate/key/CA or LTM_MCP_BEHIND_MTLS_PROXY=1"
            )
        if behind_proxy and bind_host.casefold() not in _LOOPBACK_HOSTS:
            raise RuntimeError(
                "an mTLS-terminating proxy requires the MCP backend to bind to loopback"
            )

        return cls(
            issuer_url=issuer_url,
            resource_url=resource_url,
            audience=values.get("LTM_MCP_OAUTH_AUDIENCE", "").strip() or resource_url,
            introspection_url=introspection_url,
            oauth_client_id=_required(values, "LTM_MCP_OAUTH_CLIENT_ID"),
            oauth_client_secret=_required(values, "LTM_MCP_OAUTH_CLIENT_SECRET"),
            identity_claim=identity_claim,
            host=bind_host,
            port=port,
            allowed_hosts=hosts,
            tls_certfile=certfile,
            tls_keyfile=keyfile,
            tls_ca_certs=ca_certs,
            behind_mtls_proxy=behind_proxy,
        )


def build_http_app(settings: HttpMcpSettings):
    verifier = OAuthIntrospectionTokenVerifier(
        introspection_url=settings.introspection_url,
        client_id=settings.oauth_client_id,
        client_secret=settings.oauth_client_secret,
        audience=settings.audience,
        identity_claim=settings.identity_claim,
    )
    auth = AuthSettings(
        issuer_url=settings.issuer_url,
        resource_server_url=settings.resource_url,
        required_scopes=[READ_SCOPE],
    )
    server = build_server(
        server_kwargs={"auth": auth, "token_verifier": verifier},
        require_authenticated_subject=True,
    )
    transport_security = TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=list(settings.allowed_hosts),
        allowed_origins=[],
    )
    return server.streamable_http_app(
        streamable_http_path="/mcp",
        json_response=True,
        stateless_http=True,
        transport_security=transport_security,
        host=settings.host,
    )


def main() -> None:
    import uvicorn

    settings = HttpMcpSettings.from_env()
    kwargs: dict[str, Any] = {
        "host": settings.host,
        "port": settings.port,
        "log_level": "info",
        "proxy_headers": settings.behind_mtls_proxy,
        "forwarded_allow_ips": "127.0.0.1" if settings.behind_mtls_proxy else "",
    }
    if settings.tls_certfile:
        kwargs.update(
            ssl_certfile=settings.tls_certfile,
            ssl_keyfile=settings.tls_keyfile,
            ssl_ca_certs=settings.tls_ca_certs,
            ssl_cert_reqs=ssl.CERT_REQUIRED,
        )
    uvicorn.run(build_http_app(settings), **kwargs)


if __name__ == "__main__":
    main()
