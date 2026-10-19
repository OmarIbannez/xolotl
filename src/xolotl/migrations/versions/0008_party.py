"""party core: parties, people, organisations, identifiers, contacts, addresses, relationships, documents, customer relationships and verification checks

Revision ID: 0008_party
Revises: 0007_connectors
Create Date: 2026-10-19

"""  # ruff: ignore[line-too-long]

from collections.abc import Sequence
from datetime import datetime
from uuid import UUID

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from xolotl.platform.tenancy.ddl import enable_rls

revision: str = "0008_party"
down_revision: str | None = "0007_connectors"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLES = (
    "party",
    "person",
    "organization",
    "verification_check",
    "party_identifier",
    "party_contact",
    "party_address",
    "party_relationship",
    "document",
    "customer_relationship",
)


def _keys() -> list[sa.Column[UUID]]:
    return [
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
    ]


def _created_at(name: str = "created_at") -> sa.Column[datetime]:
    return sa.Column(
        name,
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.text("now()"),
    )


def _party_fk(table: str, column: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        ["tenant_id", column],
        ["party.tenant_id", "party.id"],
        name=f"fk_{table}_tenant_id_{column}_party",
    )


def _verified_check_fk(table: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        ["tenant_id", "verified_check_id"],
        ["verification_check.tenant_id", "verification_check.id"],
        name=f"fk_{table}_verified_check",
    )


def _enum(name: str, *, nullable: bool = False) -> sa.Column[str]:
    return sa.Column(name, sa.String(length=32), nullable=nullable)


def _party_tables() -> None:
    _ = op.create_table(
        "party",
        *_keys(),
        _enum("party_type"),
        sa.Column("country_code", sa.String(length=2), nullable=False),
        sa.Column(
            "status", sa.String(length=32), nullable=False, server_default="ACTIVE"
        ),
        sa.Column("retention_until", sa.Date(), nullable=True),
        sa.Column(
            "legal_hold",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        _created_at(),
        _created_at("updated_at"),
        sa.PrimaryKeyConstraint("tenant_id", "id", name="pk_party"),
    )
    enable_rls("party")

    _ = op.create_table(
        "person",
        *_keys(),
        sa.Column("given_names", sa.LargeBinary(), nullable=True),
        sa.Column("first_surname", sa.LargeBinary(), nullable=True),
        sa.Column("second_surname", sa.LargeBinary(), nullable=True),
        sa.Column("birth_date", sa.LargeBinary(), nullable=True),
        sa.Column("birth_country", sa.String(length=2), nullable=True),
        sa.Column("birth_state", sa.Text(), nullable=True),
        sa.Column(
            "nationalities", postgresql.ARRAY(sa.String(length=2)), nullable=False
        ),
        sa.Column("gender", sa.Text(), nullable=True),
        sa.Column("occupation_code", sa.Text(), nullable=True),
        sa.Column("economic_activity_code", sa.Text(), nullable=True),
        sa.Column("is_resident", sa.Boolean(), nullable=True),
        sa.Column(
            "pep_status", sa.String(length=32), nullable=False, server_default="NONE"
        ),
        sa.Column("pep_since", sa.Date(), nullable=True),
        sa.Column("pep_until", sa.Date(), nullable=True),
        sa.Column("clave_elector", sa.LargeBinary(), nullable=True),
        sa.PrimaryKeyConstraint("tenant_id", "id", name="pk_person"),
        _party_fk("person", "id"),
    )
    enable_rls("person")

    _ = op.create_table(
        "organization",
        *_keys(),
        sa.Column("legal_name", sa.LargeBinary(), nullable=True),
        sa.Column("trade_name", sa.Text(), nullable=True),
        sa.Column("legal_form", sa.Text(), nullable=True),
        sa.Column("incorporation_date", sa.Date(), nullable=True),
        sa.Column("industry_code", sa.Text(), nullable=True),
        sa.Column("registry_ref", sa.Text(), nullable=True),
        sa.Column("country_of_incorporation", sa.String(length=2), nullable=True),
        sa.PrimaryKeyConstraint("tenant_id", "id", name="pk_organization"),
        _party_fk("organization", "id"),
    )
    enable_rls("organization")


def _verification_check() -> None:
    _ = op.create_table(
        "verification_check",
        *_keys(),
        sa.Column("party_id", sa.Uuid(), nullable=True),
        _enum("check_type"),
        sa.Column("provider", sa.Text(), nullable=False),
        _enum("mode"),
        _enum("result"),
        sa.Column("details", postgresql.JSONB(), nullable=True),
        sa.Column("evidence_key", sa.Text(), nullable=True),
        sa.Column("connector_call_id", sa.Uuid(), nullable=True),
        sa.Column("policy_snapshot_id", sa.Uuid(), nullable=True),
        _created_at(),
        sa.PrimaryKeyConstraint("tenant_id", "id", name="pk_verification_check"),
        _party_fk("verification_check", "party_id"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "policy_snapshot_id"],
            ["policy_snapshot.tenant_id", "policy_snapshot.id"],
            name="fk_verification_check_policy_snapshot",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "connector_call_id"],
            ["connector_call.tenant_id", "connector_call.id"],
            name="fk_verification_check_connector_call",
        ),
    )
    enable_rls("verification_check")


