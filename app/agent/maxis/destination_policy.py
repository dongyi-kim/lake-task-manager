"""Deployment-owned destination policy for MAXIS/Dify Service API calls.

Only an exact ASCII FQDN selected by the deployment may receive a Dify App API key.
Development retains three literal loopback names for a local stub; that exception is
not configurable and cannot be used by the production profile.
"""

from __future__ import annotations

import ipaddress
import os
import re
from typing import Literal
from urllib.parse import unquote, urlsplit, urlunsplit


RuntimeStage = Literal["dev", "internal-pilot", "prod", "test"]

DEFAULT_ALLOWED_HOSTS = frozenset({"dify.maxis.skax.co.kr"})
ALLOWED_HOSTS_ENV_NAMES = {
    "dev": "LAKE_MAXIS_DEV_ALLOWED_HOSTS",
    "internal-pilot": "LAKE_MAXIS_INTERNAL_PILOT_ALLOWED_HOSTS",
    "prod": "LAKE_MAXIS_PROD_ALLOWED_HOSTS",
}
DEV_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})

_FQDN_LABEL = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
_NUMERIC_ADDRESS = re.compile(r"^(?:(?:0x[0-9a-f]+|[0-9]+)\.?)+$", re.I)


class DestinationPolicyError(ValueError):
    """A safe URL/configuration error consumable by both UI and gateway layers."""

    def __init__(self, message: str, *, policy: bool = False):
        self.policy = bool(policy)
        super().__init__(message)


def _ascii_fqdn(value: str) -> str:
    host = str(value or "").strip().lower()
    if (
        not host
        or not host.isascii()
        or len(host) > 253
        or host.endswith(".")
        or "." not in host
        or "*" in host
    ):
        raise DestinationPolicyError(
            "MAXIS allowed hosts must be exact ASCII FQDNs."
        )
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        raise DestinationPolicyError(
            "MAXIS allowed hosts cannot contain an IP literal."
        )
    # Reject legacy IPv4 spellings such as 127.1 or 0x7f.0.0.1 even though
    # ``ipaddress`` intentionally accepts canonical forms only.
    if _NUMERIC_ADDRESS.fullmatch(host):
        raise DestinationPolicyError(
            "MAXIS allowed hosts cannot contain an IP literal."
        )
    labels = host.split(".")
    if any(not _FQDN_LABEL.fullmatch(label) for label in labels):
        raise DestinationPolicyError(
            "MAXIS allowed hosts must be exact ASCII FQDNs."
        )
    return host


def allowed_hosts(stage: RuntimeStage | str) -> frozenset[str]:
    """Resolve one stage's exact FQDN allowlist; a non-empty env value replaces defaults."""

    name = str(stage or "").strip().lower()
    if name not in ALLOWED_HOSTS_ENV_NAMES:
        if name == "test":
            return frozenset()
        raise DestinationPolicyError("MAXIS stage must be dev, internal-pilot, or prod.")
    raw = str(os.getenv(ALLOWED_HOSTS_ENV_NAMES[name]) or "").strip()
    if not raw:
        if name == "internal-pilot":
            raise DestinationPolicyError("Internal-pilot requires an explicit destination allowlist.")
        return DEFAULT_ALLOWED_HOSTS
    if len(raw) > 4096:
        raise DestinationPolicyError("MAXIS allowed-host environment value is too large.")
    parts = [part.strip() for part in raw.split(",")]
    if not parts or len(parts) > 32 or any(not part for part in parts):
        raise DestinationPolicyError(
            "MAXIS allowed hosts must be a comma-separated list of exact ASCII FQDNs."
        )
    return frozenset(_ascii_fqdn(part) for part in parts)


