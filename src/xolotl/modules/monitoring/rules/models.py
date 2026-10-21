"""The rule tables (SP-04 spec §5.1 and the 4b block of §5.2; migration 0013).

``rule_template`` and ``rule_template_version`` are shared pack content: the owner
role seeds them and the application role only reads them. The tenant tables hold a
tenant's rules, their versions under the D19 lifecycle, simulations, assignments,
hits with the transactions behind them, and the tier-limit notices. A definition is
JSON and its hash bytes, so this module needs no definition types; family, kind,
severity and outcome are VARCHAR(32) text, as an enum column would store them.
Parties, relationships, snapshots, staff users and transactions are referenced by
table name. A link to a transaction carries ``(tenant_id, occurred_at,
transaction_id)``; the foreign keys whose conventional name would pass Postgres's 63
bytes are named by hand.
"""

from datetime import date, datetime
from uuid import UUID

from pydantic import JsonValue
from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    LargeBinary,
    PrimaryKeyConstraint,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql.schema import SchemaItem

from xolotl.modules.monitoring.rules.enums import (
    HitTransactionRole,
    NoticeSource,
    RuleVersionStatus,
    SimulationStatus,
)
from xolotl.modules.monitoring.txn.schema import KeyKind
from xolotl.platform.db import Base
from xolotl.platform.ids import uuid7
from xolotl.platform.sqltypes import enum_column
from xolotl.platform.tenancy.ddl import TenantTableMixin, tenant_fk, tenant_table_args

APPROVER_NOT_AUTHOR = "approved_by IS NULL OR approved_by <> author_id"
APPROVED_NEEDS_SIMULATION = (
    "status NOT IN ('APPROVED', 'ACTIVE') "
    "OR (approved_by IS NOT NULL AND simulation_id IS NOT NULL)"
)
ACTIVE_PREDICATE = "status = 'ACTIVE'"
# A vocabulary of the pure definitions, stored as an enum column would store it.
CODE = String(32)


def _named_fk(column: str, target: str, name: str) -> ForeignKeyConstraint:
    # The conventional name would pass Postgres's 63 bytes.
    return ForeignKeyConstraint(
        ["tenant_id", column], [f"{target}.tenant_id", f"{target}.id"], name=name
    )


class RuleTemplate(Base):
    """A typology of the library (spec §9.3); shared, no ``tenant_id``."""

    __tablename__: str = "rule_template"

    code: Mapped[str] = mapped_column(Text, primary_key=True)
    family: Mapped[str] = mapped_column(CODE)
    kind: Mapped[str] = mapped_column(CODE)
    title: Mapped[str] = mapped_column(Text)
    typology: Mapped[list[str]] = mapped_column(ARRAY(Text))
    country_code: Mapped[str] = mapped_column(String(2))
    # Pack codes it applies to, e.g. MX/ITF-IFPE.
    packs: Mapped[list[str]] = mapped_column(ARRAY(Text))


class RuleTemplateVersion(Base):
    """An immutable version of a template's definition; shared."""

    __tablename__: str = "rule_template_version"
    __table_args__: tuple[SchemaItem, ...] = (
        UniqueConstraint("template_code", "version"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid7)
    template_code: Mapped[str] = mapped_column(Text, ForeignKey("rule_template.code"))
    version: Mapped[int] = mapped_column(Integer)
    definition: Mapped[dict[str, JsonValue]] = mapped_column(JSONB)
    # sha256 of the canonical JSON.
    definition_hash: Mapped[bytes] = mapped_column(LargeBinary)
    loaded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    loaded_by: Mapped[str] = mapped_column(Text)
    source_path: Mapped[str] = mapped_column(Text)


class MonitoringRule(TenantTableMixin, Base):
    """A tenant's rule: a template code, or a tenant code for a kind-authored rule."""

    __tablename__: str = "monitoring_rule"
    __table_args__: tuple[SchemaItem, ...] = tenant_table_args(
        "monitoring_rule",
        tenant_fk("created_by", "staff_user"),
        UniqueConstraint("tenant_id", "code"),
    )

    code: Mapped[str] = mapped_column(Text)
    family: Mapped[str] = mapped_column(CODE)
    kind: Mapped[str] = mapped_column(CODE)
    template_code: Mapped[str | None] = mapped_column(
        Text, ForeignKey("rule_template.code")
    )
    title: Mapped[str] = mapped_column(Text)
    created_by: Mapped[UUID] = mapped_column(Uuid)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )


