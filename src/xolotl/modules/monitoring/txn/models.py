"""The transaction tables (SP-04 spec §5.2, first block; migration 0012).

``transaction`` is partitioned monthly by ``occurred_at``: its primary key is
``(tenant_id, occurred_at, id)`` and it is declared like ``AuditEventRow``, with no
``TenantTableMixin`` (plan 4a Deviation 3). Importing this module registers it with
``xolotl.platform.partitions``, so ``migrate.upgrade()`` and the partition job keep
its months ahead. Every link to a transaction carries ``(tenant_id, occurred_at,
transaction_id)`` with a composite foreign key to that primary key (D11).
``transaction_ref`` is the idempotency key of an ``external_id``; ``raw`` holds the
submitted item, counterparty included, as ciphertext under the tenant's DATA key.
Parties, relationships, counterparties and policy snapshots are referenced by table
name, so this module imports none of their models.
"""

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    LargeBinary,
    Numeric,
    PrimaryKeyConstraint,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql.schema import SchemaItem

from xolotl.modules.monitoring.enums import (
    AccountKind,
    AccountStatus,
    BatchSource,
    FileStatus,
    ParkReason,
    TransactionStatus,
)
from xolotl.modules.monitoring.txn.schema import Channel, Direction, Instrument, Rail
from xolotl.platform.crypto.encrypted import Encrypted, EncryptedJson
from xolotl.platform.db import Base
from xolotl.platform.ids import uuid7
from xolotl.platform.partitions import PartitionedTable, register_partitioned_table
from xolotl.platform.sqltypes import enum_column
from xolotl.platform.tenancy.ddl import TenantTableMixin, tenant_fk, tenant_table_args

TABLE = "transaction"
PENDING_PREDICATE = "status IN ('RECEIVED', 'PARKED')"

register_partitioned_table(PartitionedTable(TABLE, "occurred_at", None))


def _transaction_fk(table: str) -> ForeignKeyConstraint:
    # The conventional name would pass Postgres's 63 bytes.
    return ForeignKeyConstraint(
        ["tenant_id", "occurred_at", "transaction_id"],
        ["transaction.tenant_id", "transaction.occurred_at", "transaction.id"],
        name=f"fk_{table}_transaction",
    )


class Account(TenantTableMixin, Base):
    """A relationship's account; ``DEFAULT`` when the client sends no account."""

    __tablename__: str = "account"
    __table_args__: tuple[SchemaItem, ...] = tenant_table_args(
        "account",
        tenant_fk("relationship_id", "customer_relationship"),
        tenant_fk("party_id", "party"),
        UniqueConstraint("tenant_id", "external_ref"),
    )

    relationship_id: Mapped[UUID] = mapped_column(Uuid)
    party_id: Mapped[UUID] = mapped_column(Uuid)
    # The client's reference, or 'default:<relationship_id>'.
    external_ref: Mapped[str] = mapped_column(Text)
    kind: Mapped[AccountKind] = mapped_column(
        enum_column(AccountKind),
        default=AccountKind.DEFAULT,
        server_default=AccountKind.DEFAULT.value,
    )
    currency: Mapped[str | None] = mapped_column(String(3))
    status: Mapped[AccountStatus] = mapped_column(
        enum_column(AccountStatus),
        default=AccountStatus.ACTIVE,
        server_default=AccountStatus.ACTIVE.value,
    )
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )


