"""Settings tests: URL derivation, secrets, and production guardrails."""

from __future__ import annotations

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from pydantic import ValidationError

from src.config import Settings, ensure_env_file
from src.integrations.telegram_notifier import TelegramNotifier
from src.services.approval_service import generate_keypair


def make_settings(**overrides: object) -> Settings:
    overrides.setdefault("database_url", None)
    return Settings(_env_file=None, **overrides)


def test_database_url_is_derived_and_password_is_escaped() -> None:
    settings = make_settings(postgres_user="yasmina", postgres_password="p@ss word", postgres_db="yasmina")

    assert settings.database_url == "postgresql+asyncpg://yasmina:p%40ss+word@localhost:5432/yasmina"
    assert "***" in settings.database_dsn_safe
    assert "p%40ss" not in settings.database_dsn_safe


def test_explicit_sync_url_is_upgraded_to_asyncpg() -> None:
    settings = make_settings(database_url="postgresql://u:p@db:5432/yasmina")

    assert settings.database_url == "postgresql+asyncpg://u:p@db:5432/yasmina"
    assert settings.asyncpg_dsn == "postgresql://u:p@db:5432/yasmina"


def test_clamav_defaults() -> None:
    settings = make_settings()

    assert settings.clamav_host == "localhost"
    assert settings.clamav_port == 3310
    assert settings.preflight_timeout_seconds == 5.0


def test_unsupported_database_scheme_is_rejected() -> None:
    with pytest.raises(ValidationError, match="unsupported database scheme"):
        make_settings(database_url="mysql://user:pass@localhost/yasmina")


def test_ed25519_public_key_validation() -> None:
    valid = "ab" * 32
    assert make_settings(ed25519_approval_public_key=valid.upper()).ed25519_approval_public_key == valid
    assert make_settings(ed25519_approval_public_key="").ed25519_approval_public_key is None

    with pytest.raises(ValidationError):
        make_settings(ed25519_approval_public_key="not-a-key")


def test_ed25519_private_key_derives_and_matches_public_key() -> None:
    private_hex, public_hex = generate_keypair()

    derived = make_settings(
        ed25519_approval_private_key=private_hex,
        ed25519_approval_public_key=None,
    )
    assert derived.ed25519_approval_public_key == public_hex
    assert derived.ed25519_approval_private_key is not None
    assert derived.ed25519_approval_private_key.get_secret_value() == private_hex

    paired = make_settings(
        ed25519_approval_private_key=private_hex,
        ed25519_approval_public_key=public_hex,
    )
    assert paired.ed25519_approval_public_key == public_hex

    other_private, _ = generate_keypair()
    with pytest.raises(ValidationError, match="does not match"):
        make_settings(
            ed25519_approval_private_key=other_private,
            ed25519_approval_public_key=public_hex,
        )


def test_ed25519_private_key_accepts_pem_and_rejects_garbage() -> None:
    private_hex, public_hex = generate_keypair()
    private = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(private_hex))
    pem = private.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()

    settings = make_settings(
        ed25519_approval_private_key=pem.replace("\n", "\\n"),
        ed25519_approval_public_key=None,
    )
    assert settings.ed25519_approval_public_key == public_hex

    with pytest.raises(ValidationError):
        make_settings(
            ed25519_approval_private_key="not-a-key",
            ed25519_approval_public_key=None,
        )

    assert (
        make_settings(
            ed25519_approval_private_key="",
            ed25519_approval_public_key=None,
        ).ed25519_approval_private_key
        is None
    )


def test_telegram_chat_id_accepts_a_dedicated_destination() -> None:
    settings = make_settings(
        telegram_bot_token="123:abc",
        telegram_chat_id="-100123",
        telegram_approval_chat_id="-100999",
    )

    assert TelegramNotifier(settings).chat_id == "-100123"
    fallback = make_settings(telegram_bot_token="123:abc", telegram_approval_chat_id="-100999")
    assert TelegramNotifier(fallback).chat_id == "-100999"


