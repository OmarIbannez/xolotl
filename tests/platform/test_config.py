import base64
import os
import socket
import traceback
from decimal import Decimal
from pathlib import Path
from typing import Any, cast

import pytest
from pydantic import ValidationError
from sqlalchemy import make_url

from xolotl.platform.config import (
    APP_ROLE,
    DEV_DATABASE_URL,
    DEV_JWT_SECRET,
    DEV_MASTER_KEY,
    Settings,
    get_settings,
)

ENV_EXAMPLE = Path(__file__).resolve().parents[2] / ".env.example"
CUSTOM_MASTER_KEY = base64.b64encode(b"k" * 32).decode()
SHORT_MASTER_KEY = base64.b64encode(b"s" * 31).decode()
# Prod refuses the default app URL, so the tests that build prod settings need one.
PROD_DATABASE_URL = "postgresql://xolotl_app:pw-77@db.internal:5432/xolotl"


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
    assert s.static_dir.name == "static"


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
    monkeypatch.setenv("XOLOTL_DATABASE_URL", PROD_DATABASE_URL)
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
    assert {
        "master_key",
        "object_store",
        "object_store_root",
        "packs_dir",
        "allow_unverified_pack_values",
        "units_fetch_cron",
        "connector_mode_default",
        "retention_purge_cron",
        "lists_poll_cron",
        "lists_fetch_timeout_seconds",
        "lists_max_file_mb",
        "lists_max_removal_ratio",
        "private_list_max_mb",
        "private_list_max_rows",
        "screening_index_refresh_seconds",
        "screening_private_shards",
        "screening_index_warm",
        "screening_delta_batch",
        "screening_rescreen_batch",
        "screening_periodic_cron",
        "monitoring_batch_max_items",
        "monitoring_file_max_mb",
        "monitoring_file_max_rows",
        "monitoring_file_chunk_rows",
        "monitoring_late_days",
        "monitoring_max_future_minutes",
        "monitoring_process_lease_seconds",
        "monitoring_process_page",
        "monitoring_sweep_cron",
        "monitoring_agg_backfill_batch",
        "monitoring_partitions_cron",
        "monitoring_partition_months_ahead",
        "monitoring_counterparty_screen_batch",
        "monitoring_decide_budget_ms",
        "monitoring_decide_fail_mode",
        "monitoring_retention_cron",
        "monitoring_profile_cron",
        "monitoring_profile_deviation_ratio",
        "monitoring_allow_custom_rules",
        "monitoring_rule_cache_seconds",
        "monitoring_simulation_max_age_days",
    } <= s.model_fields_set
    assert s.master_key_bytes == make_settings().master_key_bytes


def test_worker_defaults():
    s = make_settings()
    assert s.worker_tick_seconds == pytest.approx(1.0)
    assert s.worker_id == f"{socket.gethostname()}:{os.getpid()}"


def test_worker_settings_are_read_from_the_environment(monkeypatch):
    # Settings ignores names it does not know, so a misspelled field would
    # silently keep its default; setting both through their env names proves them.
    monkeypatch.setenv("XOLOTL_WORKER_TICK_SECONDS", "0.25")
    monkeypatch.setenv("XOLOTL_WORKER_ID", "worker-7")
    s = make_settings()
    assert s.worker_tick_seconds == pytest.approx(0.25)
    assert s.worker_id == "worker-7"


@pytest.mark.parametrize(
    ("name", "value", "field"),
    [
        ("XOLOTL_WORKER_TICK_SECONDS", "-1", "worker_tick_seconds"),
        ("XOLOTL_WORKER_TICK_SECONDS", "soon", "worker_tick_seconds"),
        ("XOLOTL_WORKER_ID", "", "worker_id"),
    ],
)
def test_worker_settings_reject_nonsense(monkeypatch, name, value, field):
    monkeypatch.setenv(name, value)
    with pytest.raises(ValidationError, match=field):
        _ = make_settings()


def test_a_zero_tick_is_allowed_for_tests_that_must_not_wait(monkeypatch):
    monkeypatch.setenv("XOLOTL_WORKER_TICK_SECONDS", "0")
    assert make_settings().worker_tick_seconds == 0


def test_the_api_runs_the_embedded_worker_by_default():
    assert make_settings().embedded_worker is True


