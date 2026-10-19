"""Runtime configuration, read from environment variables prefixed ``XOLOTL_``."""

import base64
import hashlib
import os
import socket
from functools import lru_cache
from pathlib import Path
from typing import ClassVar, Literal, Self

from pydantic import AliasChoices, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import make_url
from sqlalchemy.exc import ArgumentError

# The role the application signs in as: bootstrap creates it, the migrations grant to
# it, and an app URL derived from the admin URL signs in as it.
APP_ROLE = "xolotl_app"
# The driver every engine uses. Hosts hand out libpq URLs (Render's connection string
# is postgresql://..., Heroku's postgres://...), which SQLAlchemy would otherwise read
# as the psycopg2 driver or not at all.
DRIVER_SCHEME = "postgresql+psycopg://"
LIBPQ_SCHEMES = ("postgres://", "postgresql://")
DEV_JWT_SECRET = "dev-only-insecure-jwt-secret-change-me-000000"  # ruff: ignore[hardcoded-password-string]
# Public and deterministic, so for local development only: XOLOTL_ENV=prod refuses
# it. Derived from a digest because an all-zero key would be a degenerate default.
DEV_MASTER_KEY_BYTES = hashlib.sha256(b"xolotl-dev-master-key").digest()
DEV_MASTER_KEY = base64.b64encode(DEV_MASTER_KEY_BYTES).decode()
MASTER_KEY_BYTES = 32
PACKAGE_DIR = Path(__file__).resolve().parent.parent


def _decode_master_key(encoded: str) -> bytes:
    msg = f"XOLOTL_MASTER_KEY must be base64 of {MASTER_KEY_BYTES} bytes"
    try:
        key = base64.b64decode(encoded, validate=True)
    except ValueError as exc:  # binascii.Error, or text that is not ASCII
        raise ValueError(msg) from exc
    if len(key) != MASTER_KEY_BYTES:
        raise ValueError(msg)
    return key


def _with_psycopg_driver(url: str) -> str:
    for scheme in LIBPQ_SCHEMES:
        if url.startswith(scheme):
            return DRIVER_SCHEME + url.removeprefix(scheme)
    return url


def _default_worker_id() -> str:
    # Stable for the life of one process and distinct across hosts and processes,
    # which is what makes ``job.locked_by`` traceable to a worker.
    return f"{socket.gethostname()}:{os.getpid()}"


class Settings(BaseSettings):
    model_config: ClassVar[SettingsConfigDict] = SettingsConfigDict(
        env_prefix="XOLOTL_",
        env_file=".env",
        extra="ignore",
        # Errors would otherwise echo the raw input, secrets included.
        hide_input_in_errors=True,
    )

    # repr=False: the URLs carry passwords, so repr() and str() leave them out. A
    # postgres:// or postgresql:// URL gets the psycopg driver.
    database_url: str = Field(
        default="postgresql+psycopg://xolotl_app:xolotl@localhost:5432/xolotl",
        repr=False,
    )
    database_admin_url: str = Field(
        default="postgresql+psycopg://xolotl:xolotl@localhost:5432/xolotl",
        repr=False,
    )
    # A host that hands out only the owner's URL (a Render Blueprint cannot compose
    # one) sets this instead of XOLOTL_DATABASE_URL: the app URL is then the admin
    # URL signing in as APP_ROLE with this password. An explicit XOLOTL_DATABASE_URL
    # wins, and nothing is derived from the local default admin URL.
    app_db_password: SecretStr | None = Field(default=None, min_length=1)
    env: Literal["dev", "test", "prod"] = "dev"
    jwt_secret: str = Field(default=DEV_JWT_SECRET, min_length=32, repr=False)
    jwt_issuer: str = "xolotl"
    access_token_ttl_minutes: int = 480
    mfa_token_ttl_minutes: int = 5
    # Base64 of 32 bytes; wraps the tenant data keys. SecretStr keeps it out of reprs.
    master_key: SecretStr = Field(default=SecretStr(DEV_MASTER_KEY))
    # The only adapter so far; an S3 one can join the Literal when it is built.
    object_store: Literal["local"] = "local"
    object_store_root: Path = Path("./data/local/objects")
    # Pause between ticks of the worker's dispatcher, job and scheduler loops.
    worker_tick_seconds: float = Field(default=1.0, ge=0)
    worker_id: str = Field(default_factory=_default_worker_id, min_length=1)
    # The API process runs the worker loop on a thread of its own (one container);
    # false leaves it to a separate `xolotl worker` process.
    embedded_worker: bool = True
    # Connect, write and read timeout of one webhook delivery request, in seconds.
    webhook_timeout_seconds: int = Field(default=5, ge=1, le=60)
    # Render and most PaaS hosts inject a bare PORT.
    port: int = Field(
        default=8000, validation_alias=AliasChoices("PORT", "XOLOTL_PORT")
    )
    # Built console, served at "/" when the directory exists (plan 1d).
    static_dir: Path = PACKAGE_DIR / "static"
    log_level: str = "INFO"

    @field_validator("database_url", "database_admin_url")
    @classmethod
    def _use_the_psycopg_driver(cls, value: str) -> str:
        return _with_psycopg_driver(value)

    @field_validator("master_key")
    @classmethod
    def _check_master_key(cls, value: SecretStr) -> SecretStr:
        _ = _decode_master_key(value.get_secret_value())
        return value

    @model_validator(mode="after")
    def _derive_the_app_url(self) -> Self:
        given = self.model_fields_set
        if (
            self.app_db_password is None
            or "database_url" in given
            or "database_admin_url" not in given
        ):
            return self
        try:
            admin = make_url(self.database_admin_url)
        except (ArgumentError, ValueError):
            # Both messages can quote the URL, password included.
            msg = "XOLOTL_DATABASE_ADMIN_URL is not a database URL"
            raise ValueError(msg) from None
        app = admin.set(
            username=APP_ROLE, password=self.app_db_password.get_secret_value()
        )
        self.database_url = app.render_as_string(hide_password=False)
        return self

    @model_validator(mode="after")
    def _refuse_dev_secrets_in_prod(self) -> Self:
        if self.env == "prod" and self.jwt_secret == DEV_JWT_SECRET:
            msg = (
                "XOLOTL_JWT_SECRET must be set to a non-default value "
                "when XOLOTL_ENV=prod"
            )
            raise ValueError(msg)
        # Compare bytes: the same key has more than one base64 spelling.
        if self.env == "prod" and self.master_key_bytes == DEV_MASTER_KEY_BYTES:
            msg = (
                "XOLOTL_MASTER_KEY must be set to a non-default value "
                "when XOLOTL_ENV=prod"
            )
            raise ValueError(msg)
        return self

    @model_validator(mode="after")
    def _refuse_a_worker_that_never_pauses_in_prod(self) -> Self:
        # The loop runs inside the API process: without a pause it would query the
        # database back to back, competing with the requests for the pool and the
        # GIL. Tests and local runs may tick without waiting.
        if self.env == "prod" and self.worker_tick_seconds <= 0:
            msg = (
                "XOLOTL_WORKER_TICK_SECONDS must be greater than 0 when XOLOTL_ENV=prod"
            )
            raise ValueError(msg)
        return self

    @property
    def master_key_bytes(self) -> bytes:
        return _decode_master_key(self.master_key.get_secret_value())


@lru_cache
def get_settings() -> Settings:
    return Settings()