class TransactionFile(TenantTableMixin, Base):
    """An uploaded file, sealed at ``transactions/files/<id>``, and its progress."""

    __tablename__: str = "transaction_file"
    __table_args__: tuple[SchemaItem, ...] = tenant_table_args(
        "transaction_file", UniqueConstraint("tenant_id", "sha256")
    )

    storage_key: Mapped[str] = mapped_column(Text)
    sha256: Mapped[bytes] = mapped_column(LargeBinary)
    size: Mapped[int] = mapped_column(BigInteger)
    content_type: Mapped[str] = mapped_column(Text)
    status: Mapped[FileStatus] = mapped_column(enum_column(FileStatus))
    rows_total: Mapped[int | None] = mapped_column(Integer)
    rows_accepted: Mapped[int] = mapped_column(
        Integer, default=0, server_default=text("0")
    )
    rows_duplicate: Mapped[int] = mapped_column(
        Integer, default=0, server_default=text("0")
    )
    rows_rejected: Mapped[int] = mapped_column(
        Integer, default=0, server_default=text("0")
    )
    # The next row to read; a resumed job starts there.
    cursor: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    # transactions/files/<id>/errors.csv: row, column and code, never a cell value.
    errors_key: Mapped[str | None] = mapped_column(Text)
    uploaded_by: Mapped[str] = mapped_column(Text)
    uploaded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)


class TransactionBatch(TenantTableMixin, Base):
    """One API batch, file page or CLI run and its counters."""

    __tablename__: str = "transaction_batch"
    __table_args__: tuple[SchemaItem, ...] = tenant_table_args(
        "transaction_batch", tenant_fk("file_id", "transaction_file")
    )

    source: Mapped[BatchSource] = mapped_column(enum_column(BatchSource))
    file_id: Mapped[UUID | None] = mapped_column(Uuid)
    # 'api_client:<id>', 'staff:<id>' or 'cli:<user>'.
    submitted_by: Mapped[str] = mapped_column(Text)
    item_count: Mapped[int] = mapped_column(Integer)
    accepted: Mapped[int] = mapped_column(Integer)
    duplicates: Mapped[int] = mapped_column(Integer)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    request_id: Mapped[str | None] = mapped_column(Text)


