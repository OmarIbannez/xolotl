"""Runtime configuration, read from environment variables prefixed ``XOLOTL_``."""

from functools import lru_cache
from typing import ClassVar, Literal, Self

from pydantic import AliasChoices, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DEV_JWT_SECRET = "dev-only-insecure-jwt-secret-change-me-000000"  # ruff: ignore[hardcoded-password-string]


class Settings(BaseSettings):
    model_config: ClassVar[SettingsConfigDict] = SettingsConfigDict(
        env_prefix="XOLOTL_", env_file=".env", extra="ignore"
    )

    # repr=False: the URLs carry passwords, so repr() and str() leave them out.
    database_url: str = Field(
        default="postgresql+psycopg://xolotl_app:xolotl@localhost:5432/xolotl",
        repr=False,
    )
    database_admin_url: str = Field(
        default="postgresql+psycopg://xolotl:xolotl@localhost:5432/xolotl",
        repr=False,
    )
    env: Literal["dev", "test", "prod"] = "dev"
    jwt_secret: str = Field(default=DEV_JWT_SECRET, min_length=32, repr=False)
    jwt_issuer: str = "xolotl"
    access_token_ttl_minutes: int = 480
    mfa_token_ttl_minutes: int = 5
    # Render and most PaaS hosts inject a bare PORT.
    port: int = Field(
        default=8000, validation_alias=AliasChoices("PORT", "XOLOTL_PORT")
    )
    log_level: str = "INFO"

    @model_validator(mode="after")
    def _refuse_dev_secrets_in_prod(self) -> Self:
        if self.env == "prod" and self.jwt_secret == DEV_JWT_SECRET:
            msg = (
                "XOLOTL_JWT_SECRET must be set to a non-default value "
                "when XOLOTL_ENV=prod"
            )
            raise ValueError(msg)
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
