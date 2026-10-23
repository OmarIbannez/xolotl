"""The party tables (SP-02 spec §4.2): parties, their facts and their documents.

Every personal value is ciphertext under the tenant's DATA key (``Encrypted*``
columns); equality search goes through the blind-index ``value_hash`` columns, whose
tags are computed from the validator's canonical value. ``person`` and
``organization`` share their ``id`` with ``party`` (one subtype row per party). The
names are nullable because the retention purge nulls them; ``create_party`` requires
them. ``verification_check`` has no writer in SP-02.
"""

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from pydantic import JsonValue
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKeyConstraint,
    Integer,
    LargeBinary,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql.schema import SchemaItem

from xolotl.modules.party.enums import (
    AddressType,
    ContactKind,
    CustomerRelationshipStatus,
    DocumentType,
    IdentifierType,
    PartyStatus,
    PartyType,
    PepStatus,
    RelationshipType,
    VerificationResult,
)
from xolotl.platform import clock
from xolotl.platform.connectors import CheckType, ConnectorMode
from xolotl.platform.crypto.encrypted import Encrypted, EncryptedJson, EncryptedStr
from xolotl.platform.db import Base
from xolotl.platform.sqltypes import enum_column
from xolotl.platform.tenancy.ddl import TenantTableMixin, tenant_fk, tenant_table_args


def _verified_check_fk(table: str) -> ForeignKeyConstraint:
    # Named by hand: the convention's name would exceed Postgres's 63 bytes.
    return ForeignKeyConstraint(
        ["tenant_id", "verified_check_id"],
        ["verification_check.tenant_id", "verification_check.id"],
        name=f"fk_{table}_verified_check",
    )


class Party(TenantTableMixin, Base):
    __tablename__: str = "party"
    __table_args__: tuple[SchemaItem, ...] = tenant_table_args("party")

    party_type: Mapped[PartyType] = mapped_column(enum_column(PartyType))
    country_code: Mapped[str] = mapped_column(String(2))
    status: Mapped[PartyStatus] = mapped_column(
        enum_column(PartyStatus),
        default=PartyStatus.ACTIVE,
        server_default=PartyStatus.ACTIVE.value,
    )
    retention_until: Mapped[date | None] = mapped_column(Date)
    legal_hold: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), onupdate=clock.now
    )


class Person(TenantTableMixin, Base):
    __tablename__: str = "person"
    __table_args__: tuple[SchemaItem, ...] = tenant_table_args(
        "person", tenant_fk("id", "party")
    )

    given_names: Mapped[Encrypted[str] | None] = mapped_column(EncryptedStr)
    first_surname: Mapped[Encrypted[str] | None] = mapped_column(EncryptedStr)
    second_surname: Mapped[Encrypted[str] | None] = mapped_column(EncryptedStr)
    # ISO YYYY-MM-DD text, encrypted.
    birth_date: Mapped[Encrypted[str] | None] = mapped_column(EncryptedStr)
    birth_country: Mapped[str | None] = mapped_column(String(2))
    birth_state: Mapped[str | None] = mapped_column(Text)
    nationalities: Mapped[list[str]] = mapped_column(ARRAY(String(2)), default=list)
    gender: Mapped[str | None] = mapped_column(Text)
    occupation_code: Mapped[str | None] = mapped_column(Text)
    economic_activity_code: Mapped[str | None] = mapped_column(Text)
    is_resident: Mapped[bool | None] = mapped_column(Boolean)
    pep_status: Mapped[PepStatus] = mapped_column(
        enum_column(PepStatus),
        default=PepStatus.NONE,
        server_default=PepStatus.NONE.value,
    )
    pep_since: Mapped[date | None] = mapped_column(Date)
    pep_until: Mapped[date | None] = mapped_column(Date)
    clave_elector: Mapped[Encrypted[str] | None] = mapped_column(EncryptedStr)


