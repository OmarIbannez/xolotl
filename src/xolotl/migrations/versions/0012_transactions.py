"""monitoring: accounts, counterparties, partitioned transactions and aggregates

Revision ID: 0012_transactions
Revises: 0011_screening_hit_run_index
Create Date: 2026-10-20

Ten tenant tables as SP-04 spec §5.2's first block (the spec's 0011), with plan 4a's
Deviations 2 and 3: encrypted columns carry no ``_enc`` suffix, and ``transaction`` is
keyed ``(tenant_id, occurred_at, id)`` and partitioned monthly by ``occurred_at``.
Its ``transaction_default`` partition and the partitions of this month and the three
after it are hardened like ``audit_event``'s: forced row-level security and no grant,
so the application role reaches rows through the parent only. ``transaction_ref`` and
``transaction_parked`` reference a transaction through the partitioned primary key.
``counterparty`` is screening's table (spec Decision 18); its
``screening_subject_id`` gains a foreign key in 0013 and ``transaction.decision_id``
one in 0014. Every table gets forced row-level security and the default grant.
Downgrade drops the ten tables; ``transaction`` takes its partitions with it.
"""

from collections.abc import Sequence
from datetime import datetime
from uuid import UUID

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from xolotl.platform.partitions import ensure_partitions, harden_partition
from xolotl.platform.tenancy.ddl import enable_rls

revision: str = "0012_transactions"
down_revision: str | None = "0011_screening_hit_run_index"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLES = (
    "account",
    "counterparty",
    "transaction_file",
    "transaction_batch",
    "transaction",
    "transaction_ref",
    "transaction_parked",
    "agg_bucket",
    "agg_distinct",
    "agg_declaration",
)


def _keys() -> list[sa.Column[UUID]]:
    return [
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
    ]


def _now(name: str) -> sa.Column[datetime]:
    return sa.Column(
        name,
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.text("now()"),
    )


def _at(name: str, *, nullable: bool = True) -> sa.Column[datetime]:
    return sa.Column(name, sa.DateTime(timezone=True), nullable=nullable)


def _enum(
    name: str, *, nullable: bool = False, default: str | None = None
) -> sa.Column[str]:
    return sa.Column(
        name, sa.String(length=32), nullable=nullable, server_default=default
    )


def _text(name: str, *, nullable: bool = False) -> sa.Column[str]:
    return sa.Column(name, sa.Text(), nullable=nullable)


def _uuid(name: str, *, nullable: bool = False) -> sa.Column[UUID]:
    return sa.Column(name, sa.Uuid(), nullable=nullable)


def _bytes(name: str, *, nullable: bool = False) -> sa.Column[bytes]:
    return sa.Column(name, sa.LargeBinary(), nullable=nullable)


def _counter(name: str, kind: type[sa.Integer] = sa.Integer) -> sa.Column[int]:
    return sa.Column(name, kind(), nullable=False, server_default=sa.text("0"))


def _jsonb(name: str, *, nullable: bool = True) -> sa.Column[object]:
    return sa.Column(name, postgresql.JSONB(), nullable=nullable)


def _fk(
    table: str, column: str, target: str, *, name: str | None = None
) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        ["tenant_id", column],
        [f"{target}.tenant_id", f"{target}.id"],
        name=name or f"fk_{table}_tenant_id_{column}_{target}",
    )


def _transaction_fk(table: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        ["tenant_id", "occurred_at", "transaction_id"],
        ["transaction.tenant_id", "transaction.occurred_at", "transaction.id"],
        name=f"fk_{table}_transaction",
    )


