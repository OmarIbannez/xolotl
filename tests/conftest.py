"""Shared fixtures.

Tests run against a real Postgres 17 (testcontainers, or
``XOLOTL_TEST_DATABASE_ADMIN_URL`` if set) with real commits on real
connections, so row-level security and advisory locks behave as in production.
Every table except ``alembic_version`` is truncated before each test that
touches the database.
"""

import os
from collections.abc import Generator, Iterator
from typing import cast

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from psycopg import sql
from sqlalchemy import Engine, create_engine, make_url, text
from testcontainers.community.postgres import PostgresContainer

from tests.helpers import first_login, new_tenant, require_server_admin
from xolotl.app import build_app, build_key_provider
from xolotl.modules.tenants.service import CreatedTenant
from xolotl.platform import db, migrate
from xolotl.platform.config import get_settings
from xolotl.platform.crypto.provider import (
    KeyProvider,
    get_key_provider,
    set_key_provider,
)

APP_PASSWORD = "xolotl"
ADMIN_PASSWORD = "correct-horse-battery-staple"
MANAGED_OWNER = "xolotl_managed_owner"
MANAGED_DATABASE = "xolotl_managed"
BOUND_OWNER = "xolotl_bound_owner"
BOUND_DATABASE = "xolotl_bound_scratch"


@pytest.fixture(scope="session")
def admin_url() -> Iterator[str]:
    if url := os.environ.get("XOLOTL_TEST_DATABASE_ADMIN_URL"):
        yield url
        return
    with PostgresContainer("postgres:17-alpine", driver="psycopg") as pg:
        yield pg.get_connection_url()


def _reset_caches() -> None:
    get_settings.cache_clear()
    db.get_engine.cache_clear()
    db.get_admin_engine.cache_clear()
    db.get_sessionmaker.cache_clear()


@pytest.fixture(scope="session")
def engines(
    admin_url: str, tmp_path_factory: pytest.TempPathFactory
) -> Iterator[tuple[Engine, Engine]]:
    app_url = make_url(admin_url).set(username=db.APP_ROLE, password=APP_PASSWORD)
    os.environ["XOLOTL_DATABASE_ADMIN_URL"] = admin_url
    os.environ["XOLOTL_DATABASE_URL"] = app_url.render_as_string(hide_password=False)
    os.environ["XOLOTL_ENV"] = "test"
    # Every build_app() of the suite serves no console, whatever the checkout holds: a
    # build copied to src/xolotl/static (the documented place) would otherwise be
    # mounted in every API test. tests/platform/test_static.py points this at its own.
    os.environ["XOLOTL_STATIC_DIR"] = str(tmp_path_factory.mktemp("no-console"))
    _reset_caches()
    migrate.bootstrap()
    migrate.upgrade()
    # What build_app does for the application: every test that reaches the database
    # can create a tenant, which gives it keys. Tests that build the app replace it
    # with an equal one; tests that need none ask for no_key_provider.
    set_key_provider(build_key_provider(get_settings(), db.get_sessionmaker()))
    yield db.get_engine(), db.get_admin_engine()
    set_key_provider(None)
    db.get_engine().dispose()
    db.get_admin_engine().dispose()


@pytest.fixture
def clean_db(engines: tuple[Engine, Engine]) -> None:
    """Truncate every table except alembic_version before the test runs."""
    _, admin = engines
    with admin.begin() as conn:
        names = cast(
            "list[str]",
            conn.scalars(
                text(
                    (
                        "SELECT c.relname FROM pg_class c "
                        "JOIN pg_namespace n ON n.oid = c.relnamespace "
                        "WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p') "
                        "AND NOT c.relispartition AND c.relname <> 'alembic_version'"
                    ),
                )
            ).all(),
        )
        if names:
            # Replica mode disables the audit append-only triggers for this
            # transaction only; the owner may truncate everything.
            _ = conn.execute(text("SET LOCAL session_replication_role = replica"))
            quoted = ", ".join(f'"{name}"' for name in names)
            _ = conn.execute(text(f"TRUNCATE TABLE {quoted} CASCADE"))


