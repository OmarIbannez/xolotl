"""The list catalog tables (SP-03 spec §5.1 and the 0009 block of §5.2).

Four shared tables carry the public lists: ``list_source`` (the catalog, seeded from
``sources.yaml``), ``list_version`` (one row per ingested file), ``list_entry`` (one
row per entry content, in the versions ``from_seq <= seq < to_seq``) and
``list_entry_delta``. The application role writes them through column grants only
(migration 0009). Five tenant tables carry each tenant's subscriptions and private
lists; a private entry is ciphertext under the tenant's DATA key, compared through an
HMAC ``content_tag``. ``entity_type`` holds the library's ``EntityType`` values as
text, so this module does not import the library.
"""

from datetime import datetime, timedelta
from uuid import UUID

from pydantic import JsonValue
from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Interval,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql.schema import SchemaItem

from xolotl.modules.screening.enums import (
    DeltaChange,
    ListKind,
    ListRequirement,
    SourceFormat,
    VersionStatus,
)
from xolotl.platform import clock
from xolotl.platform.crypto.encrypted import Encrypted, EncryptedJson
from xolotl.platform.db import Base
from xolotl.platform.ids import uuid7
from xolotl.platform.sqltypes import enum_column
from xolotl.platform.tenancy.ddl import TenantTableMixin, tenant_fk, tenant_table_args

# One shape for both delta tables: what each change says about the two entry rows.
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


class ListSource(Base):
    __tablename__: str = "list_source"

    code: Mapped[str] = mapped_column(Text, primary_key=True)
    list_kind: Mapped[ListKind] = mapped_column(enum_column(ListKind))
    title: Mapped[str] = mapped_column(Text)
    jurisdiction: Mapped[str] = mapped_column(Text)
    format: Mapped[SourceFormat] = mapped_column(enum_column(SourceFormat))
    url: Mapped[str] = mapped_column(Text)
    poll_interval: Mapped[timedelta] = mapped_column(Interval)
    enabled: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default=text("true")
    )
    # The operational columns below are the only ones the application role may
    # update (the lease, the conditional-request validators and the outcome).
    next_poll_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_holder: Mapped[str | None] = mapped_column(Text)
    etag: Mapped[str | None] = mapped_column(Text)
    last_modified: Mapped[str | None] = mapped_column(Text)
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_change_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # A code and a short reason only, never a response body or a record.
    last_error: Mapped[str | None] = mapped_column(Text)
    consecutive_failures: Mapped[int] = mapped_column(
        Integer, default=0, server_default=text("0")
    )


class ListVersion(Base):
    __tablename__: str = "list_version"
    __table_args__: tuple[SchemaItem, ...] = (
        UniqueConstraint("list_code", "seq"),
        Index(
            "ux_list_version_active",
            "list_code",
            unique=True,
            postgresql_where=text("status = 'ACTIVE'"),
        ),
        # Not unique: a source that reverts to an earlier file is a real change.
        Index("ix_list_version_sha", "list_code", "file_sha256"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid7)
    list_code: Mapped[str] = mapped_column(Text, ForeignKey("list_source.code"))
    seq: Mapped[int] = mapped_column(Integer)
    status: Mapped[VersionStatus] = mapped_column(enum_column(VersionStatus))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source_version: Mapped[str | None] = mapped_column(Text)
    parser_version: Mapped[str] = mapped_column(Text)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # 'job:<id>' or 'cli:<user>'.
    loaded_by: Mapped[str] = mapped_column(Text)
    file_sha256: Mapped[bytes] = mapped_column(LargeBinary)
    file_size: Mapped[int] = mapped_column(BigInteger)
    storage_key: Mapped[str] = mapped_column(Text)
    entry_count: Mapped[int] = mapped_column(Integer)
    added: Mapped[int] = mapped_column(Integer)
    changed: Mapped[int] = mapped_column(Integer)
    removed: Mapped[int] = mapped_column(Integer)
    previous_version_id: Mapped[UUID | None] = mapped_column(
        Uuid, ForeignKey("list_version.id")
    )


class ListEntryRow(Base):
    __tablename__: str = "list_entry"
    __table_args__: tuple[SchemaItem, ...] = (
        UniqueConstraint("list_code", "source_entry_key", "from_seq"),
        Index(
            "ux_list_entry_current",
            "list_code",
            "source_entry_key",
            unique=True,
            postgresql_where=text("to_seq IS NULL"),
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid7)
    list_code: Mapped[str] = mapped_column(Text, ForeignKey("list_source.code"))
    source_entry_key: Mapped[str] = mapped_column(Text)
    content_hash: Mapped[bytes] = mapped_column(LargeBinary)
    entity_type: Mapped[str] = mapped_column(String(32))
    primary_name: Mapped[str] = mapped_column(Text)
    screenable: Mapped[bool] = mapped_column(Boolean)
    # ListEntry.canonical_json(); immutable.
    entry: Mapped[dict[str, JsonValue]] = mapped_column(JSONB)
    # A member of the versions from_seq <= seq < to_seq; NULL is the active one.
    from_seq: Mapped[int] = mapped_column(Integer)
    to_seq: Mapped[int | None] = mapped_column(Integer)


class ListEntryDelta(Base):
    __tablename__: str = "list_entry_delta"
    __table_args__: tuple[SchemaItem, ...] = (
        CheckConstraint(DELTA_CHANGE_CHECK, name="change"),
    )

    version_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("list_version.id"), primary_key=True
    )
    source_entry_key: Mapped[str] = mapped_column(Text, primary_key=True)
    change: Mapped[DeltaChange] = mapped_column(enum_column(DeltaChange))
    prev_entry_id: Mapped[UUID | None] = mapped_column(
        Uuid, ForeignKey("list_entry.id")
    )
    new_entry_id: Mapped[UUID | None] = mapped_column(Uuid, ForeignKey("list_entry.id"))


class TenantListSubscription(TenantTableMixin, Base):
    __tablename__: str = "tenant_list_subscription"
    __table_args__: tuple[SchemaItem, ...] = tenant_table_args(
        "tenant_list_subscription",
        UniqueConstraint("tenant_id", "list_code"),
        CheckConstraint(
            "requirement <> 'MANDATORY' OR enabled", name="mandatory_enabled"
        ),
    )

    list_code: Mapped[str] = mapped_column(Text, ForeignKey("list_source.code"))
    requirement: Mapped[ListRequirement] = mapped_column(enum_column(ListRequirement))
    enabled: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default=text("true")
    )
    # Versions up to this one have been handed to re-screening.
    last_seq: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    stale_notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    changed_by: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), onupdate=clock.now
    )


