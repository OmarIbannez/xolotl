import base64
import os
from pathlib import Path
from typing import Any, cast

import pytest
from pydantic import ValidationError

from xolotl.platform.config import (
    DEV_JWT_SECRET,
    DEV_MASTER_KEY,
    Settings,
    get_settings,
)

ENV_EXAMPLE = Path(__file__).resolve().parents[2] / ".env.example"
CUSTOM_MASTER_KEY = base64.b64encode(b"k" * 32).decode()
SHORT_MASTER_KEY = base64.b64encode(b"s" * 31).decode()


@pytest.fixture
def _clean_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Hide ambient XOLOTL_* and PORT variables; session fixtures set some."""
    for key in list(os.environ):
        name = key.upper()  # pydantic-settings matches names case-insensitively
        if name.startswith("XOLOTL_") or name == "PORT":
            monkeypatch.delenv(key)


pytestmark = pytest.mark.usefixtures("_clean_environment")


def make_settings(env_file: Path | None = None) -> Settings:
    # The type checkers synthesize ``__init__`` from the fields, which hides the
    # runtime-only ``_env_file`` argument; the cast lives here, once.
    return cast("Any", Settings)(_env_file=env_file)


def test_defaults_match_the_spec():
    s = make_settings()
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
    s = make_settings()
    assert s.jwt_issuer == "issuer-x"
    assert s.port == 9100


def test_prod_refuses_the_dev_jwt_secret(monkeypatch):
    monkeypatch.setenv("XOLOTL_ENV", "prod")
    monkeypatch.delenv("XOLOTL_JWT_SECRET", raising=False)
    with pytest.raises(ValidationError, match="JWT_SECRET"):
        _ = make_settings()


def test_prod_accepts_a_real_secret(monkeypatch):
    monkeypatch.setenv("XOLOTL_ENV", "prod")
    monkeypatch.setenv("XOLOTL_JWT_SECRET", "x" * 40)
    monkeypatch.setenv("XOLOTL_MASTER_KEY", CUSTOM_MASTER_KEY)
    s = make_settings()
    assert s.jwt_secret != DEV_JWT_SECRET
    assert s.master_key_bytes == b"k" * 32


def test_short_jwt_secret_is_rejected(monkeypatch):
    monkeypatch.setenv("XOLOTL_JWT_SECRET", "short")
    with pytest.raises(ValidationError):
        _ = make_settings()


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


def test_master_key_defaults_and_decodes(monkeypatch):
    monkeypatch.delenv("XOLOTL_MASTER_KEY", raising=False)
    s = make_settings()
    assert len(s.master_key_bytes) == 32
    assert s.master_key.get_secret_value() == DEV_MASTER_KEY
    assert "***" in repr(s)  # SecretStr never prints
    assert DEV_MASTER_KEY not in repr(s)


def test_the_dev_master_key_is_not_degenerate():
    key = base64.b64decode(DEV_MASTER_KEY, validate=True)
    assert len(key) == 32
    assert key != bytes(32)
    assert len(set(key)) >= 16  # many distinct bytes, not a repeated pattern


def test_master_key_is_read_from_the_environment(monkeypatch):
    monkeypatch.setenv("XOLOTL_MASTER_KEY", CUSTOM_MASTER_KEY)
    s = make_settings()
    assert s.master_key_bytes == b"k" * 32
    assert CUSTOM_MASTER_KEY not in repr(s)


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "not-base64!",
        "clave-secreta-ñ",
        base64.b64encode(b"short").decode(),
        SHORT_MASTER_KEY,  # 31 bytes: one too few
        base64.b64encode(b"x" * 33).decode(),  # 33 bytes: one too many
        (b"k" * 32).hex(),  # `openssl rand -hex 32`: base64 alphabet, 48 bytes
        CUSTOM_MASTER_KEY + "\n",  # a key read from a file keeps its newline
        CUSTOM_MASTER_KEY[:22] + "!" + CUSTOM_MASTER_KEY[22:],  # lax decoders skip it
    ],
    ids=[
        "empty",
        "not-base64",
        "non-ascii",
        "5-bytes",
        "31-bytes",
        "33-bytes",
        "hex-of-32-bytes",
        "trailing-newline",
        "stray-character",
    ],
)
def test_master_key_must_be_32_bytes(monkeypatch, bad):
    monkeypatch.setenv("XOLOTL_MASTER_KEY", bad)
    with pytest.raises(ValidationError, match="32 bytes"):
        _ = make_settings()


def test_prod_refuses_the_dev_master_key(monkeypatch):
    monkeypatch.setenv("XOLOTL_ENV", "prod")
    monkeypatch.setenv("XOLOTL_JWT_SECRET", "x" * 40)
    monkeypatch.delenv("XOLOTL_MASTER_KEY", raising=False)
    with pytest.raises(ValidationError, match="MASTER_KEY"):
        _ = make_settings()


def test_prod_refuses_a_respelling_of_the_dev_master_key(monkeypatch):
    # Only the two unused trailing bits differ: same bytes, different text.
    respelled = DEV_MASTER_KEY[:-2] + "J="
    assert respelled != DEV_MASTER_KEY
    assert base64.b64decode(respelled, validate=True) == base64.b64decode(
        DEV_MASTER_KEY, validate=True
    )
    monkeypatch.setenv("XOLOTL_ENV", "prod")
    monkeypatch.setenv("XOLOTL_JWT_SECRET", "x" * 40)
    monkeypatch.setenv("XOLOTL_MASTER_KEY", respelled)
    with pytest.raises(ValidationError, match="MASTER_KEY"):
        _ = make_settings()


@pytest.mark.parametrize(
    ("env", "secret"),
    [
        ({"XOLOTL_MASTER_KEY": SHORT_MASTER_KEY}, SHORT_MASTER_KEY),
        (
            {"XOLOTL_MASTER_KEY": CUSTOM_MASTER_KEY, "XOLOTL_ENV": "prod"},
            CUSTOM_MASTER_KEY,
        ),
        ({"XOLOTL_JWT_SECRET": "tiny-jwt-secret"}, "tiny-jwt-secret"),
    ],
    ids=["malformed-key", "valid-key-other-error", "short-jwt-secret"],
)
def test_validation_errors_never_echo_secrets(monkeypatch, env, secret):
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    with pytest.raises(ValidationError) as excinfo:
        _ = make_settings()
    message = str(excinfo.value)
    assert "input_value" not in message
    # An echo is truncated, so the whole secret may be absent while its head shows.
    assert secret[:8] not in message


def test_object_store_defaults_to_the_local_adapter(monkeypatch):
    monkeypatch.delenv("XOLOTL_OBJECT_STORE", raising=False)
    s = make_settings()
    assert s.object_store == "local"
    assert s.object_store_root == Path("./data/local/objects")


def test_object_store_root_is_read_from_the_environment(monkeypatch):
    monkeypatch.setenv("XOLOTL_OBJECT_STORE", "local")
    monkeypatch.setenv("XOLOTL_OBJECT_STORE_ROOT", "/var/lib/xolotl/objects")
    s = make_settings()
    assert s.object_store == "local"
    assert s.object_store_root == Path("/var/lib/xolotl/objects")


# The switch exists so another adapter can be added behind the same port; until then
# anything but "local" is refused, S3 included. Setting the variable and seeing it
# rejected also proves the field reads XOLOTL_OBJECT_STORE (unknown names are ignored).
def test_only_the_local_object_store_exists_so_far(monkeypatch):
    monkeypatch.setenv("XOLOTL_OBJECT_STORE", "s3")
    with pytest.raises(ValidationError, match="object_store"):
        _ = make_settings()


def test_env_example_is_a_valid_configuration():
    s = make_settings(ENV_EXAMPLE)
    # The keys must be spelled right: Settings ignores names it does not know.
    assert {"master_key", "object_store", "object_store_root"} <= s.model_fields_set
    assert s.master_key_bytes == make_settings().master_key_bytes
