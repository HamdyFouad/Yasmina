"""TOTP (RFC 6238) primitives, secret sealing and provisioning URIs."""

from __future__ import annotations

import base64

import pytest

from src.config import Settings
from src.services.mfa_service import (
    MfaConfigurationError,
    build_otpauth_uri,
    decrypt_secret,
    encrypt_secret,
    generate_secret,
    normalize_code,
    totp_at,
    totp_step,
    verify_totp,
)

RFC6238_SECRET = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"

# RFC 6238, Appendix B (SHA-1, 8 digits).
RFC6238_VECTORS = [
    (59, "94287082"),
    (1111111109, "07081804"),
    (1111111111, "14050471"),
    (1234567890, "89005924"),
    (2000000000, "69279037"),
    (20000000000, "65353130"),
]


def make_settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "_env_file": None,
        "database_url": None,
        "jwt_secret_key": "test-jwt-secret-key-with-at-least-32-bytes",
        "api_admin_password": "test-admin-password",
        "app_secret_key": "test-app-secret-for-mfa-sealing",
    }
    values.update(overrides)
    return Settings(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize(("timestamp", "expected"), RFC6238_VECTORS)
def test_totp_matches_rfc6238_vectors(timestamp: int, expected: str) -> None:
    assert totp_at(RFC6238_SECRET, timestamp=timestamp, digits=8) == expected


def test_totp_step_is_thirty_second_counter() -> None:
    assert totp_step(timestamp=59) == 1
    assert totp_step(timestamp=60) == 2
    assert totp_step(timestamp=59, interval=60) == 0


def test_generated_secret_is_unique_base32_of_160_bits() -> None:
    first = generate_secret()
    second = generate_secret()

    assert first != second
    assert len(first) == 32
    assert base64.b32decode(first + "=" * (-len(first) % 8)) != b""


def test_verify_accepts_current_code_and_neighbouring_window() -> None:
    now = 1234567890.0

    assert verify_totp(RFC6238_SECRET, totp_at(RFC6238_SECRET, timestamp=now), timestamp=now).valid
    previous = totp_at(RFC6238_SECRET, timestamp=now - 30)
    assert verify_totp(RFC6238_SECRET, previous, timestamp=now, window=1).valid
    assert not verify_totp(RFC6238_SECRET, previous, timestamp=now, window=0).valid


def test_verify_rejects_replayed_step_and_malformed_codes() -> None:
    now = 1234567890.0
    code = totp_at(RFC6238_SECRET, timestamp=now)
    step = totp_step(timestamp=now)

    replay = verify_totp(RFC6238_SECRET, code, timestamp=now, last_step=step)
    assert replay.valid is False
    assert replay.step is None
    assert verify_totp(RFC6238_SECRET, "12345", timestamp=now).valid is False
    assert verify_totp(RFC6238_SECRET, "abcdef", timestamp=now).valid is False


def test_normalize_code_strips_separators() -> None:
    assert normalize_code(" 123 456 ") == "123456"
    assert normalize_code("123-456") == "123456"


def test_secret_sealing_roundtrip_and_key_rotation() -> None:
    settings = make_settings()
    sealed = encrypt_secret(settings, RFC6238_SECRET)

    assert sealed != RFC6238_SECRET
    assert decrypt_secret(settings, sealed) == RFC6238_SECRET
    with pytest.raises(MfaConfigurationError, match="re-enrol"):
        decrypt_secret(make_settings(app_secret_key="a-different-app-secret"), sealed)
    with pytest.raises(MfaConfigurationError, match="re-enrol"):
        decrypt_secret(settings, "not-a-fernet-token")


def test_otpauth_uri_contains_standard_parameters() -> None:
    uri = build_otpauth_uri(
        RFC6238_SECRET,
        account="alice",
        issuer="Yasmina",
        interval=30,
        digits=6,
    )

    assert uri.startswith("otpauth://totp/Yasmina%3Aalice?")
    assert f"secret={RFC6238_SECRET}" in uri
    assert "issuer=Yasmina" in uri
    assert "algorithm=SHA1" in uri
    assert "digits=6" in uri
    assert "period=30" in uri