class Organization(TenantTableMixin, Base):
    __tablename__: str = "organization"
    __table_args__: tuple[SchemaItem, ...] = tenant_table_args(
        "organization", tenant_fk("id", "party")
    )

    legal_name: Mapped[Encrypted[str] | None] = mapped_column(EncryptedStr)
    trade_name: Mapped[str | None] = mapped_column(Text)
    legal_form: Mapped[str | None] = mapped_column(Text)
    incorporation_date: Mapped[date | None] = mapped_column(Date)
    industry_code: Mapped[str | None] = mapped_column(Text)
    registry_ref: Mapped[str | None] = mapped_column(Text)
    country_of_incorporation: Mapped[str | None] = mapped_column(String(2))


class VerificationCheck(TenantTableMixin, Base):
    __tablename__: str = "verification_check"
    __table_args__: tuple[SchemaItem, ...] = tenant_table_args(
        "verification_check",
        tenant_fk("party_id", "party"),
        ForeignKeyConstraint(
            ["tenant_id", "policy_snapshot_id"],
            ["policy_snapshot.tenant_id", "policy_snapshot.id"],
            name="fk_verification_check_policy_snapshot",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "connector_call_id"],
            ["connector_call.tenant_id", "connector_call.id"],
            name="fk_verification_check_connector_call",
        ),
    )

    party_id: Mapped[UUID | None] = mapped_column(Uuid)
    check_type: Mapped[CheckType] = mapped_column(enum_column(CheckType))
    provider: Mapped[str] = mapped_column(Text)
    mode: Mapped[ConnectorMode] = mapped_column(enum_column(ConnectorMode))
    result: Mapped[VerificationResult] = mapped_column(enum_column(VerificationResult))
    details: Mapped[dict[str, JsonValue] | None] = mapped_column(
        JSONB(none_as_null=True)
    )
    evidence_key: Mapped[str | None] = mapped_column(Text)
    connector_call_id: Mapped[UUID | None] = mapped_column(Uuid)
    policy_snapshot_id: Mapped[UUID | None] = mapped_column(Uuid)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )


class PartyIdentifier(TenantTableMixin, Base):
    __tablename__: str = "party_identifier"
    __table_args__: tuple[SchemaItem, ...] = tenant_table_args(
        "party_identifier",
        tenant_fk("party_id", "party"),
        _verified_check_fk("party_identifier"),
        UniqueConstraint("tenant_id", "id_type", "value_hash"),
    )

    party_id: Mapped[UUID] = mapped_column(Uuid)
    id_type: Mapped[IdentifierType] = mapped_column(enum_column(IdentifierType))
    country_code: Mapped[str] = mapped_column(String(2))
    value: Mapped[Encrypted[str]] = mapped_column(EncryptedStr)
    # Blind index of the canonical value, kind id_type.value.lower().
    value_hash: Mapped[bytes] = mapped_column(LargeBinary)
    verified_check_id: Mapped[UUID | None] = mapped_column(Uuid)
    valid_from: Mapped[date | None] = mapped_column(Date)
    valid_to: Mapped[date | None] = mapped_column(Date)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )


class PartyContact(TenantTableMixin, Base):
    __tablename__: str = "party_contact"
    __table_args__: tuple[SchemaItem, ...] = tenant_table_args(
        "party_contact",
        tenant_fk("party_id", "party"),
        _verified_check_fk("party_contact"),
        UniqueConstraint("tenant_id", "party_id", "kind", "value_hash"),
    )

    party_id: Mapped[UUID] = mapped_column(Uuid)
    kind: Mapped[ContactKind] = mapped_column(enum_column(ContactKind))
    value: Mapped[Encrypted[str]] = mapped_column(EncryptedStr)
    # Blind index of the canonical value, kind "email" or "phone".
    value_hash: Mapped[bytes] = mapped_column(LargeBinary)
    verified_check_id: Mapped[UUID | None] = mapped_column(Uuid)
    is_primary: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )


