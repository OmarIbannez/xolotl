"""Shared fixtures.

Tests run against a real Postgres 17 (testcontainers, or
``XOLOTL_TEST_DATABASE_ADMIN_URL`` if set) with real commits on real
connections, so row-level security and advisory locks behave as in production.
Every table except ``alembic_version`` is truncated before each test that
touches the database.
"""

import os
from collections.abc import Iterator
from typing import cast

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from psycopg import sql
from sqlalchemy import Engine, create_engine, make_url, text
from testcontainers.community.postgres import PostgresContainer

from tests.helpers import first_login, new_tenant
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
def engines(admin_url: str) -> Iterator[tuple[Engine, Engine]]:
    app_url = make_url(admin_url).set(username=db.APP_ROLE, password=APP_PASSWORD)
    os.environ["XOLOTL_DATABASE_ADMIN_URL"] = admin_url
    os.environ["XOLOTL_DATABASE_URL"] = app_url.render_as_string(hide_password=False)
    os.environ["XOLOTL_ENV"] = "test"
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
