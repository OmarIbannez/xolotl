import os
from typing import Any, cast

import pytest
from pydantic import ValidationError

from xolotl.platform.config import DEV_JWT_SECRET, Settings, get_settings


def test_defaults_match_the_spec(monkeypatch):
    for key in list(os.environ):
        if key.startswith("XOLOTL_") or key == "PORT":
            monkeypatch.delenv(key)
    s = cast("Any", Settings)(_env_file=None)
    assert (
        s.database_url == "postgresql+psycopg://xolotl_app:xolotl@localhost:5432/xolotl"
    )
    assert (
        s.database_admin_url
        == "postgresql+psycopg://xolotl:xolotl@localhost:5432/xolotl"
    )
    assert s.env == "dev"
    assert s.jwt_issuer == "xolotl"
    assert s.access_token_ttl_minutes == 480
    assert s.mfa_token_ttl_minutes == 5
    assert s.port == 8000
    assert s.log_level == "INFO"


def test_env_prefix_and_bare_port(monkeypatch):
    monkeypatch.setenv("XOLOTL_JWT_ISSUER", "issuer-x")
    monkeypatch.setenv("PORT", "9100")
    s = cast("Any", Settings)(_env_file=None)
    assert s.jwt_issuer == "issuer-x"
    assert s.port == 9100


def test_prod_refuses_the_dev_jwt_secret(monkeypatch):
    monkeypatch.setenv("XOLOTL_ENV", "prod")
    monkeypatch.delenv("XOLOTL_JWT_SECRET", raising=False)
    with pytest.raises(ValidationError, match="JWT_SECRET"):
        cast("Any", Settings)(_env_file=None)


def test_prod_accepts_a_real_secret(monkeypatch):
    monkeypatch.setenv("XOLOTL_ENV", "prod")
    monkeypatch.setenv("XOLOTL_JWT_SECRET", "x" * 40)
    assert cast("Any", Settings)(_env_file=None).jwt_secret != DEV_JWT_SECRET


def test_short_jwt_secret_is_rejected(monkeypatch):
    monkeypatch.setenv("XOLOTL_JWT_SECRET", "short")
    with pytest.raises(ValidationError):
        cast("Any", Settings)(_env_file=None)


def test_repr_hides_the_jwt_secret_and_the_database_urls(monkeypatch):
    monkeypatch.setenv("XOLOTL_JWT_SECRET", "s3cr3t-" * 6)
    monkeypatch.setenv(
        "XOLOTL_DATABASE_URL", "postgresql+psycopg://xolotl_app:app-pw-1@db:5432/x"
    )
    monkeypatch.setenv(
        "XOLOTL_DATABASE_ADMIN_URL", "postgresql+psycopg://xolotl:admin-pw-2@db:5432/x"
    )
    s = cast("Any", Settings)(_env_file=None)
    for shown in (repr(s), str(s)):
        hidden_fields = ("jwt_secret", "database_url", "database_admin_url")
        for hidden in ("s3cr3t-", "app-pw-1", "admin-pw-2", *hidden_fields):
            assert hidden not in shown
        assert "jwt_issuer='xolotl'" in shown  # everything else still renders


def test_get_settings_is_cached(monkeypatch):
    get_settings.cache_clear()
    monkeypatch.setenv("XOLOTL_JWT_ISSUER", "first")
    first = get_settings()
    monkeypatch.setenv("XOLOTL_JWT_ISSUER", "second")
    assert get_settings() is first
    get_settings.cache_clear()