class PartyAddress(TenantTableMixin, Base):
    __tablename__: str = "party_address"
    __table_args__: tuple[SchemaItem, ...] = tenant_table_args(
        "party_address", tenant_fk("party_id", "party")
    )

    party_id: Mapped[UUID] = mapped_column(Uuid)
    address_type: Mapped[AddressType] = mapped_column(enum_column(AddressType))
    address: Mapped[Encrypted[dict[str, object]]] = mapped_column(EncryptedJson)
    country_code: Mapped[str] = mapped_column(String(2))
    admin_area_code: Mapped[str | None] = mapped_column(Text)
    municipality_code: Mapped[str | None] = mapped_column(Text)
    postal_code: Mapped[str | None] = mapped_column(Text)
    valid_from: Mapped[date] = mapped_column(Date)
    valid_to: Mapped[date | None] = mapped_column(Date)


class PartyRelationship(TenantTableMixin, Base):
    __tablename__: str = "party_relationship"
    __table_args__: tuple[SchemaItem, ...] = tenant_table_args(
        "party_relationship",
        tenant_fk("from_party_id", "party"),
        tenant_fk("to_party_id", "party"),
        CheckConstraint("from_party_id <> to_party_id", name="from_to_differ"),
    )

    from_party_id: Mapped[UUID] = mapped_column(Uuid)
    to_party_id: Mapped[UUID] = mapped_column(Uuid)
    rel_type: Mapped[RelationshipType] = mapped_column(enum_column(RelationshipType))
    ownership_pct: Mapped[Decimal | None] = mapped_column(Numeric(7, 4))
    control_basis: Mapped[str | None] = mapped_column(Text)
    valid_from: Mapped[date] = mapped_column(Date)
    valid_to: Mapped[date | None] = mapped_column(Date)
    # No foreign key, as the spec: the source document may be purged first.
    source_doc_id: Mapped[UUID | None] = mapped_column(Uuid)


class Document(TenantTableMixin, Base):
    __tablename__: str = "document"
    __table_args__: tuple[SchemaItem, ...] = tenant_table_args(
        "document", tenant_fk("party_id", "party")
    )

    party_id: Mapped[UUID] = mapped_column(Uuid)
    doc_type: Mapped[DocumentType] = mapped_column(enum_column(DocumentType))
    storage_key: Mapped[str] = mapped_column(Text)
    sha256: Mapped[bytes] = mapped_column(LargeBinary)
    size: Mapped[int] = mapped_column(Integer)
    content_type: Mapped[str] = mapped_column(Text)
    extracted: Mapped[Encrypted[str] | None] = mapped_column(EncryptedStr)
    uploaded_by: Mapped[str] = mapped_column(Text)
    uploaded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )


class CustomerRelationship(TenantTableMixin, Base):
    __tablename__: str = "customer_relationship"
    __table_args__: tuple[SchemaItem, ...] = tenant_table_args(
        "customer_relationship",
        tenant_fk("party_id", "party"),
        UniqueConstraint("tenant_id", "external_ref"),
    )

    party_id: Mapped[UUID] = mapped_column(Uuid)
    external_ref: Mapped[str] = mapped_column(Text)
    product: Mapped[str | None] = mapped_column(Text)
    status: Mapped[CustomerRelationshipStatus] = mapped_column(
        enum_column(CustomerRelationshipStatus)
    )
    account_tier: Mapped[str | None] = mapped_column(Text)
    next_review_at: Mapped[date | None] = mapped_column(Date)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    # {"monthly_inflow_minor", "monthly_outflow_minor", "currency",
    # "expected_countries", "expected_instruments"}; staff set it until onboarding
    # does (SP-04 spec Decision 10; migration 0014).
    declared_profile: Mapped[dict[str, JsonValue] | None] = mapped_column(JSONB)
    # The earliest occurred_at among the relationship's processed transactions,
    # which processing keeps (earliest wins) and retention moves forward, so the
    # rules read the history's start with the row (migration 0015).
    first_processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
