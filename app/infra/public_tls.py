"""TLS policy for public Internet endpoints.

Windows' default ``ssl`` context may enumerate the current-user certificate
store. Sandboxed LTM processes are not guaranteed to have permission to open
that store, so public endpoints use the project's file-based CA bundle
explicitly. Corporate Jira/Confluence traffic keeps its separate TLS path.
"""

from __future__ import annotations

import ssl
from functools import lru_cache


def public_ca_bundle() -> str:
    """Return the deterministic CA file used for public HTTPS traffic."""
    import certifi

    return certifi.where()


@lru_cache(maxsize=1)
def public_ssl_context() -> ssl.SSLContext:
    """Build a verified context without consulting the Windows cert store."""
    return ssl.create_default_context(cafile=public_ca_bundle())