class PrivateList(TenantTableMixin, Base):
    __tablename__: str = "private_list"
    __table_args__: tuple[SchemaItem, ...] = tenant_table_args(
        "private_list",
        UniqueConstraint("tenant_id", "list_code"),
        CheckConstraint(PRIVATE_LIST_CODE_CHECK, name="code_kind"),
    )

    # MX_LPB or INTERNAL_<SLUG>.
    list_code: Mapped[str] = mapped_column(Text)
    list_kind: Mapped[ListKind] = mapped_column(enum_column(ListKind))
    title: Mapped[str] = mapped_column(Text)
    created_by: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )


class PrivateListVersion(TenantTableMixin, Base):
    __tablename__: str = "private_list_version"
    __table_args__: tuple[SchemaItem, ...] = tenant_table_args(
        "private_list_version",
        tenant_fk("list_id", "private_list"),
        UniqueConstraint("tenant_id", "list_id", "seq"),
        Index(
            "ux_private_list_version_active",
            "tenant_id",
            "list_id",
            unique=True,
            postgresql_where=text("status = 'ACTIVE'"),
        ),
    )

    list_id: Mapped[UUID] = mapped_column(Uuid)
    seq: Mapped[int] = mapped_column(Integer)
    status: Mapped[VersionStatus] = mapped_column(enum_column(VersionStatus))
    uploaded_by: Mapped[str] = mapped_column(Text)
    uploaded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    file_sha256: Mapped[bytes] = mapped_column(LargeBinary)
    file_size: Mapped[int] = mapped_column(Integer)
    content_type: Mapped[str] = mapped_column(Text)
    # The upload sealed with the tenant DATA key, then stored.
    storage_key: Mapped[str] = mapped_column(Text)
    entry_count: Mapped[int] = mapped_column(Integer)
    added: Mapped[int] = mapped_column(Integer)
    changed: Mapped[int] = mapped_column(Integer)
    removed: Mapped[int] = mapped_column(Integer)


class PrivateListEntryRow(TenantTableMixin, Base):
    __tablename__: str = "private_list_entry"
    __table_args__: tuple[SchemaItem, ...] = tenant_table_args(
        "private_list_entry",
        tenant_fk("list_id", "private_list"),
        # Named by hand: the convention's name would exceed Postgres's 63 bytes.
        UniqueConstraint(
            "tenant_id",
            "list_id",
            "source_entry_key",
            "from_seq",
            name="uq_private_list_entry_key_from_seq",
        ),
        Index(
            "ux_private_list_entry_current",
            "tenant_id",
            "list_id",
            "source_entry_key",
            unique=True,
            postgresql_where=text("to_seq IS NULL"),
        ),
    )

    list_id: Mapped[UUID] = mapped_column(Uuid)
    # The file's clave column, else a keyed tag of name and birth date.
    source_entry_key: Mapped[str] = mapped_column(Text)
    # HMAC under the tenant INDEX key of the canonical JSON: no dictionary attack.
    content_tag: Mapped[bytes] = mapped_column(LargeBinary)
    entity_type: Mapped[str] = mapped_column(String(32))
    # ListEntry.canonical_json(), encrypted.
    entry: Mapped[Encrypted[dict[str, object]]] = mapped_column(EncryptedJson)
    from_seq: Mapped[int] = mapped_column(Integer)
    to_seq: Mapped[int | None] = mapped_column(Integer)


class PrivateListEntryDelta(Base):
    __tablename__: str = "private_list_entry_delta"
    # The foreign keys are named by hand: the convention's names would exceed
    # Postgres's 63 bytes.
    __table_args__: tuple[SchemaItem, ...] = (
        ForeignKeyConstraint(
            ["tenant_id", "version_id"],
            ["private_list_version.tenant_id", "private_list_version.id"],
            name="fk_private_list_entry_delta_version",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "prev_entry_id"],
            ["private_list_entry.tenant_id", "private_list_entry.id"],
            name="fk_private_list_entry_delta_prev_entry",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "new_entry_id"],
            ["private_list_entry.tenant_id", "private_list_entry.id"],
            name="fk_private_list_entry_delta_new_entry",
        ),
        CheckConstraint(DELTA_CHANGE_CHECK, name="change"),
    )

    tenant_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    version_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    source_entry_key: Mapped[str] = mapped_column(Text, primary_key=True)
    change: Mapped[DeltaChange] = mapped_column(enum_column(DeltaChange))
    prev_entry_id: Mapped[UUID | None] = mapped_column(Uuid)
    new_entry_id: Mapped[UUID | None] = mapped_column(Uuid)