def _accounts_and_counterparties() -> None:
    _ = op.create_table(
        "account",
        *_keys(),
        _uuid("relationship_id"),
        _uuid("party_id"),
        _text("external_ref"),
        _enum("kind", default="DEFAULT"),
        sa.Column("currency", sa.String(length=3), nullable=True),
        _enum("status", default="ACTIVE"),
        _now("first_seen_at"),
        _now("last_seen_at"),
        sa.PrimaryKeyConstraint("tenant_id", "id", name="pk_account"),
        sa.UniqueConstraint(
            "tenant_id", "external_ref", name="uq_account_tenant_id_external_ref"
        ),
        _fk("account", "relationship_id", "customer_relationship"),
        _fk("account", "party_id", "party"),
    )
    enable_rls("account")

    _ = op.create_table(
        "counterparty",
        *_keys(),
        _enum("kind"),
        _bytes("name", nullable=True),
        _bytes("name_hash", nullable=True),
        _bytes("account", nullable=True),
        _bytes("account_hash", nullable=True),
        _text("institution", nullable=True),
        sa.Column("country", sa.String(length=2), nullable=True),
        _bytes("wallet", nullable=True),
        _bytes("wallet_hash", nullable=True),
        _text("wallet_network", nullable=True),
        _enum("identifier_type", nullable=True),
        _bytes("identifier", nullable=True),
        _bytes("identifier_hash", nullable=True),
        _bytes("dedup_hash"),
        _enum("screening_status", default="UNSCREENED"),
        _at("screened_at"),
        _uuid("screening_subject_id", nullable=True),
        _now("first_seen_at"),
        _now("last_seen_at"),
        _counter("txn_count", sa.BigInteger),
        sa.PrimaryKeyConstraint("tenant_id", "id", name="pk_counterparty"),
        sa.UniqueConstraint(
            "tenant_id", "dedup_hash", name="uq_counterparty_tenant_id_dedup_hash"
        ),
        sa.CheckConstraint(
            "name_hash IS NOT NULL OR account_hash IS NOT NULL "
            "OR wallet_hash IS NOT NULL OR identifier_hash IS NOT NULL",
            name="ck_counterparty_one_component",
        ),
    )
    op.create_index(
        "ix_counterparty_name_hash", "counterparty", ["tenant_id", "name_hash"]
    )
    op.create_index(
        "ix_counterparty_wallet_hash", "counterparty", ["tenant_id", "wallet_hash"]
    )
    enable_rls("counterparty")


def _batches_and_files() -> None:
    _ = op.create_table(
        "transaction_file",
        *_keys(),
        _text("storage_key"),
        _bytes("sha256"),
        sa.Column("size", sa.BigInteger(), nullable=False),
        _text("content_type"),
        _enum("status"),
        sa.Column("rows_total", sa.Integer(), nullable=True),
        _counter("rows_accepted"),
        _counter("rows_duplicate"),
        _counter("rows_rejected"),
        _counter("cursor"),
        _text("errors_key", nullable=True),
        _text("uploaded_by"),
        _now("uploaded_at"),
        _at("finished_at"),
        _text("last_error", nullable=True),
        sa.PrimaryKeyConstraint("tenant_id", "id", name="pk_transaction_file"),
        sa.UniqueConstraint(
            "tenant_id", "sha256", name="uq_transaction_file_tenant_id_sha256"
        ),
    )
    enable_rls("transaction_file")

    _ = op.create_table(
        "transaction_batch",
        *_keys(),
        _enum("source"),
        _uuid("file_id", nullable=True),
        _text("submitted_by"),
        sa.Column("item_count", sa.Integer(), nullable=False),
        sa.Column("accepted", sa.Integer(), nullable=False),
        sa.Column("duplicates", sa.Integer(), nullable=False),
        _now("received_at"),
        _text("request_id", nullable=True),
        sa.PrimaryKeyConstraint("tenant_id", "id", name="pk_transaction_batch"),
        _fk("transaction_batch", "file_id", "transaction_file"),
    )
    enable_rls("transaction_batch")


