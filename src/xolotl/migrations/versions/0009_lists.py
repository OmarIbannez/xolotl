"""lists: the shared list catalog, tenant subscriptions and private lists

Revision ID: 0009_lists
Revises: 0008_party
Create Date: 2026-10-19

Creates the ``pg_trgm`` extension that migration 0010 indexes names with. Four shared
tables (``list_source``, ``list_version``, ``list_entry``, ``list_entry_delta``) carry
no row security. The application role reads all four and writes them through column
grants only: the operational columns of ``list_source`` (lease, validators, outcome),
``list_version.status``, ``list_entry.to_seq``, and inserts into the last three. The
catalog columns of ``list_source`` are written by the owner (``xolotl lists seed``).

Five tenant tables follow the kernel's conventions: ``(tenant_id, id)`` primary key
(``private_list_entry_delta`` is keyed naturally), composite foreign keys into the
tenant, forced row-level security. ``private_list_entry.entry`` holds ciphertext under
the tenant's DATA key.
"""

from collections.abc import Sequence
from datetime import datetime
from uuid import UUID

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from xolotl.platform.db import APP_ROLE
from xolotl.platform.tenancy.ddl import enable_rls, grant_shared

revision: str = "0009_lists"
down_revision: str | None = "0008_party"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SHARED = ("list_source", "list_version", "list_entry", "list_entry_delta")
TENANT = (
    "tenant_list_subscription",
    "private_list",
    "private_list_version",
    "private_list_entry",
    "private_list_entry_delta",
)
LIST_SOURCE_OPERATIONAL = (
    "next_poll_at",
    "lease_until",
    "lease_holder",
    "etag",
    "last_modified",
    "last_attempt_at",
    "last_success_at",
    "last_change_at",
    "last_error",
    "consecutive_failures",
)
DELTA_CHANGE_CHECK = (
    "(change = 'ADDED' AND prev_entry_id IS NULL AND new_entry_id IS NOT NULL) "
    "OR (change = 'CHANGED' AND prev_entry_id IS NOT NULL "
    "AND new_entry_id IS NOT NULL) "
    "OR (change = 'REMOVED' AND prev_entry_id IS NOT NULL AND new_entry_id IS NULL)"
)
PRIVATE_LIST_CODE_CHECK = (
    "list_code = 'MX_LPB' AND list_kind = 'BLOCKED' "
    "OR list_code LIKE 'INTERNAL\\_%' AND list_kind <> 'BLOCKED'"
)


def _keys() -> list[sa.Column[UUID]]:
    return [
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
    ]


def _at(name: str, *, nullable: bool = True) -> sa.Column[datetime]:
    return sa.Column(name, sa.DateTime(timezone=True), nullable=nullable)


def _now(name: str) -> sa.Column[datetime]:
    return sa.Column(
        name,
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.text("now()"),
    )


def _enum(name: str) -> sa.Column[str]:
    return sa.Column(name, sa.String(length=32), nullable=False)


def _int(name: str, *, nullable: bool = False) -> sa.Column[int]:
    return sa.Column(name, sa.Integer(), nullable=nullable)


def _counts() -> list[sa.Column[int]]:
    return [_int("entry_count"), _int("added"), _int("changed"), _int("removed")]


def _list_source() -> None:
    _ = op.create_table(
        "list_source",
        sa.Column("code", sa.Text(), nullable=False),
        _enum("list_kind"),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("jurisdiction", sa.Text(), nullable=False),
        _enum("format"),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("poll_interval", sa.Interval(), nullable=False),
        sa.Column(
            "enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")
        ),
        _now("next_poll_at"),
        _at("lease_until"),
        sa.Column("lease_holder", sa.Text(), nullable=True),
        sa.Column("etag", sa.Text(), nullable=True),
        sa.Column("last_modified", sa.Text(), nullable=True),
        _at("last_attempt_at"),
        _at("last_success_at"),
        _at("last_change_at"),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column(
            "consecutive_failures",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.PrimaryKeyConstraint("code", name="pk_list_source"),
    )
    grant_shared("list_source")
    columns = ", ".join(LIST_SOURCE_OPERATIONAL)
    op.execute(f"GRANT UPDATE ({columns}) ON list_source TO {APP_ROLE}")