@pytest.mark.parametrize(
    ("value", "expected"), [("false", False), ("0", False), ("true", True)]
)
def test_the_embedded_worker_is_read_from_the_environment(monkeypatch, value, expected):
    monkeypatch.setenv("XOLOTL_EMBEDDED_WORKER", value)
    assert make_settings().embedded_worker is expected


def test_the_embedded_worker_setting_rejects_nonsense(monkeypatch):
    monkeypatch.setenv("XOLOTL_EMBEDDED_WORKER", "sometimes")
    with pytest.raises(ValidationError, match="embedded_worker"):
        _ = make_settings()


def _prod(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XOLOTL_ENV", "prod")
    monkeypatch.setenv("XOLOTL_JWT_SECRET", "x" * 40)
    monkeypatch.setenv("XOLOTL_MASTER_KEY", CUSTOM_MASTER_KEY)
    monkeypatch.setenv("XOLOTL_DATABASE_URL", PROD_DATABASE_URL)


def test_prod_refuses_a_worker_that_never_pauses(monkeypatch):
    # The loop runs inside the API process: no pause is a hot loop beside requests.
    _prod(monkeypatch)
    monkeypatch.setenv("XOLOTL_WORKER_TICK_SECONDS", "0")
    with pytest.raises(ValidationError, match="WORKER_TICK_SECONDS"):
        _ = make_settings()


def test_prod_accepts_a_positive_tick(monkeypatch):
    _prod(monkeypatch)
    monkeypatch.setenv("XOLOTL_WORKER_TICK_SECONDS", "0.5")
    assert make_settings().worker_tick_seconds == pytest.approx(0.5)


def test_the_webhook_timeout_defaults_to_five_seconds():
    assert make_settings().webhook_timeout_seconds == 5


def test_the_webhook_timeout_is_read_from_the_environment(monkeypatch):
    # Settings ignores names it does not know, so setting the variable and seeing
    # the value change proves the field reads XOLOTL_WEBHOOK_TIMEOUT_SECONDS.
    monkeypatch.setenv("XOLOTL_WEBHOOK_TIMEOUT_SECONDS", "12")
    assert make_settings().webhook_timeout_seconds == 12


# Zero would read as "no time at all" to the HTTP client, and more than a minute
# would hold a delivery's database lock for longer than a job's lease.
@pytest.mark.parametrize("value", ["0", "-3", "61", "soon", "2.5"])
def test_the_webhook_timeout_rejects_nonsense(monkeypatch, value):
    monkeypatch.setenv("XOLOTL_WEBHOOK_TIMEOUT_SECONDS", value)
    with pytest.raises(ValidationError, match="webhook_timeout_seconds"):
        _ = make_settings()


def test_the_connect_timeout_defaults_to_ten_seconds():
    assert make_settings().database_connect_timeout_seconds == 10


def test_the_connect_timeout_is_read_from_the_environment(monkeypatch):
    # Settings ignores names it does not know, so setting the variable and seeing
    # the value change proves the field reads XOLOTL_DATABASE_CONNECT_TIMEOUT_SECONDS.
    monkeypatch.setenv("XOLOTL_DATABASE_CONNECT_TIMEOUT_SECONDS", "30")
    assert make_settings().database_connect_timeout_seconds == 30


# libpq treats a connect_timeout under 2 as 2, so 2 is the floor; five minutes is
# longer than any start should wait to learn that the database cannot be reached.
@pytest.mark.parametrize("value", ["0", "1", "-5", "301", "soon", "2.5"])
def test_the_connect_timeout_rejects_nonsense(monkeypatch, value):
    monkeypatch.setenv("XOLOTL_DATABASE_CONNECT_TIMEOUT_SECONDS", value)
    with pytest.raises(ValidationError, match="database_connect_timeout_seconds"):
        _ = make_settings()


# --- Database URLs as a host hands them out (Render) ---------------------------------
# Render's connectionString is postgresql://..., Heroku's postgres://...: libpq URLs,
# which SQLAlchemy would read as the psycopg2 driver or not at all.
OWNER_URL = "postgres://xolotl:owner-pw-77@dpg-d1abc-a:5432/xolotl"
# What Render's generateValue makes: base64 of 32 random bytes, "+", "/" and "=" too.
APP_DB_PASSWORD = "B0jrphAPOY7pg92AN0c9MN4ye+czLMdwnx4OkA1/FUk="


@pytest.mark.parametrize("name", ["XOLOTL_DATABASE_URL", "XOLOTL_DATABASE_ADMIN_URL"])
@pytest.mark.parametrize("scheme", ["postgres", "postgresql"])
def test_libpq_urls_get_the_psycopg_driver(monkeypatch, name, scheme):
    monkeypatch.setenv(name, f"{scheme}://u:p@db.internal:5432/x?sslmode=require")
    s = make_settings()
    assert getattr(s, name.removeprefix("XOLOTL_").lower()) == (
        "postgresql+psycopg://u:p@db.internal:5432/x?sslmode=require"
    )


def test_a_url_that_names_its_driver_is_kept(monkeypatch):
    url = "postgresql+psycopg://xolotl_app:pw@db:5432/x"
    monkeypatch.setenv("XOLOTL_DATABASE_URL", url)
    assert make_settings().database_url == url


def test_the_app_url_is_the_admin_url_signing_in_as_the_app_role(monkeypatch):
    monkeypatch.setenv("XOLOTL_DATABASE_ADMIN_URL", OWNER_URL)
    monkeypatch.setenv("XOLOTL_APP_DB_PASSWORD", APP_DB_PASSWORD)
    s = make_settings()
    app, admin = make_url(s.database_url), make_url(s.database_admin_url)
    assert (app.drivername, app.username, app.password) == (
        "postgresql+psycopg",
        APP_ROLE,
        APP_DB_PASSWORD,
    )
    assert (app.host, app.port, app.database) == ("dpg-d1abc-a", 5432, "xolotl")
    assert (admin.drivername, admin.username, admin.password) == (
        "postgresql+psycopg",
        "xolotl",
        "owner-pw-77",
    )


def test_the_derived_url_keeps_the_admin_urls_host_options_and_missing_port(
    monkeypatch,
):
    monkeypatch.setenv(
        "XOLOTL_DATABASE_ADMIN_URL",
        "postgresql://xolotl:pw@dpg-d1abc-a/xolotl?sslmode=require",
    )
    monkeypatch.setenv("XOLOTL_APP_DB_PASSWORD", "app-pw")
    app = make_url(make_settings().database_url)
    assert (app.host, app.port, app.database) == ("dpg-d1abc-a", None, "xolotl")
    assert dict(app.query) == {"sslmode": "require"}


def test_an_explicit_app_url_wins_over_the_app_password(monkeypatch):
    monkeypatch.setenv(
        "XOLOTL_DATABASE_URL", "postgres://xolotl_app:explicit@db:5432/x"
    )
    monkeypatch.setenv("XOLOTL_DATABASE_ADMIN_URL", OWNER_URL)
    monkeypatch.setenv("XOLOTL_APP_DB_PASSWORD", APP_DB_PASSWORD)
    s = make_settings()
    assert s.database_url == "postgresql+psycopg://xolotl_app:explicit@db:5432/x"


def test_the_app_password_without_an_admin_url_changes_nothing(monkeypatch):
    # Derived from a configured admin URL only, never from the local default.
    monkeypatch.setenv("XOLOTL_APP_DB_PASSWORD", APP_DB_PASSWORD)
    s = make_settings()
    assert s.database_url == Settings.model_fields["database_url"].default
    assert APP_DB_PASSWORD not in s.database_url


def test_repr_hides_the_app_password_and_the_derived_url(monkeypatch):
    monkeypatch.setenv("XOLOTL_DATABASE_ADMIN_URL", OWNER_URL)
    monkeypatch.setenv("XOLOTL_APP_DB_PASSWORD", APP_DB_PASSWORD)
    s = make_settings()
    for shown in (repr(s), str(s)):
        for hidden in (APP_DB_PASSWORD, "owner-pw-77", "dpg-d1abc-a", "database_url"):
            assert hidden not in shown
        assert "app_db_password=SecretStr('**********')" in shown


@pytest.mark.parametrize(
    "admin_url",
    [
        "not a database url, owner-pw-77",
        "postgresql://xolotl:owner-pw-77@db:port/xolotl",
    ],
    ids=["unparsable", "port-not-a-number"],
)
def test_an_admin_url_that_does_not_parse_fails_without_echoing_it(
    monkeypatch, admin_url
):
    monkeypatch.setenv("XOLOTL_DATABASE_ADMIN_URL", admin_url)
    monkeypatch.setenv("XOLOTL_APP_DB_PASSWORD", APP_DB_PASSWORD)
    with pytest.raises(ValidationError, match="XOLOTL_DATABASE_ADMIN_URL") as excinfo:
        _ = make_settings()
    shown = "".join(traceback.format_exception(excinfo.value))
    assert "owner-pw-77" not in shown
    assert APP_DB_PASSWORD[:8] not in shown


def test_an_empty_app_password_is_refused(monkeypatch):
    monkeypatch.setenv("XOLOTL_DATABASE_ADMIN_URL", OWNER_URL)
    monkeypatch.setenv("XOLOTL_APP_DB_PASSWORD", "")
    with pytest.raises(ValidationError, match="app_db_password"):
        _ = make_settings()


# --- XOLOTL_ENV=prod and the database URL --------------------------------------------
# The default app URL signs in with a public password, so prod refuses it the way it
# refuses the development JWT secret and master key; a process that forgot its
# database settings must not try localhost.
def test_prod_refuses_the_default_database_url(monkeypatch):
    monkeypatch.setenv("XOLOTL_ENV", "prod")
    monkeypatch.setenv("XOLOTL_JWT_SECRET", "x" * 40)
    monkeypatch.setenv("XOLOTL_MASTER_KEY", CUSTOM_MASTER_KEY)
    with pytest.raises(ValidationError, match="DATABASE_URL"):
        _ = make_settings()


def test_prod_refuses_the_default_database_url_even_when_spelled_out(monkeypatch):
    monkeypatch.setenv("XOLOTL_ENV", "prod")
    monkeypatch.setenv("XOLOTL_JWT_SECRET", "x" * 40)
    monkeypatch.setenv("XOLOTL_MASTER_KEY", CUSTOM_MASTER_KEY)
    monkeypatch.setenv("XOLOTL_DATABASE_URL", DEV_DATABASE_URL)
    with pytest.raises(ValidationError, match="DATABASE_URL"):
        _ = make_settings()


def test_prod_accepts_the_app_url_derived_from_the_admin_url(monkeypatch):
    # What Render gives: the owner's URL and the app password, no app URL.
    monkeypatch.setenv("XOLOTL_ENV", "prod")
    monkeypatch.setenv("XOLOTL_JWT_SECRET", "x" * 40)
    monkeypatch.setenv("XOLOTL_MASTER_KEY", CUSTOM_MASTER_KEY)
    monkeypatch.setenv("XOLOTL_DATABASE_ADMIN_URL", OWNER_URL)
    monkeypatch.setenv("XOLOTL_APP_DB_PASSWORD", APP_DB_PASSWORD)
    s = make_settings()
    assert make_url(s.database_url).username == APP_ROLE


def test_sp02_settings_have_their_defaults():
    s = make_settings()
    assert s.packs_dir == Path("./policy-packs")
    assert s.allow_unverified_pack_values is False
    assert s.banxico_token is None
    assert s.units_fetch_cron == "0 18 * * 1-5"


def test_sp02_settings_read_their_variables(monkeypatch):
    # Settings ignores names it does not know, so setting the variable and seeing the
    # value change proves the field reads XOLOTL_...
    monkeypatch.setenv("XOLOTL_PACKS_DIR", "/srv/packs")
    monkeypatch.setenv("XOLOTL_ALLOW_UNVERIFIED_PACK_VALUES", "true")
    monkeypatch.setenv("XOLOTL_BANXICO_TOKEN", "tok-123")
    monkeypatch.setenv("XOLOTL_UNITS_FETCH_CRON", "30 17 * * 1-5")
    s = make_settings()
    assert s.packs_dir == Path("/srv/packs")
    assert s.allow_unverified_pack_values is True
    assert s.banxico_token is not None
    assert s.banxico_token.get_secret_value() == "tok-123"
    assert "tok-123" not in repr(s)
    assert s.units_fetch_cron == "30 17 * * 1-5"


def test_a_bad_units_fetch_cron_is_refused(monkeypatch):
    monkeypatch.setenv("XOLOTL_UNITS_FETCH_CRON", "every day")
    with pytest.raises(ValidationError, match="UNITS_FETCH_CRON"):
        _ = make_settings()


def test_prod_refuses_unverified_pack_values(monkeypatch):
    _prod(monkeypatch)
    monkeypatch.setenv("XOLOTL_ALLOW_UNVERIFIED_PACK_VALUES", "true")
    with pytest.raises(ValidationError, match="ALLOW_UNVERIFIED_PACK_VALUES"):
        _ = make_settings()


def test_connector_settings_default_to_sandbox_and_no_token():
    s = make_settings()
    assert s.connector_mode_default == "SANDBOX"
    assert (
        s.banxico_token is None
    )  # plan 2a's A1 field: a cross-check that A1 is merged


def test_connector_settings_are_read_from_the_environment(monkeypatch):
    # Settings ignores names it does not know, so setting the variable and seeing
    # the value change proves the field reads XOLOTL_CONNECTOR_MODE_DEFAULT.
    # (XOLOTL_BANXICO_TOKEN is plan 2a's A1 field: its
    # test_sp02_settings_read_their_variables already pins the read and the repr.)
    monkeypatch.setenv("XOLOTL_CONNECTOR_MODE_DEFAULT", "LIVE")
    s = make_settings()
    assert s.connector_mode_default == "LIVE"


def test_the_connector_mode_rejects_nonsense(monkeypatch):
    monkeypatch.setenv("XOLOTL_CONNECTOR_MODE_DEFAULT", "demo")
    with pytest.raises(ValidationError, match="connector_mode_default"):
        _ = make_settings()


def test_retention_purge_cron_defaults_and_reads_its_variable(monkeypatch):
    assert make_settings().retention_purge_cron == "0 3 * * *"
    monkeypatch.setenv("XOLOTL_RETENTION_PURGE_CRON", "30 2 * * *")
    assert make_settings().retention_purge_cron == "30 2 * * *"


def test_retention_purge_cron_must_be_a_cron_expression(monkeypatch):
    monkeypatch.setenv("XOLOTL_RETENTION_PURGE_CRON", "every night")
    with pytest.raises(ValidationError, match="RETENTION_PURGE_CRON"):
        _ = make_settings()


def test_list_settings_default_to_the_spec_values():
    s = make_settings()
    assert s.lists_poll_cron == "* * * * *"
    assert s.lists_fetch_timeout_seconds == 120
    assert s.lists_max_file_mb == 300
    assert s.lists_max_removal_ratio == Decimal("0.20")
    assert s.private_list_max_mb == 20
    assert s.private_list_max_rows == 100000
    assert s.lists_fetch_enabled is None
    assert s.screening_index_refresh_seconds == 30
    assert s.screening_private_shards == 64
    assert s.screening_index_warm is True
    assert s.screening_delta_batch == 200
    assert s.screening_rescreen_batch == 500
    assert s.screening_periodic_cron == "0 2 1 * *"


@pytest.mark.parametrize(
    ("env", "enabled", "expected"),
    [
        ("dev", None, False),
        ("test", None, False),
        ("prod", None, True),
        ("dev", "true", True),
        ("prod", "false", False),
    ],
)
def test_list_fetching_follows_the_environment_unless_set(
    monkeypatch, env, enabled, expected
):
    if env == "prod":
        _prod(monkeypatch)
    else:
        monkeypatch.setenv("XOLOTL_ENV", env)
    if enabled is not None:
        monkeypatch.setenv("XOLOTL_LISTS_FETCH_ENABLED", enabled)
    assert make_settings().lists_fetching is expected


def test_the_poll_cron_is_validated(monkeypatch):
    monkeypatch.setenv("XOLOTL_LISTS_POLL_CRON", "every minute")
    with pytest.raises(ValidationError, match="lists_poll_cron"):
        _ = make_settings()


def test_the_periodic_rescreen_cron_is_validated(monkeypatch):
    monkeypatch.setenv("XOLOTL_SCREENING_PERIODIC_CRON", "monthly")
    with pytest.raises(ValidationError, match="SCREENING_PERIODIC_CRON"):
        _ = make_settings()


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("XOLOTL_LISTS_FETCH_TIMEOUT_SECONDS", "4"),
        ("XOLOTL_LISTS_FETCH_TIMEOUT_SECONDS", "601"),
        ("XOLOTL_LISTS_MAX_FILE_MB", "0"),
        ("XOLOTL_LISTS_MAX_FILE_MB", "2049"),
        ("XOLOTL_LISTS_MAX_REMOVAL_RATIO", "0"),
        ("XOLOTL_LISTS_MAX_REMOVAL_RATIO", "1.5"),
        ("XOLOTL_PRIVATE_LIST_MAX_MB", "201"),
        ("XOLOTL_PRIVATE_LIST_MAX_ROWS", "0"),
        ("XOLOTL_SCREENING_INDEX_REFRESH_SECONDS", "0"),
        ("XOLOTL_SCREENING_PRIVATE_SHARDS", "0"),
    ],
)
def test_list_limits_reject_nonsense(monkeypatch, name, value):
    monkeypatch.setenv(name, value)
    with pytest.raises(ValidationError, match=name.removeprefix("XOLOTL_").lower()):
        _ = make_settings()


