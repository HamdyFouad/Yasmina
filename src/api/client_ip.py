"""Client IP extraction for proxied deployments.

``X-Forwarded-For`` is only trusted when ``TRUST_PROXY_HEADERS`` is enabled,
which must only happen for an API that is not directly reachable (for example
behind the production reverse proxy). The left-most address is the original
client as long as the proxy overwrites/appends the header correctly.
"""

from __future__ import annotations

import ipaddress
from collections.abc import Mapping

__all__ = [
    "first_forwarded_ip",
    "normalize_ip",
    "resolve_client_ip",
]


def normalize_ip(raw: str | None) -> str | None:
    """Return a canonical IP address string, tolerating ports/brackets."""

    if raw is None:
        return None
    candidate = raw.strip()
    if not candidate:
        return None
    if candidate.startswith("[") and "]" in candidate:
        candidate = candidate[1 : candidate.index("]")]
    try:
        return str(ipaddress.ip_address(candidate))
    except ValueError:
        pass
    # IPv4 with a trailing :port.
    if candidate.count(":") == 1:
        host = candidate.rsplit(":", 1)[0]
        try:
            return str(ipaddress.ip_address(host))
        except ValueError:
            return None
    return None


def first_forwarded_ip(header: str | None) -> str | None:
    if not header:
        return None
    for part in header.split(","):
        address = normalize_ip(part)
        if address is not None:
            return address
    return None


def resolve_client_ip(
    *,
    peer: str | None,
    headers: Mapping[str, str],
    trust_proxy_headers: bool,
) -> str:
    """Resolve the rate-limit/log client identity for one request."""

    if trust_proxy_headers:
        forwarded = first_forwarded_ip(headers.get("x-forwarded-for"))
        if forwarded is not None:
            return forwarded
        real_ip = normalize_ip(headers.get("x-real-ip"))
        if real_ip is not None:
            return real_ip
    return normalize_ip(peer) or (peer or "unknown")