def canonical_maxis_api_base(
    value: str,
    *,
    stage: RuntimeStage | str,
    require_https: bool,
) -> str:
    """Canonicalize and authorize one Dify Service API base URL.

    Runtime callers are allowlisted. ``test`` exists solely for isolated transport
    unit tests; configured profiles have immutable stages and cannot select it.
    """

    raw = str(value or "").strip()
    if not raw:
        raise DestinationPolicyError("MAXIS Base URL is required.")
    if "\\" in raw or any(ord(character) <= 32 for character in raw):
        raise DestinationPolicyError("MAXIS Base URL contains invalid characters.")
    name = str(stage or "").strip().lower()
    if name not in {"dev", "internal-pilot", "prod", "test"}:
        raise DestinationPolicyError("MAXIS stage must be dev, internal-pilot, prod, or test.")
    # Parse deployment policy before inspecting the destination.  A malformed
    # allowlist must disable the whole stage, including the dev loopback stub;
    # otherwise an operator typo could remain latent until a later remote call.
    deployment_hosts = (
        allowed_hosts(name) if name != "test" else frozenset()
    )
    try:
        parsed = urlsplit(raw)
        port = parsed.port
    except ValueError as exc:
        raise DestinationPolicyError("MAXIS Base URL is invalid.") from exc
    scheme = parsed.scheme.lower()
    if scheme not in {"http", "https"} or not parsed.hostname:
        raise DestinationPolicyError("MAXIS Base URL must be an absolute HTTP(S) URL.")
    # ``urlsplit`` returns an empty string for authorities such as ``@host``.
    # Presence, not truthiness, is what makes userinfo unsafe.
    if parsed.username is not None or parsed.password is not None:
        raise DestinationPolicyError(
            "MAXIS Base URL cannot contain credentials.", policy=True,
        )
    if parsed.query or parsed.fragment:
        raise DestinationPolicyError(
            "MAXIS Base URL cannot contain a query or fragment.", policy=True,
        )
    if port == 0:
        raise DestinationPolicyError("MAXIS Base URL port is invalid.")

    host = parsed.hostname.lower()
    is_dev_stub = name == "dev" and host in DEV_LOOPBACK_HOSTS
    if name in {"internal-pilot", "prod"} and host in DEV_LOOPBACK_HOSTS:
        raise DestinationPolicyError(
            f"The {name} MAXIS profile cannot target loopback.", policy=True,
        )
    if scheme != "https" and (
        require_https or (name != "test" and not is_dev_stub)
    ):
        raise DestinationPolicyError(
            "HTTPS is required except for an exact dev loopback endpoint.", policy=True,
        )
    if name != "test" and not is_dev_stub and port not in {None, 443}:
        raise DestinationPolicyError(
            "Remote MAXIS endpoints must use the default HTTPS port 443.", policy=True,
        )

    if name != "test" and not is_dev_stub:
        try:
            ipaddress.ip_address(host)
        except ValueError:
            # Non-canonical numeric address forms must not be interpreted as FQDNs.
            if _NUMERIC_ADDRESS.fullmatch(host):
                raise DestinationPolicyError(
                    "MAXIS remote endpoints cannot use an IP literal.", policy=True,
                )
        else:
            raise DestinationPolicyError(
                "MAXIS remote endpoints cannot use an IP literal.", policy=True,
            )
        canonical_host = _ascii_fqdn(host)
        if canonical_host not in deployment_hosts:
            raise DestinationPolicyError(
                "MAXIS Base URL host is not in the deployment allowlist.", policy=True,
            )
        host = canonical_host
    rendered_host = f"[{host}]" if ":" in host and not host.startswith("[") else host
    # Normalize an explicit allowlisted remote :443 to the same HTTPS origin.  The
    # isolated ``test`` transport retains whatever port its mock URL declared.
    normalize_remote_443 = (
        name != "test" and not is_dev_stub
        and scheme == "https" and port == 443
    )
    if port is not None and not normalize_remote_443:
        rendered_host += f":{port}"
    path = (parsed.path or "").rstrip("/")
    if any(part == ".." for part in unquote(path).split("/")):
        raise DestinationPolicyError(
            "MAXIS Base URL cannot contain a parent path segment.", policy=True,
        )
    if path.endswith("/chat-messages") or path.endswith("/parameters"):
        raise DestinationPolicyError(
            "MAXIS Base URL must identify the /v1 API root, not one endpoint."
        )
    if not path:
        path = "/v1"
    elif not path.endswith("/v1"):
        raise DestinationPolicyError("MAXIS Base URL must end with /v1.")
    return urlunsplit((scheme, rendered_host, path, "", ""))


__all__ = [
    "ALLOWED_HOSTS_ENV_NAMES",
    "DEFAULT_ALLOWED_HOSTS",
    "DEV_LOOPBACK_HOSTS",
    "DestinationPolicyError",
    "allowed_hosts",
    "canonical_maxis_api_base",
]
