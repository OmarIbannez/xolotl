"""``serve`` bootstraps the app role when it cannot sign in (``ensure_app_role``).

On a host like Render the first deploy meets a database without the app role, and a
password changed in the dashboard reaches the server before it reaches the role. The
tests change the shared role's password; each puts the suite's password back.
"""

import logging
import re
from collections.abc import Generator, Iterator
from contextlib import contextmanager
from typing import Any, NoReturn, cast

import pytest
from psycopg import sql
from sqlalchemy import Engine, create_engine, event, make_url, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.pool import NullPool

from xolotl.platform import migrate
from xolotl.platform.config import Settings, get_settings
from xolotl.platform.db import APP_ROLE, BootstrapError

MIGRATE_LOGGER = "xolotl.platform.migrate"
ROTATED = "rotated-in-the-dashboard-5150"
CLEAR = "clear-text-9001"
# What libpq builds for the server's default iteration count, followed by the salt,
# the stored key and the server key, each in base64.
VERIFIER_PREFIX = "SCRAM-SHA-256$4096:"
B64 = r"[A-Za-z0-9+/]+=*"
VERIFIER_SHAPE = rf"SCRAM-SHA-256\$\d+:{B64}\${B64}:{B64}"


@contextmanager
def admin_statements(engine: Engine) -> Generator[list[str]]:
    statements: list[str] = []

    def keep(conn, cursor, statement, parameters, context, executemany) -> None:
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", keep)
    try:
        yield statements
    finally:
        event.remove(engine, "before_cursor_execute", keep)


def set_app_password(admin: Engine, password: str) -> None:
    with admin.begin() as conn:
        migrate.exec_literal_ddl(
            conn,
            sql.SQL("ALTER ROLE {} PASSWORD {}").format(
                sql.Identifier(APP_ROLE), sql.Literal(password)
            ),
        )


def signs_in(url: str) -> bool:
    # A connection of its own: a pooled one opened earlier proves nothing.
    probe = create_engine(url, poolclass=NullPool)
    try:
        with probe.connect() as conn:
            return conn.scalar(text("SELECT current_user")) == APP_ROLE
    except OperationalError:
        return False
    finally:
        probe.dispose()


@pytest.fixture
def password_restored(admin_engine: Engine) -> Iterator[None]:
    suite_password = make_url(get_settings().database_url).password
    assert suite_password
    yield
    set_app_password(admin_engine, suite_password)


def render_shaped_settings(monkeypatch: pytest.MonkeyPatch, password: str) -> Settings:
    """Build settings the way Render's Blueprint gives them: owner URL, app password.

    The admin URL is the suite's own, spelled ``postgres://`` as a host hands it out,
    and XOLOTL_DATABASE_URL is unset, so the app URL is derived from the two.

    Returns:
        The settings, built from that environment.

    """
    admin = make_url(get_settings().database_admin_url).set(drivername="postgres")
    monkeypatch.delenv("XOLOTL_DATABASE_URL")
    monkeypatch.setenv(
        "XOLOTL_DATABASE_ADMIN_URL", admin.render_as_string(hide_password=False)
    )
    monkeypatch.setenv("XOLOTL_APP_DB_PASSWORD", password)
    return cast("Any", Settings)(_env_file=None)


def migrate_messages(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.name == MIGRATE_LOGGER]


def test_a_role_that_signs_in_is_left_alone(
    admin_engine: Engine, caplog: pytest.LogCaptureFixture
):
    caplog.set_level(logging.INFO, logger=MIGRATE_LOGGER)
    with admin_statements(admin_engine) as statements:
        assert migrate.ensure_app_role() is False
    assert statements == []
    assert migrate_messages(caplog) == []


def test_a_password_changed_elsewhere_is_set_back_to_the_configured_one(
    admin_engine: Engine, password_restored: None, caplog: pytest.LogCaptureFixture
):
    caplog.set_level(logging.INFO, logger=MIGRATE_LOGGER)
    configured = get_settings().database_url
    set_app_password(admin_engine, ROTATED)
    assert not signs_in(configured)
    assert migrate.ensure_app_role() is True
    assert signs_in(configured)
    assert migrate_messages(caplog) == ["app role cannot sign in; bootstrapping it"]


def test_render_shaped_settings_bootstrap_the_role_with_the_derived_url(
    admin_engine: Engine,
    password_restored: None,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
):
    caplog.set_level(logging.DEBUG)
    settings = render_shaped_settings(monkeypatch, ROTATED)
    assert make_url(settings.database_url).username == APP_ROLE
    monkeypatch.setattr(migrate, "get_settings", lambda: settings)
    assert not signs_in(settings.database_url)  # the role has the suite's password
    assert migrate.ensure_app_role() is True
    assert signs_in(settings.database_url)
    assert migrate.ensure_app_role() is False  # and the next start leaves it alone
    assert migrate_messages(caplog) == ["app role cannot sign in; bootstrapping it"]
    assert ROTATED not in caplog.text  # the configured password is never logged


def test_bootstrap_sends_the_password_as_a_scram_verifier_and_never_in_clear(
    admin_engine: Engine, password_restored: None, monkeypatch: pytest.MonkeyPatch
):
    app_url = make_url(get_settings().database_url).set(password=CLEAR)
    settings = get_settings().model_copy(
        update={"database_url": app_url.render_as_string(hide_password=False)}
    )
    monkeypatch.setattr(migrate, "get_settings", lambda: settings)
    with admin_statements(admin_engine) as statements:
        migrate.bootstrap()
    prefix = f'ALTER ROLE "{APP_ROLE}" WITH LOGIN PASSWORD \''
    alters = [s for s in statements if s.startswith(prefix)]
    assert len(alters) == 1
    assert alters[0].removeprefix(prefix).startswith(VERIFIER_PREFIX)
    assert not [s for s in statements if CLEAR in s]
    # The verifier authenticates the clear password over SCRAM, and the server
    # stores it as it is (the suite's admin is a superuser, so it may look).
    assert signs_in(settings.database_url)
    with admin_engine.connect() as conn:
        stored = cast(
            "str",
            conn.scalar(
                text("SELECT rolpassword FROM pg_authid WHERE rolname = :role"),
                {"role": APP_ROLE},
            ),
        )
    assert stored.startswith("SCRAM-SHA-256$")


