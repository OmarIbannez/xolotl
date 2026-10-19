"""Alembic entry points and database bootstrap (spec §4.5, §10)."""

import logging
from pathlib import Path
from typing import LiteralString, cast

import psycopg
from alembic import command
from alembic.config import Config
from psycopg import sql
from sqlalchemy import Connection, create_engine, make_url, text
from sqlalchemy.exc import DBAPIError, OperationalError
from sqlalchemy.pool import NullPool

from xolotl.platform.audit.partitions import ensure_partitions
from xolotl.platform.config import get_settings
from xolotl.platform.db import (
    APP_ROLE,
    BootstrapError,
    RoleBypassError,
    get_admin_engine,
)

logger = logging.getLogger(__name__)

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"
# Read as the owner role: is the role a superuser, does it have BYPASSRLS, and is
# it a member of the owner (inherited, or reachable with SET ROLE)? A superuser
# counts as a member of every role, so membership is reported for other roles only.
ROLE_ATTRIBUTES_SQL = text(
    """
    SELECT rolsuper, rolbypassrls,
           NOT rolsuper AND pg_has_role(rolname, current_user, 'MEMBER')
    FROM pg_roles WHERE rolname = :role
    """
)
# The clause that takes each removable attribute away.
_REMOVALS: dict[str, LiteralString] = {
    "SUPERUSER": "NOSUPERUSER",
    "BYPASSRLS": "NOBYPASSRLS",
}


def alembic_config() -> Config:
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    return config


def upgrade(revision: str = "head") -> None:
    """Apply migrations, then create any audit partition that is missing.

    ``serve`` runs this on every start, so this month and the three after it get
    their partitions; the monthly job that keeps them ahead arrives in plan 1c. A
    month that cannot be created yet is logged and skipped (``ensure_partitions``),
    and the upgrade, so the start, carries on.
    """
    command.upgrade(alembic_config(), revision)
    with get_admin_engine().begin() as conn:
        _ = ensure_partitions(conn, months_ahead=3)


def _app_password() -> str:
    url = make_url(get_settings().database_url)
    if url.username != APP_ROLE or not url.password:
        msg = f"XOLOTL_DATABASE_URL must authenticate as {APP_ROLE} with a password"
        raise ValueError(msg)
    return url.password


def exec_literal_ddl(conn: Connection, statement: sql.Composable) -> None:
    """Run DDL built with psycopg's sql module through SQLAlchemy.

    exec_driver_sql hands psycopg an empty parameter set, so psycopg still
    rewrites ``%`` sequences; doubling them keeps literals such as passwords intact.
    """
    _ = conn.exec_driver_sql(statement.as_string().replace("%", "%%"))


def _bypass_attributes(conn: Connection, role: str) -> list[str]:
    """Name each way ``role`` gets round row-level security, if any.

    ``conn`` runs as the owner role. A superuser or ``BYPASSRLS`` role ignores row
    security altogether, and a member of the owner role can act as the owner of
    every table and switch it off.

    Returns:
        ``SUPERUSER``, ``BYPASSRLS`` and ``member of the owner role``, for each that
        applies; empty when row-level security binds ``role``.

    """
    held = cast(
        "tuple[bool, bool, bool]",
        tuple(conn.execute(ROLE_ATTRIBUTES_SQL, {"role": role}).one()),
    )
    names = ("SUPERUSER", "BYPASSRLS", "member of the owner role")
    return [name for name, applies in zip(names, held, strict=True) if applies]


def verify_app_role(conn: Connection, *, role: str = APP_ROLE) -> None:
    """Check that row-level security binds ``role``.

    Raises:
        RoleBypassError: Naming each way ``role`` gets round it: it is a superuser,
            has ``BYPASSRLS`` or is a member of the owner role ``conn`` runs as.

    """
    if attributes := _bypass_attributes(conn, role):
        msg = f"role {role} can bypass row-level security: {', '.join(attributes)}"
        raise RoleBypassError(msg)