class MonitoringRuleVersion(TenantTableMixin, Base):
    """One version of a rule, with the tenant's params, under D19."""

    __tablename__: str = "monitoring_rule_version"
    __table_args__: tuple[SchemaItem, ...] = tenant_table_args(
        "monitoring_rule_version",
        tenant_fk("rule_id", "monitoring_rule"),
        tenant_fk("author_id", "staff_user"),
        tenant_fk("approved_by", "staff_user"),
        _named_fk(
            "retired_by_version_id",
            "monitoring_rule_version",
            "fk_rule_version_retired_by",
        ),
        # rule_simulation references this table too; the cycle is closed by ALTER.
        ForeignKeyConstraint(
            ["tenant_id", "simulation_id"],
            ["rule_simulation.tenant_id", "rule_simulation.id"],
            name="fk_rule_version_simulation",
            use_alter=True,
        ),
        ForeignKeyConstraint(
            ["template_version_id"],
            ["rule_template_version.id"],
            name="fk_rule_version_template_version",
        ),
        UniqueConstraint("tenant_id", "rule_id", "version"),
        # SEC-501 in the database too: the approver is never the author.
        CheckConstraint(APPROVER_NOT_AUTHOR, name="approver_not_author"),
        CheckConstraint(APPROVED_NEEDS_SIMULATION, name="approved_needs_simulation"),
        Index(
            "ux_rule_version_active",
            "tenant_id",
            "rule_id",
            unique=True,
            postgresql_where=text(ACTIVE_PREDICATE),
        ),
    )

    rule_id: Mapped[UUID] = mapped_column(Uuid)
    version: Mapped[int] = mapped_column(Integer)
    template_version_id: Mapped[UUID | None] = mapped_column(Uuid)
    # The RuleDefinition with the tenant's params and segment (spec §9.1).
    definition: Mapped[dict[str, JsonValue]] = mapped_column(JSONB)
    # sha256 of the canonical JSON: the compiled hash every hit stores.
    definition_hash: Mapped[bytes] = mapped_column(LargeBinary)
    status: Mapped[RuleVersionStatus] = mapped_column(
        enum_column(RuleVersionStatus),
        default=RuleVersionStatus.DRAFT,
        server_default=RuleVersionStatus.DRAFT.value,
    )
    # Evaluated by the decide path too (spec §12).
    sync: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false")
    )
    author_id: Mapped[UUID] = mapped_column(Uuid)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    approved_by: Mapped[UUID | None] = mapped_column(Uuid)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    simulation_id: Mapped[UUID | None] = mapped_column(Uuid)
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    retired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    retired_by_version_id: Mapped[UUID | None] = mapped_column(Uuid)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )


class RuleSimulation(TenantTableMixin, Base):
    """A replay of history through one version, in memory (spec §13.1)."""

    __tablename__: str = "rule_simulation"
    __table_args__: tuple[SchemaItem, ...] = tenant_table_args(
        "rule_simulation",
        _named_fk(
            "rule_version_id",
            "monitoring_rule_version",
            "fk_rule_simulation_rule_version",
        ),
        tenant_fk("requested_by", "staff_user"),
    )

    rule_version_id: Mapped[UUID] = mapped_column(Uuid)
    requested_by: Mapped[UUID] = mapped_column(Uuid)
    range_start: Mapped[date] = mapped_column(Date)
    range_end: Mapped[date] = mapped_column(Date)
    status: Mapped[SimulationStatus] = mapped_column(enum_column(SimulationStatus))
    # The version's hash at request time; a changed definition invalidates it.
    definition_hash: Mapped[bytes] = mapped_column(LargeBinary)
    transactions_read: Mapped[int] = mapped_column(
        BigInteger, default=0, server_default=text("0")
    )
    hit_count: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    customers_hit: Mapped[int] = mapped_column(
        Integer, default=0, server_default=text("0")
    )
    hits_per_week: Mapped[list[JsonValue] | None] = mapped_column(JSONB)
    overlap: Mapped[dict[str, JsonValue] | None] = mapped_column(JSONB)
    # Up to 50 windows, ids and aggregates only.
    sample: Mapped[list[JsonValue] | None] = mapped_column(JSONB)
    cursor: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # The exception class and code, never data.
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )


class RuleAssignment(TenantTableMixin, Base):
    """Where and from when a version applies, as ``tenant_pack_assignment``."""

    __tablename__: str = "rule_assignment"
    __table_args__: tuple[SchemaItem, ...] = tenant_table_args(
        "rule_assignment",
        _named_fk(
            "rule_version_id",
            "monitoring_rule_version",
            "fk_rule_assignment_rule_version",
        ),
        tenant_fk("approved_by", "staff_user"),
    )

    rule_version_id: Mapped[UUID] = mapped_column(Uuid)
    # Null applies the version to every product.
    product: Mapped[str | None] = mapped_column(Text)
    # Risk ratings, tiers and monitoring profiles; null is everyone.
    segment_filter: Mapped[dict[str, JsonValue] | None] = mapped_column(JSONB)
    effective_from: Mapped[date] = mapped_column(Date)
    effective_to: Mapped[date | None] = mapped_column(Date)
    approved_by: Mapped[UUID] = mapped_column(Uuid)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )


