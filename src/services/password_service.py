"""Argon2id password hashing and password-policy validation."""

from __future__ import annotations

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

__all__ = [
    "PasswordHashError",
    "dummy_verify",
    "hash_password",
    "password_needs_rehash",
    "validate_password",
    "verify_password",
]

_HASHER = PasswordHasher(
    time_cost=3,
    memory_cost=65536,
    parallelism=4,
    hash_len=32,
    salt_len=16,
)
_DUMMY_HASH = _HASHER.hash("yasmina-timing-equalizer")


class PasswordHashError(ValueError):
    """Raised when a password cannot be hashed or verified."""


def validate_password(password: str, *, min_length: int = 12) -> str:
    if not isinstance(password, str) or len(password) < min_length:
        raise PasswordHashError(f"password must contain at least {min_length} characters")
    if password.strip() != password or not password.strip():
        raise PasswordHashError("password must not start/end with whitespace or be empty")
    if password.lower() == password and password.upper() == password:
        raise PasswordHashError("password must contain letters")
    return password


def hash_password(password: str, *, min_length: int = 12) -> str:
    validate_password(password, min_length=min_length)
    return _HASHER.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    if not password or not password_hash:
        return False
    try:
        return _HASHER.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def password_needs_rehash(password_hash: str) -> bool:
    try:
        return _HASHER.check_needs_rehash(password_hash)
    except InvalidHashError:
        return True


def dummy_verify(password: str) -> None:
    """Burn a comparable amount of time when a username does not exist."""

    verify_password(password, _DUMMY_HASH)