def test_monitoring_settings_default_to_the_spec_values():
    s = make_settings()
    assert s.monitoring_batch_max_items == 1000
    assert s.monitoring_file_max_mb == 50
    assert s.monitoring_file_max_rows == 500000
    assert s.monitoring_file_chunk_rows == 1000
    assert s.monitoring_late_days == 7
    assert s.monitoring_max_future_minutes == 10
    assert s.monitoring_process_lease_seconds == 300
    assert s.monitoring_process_page == 200
    assert s.monitoring_sweep_cron == "* * * * *"
    assert s.monitoring_agg_backfill_batch == 5000
    assert s.monitoring_partitions_cron == "0 1 * * *"
    assert s.monitoring_partition_months_ahead == 3
    assert s.monitoring_counterparty_screen_batch == 100
    assert s.monitoring_decide_budget_ms == 80
    assert s.monitoring_decide_fail_mode == "FAIL_OPEN"
    assert s.monitoring_retention_cron == "0 5 1 * *"
    assert s.monitoring_profile_cron == "0 3 2 * *"
    assert s.monitoring_profile_deviation_ratio == Decimal("2.0")
    assert s.monitoring_allow_custom_rules is False
    assert s.monitoring_rule_cache_seconds == 30
    assert s.monitoring_simulation_max_age_days == 30