def test_production_requires_changed_secrets() -> None:
    with pytest.raises(ValidationError, match="APP_SECRET_KEY"):
        make_settings(app_env="production", app_secret_key="change-me-app-secret")

    with pytest.raises(ValidationError, match="JWT_SECRET_KEY"):
        make_settings(
            app_env="production",
            app_secret_key="a-real-secret",
            jwt_secret_key="change-me-jwt-secret-change-me-now",
        )

    with pytest.raises(ValidationError, match="API_ADMIN_PASSWORD"):
        make_settings(
            app_env="production",
            app_secret_key="a-real-secret",
            jwt_secret_key="a-real-jwt-secret-with-32-bytes-ok",
            api_admin_password="change-me-admin-password",
        )

    with pytest.raises(ValidationError, match="N8N_WEBHOOK_HMAC_SECRET"):
        make_settings(
            app_env="production",
            app_secret_key="a-real-secret",
            jwt_secret_key="a-real-jwt-secret-with-32-bytes-ok",
            api_admin_password="a-real-admin-password",
            n8n_webhook_hmac_secret="change-me-n8n-hmac",
        )

    settings = make_settings(
        app_env="production",
        app_secret_key="a-real-secret",
        jwt_secret_key="a-real-jwt-secret-with-32-bytes-ok",
        api_admin_password="a-real-admin-password",
    )
    assert settings.is_production is True


def test_pricing_defaults() -> None:
    settings = make_settings()

    assert settings.default_currency == "EUR"
    assert settings.default_vat_rate == pytest.approx(0.19)
    assert settings.default_margin_rate == pytest.approx(0.15)
    assert settings.embedding_dimensions == 1024


def test_integration_defaults() -> None:
    settings = make_settings()

    assert settings.erpnext_max_retries == 2
    assert settings.erpnext_item_group == "Products"
    assert settings.n8n_callback_max_age_seconds == 300
    assert settings.n8n_rfq_webhook_path == "/webhook/yasmina/rfq"


def test_scraper_defaults() -> None:
    settings = make_settings()

    assert settings.scraper_request_delay_seconds == 2.0
    assert settings.scraper_max_pages == 5
    assert settings.scraper_archive_raw is True
    assert settings.scraper_user_agents == []
    assert settings.scraper_schedule_cron == "0 2 * * *"


def test_mfa_totp_settings_accept_env_style_values(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("MFA_TOTP_DIGITS", "8")
    monkeypatch.setenv("MFA_TOTP_INTERVAL_SECONDS", "30")
    monkeypatch.setenv("MFA_TOTP_WINDOW", "1")
    monkeypatch.setenv("AUTH_TOKEN_RETENTION_DAYS", "14")

    settings = make_settings()

    assert settings.mfa_totp_digits == 8
    assert settings.mfa_totp_interval_seconds == 30
    assert settings.mfa_totp_window == 1
    assert settings.auth_token_retention_days == 14


def test_mfa_totp_digits_rejects_unsupported_values() -> None:
    with pytest.raises(ValidationError, match="must be 6 or 8"):
        make_settings(mfa_totp_digits=7)


def test_ensure_env_file_copies_template_once(workspace_tmp, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.delenv("APP_ENV", raising=False)
    template = workspace_tmp / "example.env"
    template.write_text("APP_ENV=local\n", encoding="utf-8")
    env = workspace_tmp / "local.env"

    assert ensure_env_file(env, template) is True
    assert env.read_text(encoding="utf-8") == "APP_ENV=local\n"
    assert ensure_env_file(env, template) is False


def test_ensure_env_file_never_copies_in_production(workspace_tmp, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("APP_ENV", "production")
    template = workspace_tmp / "example.env"
    template.write_text("APP_ENV=production\n", encoding="utf-8")
    env = workspace_tmp / "production.env"

    assert ensure_env_file(env, template) is False
    assert not env.exists()