@pytest.fixture
def no_key_provider() -> Iterator[None]:
    """Run the test in a process where nothing has installed a key provider."""
    try:
        previous: KeyProvider | None = get_key_provider()
    except RuntimeError:
        previous = None
    set_key_provider(None)
    yield
    set_key_provider(previous)


@pytest.fixture
def engine(engines: tuple[Engine, Engine], clean_db: None) -> Engine:
    return engines[0]


@pytest.fixture
def admin_engine(engines: tuple[Engine, Engine], clean_db: None) -> Engine:
    return engines[1]


@pytest.fixture
def app(engine: Engine) -> FastAPI:
    return build_app()


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app) as c:
        yield c


@pytest.fixture
def tenant(engine: Engine) -> CreatedTenant:
    return new_tenant()


@pytest.fixture
def admin_headers(client: TestClient, tenant: CreatedTenant) -> dict[str, str]:
    """Bearer headers for the tenant admin once the forced password change is done.

    Returns:
        A mapping holding the ``Authorization`` header with the token the password
        change returned (the login token stops working once the password changes).

    """
    return first_login(
        client, tenant.slug, tenant.admin_email, tenant.admin_password, ADMIN_PASSWORD
    )


@pytest.fixture
def managed_owner(admin_engine: Engine, monkeypatch) -> Iterator[tuple[Engine, str]]:
    """Point bootstrap at a database owned by a CREATEROLE role, not a superuser.

    That is the owner managed Postgres (RDS, Cloud SQL) hands out. It holds ADMIN
    OPTION on the app role, as PG16+ grants a role's creator; roles are cluster-wide,
    so the app role is the suite's own and keeps its password.

    Yields:
        The owner's engine, which bootstrap now runs on, and the app role's URL for
        that database.

    """
    autocommit = admin_engine.execution_options(isolation_level="AUTOCOMMIT")
    with autocommit.connect() as conn:
        for statement in (
            f"CREATE ROLE {MANAGED_OWNER} LOGIN CREATEROLE PASSWORD 'managed'",
            (
                f"GRANT {db.APP_ROLE} TO {MANAGED_OWNER} "
                "WITH ADMIN OPTION, INHERIT FALSE, SET FALSE"
            ),
            f"CREATE DATABASE {MANAGED_DATABASE} OWNER {MANAGED_OWNER}",
        ):
            _ = conn.execute(text(statement))
    settings = get_settings()
    owner_url = make_url(settings.database_admin_url).set(
        username=MANAGED_OWNER, password="managed", database=MANAGED_DATABASE
    )
    app_url = make_url(settings.database_url).set(database=MANAGED_DATABASE)
    managed = settings.model_copy(
        update={
            "database_admin_url": owner_url.render_as_string(hide_password=False),
            "database_url": app_url.render_as_string(hide_password=False),
        }
    )
    owner = create_engine(managed.database_admin_url)
    monkeypatch.setattr(migrate, "get_settings", lambda: managed)
    monkeypatch.setattr(migrate, "get_admin_engine", lambda: owner)
    try:
        yield owner, managed.database_url
    finally:
        owner.dispose()
        with autocommit.connect() as conn:
            _ = conn.execute(text(f"DROP DATABASE {MANAGED_DATABASE} WITH (FORCE)"))
            _ = conn.execute(text(f"DROP ROLE {MANAGED_OWNER}"))


def _drop_bound_owner(admin_engine: Engine) -> None:
    with admin_engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        _ = conn.execute(text(f"DROP DATABASE IF EXISTS {BOUND_DATABASE} WITH (FORCE)"))
        _ = conn.execute(text(f"DROP ROLE IF EXISTS {BOUND_OWNER}"))