def _app_role_ddl(conn: Connection, statement: sql.Composable, *, purpose: str) -> None:
    """Run DDL that creates or alters the app role.

    Raises:
        BootstrapError: When the database refuses the statement. The message says
            what could not be done and names the role and the SQLSTATE only. The
            statement carries the password's SCRAM verifier, never the password
            (``scram_verifier``), and ``from None`` still keeps the driver's error,
            statement included, out of the traceback.

    """
    try:
        exec_literal_ddl(conn, statement)
    except (DBAPIError, psycopg.Error) as exc:
        cause = exc.orig if isinstance(exc, DBAPIError) else exc
        sqlstate = cast("str | None", getattr(cause, "sqlstate", None))
        msg = f"could not {purpose} role {APP_ROLE} (sqlstate {sqlstate})"
        raise BootstrapError(msg) from None


def _alter_app_role(conn: Connection, clauses: sql.Composable, *, purpose: str) -> None:
    """Run ``ALTER ROLE xolotl_app WITH <clauses>``, the only way bootstrap alters it.

    A ``PASSWORD`` clause carries the password's SCRAM verifier (``scram_verifier``),
    never the password. A refusal is a ``BootstrapError`` naming the role and the
    SQLSTATE only (see ``_app_role_ddl``).
    """
    _app_role_ddl(
        conn,
        sql.SQL("ALTER ROLE {} WITH {}").format(sql.Identifier(APP_ROLE), clauses),
        purpose=purpose,
    )


def scram_verifier(conn: Connection, password: str) -> str:
    """Return the SCRAM-SHA-256 verifier of ``password`` for the app role.

    libpq computes it on the application's side (``PQencryptPasswordConn``, what
    psql's password command uses) with a fresh salt and the iteration count the
    server reports, and applies SASLprep itself. The server stores a verifier it is
    handed as it is, so the ``ALTER ROLE`` that sets the password carries this and
    never the password: a refused statement reaches the server log without it.

    Returns:
        ``SCRAM-SHA-256$<iterations>:<salt>$<stored key>:<server key>``.

    Raises:
        TypeError: When psycopg, whose libpq does the work, is not behind ``conn``.

    """
    raw = conn.connection.dbapi_connection
    if not isinstance(raw, psycopg.Connection):
        msg = f"scram_verifier needs a psycopg connection, not {type(raw).__name__}"
        raise TypeError(msg)
    return raw.pgconn.encrypt_password(
        password.encode(), APP_ROLE.encode(), b"scram-sha-256"
    ).decode()


def _remove_bypass_attributes(conn: Connection) -> None:
    """Take ``SUPERUSER`` and ``BYPASSRLS`` away from the app role if it has them.

    On PG16 and later only a superuser may change ``SUPERUSER`` and only a
    ``BYPASSRLS`` role may change ``BYPASSRLS``, and a managed-Postgres owner is
    neither, so the attributes are named only when one must go. Only such a role
    could have granted one, so one is usually at hand; otherwise the ALTER fails
    with a ``BootstrapError`` naming them.
    """
    held = [name for name in _bypass_attributes(conn, APP_ROLE) if name in _REMOVALS]
    if held:
        _alter_app_role(
            conn,
            sql.SQL(" ".join(_REMOVALS[name] for name in held)),
            purpose=f"remove {' and '.join(held)} from",
        )