def test_the_verifier_is_salted_and_libpqs(admin_engine: Engine):
    with admin_engine.connect() as conn:
        first = migrate.scram_verifier(conn, "same")
        second = migrate.scram_verifier(conn, "same")
    assert first != second  # a fresh salt each time
    for verifier in (first, second):
        assert verifier.startswith(VERIFIER_PREFIX)
        assert re.fullmatch(VERIFIER_SHAPE, verifier), verifier


PLAIN_OWNER = "xolotl_plain_owner"
PLAIN_DATABASE = "xolotl_plain"
# A role the cluster does not have: the suite's app role exists cluster-wide, and
# roles cannot be hidden per database.
UNCREATED_ROLE = "xolotl_never_created"


@pytest.fixture
def owner_without_createrole(
    admin_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> Iterator[None]:
    """Point bootstrap at a database whose owner may not create roles.

    The host's default user on a managed Postgres that grants no ``CREATEROLE``. The
    app role's name is one that does not exist, so bootstrap has to create it.
    """
    autocommit = admin_engine.execution_options(isolation_level="AUTOCOMMIT")
    with autocommit.connect() as conn:
        _ = conn.execute(text(f"CREATE ROLE {PLAIN_OWNER} LOGIN PASSWORD 'plain'"))
        _ = conn.execute(text(f"CREATE DATABASE {PLAIN_DATABASE} OWNER {PLAIN_OWNER}"))
    settings = get_settings()
    owner_url = make_url(settings.database_admin_url).set(
        username=PLAIN_OWNER, password="plain", database=PLAIN_DATABASE
    )
    app_url = owner_url.set(username=UNCREATED_ROLE, password="app-pw-unused")
    plain = settings.model_copy(
        update={
            "database_admin_url": owner_url.render_as_string(hide_password=False),
            "database_url": app_url.render_as_string(hide_password=False),
        }
    )
    owner = create_engine(plain.database_admin_url)
    monkeypatch.setattr(migrate, "APP_ROLE", UNCREATED_ROLE)
    monkeypatch.setattr(migrate, "get_settings", lambda: plain)
    monkeypatch.setattr(migrate, "get_admin_engine", lambda: owner)
    try:
        yield
    finally:
        owner.dispose()
        with autocommit.connect() as conn:
            _ = conn.execute(text(f"DROP DATABASE {PLAIN_DATABASE} WITH (FORCE)"))
            _ = conn.execute(text(f"DROP ROLE {PLAIN_OWNER}"))


def test_an_owner_that_may_not_create_roles_fails_bootstrap_in_one_line(
    owner_without_createrole: None, admin_engine: Engine
):
    with pytest.raises(BootstrapError) as caught:
        migrate.bootstrap()
    assert str(caught.value) == (
        f"could not create role {UNCREATED_ROLE} (sqlstate 42501)"
    )
    assert caught.value.__suppress_context__  # the driver's text stays out
    with admin_engine.connect() as conn:
        assert (
            conn.scalar(
                text("SELECT count(*) FROM pg_roles WHERE rolname = :role"),
                {"role": UNCREATED_ROLE},
            )
            == 0
        )


def test_without_an_admin_url_nothing_is_probed_or_bootstrapped(
    engine: Engine, monkeypatch: pytest.MonkeyPatch
):
    # Only the local default admin URL: the default must never be touched, so an app
    # URL that cannot sign in is left for the role check to report.
    monkeypatch.delenv("XOLOTL_DATABASE_ADMIN_URL")
    monkeypatch.setenv(
        "XOLOTL_DATABASE_URL", "postgresql+psycopg://xolotl_app:x@127.0.0.1:1/none"
    )
    settings = cast("Any", Settings)(_env_file=None)
    assert "database_admin_url" not in settings.model_fields_set

    def refuse() -> NoReturn:
        msg = "the default admin URL must not be used"
        raise AssertionError(msg)

    monkeypatch.setattr(migrate, "get_settings", lambda: settings)
    monkeypatch.setattr(migrate, "get_admin_engine", refuse)
    assert migrate.ensure_app_role() is False


def test_an_admin_url_without_an_app_credential_is_refused_before_touching_the_database(
    admin_engine: Engine, monkeypatch: pytest.MonkeyPatch
):
    # A deploy that sets the owner URL but forgets the app password would otherwise
    # create xolotl_app with the default password on the owner's database.
    monkeypatch.delenv("XOLOTL_DATABASE_URL")
    monkeypatch.delenv("XOLOTL_APP_DB_PASSWORD", raising=False)
    bare = cast("Any", Settings)(_env_file=None)
    assert "database_admin_url" in bare.model_fields_set
    assert "database_url" not in bare.model_fields_set
    assert bare.app_db_password is None
    monkeypatch.setattr(migrate, "get_settings", lambda: bare)
    with (
        admin_statements(admin_engine) as statements,
        pytest.raises(BootstrapError, match="XOLOTL_APP_DB_PASSWORD"),
    ):
        _ = migrate.ensure_app_role()
    assert statements == []
