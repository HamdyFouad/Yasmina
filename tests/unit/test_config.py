"""Settings tests: URL derivation, secrets, and production guardrails."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.config import Settings, ensure_env_file


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
            jwt_secret_key="a-real-jwt-secret-with-32-bytes",
            api_admin_password="change-me-admin-password",
        )

    with pytest.raises(ValidationError, match="N8N_WEBHOOK_HMAC_SECRET"):
        make_settings(
            app_env="production",
            app_secret_key="a-real-secret",
            jwt_secret_key="a-real-jwt-secret-with-32-bytes",
            api_admin_password="a-real-admin-password",
            n8n_webhook_hmac_secret="change-me-n8n-hmac",
        )

    settings = make_settings(
        app_env="production",
        app_secret_key="a-real-secret",
        jwt_secret_key="a-real-jwt-secret-with-32-bytes",
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
