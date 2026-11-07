"""Client-IP resolution behind an optional reverse proxy."""

from __future__ import annotations

from src.api.client_ip import first_forwarded_ip, normalize_ip, resolve_client_ip


def test_normalize_ip_handles_ports_brackets_and_invalid_values() -> None:
    assert normalize_ip("203.0.113.7") == "203.0.113.7"
    assert normalize_ip("203.0.113.7:51234") == "203.0.113.7"
    assert normalize_ip("[2001:db8::1]") == "2001:db8::1"
    assert normalize_ip("2001:db8::1") == "2001:db8::1"
    assert normalize_ip("unknown") is None
    assert normalize_ip("") is None


def test_forwarded_for_uses_first_valid_address() -> None:
    assert first_forwarded_ip("203.0.113.7, 10.0.0.2") == "203.0.113.7"
    assert first_forwarded_ip("garbage, 198.51.100.4") == "198.51.100.4"
    assert first_forwarded_ip(None) is None


def test_proxy_headers_are_only_trusted_when_enabled() -> None:
    headers = {"x-forwarded-for": "203.0.113.7, 10.0.0.2"}

    assert resolve_client_ip(peer="127.0.0.1", headers=headers, trust_proxy_headers=False) == "127.0.0.1"
    assert resolve_client_ip(peer="127.0.0.1", headers=headers, trust_proxy_headers=True) == "203.0.113.7"


def test_x_real_ip_is_the_fallback_and_invalid_headers_are_ignored() -> None:
    assert (
        resolve_client_ip(
            peer="10.0.0.5",
            headers={"x-real-ip": "198.51.100.9"},
            trust_proxy_headers=True,
        )
        == "198.51.100.9"
    )
    assert (
        resolve_client_ip(
            peer="10.0.0.5",
            headers={"x-forwarded-for": "not-an-ip"},
            trust_proxy_headers=True,
        )
        == "10.0.0.5"
    )
    assert resolve_client_ip(peer=None, headers={}, trust_proxy_headers=True) == "unknown"