def bootstrap() -> None:
    """Create the application role if missing and sync its password and grants.

    Idempotent. Runs as the owner role (``DATABASE_ADMIN_URL``), which needs
    ``CREATEROLE`` but not ``SUPERUSER``: a role that already satisfies the
    invariants only gets ``LOGIN`` and its password set (on PG16 and later the owner
    needs ``ADMIN OPTION`` on it for that, which it has when it created the role).
    ``SUPERUSER`` or ``BYPASSRLS`` is removed first if the role has either. A CREATE
    or ALTER the database refuses (an owner without ``CREATEROLE`` cannot create the
    role) fails the bootstrap with ``BootstrapError``, which names the role and the
    SQLSTATE but never the statement. The password travels as its SCRAM-SHA-256
    verifier, computed by libpq on the application's side (``scram_verifier``), so
    an ALTER the server refuses and logs carries the verifier and never the
    password. Every run then verifies the role with ``verify_app_role``, so a role
    that could bypass row-level security fails the bootstrap (``RoleBypassError``,
    naming the attribute). Either way nothing is changed.
    """
    password = _app_password()
    database = make_url(get_settings().database_admin_url).database or "xolotl"
    with get_admin_engine().begin() as conn:
        exists = cast(
            "int | None",
            conn.scalar(
                text("SELECT 1 FROM pg_roles WHERE rolname = :role"), {"role": APP_ROLE}
            ),
        )
        if exists is None:
            _app_role_ddl(
                conn,
                sql.SQL(
                    "CREATE ROLE {} LOGIN NOBYPASSRLS NOSUPERUSER NOCREATEDB"
                ).format(sql.Identifier(APP_ROLE)),
                purpose="create",
            )
        # Before the password: a non-superuser owner may not alter a superuser role.
        _remove_bypass_attributes(conn)
        _alter_app_role(
            conn,
            sql.SQL("LOGIN PASSWORD {}").format(
                sql.Literal(scram_verifier(conn, password))
            ),
            purpose="set LOGIN and the password of",
        )
        exec_literal_ddl(
            conn,
            sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(
                sql.Identifier(database), sql.Identifier(APP_ROLE)
            ),
        )
        exec_literal_ddl(
            conn,
            sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(
                sql.Identifier(APP_ROLE)
            ),
        )
        verify_app_role(conn)


def _app_role_signs_in(url: str) -> bool:
    # A connection of its own, outside the application's pool: one pooled before a
    # password change would still answer. The server reports a missing role and a
    # wrong password alike, and either way the sign-in fails.
    probe = create_engine(url, poolclass=NullPool, hide_parameters=True)
    try:
        with probe.connect():
            pass
    except OperationalError:
        return False
    finally:
        probe.dispose()
    return True


def ensure_app_role() -> bool:
    """Bootstrap the app role when it cannot sign in with the configured password.

    ``serve`` runs this before the migrations, which grant to the role. A database
    the application has never started on (a host's first deploy) has no app role
    yet, and after ``XOLOTL_APP_DB_PASSWORD`` changes the role still has the old
    password; both fail the sign-in, and ``bootstrap`` creates the role or sets its
    password. A role that signs in is left as it is. Nothing happens unless
    ``XOLOTL_DATABASE_ADMIN_URL`` is set: the default points at a local development
    database. When the sign-in fails because the database is down, the bootstrap
    fails too.

    Returns:
        Whether it bootstrapped the role.

    Raises:
        BootstrapError: When the admin URL is set without an app-role credential
            (``XOLOTL_APP_DB_PASSWORD`` or ``XOLOTL_DATABASE_URL``): bootstrapping
            would create the role with the default password.

    """
    settings = get_settings()
    if "database_admin_url" not in settings.model_fields_set:
        return False
    if (
        "database_url" not in settings.model_fields_set
        and settings.app_db_password is None
    ):
        msg = (
            "XOLOTL_DATABASE_ADMIN_URL is set but no app-role credential is: set "
            "XOLOTL_APP_DB_PASSWORD (or XOLOTL_DATABASE_URL) so the role is never "
            "created with the default password"
        )
        raise BootstrapError(msg)
    if _app_role_signs_in(settings.database_url):
        return False
    logger.info("app role cannot sign in; bootstrapping it", extra={"role": APP_ROLE})
    bootstrap()
    return True


def reset() -> None:
    """Drop and recreate the public schema, then bootstrap and migrate.

    Refuses to run when ``XOLOTL_ENV=prod``.

    Raises:
        RuntimeError: When ``XOLOTL_ENV=prod``.

    """
    if get_settings().env == "prod":
        msg = "db reset is not allowed when XOLOTL_ENV=prod"
        raise RuntimeError(msg)
    with get_admin_engine().begin() as conn:
        _ = conn.execute(text("DROP SCHEMA public CASCADE"))
        _ = conn.execute(text("CREATE SCHEMA public"))
    bootstrap()
    upgrade()