class Transaction(Base):
    """One transaction, monthly-partitioned by ``occurred_at`` (Deviation 3)."""

    __tablename__: str = TABLE
    __table_args__: tuple[SchemaItem | dict[str, str], ...] = (
        PrimaryKeyConstraint("tenant_id", "occurred_at", "id"),
        tenant_fk("party_id", "party"),
        tenant_fk("relationship_id", "customer_relationship"),
        tenant_fk("account_id", "account"),
        tenant_fk("counterparty_id", "counterparty"),
        tenant_fk("batch_id", "transaction_batch"),
        tenant_fk("policy_snapshot_id", "policy_snapshot"),
        # txn_decision references this table too; the cycle is closed by ALTER.
        ForeignKeyConstraint(
            ["tenant_id", "decided_at", "decision_id"],
            ["txn_decision.tenant_id", "txn_decision.decided_at", "txn_decision.id"],
            name="fk_transaction_decision",
            use_alter=True,
        ),
        Index(
            "ix_transaction_party_time",
            "tenant_id",
            "party_id",
            text("occurred_at DESC"),
        ),
        Index(
            "ix_transaction_relationship_time",
            "tenant_id",
            "relationship_id",
            text("occurred_at DESC"),
        ),
        Index(
            "ix_transaction_counterparty",
            "tenant_id",
            "counterparty_id",
            text("occurred_at DESC"),
        ),
        Index(
            "ix_transaction_pending",
            "tenant_id",
            "party_id",
            "occurred_at",
            postgresql_where=text(PENDING_PREDICATE),
        ),
        Index(
            "ix_transaction_period",
            "tenant_id",
            text("(period_keys ->> 'MONTH')"),
            "instrument",
        ),
        {"postgresql_partition_by": "RANGE (occurred_at)"},
    )

    tenant_id: Mapped[UUID] = mapped_column(Uuid)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    id: Mapped[UUID] = mapped_column(Uuid, default=uuid7)
    external_id: Mapped[str] = mapped_column(Text)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    batch_id: Mapped[UUID] = mapped_column(Uuid)
    party_id: Mapped[UUID] = mapped_column(Uuid)
    relationship_id: Mapped[UUID] = mapped_column(Uuid)
    account_id: Mapped[UUID] = mapped_column(Uuid)
    product: Mapped[str | None] = mapped_column(Text)
    direction: Mapped[Direction] = mapped_column(enum_column(Direction))
    rail: Mapped[Rail] = mapped_column(enum_column(Rail))
    instrument: Mapped[Instrument] = mapped_column(enum_column(Instrument))
    channel: Mapped[Channel] = mapped_column(enum_column(Channel))
    # Fiat, in minor units of ``currency``.
    amount_minor: Mapped[int] = mapped_column(BigInteger)
    currency: Mapped[str] = mapped_column(String(3))
    fee_minor: Mapped[int | None] = mapped_column(BigInteger)
    asset_qty: Mapped[Decimal | None] = mapped_column(Numeric(28, 10))
    asset_code: Mapped[str | None] = mapped_column(Text)
    # Keyed "<unit>/<rule>" (spec Decision 19); set at processing.
    unit_amounts: Mapped[dict[str, object] | None] = mapped_column(JSONB)
    counterparty_id: Mapped[UUID | None] = mapped_column(Uuid)
    counterparty_country: Mapped[str | None] = mapped_column(String(2))
    # The counterparty's screening status when the row was processed.
    counterparty_screening_status: Mapped[str | None] = mapped_column(Text)
    device_hash: Mapped[bytes | None] = mapped_column(LargeBinary)
    geo_country: Mapped[str | None] = mapped_column(String(2))
    geo_admin_area: Mapped[str | None] = mapped_column(Text)
    # No wallet address: that is the counterparty's, encrypted there and in raw.
    crypto: Mapped[dict[str, object] | None] = mapped_column(JSONB)
    fx: Mapped[dict[str, object] | None] = mapped_column(JSONB)
    purpose_code: Mapped[str | None] = mapped_column(Text)
    # DAY, MONTH, QUARTER and BUSINESS_DAY in America/Mexico_City.
    period_keys: Mapped[dict[str, object] | None] = mapped_column(JSONB)
    tier: Mapped[str | None] = mapped_column(Text)
    risk_rating: Mapped[str | None] = mapped_column(Text)
    monitoring_profile: Mapped[str] = mapped_column(
        Text, default="STD", server_default="STD"
    )
    policy_snapshot_id: Mapped[UUID | None] = mapped_column(Uuid)
    status: Mapped[TransactionStatus] = mapped_column(
        enum_column(TransactionStatus),
        default=TransactionStatus.RECEIVED,
        server_default=TransactionStatus.RECEIVED.value,
    )
    late: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false")
    )
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # The decision this transaction was linked to (migration 0014's foreign key).
    decision_id: Mapped[UUID | None] = mapped_column(Uuid)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # sha256 of the canonical input; an idempotent re-send compares it.
    content_hash: Mapped[bytes] = mapped_column(LargeBinary)
    raw: Mapped[Encrypted[dict[str, object]]] = mapped_column(EncryptedJson)


class TransactionRef(Base):
    """The idempotency key of an ``external_id`` (D11), written with its row."""

    __tablename__: str = "transaction_ref"
    __table_args__: tuple[SchemaItem, ...] = (
        PrimaryKeyConstraint("tenant_id", "external_id"),
        _transaction_fk("transaction_ref"),
    )

    tenant_id: Mapped[UUID] = mapped_column(Uuid)
    external_id: Mapped[str] = mapped_column(Text)
    transaction_id: Mapped[UUID] = mapped_column(Uuid)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    content_hash: Mapped[bytes] = mapped_column(LargeBinary)


class TransactionParked(Base):
    """Why a ``PARKED`` transaction waits, and how often it was retried."""

    __tablename__: str = "transaction_parked"
    __table_args__: tuple[SchemaItem, ...] = (
        PrimaryKeyConstraint("tenant_id", "transaction_id"),
        _transaction_fk("transaction_parked"),
    )

    tenant_id: Mapped[UUID] = mapped_column(Uuid)
    transaction_id: Mapped[UUID] = mapped_column(Uuid)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    reason: Mapped[ParkReason] = mapped_column(enum_column(ParkReason))
    unit_code: Mapped[str | None] = mapped_column(Text)
    needed_on: Mapped[date | None] = mapped_column(Date)
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    parked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    last_error: Mapped[str | None] = mapped_column(Text)