def _list_version() -> None:
    _ = op.create_table(
        "list_version",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("list_code", sa.Text(), nullable=False),
        _int("seq"),
        _enum("status"),
        _at("published_at"),
        sa.Column("source_version", sa.Text(), nullable=True),
        sa.Column("parser_version", sa.Text(), nullable=False),
        _at("fetched_at", nullable=False),
        sa.Column("loaded_by", sa.Text(), nullable=False),
        sa.Column("file_sha256", sa.LargeBinary(), nullable=False),
        sa.Column("file_size", sa.BigInteger(), nullable=False),
        sa.Column("storage_key", sa.Text(), nullable=False),
        *_counts(),
        sa.Column("previous_version_id", sa.Uuid(), nullable=True),
        sa.ForeignKeyConstraint(
            ["list_code"],
            ["list_source.code"],
            name="fk_list_version_list_code_list_source",
        ),
        sa.ForeignKeyConstraint(
            ["previous_version_id"],
            ["list_version.id"],
            name="fk_list_version_previous_version_id_list_version",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_list_version"),
        sa.UniqueConstraint("list_code", "seq", name="uq_list_version_list_code_seq"),
    )
    op.create_index(
        "ux_list_version_active",
        "list_version",
        ["list_code"],
        unique=True,
        postgresql_where=sa.text("status = 'ACTIVE'"),
    )
    # Not unique: a source that reverts to an earlier file is a real change.
    op.create_index("ix_list_version_sha", "list_version", ["list_code", "file_sha256"])
    grant_shared("list_version")
    op.execute(f"GRANT INSERT, UPDATE (status) ON list_version TO {APP_ROLE}")


def _list_entry() -> None:
    _ = op.create_table(
        "list_entry",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("list_code", sa.Text(), nullable=False),
        sa.Column("source_entry_key", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.LargeBinary(), nullable=False),
        sa.Column("entity_type", sa.String(length=32), nullable=False),
        sa.Column("primary_name", sa.Text(), nullable=False),
        sa.Column("screenable", sa.Boolean(), nullable=False),
        sa.Column("entry", postgresql.JSONB(), nullable=False),
        _int("from_seq"),
        _int("to_seq", nullable=True),
        sa.ForeignKeyConstraint(
            ["list_code"],
            ["list_source.code"],
            name="fk_list_entry_list_code_list_source",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_list_entry"),
        sa.UniqueConstraint(
            "list_code",
            "source_entry_key",
            "from_seq",
            name="uq_list_entry_list_code_source_entry_key_from_seq",
        ),
    )
    op.create_index(
        "ux_list_entry_current",
        "list_entry",
        ["list_code", "source_entry_key"],
        unique=True,
        postgresql_where=sa.text("to_seq IS NULL"),
    )
    grant_shared("list_entry")
    op.execute(f"GRANT INSERT, UPDATE (to_seq) ON list_entry TO {APP_ROLE}")

    _ = op.create_table(
        "list_entry_delta",
        sa.Column("version_id", sa.Uuid(), nullable=False),
        sa.Column("source_entry_key", sa.Text(), nullable=False),
        _enum("change"),
        sa.Column("prev_entry_id", sa.Uuid(), nullable=True),
        sa.Column("new_entry_id", sa.Uuid(), nullable=True),
        sa.ForeignKeyConstraint(
            ["version_id"],
            ["list_version.id"],
            name="fk_list_entry_delta_version_id_list_version",
        ),
        sa.ForeignKeyConstraint(
            ["prev_entry_id"],
            ["list_entry.id"],
            name="fk_list_entry_delta_prev_entry_id_list_entry",
        ),
        sa.ForeignKeyConstraint(
            ["new_entry_id"],
            ["list_entry.id"],
            name="fk_list_entry_delta_new_entry_id_list_entry",
        ),
        sa.PrimaryKeyConstraint(
            "version_id", "source_entry_key", name="pk_list_entry_delta"
        ),
        sa.CheckConstraint(DELTA_CHANGE_CHECK, name="ck_list_entry_delta_change"),
    )
    grant_shared("list_entry_delta")
    op.execute(f"GRANT INSERT ON list_entry_delta TO {APP_ROLE}")


def _subscriptions() -> None:
    _ = op.create_table(
        "tenant_list_subscription",
        *_keys(),
        sa.Column("list_code", sa.Text(), nullable=False),
        _enum("requirement"),
        sa.Column(
            "enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")
        ),
        sa.Column(
            "last_seq", sa.Integer(), nullable=False, server_default=sa.text("0")
        ),
        _at("stale_notified_at"),
        sa.Column("changed_by", sa.Text(), nullable=True),
        _now("updated_at"),
        sa.ForeignKeyConstraint(
            ["list_code"],
            ["list_source.code"],
            name="fk_tenant_list_subscription_list_code_list_source",
        ),
        sa.PrimaryKeyConstraint("tenant_id", "id", name="pk_tenant_list_subscription"),
        sa.UniqueConstraint(
            "tenant_id",
            "list_code",
            name="uq_tenant_list_subscription_tenant_id_list_code",
        ),
        sa.CheckConstraint(
            "requirement <> 'MANDATORY' OR enabled",
            name="ck_tenant_list_subscription_mandatory_enabled",
        ),
    )
    enable_rls("tenant_list_subscription")


def _private_lists() -> None:
    _ = op.create_table(
        "private_list",
        *_keys(),
        sa.Column("list_code", sa.Text(), nullable=False),
        _enum("list_kind"),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("created_by", sa.Text(), nullable=False),
        _now("created_at"),
        sa.PrimaryKeyConstraint("tenant_id", "id", name="pk_private_list"),
        sa.UniqueConstraint(
            "tenant_id", "list_code", name="uq_private_list_tenant_id_list_code"
        ),
        sa.CheckConstraint(PRIVATE_LIST_CODE_CHECK, name="ck_private_list_code_kind"),
    )
    enable_rls("private_list")

    _ = op.create_table(
        "private_list_version",
        *_keys(),
        sa.Column("list_id", sa.Uuid(), nullable=False),
        _int("seq"),
        _enum("status"),
        sa.Column("uploaded_by", sa.Text(), nullable=False),
        _now("uploaded_at"),
        sa.Column("file_sha256", sa.LargeBinary(), nullable=False),
        _int("file_size"),
        sa.Column("content_type", sa.Text(), nullable=False),
        sa.Column("storage_key", sa.Text(), nullable=False),
        *_counts(),
        sa.ForeignKeyConstraint(
            ["tenant_id", "list_id"],
            ["private_list.tenant_id", "private_list.id"],
            name="fk_private_list_version_tenant_id_list_id_private_list",
        ),
        sa.PrimaryKeyConstraint("tenant_id", "id", name="pk_private_list_version"),
        sa.UniqueConstraint(
            "tenant_id",
            "list_id",
            "seq",
            name="uq_private_list_version_tenant_id_list_id_seq",
        ),
    )
    op.create_index(
        "ux_private_list_version_active",
        "private_list_version",
        ["tenant_id", "list_id"],
        unique=True,
        postgresql_where=sa.text("status = 'ACTIVE'"),
    )
    enable_rls("private_list_version")

    _ = op.create_table(
        "private_list_entry",
        *_keys(),
        sa.Column("list_id", sa.Uuid(), nullable=False),
        sa.Column("source_entry_key", sa.Text(), nullable=False),
        sa.Column("content_tag", sa.LargeBinary(), nullable=False),
        sa.Column("entity_type", sa.String(length=32), nullable=False),
        sa.Column("entry", sa.LargeBinary(), nullable=False),
        _int("from_seq"),
        _int("to_seq", nullable=True),
        sa.ForeignKeyConstraint(
            ["tenant_id", "list_id"],
            ["private_list.tenant_id", "private_list.id"],
            name="fk_private_list_entry_tenant_id_list_id_private_list",
        ),
        sa.PrimaryKeyConstraint("tenant_id", "id", name="pk_private_list_entry"),
        sa.UniqueConstraint(
            "tenant_id",
            "list_id",
            "source_entry_key",
            "from_seq",
            name="uq_private_list_entry_key_from_seq",
        ),
    )
    op.create_index(
        "ux_private_list_entry_current",
        "private_list_entry",
        ["tenant_id", "list_id", "source_entry_key"],
        unique=True,
        postgresql_where=sa.text("to_seq IS NULL"),
    )
    enable_rls("private_list_entry")

    _ = op.create_table(
        "private_list_entry_delta",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("version_id", sa.Uuid(), nullable=False),
        sa.Column("source_entry_key", sa.Text(), nullable=False),
        _enum("change"),
        sa.Column("prev_entry_id", sa.Uuid(), nullable=True),
        sa.Column("new_entry_id", sa.Uuid(), nullable=True),
        sa.ForeignKeyConstraint(
            ["tenant_id", "version_id"],
            ["private_list_version.tenant_id", "private_list_version.id"],
            name="fk_private_list_entry_delta_version",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "prev_entry_id"],
            ["private_list_entry.tenant_id", "private_list_entry.id"],
            name="fk_private_list_entry_delta_prev_entry",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "new_entry_id"],
            ["private_list_entry.tenant_id", "private_list_entry.id"],
            name="fk_private_list_entry_delta_new_entry",
        ),
        sa.PrimaryKeyConstraint(
            "tenant_id",
            "version_id",
            "source_entry_key",
            name="pk_private_list_entry_delta",
        ),
        sa.CheckConstraint(
            DELTA_CHANGE_CHECK, name="ck_private_list_entry_delta_change"
        ),
    )
    enable_rls("private_list_entry_delta")


def upgrade() -> None:
    # As the owner role; 0010 builds trigram indexes on party names with it.
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    _list_source()
    _list_version()
    _list_entry()
    _subscriptions()
    _private_lists()


def downgrade() -> None:
    # Dropping a table drops its indexes, policy and grants with it. The extension
    # stays: other objects of the database may use it.
    for table in (*reversed(TENANT), *reversed(SHARED)):
        op.drop_table(table)
