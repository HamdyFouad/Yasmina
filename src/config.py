"""Application settings for Project Yasmina.

Settings are loaded from environment variables and, when present, a local
``.env`` file (see ``.env.example``). Secrets use :class:`pydantic.SecretStr`
so they never leak into logs or tracebacks.
"""

from __future__ import annotations

import os
import re
import shutil
from functools import lru_cache
from pathlib import Path
from typing import Literal, Self
from urllib.parse import quote_plus, urlsplit

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

__all__ = ["Settings", "ensure_env_file", "get_settings", "reset_settings_cache"]

_HEX64_RE = re.compile(r"^[0-9a-f]{64}$")
_SUPPORTED_DB_SCHEMES = {"postgresql", "postgresql+asyncpg"}


class Settings(BaseSettings):
    """Typed, validated configuration for the whole application."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        validate_default=True,
    )

    # --- Application -------------------------------------------------------
    app_env: Literal["local", "test", "staging", "production"] = "local"
    app_debug: bool = False
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    app_timezone: str = "Europe/Berlin"
    app_secret_key: SecretStr = SecretStr("change-me-app-secret")

    # --- API / JWT ---------------------------------------------------------
    jwt_secret_key: SecretStr = SecretStr("change-me-jwt-secret-change-me-now")
    jwt_algorithm: Literal["HS256", "HS384", "HS512"] = "HS256"
    jwt_expiry_minutes: int = Field(default=60, ge=1, le=1440)
    jwt_issuer: str = "yasmina"
    api_admin_username: str = "admin"
    api_admin_password: SecretStr = SecretStr("change-me-admin-password")
    max_upload_size_mb: int = Field(default=25, ge=1, le=200)

    # --- Company / invoice seller -----------------------------------------
    company_name: str = "Yasmina GmbH"
    company_street: str | None = None
    company_city: str | None = None
    company_postal_code: str | None = None
    company_country_code: str = "DE"
    company_vat_id: str | None = None
    company_iban: str | None = None
    company_email: str | None = None

    # --- Database ----------------------------------------------------------
    postgres_user: str = "yasmina"
    postgres_password: SecretStr = SecretStr("yasmina")
    postgres_db: str = "yasmina"
    postgres_host: str = "localhost"
    postgres_port: int = Field(default=5432, ge=1, le=65535)
    database_url: str | None = None
    database_echo: bool = False
    database_pool_size: int = Field(default=10, ge=1, le=50)

    # --- MinIO -------------------------------------------------------------
    minio_endpoint: str = "localhost:9000"
    minio_access_key: SecretStr = SecretStr("yasmina-minio")
    minio_secret_key: SecretStr = SecretStr("change-me-minio")
    minio_bucket: str = "yasmina"
    minio_secure: bool = False

    # --- ClamAV ------------------------------------------------------------
    clamav_host: str = "localhost"
    clamav_port: int = Field(default=3310, ge=1, le=65535)
    clamav_timeout_seconds: float = Field(default=30.0, gt=0, le=300)
    preflight_timeout_seconds: float = Field(default=5.0, gt=0, le=60)

    # --- LLM / embeddings --------------------------------------------------
    deepseek_api_key: SecretStr | None = None
    deepseek_base_url: str = "https://api.deepseek.com/v1"
    deepseek_chat_model: str = "deepseek-chat"
    deepseek_reasoner_model: str = "deepseek-reasoner"
    deepseek_temperature: float = Field(default=0.0, ge=0.0, le=2.0)
    embedding_model: str = "BAAI/bge-m3"
    embedding_dimensions: int = Field(default=1024, ge=1, le=8192)

    # --- n8n ---------------------------------------------------------------
    n8n_webhook_base_url: str = "http://localhost:5678"
    n8n_webhook_hmac_secret: SecretStr | None = None

    # --- ERPNext -----------------------------------------------------------
    erpnext_base_url: str = "http://localhost:8080"
    erpnext_api_key: SecretStr | None = None
    erpnext_api_secret: SecretStr | None = None
    erpnext_company: str = "Yasmina"

    # --- HITL approval -----------------------------------------------------
    ed25519_approval_public_key: str | None = None
    approval_signature_required: bool = True

    # --- Pricing -----------------------------------------------------------
    default_currency: str = "EUR"
    default_vat_rate: float = Field(default=0.19, ge=0.0, le=1.0)
    default_margin_rate: float = Field(default=0.15, ge=0.0, le=5.0)
    offer_validity_days: int = Field(default=30, ge=1, le=365)

    # --- Storage / audit ---------------------------------------------------
    gaeb_export_dir: Path = Path("var/exports/gaeb")
    audit_hash_algorithm: Literal["sha256"] = "sha256"

    # ------------------------------------------------------------------
    # Validators
    # ------------------------------------------------------------------

    @field_validator("database_url", mode="before")
    @classmethod
    def _empty_to_none(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("database_url")
    @classmethod
    def _validate_database_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        parts = urlsplit(value)
        if parts.scheme not in _SUPPORTED_DB_SCHEMES:
            raise ValueError(f"unsupported database scheme {parts.scheme!r}; expected postgresql+asyncpg")
        if parts.scheme == "postgresql":
            value = "postgresql+asyncpg://" + value.split("://", 1)[1]
        if not parts.hostname or not parts.path.strip("/"):
            raise ValueError("database URL must include a host and a database name")
        return value

    @field_validator("ed25519_approval_public_key")
    @classmethod
    def _validate_public_key(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        normalized = value.strip().lower()
        if not _HEX64_RE.fullmatch(normalized):
            raise ValueError("ed25519 public key must be 32 bytes encoded as 64 lowercase hex characters")
        return normalized

    @field_validator("default_currency")
    @classmethod
    def _validate_currency(cls, value: str) -> str:
        normalized = value.strip().upper()
        if normalized not in {"EUR", "USD", "CHF"}:
            raise ValueError(f"unsupported currency {value!r}")
        return normalized

    @model_validator(mode="after")
    def _assemble_database_url(self) -> Self:
        if self.database_url:
            return self

        user = quote_plus(self.postgres_user)
        password = quote_plus(self.postgres_password.get_secret_value())
        database = quote_plus(self.postgres_db)
        url = f"postgresql+asyncpg://{user}:{password}@{self.postgres_host}:{self.postgres_port}/{database}"
        object.__setattr__(self, "database_url", url)
        return self

    @model_validator(mode="after")
    def _check_production_guardrails(self) -> Self:
        if self.app_env == "production":
            if self.app_secret_key.get_secret_value() == "change-me-app-secret":
                raise ValueError("APP_SECRET_KEY must be changed in production")
            if self.jwt_secret_key.get_secret_value() == "change-me-jwt-secret-change-me-now":
                raise ValueError("JWT_SECRET_KEY must be changed in production")
            if self.api_admin_password.get_secret_value() == "change-me-admin-password":
                raise ValueError("API_ADMIN_PASSWORD must be changed in production")
        return self

    # ------------------------------------------------------------------
    # Derived helpers
    # ------------------------------------------------------------------

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    @property
    def asyncpg_dsn(self) -> str:
        """Database URL in the plain ``postgresql://`` form asyncpg expects."""

        assert self.database_url is not None  # guaranteed by validator
        return self.database_url.replace("postgresql+asyncpg://", "postgresql://", 1)

    @property
    def database_dsn_safe(self) -> str:
        """Database URL with the password masked, suitable for logs."""

        assert self.database_url is not None  # guaranteed by validator
        parts = urlsplit(self.database_url)
        if parts.password is None:
            return self.database_url
        masked = parts._replace(netloc=f"{parts.username}:***@{parts.hostname}:{parts.port}")
        return masked.geturl()


def ensure_env_file(
    env_path: Path | None = None,
    template_path: Path | None = None,
) -> bool:
    """Create a local ``.env`` from ``.env.example`` when it is missing.

    Returns ``True`` when a file was created. Production is deliberately
    excluded: production deployments must inject real secrets through the
    environment instead of silently copying development placeholders.
    """

    env = env_path if env_path is not None else Path(".env")
    template = template_path if template_path is not None else Path(".env.example")

    if env.exists() or not template.is_file():
        return False
    if os.getenv("APP_ENV", "").strip().lower() == "production":
        return False

    shutil.copyfile(template, env)
    return True


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings singleton."""

    return Settings()


def reset_settings_cache() -> None:
    """Clear the settings cache (used by tests after mutating the environment)."""

    get_settings.cache_clear()
