"""TOTP multi-factor authentication primitives (RFC 6238) and secret sealing.

The TOTP implementation is deliberately dependency-free: HMAC-SHA1, dynamic
truncation and a 30-second time step are defined by RFC 4226/6238 and are
verified against the RFC 6238 test vectors in the unit suite. Enrolled secrets
are sealed with Fernet (AES-128-CBC + HMAC) using a key derived from
``APP_SECRET_KEY`` so a database dump does not expose second factors.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import quote, urlencode

from cryptography.fernet import Fernet, InvalidToken

from src.config import Settings

__all__ = [
    "MfaConfigurationError",
    "MfaVerification",
    "build_otpauth_uri",
    "decrypt_secret",
    "encrypt_secret",
    "generate_secret",
    "normalize_code",
    "totp_at",
    "totp_step",
    "verify_totp",
]

_DEFAULT_INTERVAL = 30
_DEFAULT_DIGITS = 6
_SECRET_BYTES = 20


class MfaConfigurationError(RuntimeError):
    """Raised when an MFA secret cannot be sealed or opened."""


def generate_secret() -> str:
    """Return a fresh 160-bit base32 secret without padding."""

    return base64.b32encode(secrets.token_bytes(_SECRET_BYTES)).decode("ascii").rstrip("=")


def _secret_bytes(secret: str) -> bytes:
    normalized = secret.strip().replace(" ", "").upper()
    padding = "=" * (-len(normalized) % 8)
    try:
        return base64.b32decode(normalized + padding, casefold=True)
    except (binascii.Error, ValueError) as exc:
        raise MfaConfigurationError("MFA secret is not valid base32") from exc


def _fernet(settings: Settings) -> Fernet:
    key = hashlib.sha256(settings.app_secret_key.get_secret_value().encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(key))


def encrypt_secret(settings: Settings, secret: str) -> str:
    """Seal a base32 TOTP secret for storage."""

    return _fernet(settings).encrypt(secret.encode("ascii")).decode("ascii")


def decrypt_secret(settings: Settings, sealed: str) -> str:
    try:
        return _fernet(settings).decrypt(sealed.encode("ascii")).decode("ascii")
    except (InvalidToken, ValueError) as exc:
        raise MfaConfigurationError("stored MFA secret cannot be opened; re-enrol the second factor") from exc


def totp_step(*, timestamp: float | None = None, interval: int = _DEFAULT_INTERVAL) -> int:
    """Return the RFC 6238 time-step counter for a POSIX timestamp."""

    moment = datetime.now(UTC).timestamp() if timestamp is None else timestamp
    return int(moment // interval)


def _hotp(secret: str, counter: int, digits: int) -> str:
    digest = hmac.new(
        _secret_bytes(secret),
        counter.to_bytes(8, "big"),
        hashlib.sha1,
    ).digest()
    offset = digest[-1] & 0x0F
    binary = int.from_bytes(digest[offset : offset + 4], "big") & 0x7FFFFFFF
    return str(binary % (10**digits)).zfill(digits)


def totp_at(
    secret: str,
    *,
    timestamp: float | None = None,
    interval: int = _DEFAULT_INTERVAL,
    digits: int = _DEFAULT_DIGITS,
) -> str:
    """Compute the TOTP code for a secret at a POSIX timestamp."""

    return _hotp(secret, totp_step(timestamp=timestamp, interval=interval), digits)


def normalize_code(code: str) -> str:
    """Strip separators/whitespace from a user-supplied one-time code."""

    return "".join(character for character in code if not character.isspace() and character != "-")


@dataclass(frozen=True, slots=True)
class MfaVerification:
    """Outcome of checking a TOTP code, including the matched time step."""

    valid: bool
    step: int | None

    def as_dict(self) -> dict[str, object]:
        return {"valid": self.valid, "step": self.step}


def verify_totp(
    secret: str,
    code: str,
    *,
    timestamp: float | None = None,
    interval: int = _DEFAULT_INTERVAL,
    digits: int = _DEFAULT_DIGITS,
    window: int = 1,
    last_step: int | None = None,
) -> MfaVerification:
    """Verify a TOTP code within ``±window`` steps and reject replays.

    ``last_step`` is the highest step already accepted for the account; a code
    whose step is not strictly greater than it has been used before and is
    rejected even though the HMAC still matches.
    """

    candidate = normalize_code(code)
    if not candidate.isdigit() or len(candidate) != digits:
        return MfaVerification(valid=False, step=None)
    current = totp_step(timestamp=timestamp, interval=interval)
    for step in range(current - window, current + window + 1):
        if step < 0:
            continue
        if last_step is not None and step <= last_step:
            continue
        if hmac.compare_digest(_hotp(secret, step, digits), candidate):
            return MfaVerification(valid=True, step=step)
    return MfaVerification(valid=False, step=None)


def build_otpauth_uri(
    secret: str,
    *,
    account: str,
    issuer: str,
    interval: int = _DEFAULT_INTERVAL,
    digits: int = _DEFAULT_DIGITS,
) -> str:
    """Build the standard ``otpauth://totp`` provisioning URI for authenticators."""

    label = quote(f"{issuer}:{account}", safe="")
    query = urlencode(
        {
            "secret": secret,
            "issuer": issuer,
            "algorithm": "SHA1",
            "digits": digits,
            "period": interval,
        }
    )
    return f"otpauth://totp/{label}?{query}"