def _server_admin_may_create(admin_engine: Engine) -> bool:
    with admin_engine.connect() as conn:
        return bool(
            conn.scalar(
                text(
                    """
                    SELECT rolsuper OR (rolcreaterole AND rolcreatedb)
                    FROM pg_roles WHERE rolname = current_user
                    """
                )
            )
        )


@pytest.fixture
def bound_owner_database(
    admin_engine: Engine, admin_url: str, monkeypatch: pytest.MonkeyPatch
) -> Generator[tuple[Engine, Engine]]:
    """Create a database owned by a role that row-level security binds.

    The session database belongs to a superuser, whom row-level security never binds,
    so a migration that forgets to scope its statements to a tenant (or deletes rows
    without naming one) passes there and finds no row on a managed server: an owner with
    neither SUPERUSER nor BYPASSRLS, which is what ``bootstrap`` is documented to need.
    The migrations run against this database as that owner: the alembic environment
    takes its engine from ``db``. A server whose admin role cannot create the role and
    the database fails the run (``require_server_admin``) instead of skipping the test.

    Yields:
        The owner's engine and a superuser's engine on the same database.

    """
    require_server_admin(allowed=_server_admin_may_create(admin_engine))
    _drop_bound_owner(admin_engine)  # whatever an interrupted run left behind
    with admin_engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        _ = conn.execute(
            text(
                f"""
                CREATE ROLE {BOUND_OWNER} LOGIN PASSWORD 'owner-password'
                NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE
                """
            )
        )
        _ = conn.execute(text(f"CREATE DATABASE {BOUND_DATABASE} OWNER {BOUND_OWNER}"))
    root_url = make_url(admin_url).set(database=BOUND_DATABASE)
    owner_url = root_url.set(username=BOUND_OWNER, password="owner-password")
    owner = create_engine(owner_url, hide_parameters=True)
    root = create_engine(root_url, hide_parameters=True)
    monkeypatch.setattr(db, "get_admin_engine", lambda: owner)
    try:
        yield owner, root
    finally:
        owner.dispose()
        root.dispose()
        _drop_bound_owner(admin_engine)


@pytest.fixture
def owner_without_admin_option(
    admin_engine: Engine, managed_owner: tuple[Engine, str], monkeypatch
) -> Iterator[str]:
    """Leave the managed owner as if someone else had created the app role.

    The owner loses its ADMIN OPTION on the app role, so PG16+ refuses its ALTER of
    the role's password. Bootstrap gets a distinctive app password, which a test can
    look for in error output; teardown sets the shared role's password back anyway.

    Yields:
        The password bootstrap now tries to set.

    """
    owner, managed_app_url = managed_owner
    with admin_engine.begin() as conn:
        _ = conn.execute(text(f"REVOKE {db.APP_ROLE} FROM {MANAGED_OWNER}"))
    app_url = make_url(managed_app_url)
    secret = "never-shown-4711"
    with_secret = get_settings().model_copy(
        update={
            "database_admin_url": owner.url.render_as_string(hide_password=False),
            "database_url": app_url.set(password=secret).render_as_string(
                hide_password=False
            ),
        }
    )
    monkeypatch.setattr(migrate, "get_settings", lambda: with_secret)
    try:
        yield secret
    finally:
        with admin_engine.begin() as conn:
            migrate.exec_literal_ddl(
                conn,
                sql.SQL("ALTER ROLE {} PASSWORD {}").format(
                    sql.Identifier(db.APP_ROLE), sql.Literal(app_url.password)
                ),
            )


def pytest_configure() -> None:
    """Build every app of the suite without its worker thread.

    ``build_app`` starts the embedded worker by default (``XOLOTL_EMBEDDED_WORKER``),
    and a thread handing out events and running jobs behind every API test would race
    the tests' own assertions and the truncation between tests. The tests of the
    embedded worker turn it on for themselves.
    """
    os.environ["XOLOTL_EMBEDDED_WORKER"] = "false"