def _fact_tables() -> None:
    _ = op.create_table(
        "party_identifier",
        *_keys(),
        sa.Column("party_id", sa.Uuid(), nullable=False),
        _enum("id_type"),
        sa.Column("country_code", sa.String(length=2), nullable=False),
        sa.Column("value", sa.LargeBinary(), nullable=False),
        sa.Column("value_hash", sa.LargeBinary(), nullable=False),
        sa.Column("verified_check_id", sa.Uuid(), nullable=True),
        sa.Column("valid_from", sa.Date(), nullable=True),
        sa.Column("valid_to", sa.Date(), nullable=True),
        _created_at(),
        sa.PrimaryKeyConstraint("tenant_id", "id", name="pk_party_identifier"),
        _party_fk("party_identifier", "party_id"),
        _verified_check_fk("party_identifier"),
        sa.UniqueConstraint(
            "tenant_id",
            "id_type",
            "value_hash",
            name="uq_party_identifier_tenant_id_id_type_value_hash",
        ),
    )
    enable_rls("party_identifier")

    _ = op.create_table(
        "party_contact",
        *_keys(),
        sa.Column("party_id", sa.Uuid(), nullable=False),
        _enum("kind"),
        sa.Column("value", sa.LargeBinary(), nullable=False),
        sa.Column("value_hash", sa.LargeBinary(), nullable=False),
        sa.Column("verified_check_id", sa.Uuid(), nullable=True),
        sa.Column(
            "is_primary",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        _created_at(),
        sa.PrimaryKeyConstraint("tenant_id", "id", name="pk_party_contact"),
        _party_fk("party_contact", "party_id"),
        _verified_check_fk("party_contact"),
        sa.UniqueConstraint(
            "tenant_id",
            "party_id",
            "kind",
            "value_hash",
            name="uq_party_contact_tenant_id_party_id_kind_value_hash",
        ),
    )
    enable_rls("party_contact")

    _ = op.create_table(
        "party_address",
        *_keys(),
        sa.Column("party_id", sa.Uuid(), nullable=False),
        _enum("address_type"),
        sa.Column("address", sa.LargeBinary(), nullable=False),
        sa.Column("country_code", sa.String(length=2), nullable=False),
        sa.Column("admin_area_code", sa.Text(), nullable=True),
        sa.Column("municipality_code", sa.Text(), nullable=True),
        sa.Column("postal_code", sa.Text(), nullable=True),
        sa.Column("valid_from", sa.Date(), nullable=False),
        sa.Column("valid_to", sa.Date(), nullable=True),
        sa.PrimaryKeyConstraint("tenant_id", "id", name="pk_party_address"),
        _party_fk("party_address", "party_id"),
    )
    enable_rls("party_address")

    _ = op.create_table(
        "party_relationship",
        *_keys(),
        sa.Column("from_party_id", sa.Uuid(), nullable=False),
        sa.Column("to_party_id", sa.Uuid(), nullable=False),
        _enum("rel_type"),
        sa.Column("ownership_pct", sa.Numeric(precision=7, scale=4), nullable=True),
        sa.Column("control_basis", sa.Text(), nullable=True),
        sa.Column("valid_from", sa.Date(), nullable=False),
        sa.Column("valid_to", sa.Date(), nullable=True),
        sa.Column("source_doc_id", sa.Uuid(), nullable=True),
        sa.PrimaryKeyConstraint("tenant_id", "id", name="pk_party_relationship"),
        _party_fk("party_relationship", "from_party_id"),
        _party_fk("party_relationship", "to_party_id"),
        sa.CheckConstraint(
            "from_party_id <> to_party_id",
            name="ck_party_relationship_from_to_differ",
        ),
    )
    enable_rls("party_relationship")


def _customer_tables() -> None:
    _ = op.create_table(
        "document",
        *_keys(),
        sa.Column("party_id", sa.Uuid(), nullable=False),
        _enum("doc_type"),
        sa.Column("storage_key", sa.Text(), nullable=False),
        sa.Column("sha256", sa.LargeBinary(), nullable=False),
        sa.Column("size", sa.Integer(), nullable=False),
        sa.Column("content_type", sa.Text(), nullable=False),
        sa.Column("extracted", sa.LargeBinary(), nullable=True),
        sa.Column("uploaded_by", sa.Text(), nullable=False),
        _created_at("uploaded_at"),
        sa.PrimaryKeyConstraint("tenant_id", "id", name="pk_document"),
        _party_fk("document", "party_id"),
    )
    enable_rls("document")

    _ = op.create_table(
        "customer_relationship",
        *_keys(),
        sa.Column("party_id", sa.Uuid(), nullable=False),
        sa.Column("external_ref", sa.Text(), nullable=False),
        sa.Column("product", sa.Text(), nullable=True),
        _enum("status"),
        sa.Column("account_tier", sa.Text(), nullable=True),
        sa.Column("next_review_at", sa.Date(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        _created_at(),
        sa.PrimaryKeyConstraint("tenant_id", "id", name="pk_customer_relationship"),
        _party_fk("customer_relationship", "party_id"),
        sa.UniqueConstraint(
            "tenant_id",
            "external_ref",
            name="uq_customer_relationship_tenant_id_external_ref",
        ),
    )
    enable_rls("customer_relationship")


def upgrade() -> None:
    _party_tables()
    _verification_check()
    _fact_tables()
    _customer_tables()


def downgrade() -> None:
    # Dropping a table drops its policy and grants with it.
    for table in reversed(TABLES):
        op.drop_table(table)