@pytest.mark.parametrize(
    "name",
    [
        "XOLOTL_MONITORING_SWEEP_CRON",
        "XOLOTL_MONITORING_PARTITIONS_CRON",
        "XOLOTL_MONITORING_RETENTION_CRON",
        "XOLOTL_MONITORING_PROFILE_CRON",
    ],
)
def test_the_monitoring_crons_are_validated(monkeypatch, name):
    monkeypatch.setenv(name, "daily")
    with pytest.raises(ValidationError, match=name.removeprefix("XOLOTL_")):
        _ = make_settings()


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("XOLOTL_MONITORING_BATCH_MAX_ITEMS", "10001"),
        ("XOLOTL_MONITORING_FILE_MAX_MB", "0"),
        ("XOLOTL_MONITORING_PROCESS_LEASE_SECONDS", "29"),
        ("XOLOTL_MONITORING_PARTITION_MONTHS_AHEAD", "13"),
        ("XOLOTL_MONITORING_COUNTERPARTY_SCREEN_BATCH", "0"),
        ("XOLOTL_MONITORING_RULE_CACHE_SECONDS", "-1"),
        ("XOLOTL_MONITORING_SIMULATION_MAX_AGE_DAYS", "0"),
        ("XOLOTL_MONITORING_DECIDE_BUDGET_MS", "0"),
        ("XOLOTL_MONITORING_DECIDE_FAIL_MODE", "sideways"),
        ("XOLOTL_MONITORING_PROFILE_DEVIATION_RATIO", "0"),
    ],
)
def test_monitoring_limits_reject_nonsense(monkeypatch, name, value):
    monkeypatch.setenv(name, value)
    with pytest.raises(ValidationError, match=name.removeprefix("XOLOTL_").lower()):
        _ = make_settings()
