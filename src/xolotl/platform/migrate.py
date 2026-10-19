"""Alembic entry points and database bootstrap (spec §4.5, §10)."""

from pathlib import Path
from typing import LiteralString, cast

import psycopg
from alembic import command
from alembic.config import Config
from psycopg import sql
from sqlalchemy import Connection, make_url, text
from sqlalchemy.exc import DBAPIError

from xolotl.platform.audit.partitions import ensure_partitions
from xolotl.platform.config import get_settings
from xolotl.platform.db import (
    APP_ROLE,
    BootstrapError,
    RoleBypassError,
    get_admin_engine,
)

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


def _alter_app_role(conn: Connection, clauses: sql.Composable, *, purpose: str) -> None:
    """Run ``ALTER ROLE xolotl_app WITH <clauses>``, the only way bootstrap alters it.

    Raises:
        BootstrapError: When the database refuses the statement. The message says
            what could not be done and names the role and the SQLSTATE only: the
            statement can carry the password, so ``from None`` also keeps the
            driver's error, statement included, out of the traceback.

    """
    try:
        exec_literal_ddl(
            conn,
            sql.SQL("ALTER ROLE {} WITH {}").format(sql.Identifier(APP_ROLE), clauses),
        )
    except (DBAPIError, psycopg.Error) as exc:
        cause = exc.orig if isinstance(exc, DBAPIError) else exc
        sqlstate = cast("str | None", getattr(cause, "sqlstate", None))
        msg = f"could not {purpose} role {APP_ROLE} (sqlstate {sqlstate})"
        raise BootstrapError(msg) from None


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
    ``SUPERUSER`` or ``BYPASSRLS`` is removed first if the role has either. An
    ALTER the database refuses fails the bootstrap with ``BootstrapError``, which
    names the role and the SQLSTATE but never the statement (it can carry the
    password). Every run then verifies the role with ``verify_app_role``, so a role
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
            exec_literal_ddl(
                conn,
                sql.SQL(
                    "CREATE ROLE {} LOGIN NOBYPASSRLS NOSUPERUSER NOCREATEDB"
                ).format(sql.Identifier(APP_ROLE)),
            )
        # Before the password: a non-superuser owner may not alter a superuser role.
        _remove_bypass_attributes(conn)
        _alter_app_role(
            conn,
            sql.SQL("LOGIN PASSWORD {}").format(sql.Literal(password)),
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