def _transactions() -> None:
    _ = op.create_table(
        "transaction",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        _at("occurred_at", nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        _text("external_id"),
        _now("received_at"),
        _uuid("batch_id"),
        _uuid("party_id"),
        _uuid("relationship_id"),
        _uuid("account_id"),
        _text("product", nullable=True),
        _enum("direction"),
        _enum("rail"),
        _enum("instrument"),
        _enum("channel"),
        sa.Column("amount_minor", sa.BigInteger(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("fee_minor", sa.BigInteger(), nullable=True),
        sa.Column("asset_qty", sa.Numeric(28, 10), nullable=True),
        _text("asset_code", nullable=True),
        _jsonb("unit_amounts"),
        _uuid("counterparty_id", nullable=True),
        sa.Column("counterparty_country", sa.String(length=2), nullable=True),
        _text("counterparty_screening_status", nullable=True),
        _bytes("device_hash", nullable=True),
        sa.Column("geo_country", sa.String(length=2), nullable=True),
        _text("geo_admin_area", nullable=True),
        _jsonb("crypto"),
        _jsonb("fx"),
        _text("purpose_code", nullable=True),
        _jsonb("period_keys"),
        _text("tier", nullable=True),
        _text("risk_rating", nullable=True),
        sa.Column(
            "monitoring_profile", sa.Text(), nullable=False, server_default="STD"
        ),
        _uuid("policy_snapshot_id", nullable=True),
        _enum("status", default="RECEIVED"),
        sa.Column(
            "late", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
        _at("processed_at"),
        _uuid("decision_id", nullable=True),
        _at("decided_at"),
        _bytes("content_hash"),
        _bytes("raw"),
        sa.PrimaryKeyConstraint(
            "tenant_id", "occurred_at", "id", name="pk_transaction"
        ),
        _fk("transaction", "party_id", "party"),
        _fk("transaction", "relationship_id", "customer_relationship"),
        _fk("transaction", "account_id", "account"),
        _fk("transaction", "counterparty_id", "counterparty"),
        _fk("transaction", "batch_id", "transaction_batch"),
        _fk("transaction", "policy_snapshot_id", "policy_snapshot"),
        postgresql_partition_by="RANGE (occurred_at)",
    )
    for name, column in (
        ("ix_transaction_party_time", "party_id"),
        ("ix_transaction_relationship_time", "relationship_id"),
        ("ix_transaction_counterparty", "counterparty_id"),
    ):
        op.create_index(
            name, "transaction", ["tenant_id", column, sa.text("occurred_at DESC")]
        )
    op.create_index(
        "ix_transaction_pending",
        "transaction",
        ["tenant_id", "party_id", "occurred_at"],
        postgresql_where=sa.text("status IN ('RECEIVED', 'PARKED')"),
    )
    op.create_index(
        "ix_transaction_period",
        "transaction",
        ["tenant_id", sa.text("(period_keys ->> 'MONTH')"), "instrument"],
    )
    enable_rls("transaction")
    op.execute("CREATE TABLE transaction_default PARTITION OF transaction DEFAULT")
    conn = op.get_bind()
    harden_partition(conn, "transaction_default")
    _ = ensure_partitions(conn, table="transaction", months_ahead=3)

    _ = op.create_table(
        "transaction_ref",
        _uuid("tenant_id"),
        _text("external_id"),
        _uuid("transaction_id"),
        _at("occurred_at", nullable=False),
        _bytes("content_hash"),
        sa.PrimaryKeyConstraint("tenant_id", "external_id", name="pk_transaction_ref"),
        _transaction_fk("transaction_ref"),
    )
    enable_rls("transaction_ref")

    _ = op.create_table(
        "transaction_parked",
        _uuid("tenant_id"),
        _uuid("transaction_id"),
        _at("occurred_at", nullable=False),
        _enum("reason"),
        _text("unit_code", nullable=True),
        sa.Column("needed_on", sa.Date(), nullable=True),
        _counter("attempts"),
        _now("parked_at"),
        _text("last_error", nullable=True),
        sa.PrimaryKeyConstraint(
            "tenant_id", "transaction_id", name="pk_transaction_parked"
        ),
        _transaction_fk("transaction_parked"),
    )
    enable_rls("transaction_parked")


def _aggregates() -> None:
    bucket_key = ("tenant_id", "key_kind", "key", "agg_id", "bucket_start")
    _ = op.create_table(
        "agg_bucket",
        _uuid("tenant_id"),
        _enum("key_kind"),
        _text("key"),
        _text("agg_id"),
        _at("bucket_start", nullable=False),
        _enum("bucket_kind"),
        _at("bucket_end"),
        sa.Column(
            "value",
            sa.Numeric(28, 10),
            nullable=False,
            server_default=sa.text("0"),
        ),
        _counter("count", sa.BigInteger),
        sa.Column("max_value", sa.Numeric(28, 10), nullable=True),
        sa.Column("min_value", sa.Numeric(28, 10), nullable=True),
        _at("first_at"),
        _at("last_at"),
        _now("updated_at"),
        sa.PrimaryKeyConstraint(*bucket_key, name="pk_agg_bucket"),
    )
    op.create_index(
        "ix_agg_bucket_window",
        "agg_bucket",
        [
            "tenant_id",
            "key_kind",
            "key",
            "agg_id",
            "bucket_kind",
            sa.text("bucket_start DESC"),
        ],
    )
    enable_rls("agg_bucket")

    _ = op.create_table(
        "agg_distinct",
        _uuid("tenant_id"),
        _enum("key_kind"),
        _text("key"),
        _text("agg_id"),
        _at("bucket_start", nullable=False),
        _bytes("member_hash"),
        sa.PrimaryKeyConstraint(*bucket_key, "member_hash", name="pk_agg_distinct"),
    )
    enable_rls("agg_distinct")

    _ = op.create_table(
        "agg_declaration",
        _uuid("tenant_id"),
        _text("agg_id"),
        _jsonb("declaration", nullable=False),
        _enum("bucket_kind"),
        _enum("window_kind"),
        _text("window"),
        _at("backfilled_through"),
        _now("created_at"),
        sa.PrimaryKeyConstraint("tenant_id", "agg_id", name="pk_agg_declaration"),
    )
    enable_rls("agg_declaration")


def upgrade() -> None:
    _accounts_and_counterparties()
    _batches_and_files()
    _transactions()
    _aggregates()


def downgrade() -> None:
    # Dropping a table drops its indexes, policy, grants and partitions with it.
    for table in reversed(TABLES):
        op.drop_table(table)