class RuleHit(TenantTableMixin, Base):
    """One firing per version and dedup key (D13, NFR-TM-004); not an alert."""

    __tablename__: str = "rule_hit"
    __table_args__: tuple[SchemaItem, ...] = tenant_table_args(
        "rule_hit",
        tenant_fk("rule_version_id", "monitoring_rule_version"),
        tenant_fk("party_id", "party"),
        tenant_fk("relationship_id", "customer_relationship"),
        tenant_fk("policy_snapshot_id", "policy_snapshot"),
        tenant_fk("assignment_id", "rule_assignment"),
        ForeignKeyConstraint(
            ["tenant_id", "first_occurred_at", "first_transaction_id"],
            ["transaction.tenant_id", "transaction.occurred_at", "transaction.id"],
            name="fk_rule_hit_first_transaction",
        ),
        UniqueConstraint("tenant_id", "rule_version_id", "dedup_key"),
        Index("ix_rule_hit_queue", "tenant_id", "severity", text("created_at DESC")),
        Index("ix_rule_hit_party", "tenant_id", "party_id", text("created_at DESC")),
        Index("ix_rule_hit_rule_period", "tenant_id", "rule_version_id", "created_at"),
    )

    rule_version_id: Mapped[UUID] = mapped_column(Uuid)
    rule_code: Mapped[str] = mapped_column(Text)
    key_kind: Mapped[KeyKind] = mapped_column(enum_column(KeyKind))
    key: Mapped[str] = mapped_column(Text)
    # '<key_kind>:<key>:<window_bucket>' (spec §9.6).
    dedup_key: Mapped[str] = mapped_column(Text)
    party_id: Mapped[UUID] = mapped_column(Uuid)
    relationship_id: Mapped[UUID] = mapped_column(Uuid)
    severity: Mapped[str] = mapped_column(CODE)
    outcome: Mapped[str] = mapped_column(CODE, default="ALERT", server_default="ALERT")
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    window_end: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # Amounts, units, codes and ids only, never a name (PRV-501).
    evidence: Mapped[dict[str, JsonValue]] = mapped_column(JSONB)
    cooldown_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    fired_count: Mapped[int] = mapped_column(
        Integer, default=1, server_default=text("1")
    )
    policy_snapshot_id: Mapped[UUID] = mapped_column(Uuid)
    definition_hash: Mapped[bytes] = mapped_column(LargeBinary)
    assignment_id: Mapped[UUID] = mapped_column(Uuid)
    first_transaction_id: Mapped[UUID] = mapped_column(Uuid)
    first_occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    # A workflow run (D9), unused in SP-04.
    run_id: Mapped[UUID | None] = mapped_column(Uuid)


class RuleHitTransaction(Base):
    """A transaction behind a hit: the trigger, the window or a cooldown firing."""

    __tablename__: str = "rule_hit_transaction"
    __table_args__: tuple[SchemaItem, ...] = (
        PrimaryKeyConstraint("tenant_id", "hit_id", "transaction_id"),
        tenant_fk("hit_id", "rule_hit"),
        ForeignKeyConstraint(
            ["tenant_id", "occurred_at", "transaction_id"],
            ["transaction.tenant_id", "transaction.occurred_at", "transaction.id"],
            name="fk_rule_hit_transaction_txn",
        ),
        Index("ix_rule_hit_transaction_txn", "tenant_id", "transaction_id"),
    )

    tenant_id: Mapped[UUID] = mapped_column(Uuid)
    hit_id: Mapped[UUID] = mapped_column(Uuid)
    transaction_id: Mapped[UUID] = mapped_column(Uuid)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    role: Mapped[HitTransactionRole] = mapped_column(enum_column(HitTransactionRole))


class TierLimitNotice(Base):
    """One tier-limit webhook per relationship, limit and period (spec §12.2)."""

    __tablename__: str = "tier_limit_notice"
    __table_args__: tuple[SchemaItem, ...] = (
        PrimaryKeyConstraint(
            "tenant_id", "relationship_id", "limit_code", "period_key"
        ),
        _named_fk(
            "relationship_id",
            "customer_relationship",
            "fk_tier_limit_notice_relationship",
        ),
    )

    tenant_id: Mapped[UUID] = mapped_column(Uuid)
    relationship_id: Mapped[UUID] = mapped_column(Uuid)
    limit_code: Mapped[str] = mapped_column(Text)
    period_key: Mapped[str] = mapped_column(Text)
    first_sent_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    # Whichever path published monitoring.tier_limit_reached.v1 first.
    source: Mapped[NoticeSource] = mapped_column(enum_column(NoticeSource))
