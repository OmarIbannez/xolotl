"""Conventions for tenant tables and the RLS DDL migrations emit (spec §4.3)."""

from uuid import UUID

from alembic import op
from sqlalchemy import ForeignKeyConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql.schema import SchemaItem

from xolotl.platform.db import APP_ROLE
from xolotl.platform.ids import uuid7
from xolotl.platform.tenancy.registry import SHARED_WRITABLE

TENANT_PREDICATE = (
    "tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid"
)
TENANT_TABLE_PREDICATE = (
    "id = NULLIF(current_setting('app.tenant_id', true), '')::uuid "
    "OR current_setting('app.actor_type', true) = 'PLATFORM_OPERATOR'"
)
# What the application role may do on a tenant table; append-only tables (the
# registry's APPEND_ONLY_TABLES) get only the second set.
DML_PRIVILEGES = "SELECT, INSERT, UPDATE, DELETE"
APPEND_ONLY_PRIVILEGES = "SELECT, INSERT"


class TenantTableMixin:
    """``tenant_id`` first in the primary key, ``id`` a UUIDv7."""

    tenant_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid7)


def tenant_table_args(table: str, *extra: SchemaItem) -> tuple[SchemaItem, ...]:
    """Build ``__table_args__`` for a tenant table.

    The primary key ``(tenant_id, id)`` is the target of composite foreign keys, so
    no separate ``UNIQUE (tenant_id, id)`` is added (Postgres discards it as a
    duplicate of the primary key). A table that has an ``id`` but whose primary key
    is not ``(tenant_id, id)`` must declare its own unique constraint in ``extra``.
    A table keyed naturally, with no ``id`` column (``event_consumption``,
    ``workflow_step``), has nothing to declare.
    ``table`` stays in the signature so every tenant model declares its arguments
    through this one function.

    Returns:
        ``extra`` as a tuple.

    """
    del table
    return tuple(extra)


def tenant_fk(
    column: str, target: str, *, target_column: str = "id", ondelete: str | None = None
) -> ForeignKeyConstraint:
    """Build a composite foreign key that keeps a reference inside one tenant.

    Returns:
        ``FOREIGN KEY (tenant_id, <column>)`` referencing ``<target>`` on
        ``(tenant_id, <target_column>)``.

    """
    return ForeignKeyConstraint(
        ["tenant_id", column],
        [f"{target}.tenant_id", f"{target}.{target_column}"],
        ondelete=ondelete,
    )


def rls_statements(
    table: str, *, predicate: str = TENANT_PREDICATE, grant: str | None = DML_PRIVILEGES
) -> list[str]:
    """Build the DDL that isolates ``table``: forced RLS, its policy, the grant.

    ``grant`` lists the privileges the application role gets; ``None`` grants
    nothing (partitions are reached through their parent only).

    Returns:
        The statements, in the order they must run.

    """
    statements = [
        f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY",
        f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY",
        (
            f"CREATE POLICY tenant_isolation ON {table} AS PERMISSIVE FOR ALL "
            f"USING ({predicate}) WITH CHECK ({predicate})"
        ),
    ]
    if grant is not None:
        statements.append(f"GRANT {grant} ON {table} TO {APP_ROLE}")
    return statements


def restrictive_policy_statement(
    table: str, name: str, using_sql: str, check_sql: str | None = None
) -> str:
    check = using_sql if check_sql is None else check_sql
    return (
        f"CREATE POLICY {name} ON {table} AS RESTRICTIVE FOR ALL "
        f"USING ({using_sql}) WITH CHECK ({check})"
    )


def shared_grant_statements(table: str) -> list[str]:
    privileges = (
        "SELECT, INSERT, UPDATE, DELETE" if table in SHARED_WRITABLE else "SELECT"
    )
    return [f"GRANT {privileges} ON {table} TO {APP_ROLE}"]


# Alembic wrappers: call these from migrations.


def enable_rls(
    table: str, *, predicate: str = TENANT_PREDICATE, grant: str | None = DML_PRIVILEGES
) -> None:
    for statement in rls_statements(table, predicate=predicate, grant=grant):
        op.execute(statement)


def add_restrictive_policy(
    table: str, name: str, using_sql: str, check_sql: str | None = None
) -> None:
    op.execute(restrictive_policy_statement(table, name, using_sql, check_sql))


def grant_shared(table: str) -> None:
    for statement in shared_grant_statements(table):
        op.execute(statement)
