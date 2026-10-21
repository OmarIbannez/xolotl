"""monitoring: rule templates, versions, hits, captures and the counterparty index

Revision ID: 0013_rules_captures
Revises: 0012_transactions
Create Date: 2026-10-20

SP-04 spec §5.1 and the 4b block of §5.2 (the spec's 0012). ``rule_template`` and
``rule_template_version`` are shared pack content: the application role may only
read them. Eleven tenant tables get forced row-level security and the default
grant: the rules, their versions and simulations, assignments, hits and the
transactions behind them, capture groups, reportable events, tier-limit notices,
capture periods and ``counterparty_name_index``. ``monitoring_rule_version`` and
``rule_simulation`` reference each other, so ``fk_rule_version_simulation`` is added
once both exist. A counterparty becomes a screening subject: ``screening_subject``
gains ``counterparty_id`` and the ``COUNTERPARTY`` branch of its kind check, and
``counterparty.screening_subject_id`` its foreign key. Downgrade reverses each step.
"""

from collections.abc import Sequence
from datetime import date, datetime
from uuid import UUID

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from xolotl.platform.tenancy.ddl import enable_rls, grant_shared

revision: str = "0013_rules_captures"
down_revision: str | None = "0012_transactions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SHARED = ("rule_template", "rule_template_version")
TENANT_TABLES = (
    "monitoring_rule",
    "monitoring_rule_version",
    "rule_simulation",
    "rule_assignment",
    "rule_hit",
    "rule_hit_transaction",
    "capture_group",
    "reportable_event",
    "tier_limit_notice",
    "capture_period",
)
# op.f() keeps a name as written: the metadata's naming convention would otherwise
# prefix a check's name again, which is how 0010 named its own check.
SUBJECT_CHECK = "ck_screening_subject_kind_input"
SUBJECT_CHECK_0010 = "ck_screening_subject_ck_screening_subject_kind_input"
SUBJECT_KIND_0010 = (
    "(kind = 'PARTY' AND party_id IS NOT NULL AND input IS NULL) "
    "OR (kind = 'AD_HOC' AND party_id IS NULL AND input IS NOT NULL)"
)
SUBJECT_KIND_0013 = (
    "(kind = 'PARTY' AND party_id IS NOT NULL AND counterparty_id IS NULL "
    "AND input IS NULL) "
    "OR (kind = 'AD_HOC' AND party_id IS NULL AND counterparty_id IS NULL "
    "AND input IS NOT NULL) "
    "OR (kind = 'COUNTERPARTY' AND party_id IS NULL AND counterparty_id IS NOT NULL "
    "AND input IS NULL)"
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


def _texts(name: str) -> sa.Column[list[str]]:
    return sa.Column(name, postgresql.ARRAY(sa.Text()), nullable=False)


def _uuid(name: str, *, nullable: bool = False) -> sa.Column[UUID]:
    return sa.Column(name, sa.Uuid(), nullable=nullable)


def _bytes(name: str) -> sa.Column[bytes]:
    return sa.Column(name, sa.LargeBinary(), nullable=False)


def _counter(
    name: str, kind: type[sa.Integer] = sa.Integer, default: int = 0
) -> sa.Column[int]:
    return sa.Column(name, kind(), nullable=False, server_default=sa.text(str(default)))


def _jsonb(name: str, *, nullable: bool = True) -> sa.Column[object]:
    return sa.Column(name, postgresql.JSONB(), nullable=nullable)


def _date(name: str, *, nullable: bool = False) -> sa.Column[date]:
    return sa.Column(name, sa.Date(), nullable=nullable)


def _fk(
    table: str, column: str, target: str, *, name: str | None = None
) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        ["tenant_id", column],
        [f"{target}.tenant_id", f"{target}.id"],
        name=name or f"fk_{table}_tenant_id_{column}_{target}",
    )


def _transaction_fk(
    name: str, columns: tuple[str, str] = ("occurred_at", "transaction_id")
) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        ["tenant_id", *columns],
        ["transaction.tenant_id", "transaction.occurred_at", "transaction.id"],
        name=name,
    )


def _pk(table: str) -> sa.PrimaryKeyConstraint:
    return sa.PrimaryKeyConstraint("tenant_id", "id", name=f"pk_{table}")


def _templates() -> None:
    _ = op.create_table(
        "rule_template",
        _text("code"),
        _enum("family"),
        _enum("kind"),
        _text("title"),
        _texts("typology"),
        sa.Column("country_code", sa.String(length=2), nullable=False),
        _texts("packs"),
        sa.PrimaryKeyConstraint("code", name="pk_rule_template"),
    )
    grant_shared("rule_template")

    _ = op.create_table(
        "rule_template_version",
        _uuid("id"),
        _text("template_code"),
        sa.Column("version", sa.Integer(), nullable=False),
        _jsonb("definition", nullable=False),
        _bytes("definition_hash"),
        _now("loaded_at"),
        _text("loaded_by"),
        _text("source_path"),
        sa.PrimaryKeyConstraint("id", name="pk_rule_template_version"),
        sa.ForeignKeyConstraint(
            ["template_code"],
            ["rule_template.code"],
            name="fk_rule_template_version_template_code_rule_template",
        ),
        sa.UniqueConstraint(
            "template_code",
            "version",
            name="uq_rule_template_version_template_code_version",
        ),
    )
    grant_shared("rule_template_version")


def _rules() -> None:
    _ = op.create_table(
        "monitoring_rule",
        *_keys(),
        _text("code"),
        _enum("family"),
        _enum("kind"),
        _text("template_code", nullable=True),
        _text("title"),
        _uuid("created_by"),
        _now("created_at"),
        _pk("monitoring_rule"),
        sa.UniqueConstraint(
            "tenant_id", "code", name="uq_monitoring_rule_tenant_id_code"
        ),
        _fk("monitoring_rule", "created_by", "staff_user"),
        sa.ForeignKeyConstraint(
            ["template_code"],
            ["rule_template.code"],
            name="fk_monitoring_rule_template_code_rule_template",
        ),
    )

    table = "monitoring_rule_version"
    _ = op.create_table(
        table,
        *_keys(),
        _uuid("rule_id"),
        sa.Column("version", sa.Integer(), nullable=False),
        _uuid("template_version_id", nullable=True),
        _jsonb("definition", nullable=False),
        _bytes("definition_hash"),
        _enum("status", default="DRAFT"),
        sa.Column(
            "sync", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
        _uuid("author_id"),
        _at("submitted_at"),
        _uuid("approved_by", nullable=True),
        _at("approved_at"),
        _uuid("simulation_id", nullable=True),
        _at("activated_at"),
        _at("retired_at"),
        _uuid("retired_by_version_id", nullable=True),
        _now("created_at"),
        _pk(table),
        sa.UniqueConstraint(
            "tenant_id",
            "rule_id",
            "version",
            name="uq_monitoring_rule_version_tenant_id_rule_id_version",
        ),
        _fk(table, "rule_id", "monitoring_rule"),
        _fk(table, "author_id", "staff_user"),
        _fk(table, "approved_by", "staff_user"),
        _fk(
            table,
            "retired_by_version_id",
            table,
            name="fk_rule_version_retired_by",
        ),
        sa.ForeignKeyConstraint(
            ["template_version_id"],
            ["rule_template_version.id"],
            name="fk_rule_version_template_version",
        ),
        sa.CheckConstraint(
            "approved_by IS NULL OR approved_by <> author_id",
            name=op.f("ck_monitoring_rule_version_approver_not_author"),
        ),
        sa.CheckConstraint(
            "status NOT IN ('APPROVED', 'ACTIVE') "
            "OR (approved_by IS NOT NULL AND simulation_id IS NOT NULL)",
            name=op.f("ck_monitoring_rule_version_approved_needs_simulation"),
        ),
    )
    op.create_index(
        "ux_rule_version_active",
        table,
        ["tenant_id", "rule_id"],
        unique=True,
        postgresql_where=sa.text("status = 'ACTIVE'"),
    )

    _ = op.create_table(
        "rule_simulation",
        *_keys(),
        _uuid("rule_version_id"),
        _uuid("requested_by"),
        _date("range_start"),
        _date("range_end"),
        _enum("status"),
        _bytes("definition_hash"),
        _counter("transactions_read", sa.BigInteger),
        _counter("hit_count"),
        _counter("customers_hit"),
        _jsonb("hits_per_week"),
        _jsonb("overlap"),
        _jsonb("sample"),
        _text("cursor", nullable=True),
        _at("started_at"),
        _at("finished_at"),
        _text("last_error", nullable=True),
        _now("created_at"),
        _pk("rule_simulation"),
        _fk(
            "rule_simulation",
            "rule_version_id",
            table,
            name="fk_rule_simulation_rule_version",
        ),
        _fk("rule_simulation", "requested_by", "staff_user"),
    )
    op.create_foreign_key(
        "fk_rule_version_simulation",
        table,
        "rule_simulation",
        ["tenant_id", "simulation_id"],
        ["tenant_id", "id"],
    )

    _ = op.create_table(
        "rule_assignment",
        *_keys(),
        _uuid("rule_version_id"),
        _text("product", nullable=True),
        _jsonb("segment_filter"),
        _date("effective_from"),
        _date("effective_to", nullable=True),
        _uuid("approved_by"),
        _now("created_at"),
        _pk("rule_assignment"),
        _fk(
            "rule_assignment",
            "rule_version_id",
            table,
            name="fk_rule_assignment_rule_version",
        ),
        _fk("rule_assignment", "approved_by", "staff_user"),
    )


def _hits() -> None:
    _ = op.create_table(
        "rule_hit",
        *_keys(),
        _uuid("rule_version_id"),
        _text("rule_code"),
        _enum("key_kind"),
        _text("key"),
        _text("dedup_key"),
        _uuid("party_id"),
        _uuid("relationship_id"),
        _enum("severity"),
        _enum("outcome", default="ALERT"),
        _at("window_start", nullable=False),
        _at("window_end", nullable=False),
        _jsonb("evidence", nullable=False),
        _at("cooldown_until"),
        _counter("fired_count", default=1),
        _uuid("policy_snapshot_id"),
        _bytes("definition_hash"),
        _uuid("assignment_id"),
        _uuid("first_transaction_id"),
        _at("first_occurred_at", nullable=False),
        _now("created_at"),
        _uuid("run_id", nullable=True),
        _pk("rule_hit"),
        sa.UniqueConstraint(
            "tenant_id",
            "rule_version_id",
            "dedup_key",
            name="uq_rule_hit_tenant_id_rule_version_id_dedup_key",
        ),
        _fk("rule_hit", "rule_version_id", "monitoring_rule_version"),
        _fk("rule_hit", "party_id", "party"),
        _fk("rule_hit", "relationship_id", "customer_relationship"),
        _fk("rule_hit", "policy_snapshot_id", "policy_snapshot"),
        _fk("rule_hit", "assignment_id", "rule_assignment"),
        _transaction_fk(
            "fk_rule_hit_first_transaction",
            ("first_occurred_at", "first_transaction_id"),
        ),
    )
    op.create_index(
        "ix_rule_hit_queue",
        "rule_hit",
        ["tenant_id", "severity", sa.text("created_at DESC")],
    )
    op.create_index(
        "ix_rule_hit_party",
        "rule_hit",
        ["tenant_id", "party_id", sa.text("created_at DESC")],
    )
    op.create_index(
        "ix_rule_hit_rule_period",
        "rule_hit",
        ["tenant_id", "rule_version_id", "created_at"],
    )

    _ = op.create_table(
        "rule_hit_transaction",
        _uuid("tenant_id"),
        _uuid("hit_id"),
        _uuid("transaction_id"),
        _at("occurred_at", nullable=False),
        _enum("role"),
        sa.PrimaryKeyConstraint(
            "tenant_id", "hit_id", "transaction_id", name="pk_rule_hit_transaction"
        ),
        _fk("rule_hit_transaction", "hit_id", "rule_hit"),
        _transaction_fk("fk_rule_hit_transaction_txn"),
    )
    op.create_index(
        "ix_rule_hit_transaction_txn",
        "rule_hit_transaction",
        ["tenant_id", "transaction_id"],
    )


def _captures() -> None:
    _ = op.create_table(
        "capture_group",
        *_keys(),
        _text("capture_code"),
        _enum("key_kind"),
        _text("key"),
        _text("period_key"),
        _at("window_start", nullable=False),
        _at("window_end", nullable=False),
        _at("threshold_crossed_at"),
        _counter("accumulated_minor", sa.BigInteger),
        sa.Column(
            "accumulated_unit",
            sa.Numeric(28, 10),
            nullable=False,
            server_default=sa.text("0"),
        ),
        _enum("status", default="OPEN"),
        _uuid("superseded_by_id", nullable=True),
        _now("created_at"),
        _pk("capture_group"),
        _fk(
            "capture_group",
            "superseded_by_id",
            "capture_group",
            name="fk_capture_group_superseded_by",
        ),
    )
    op.create_index(
        "ux_capture_group_open",
        "capture_group",
        ["tenant_id", "capture_code", "key_kind", "key", "window_start"],
        unique=True,
        postgresql_where=sa.text("status <> 'SUPERSEDED'"),
    )

    table = "reportable_event"
    _ = op.create_table(
        table,
        *_keys(),
        _text("capture_code"),
        _text("report_code"),
        _enum("capture_kind"),
        _uuid("transaction_id"),
        _at("occurred_at", nullable=False),
        _uuid("party_id"),
        _uuid("relationship_id"),
        _text("product", nullable=True),
        _text("period_key"),
        _uuid("capture_group_id", nullable=True),
        _jsonb("fields", nullable=False),
        _jsonb("unit_values", nullable=False),
        _enum("status", default="CAPTURED"),
        _uuid("supersedes_id", nullable=True),
        _uuid("superseded_by_id", nullable=True),
        _text("void_reason", nullable=True),
        _uuid("policy_snapshot_id"),
        _now("captured_at"),
        sa.Column(
            "late", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
        _pk(table),
        sa.UniqueConstraint(
            "tenant_id",
            "capture_code",
            "transaction_id",
            name="uq_reportable_event_tenant_id_capture_code_transaction_id",
        ),
        _transaction_fk("fk_reportable_event_transaction"),
        _fk(table, "party_id", "party"),
        _fk(
            table,
            "relationship_id",
            "customer_relationship",
            name="fk_reportable_event_relationship",
        ),
        _fk(table, "capture_group_id", "capture_group"),
        _fk(table, "supersedes_id", table, name="fk_reportable_event_supersedes"),
        _fk(table, "superseded_by_id", table, name="fk_reportable_event_superseded_by"),
        _fk(
            table,
            "policy_snapshot_id",
            "policy_snapshot",
            name="fk_reportable_event_policy_snapshot",
        ),
    )
    op.create_index(
        "ix_reportable_event_period",
        table,
        ["tenant_id", "report_code", "period_key", "status"],
    )
    op.create_index(
        "ix_reportable_event_transaction", table, ["tenant_id", "transaction_id"]
    )

    _ = op.create_table(
        "tier_limit_notice",
        _uuid("tenant_id"),
        _uuid("relationship_id"),
        _text("limit_code"),
        _text("period_key"),
        _now("first_sent_at"),
        _enum("source"),
        sa.PrimaryKeyConstraint(
            "tenant_id",
            "relationship_id",
            "limit_code",
            "period_key",
            name="pk_tier_limit_notice",
        ),
        _fk(
            "tier_limit_notice",
            "relationship_id",
            "customer_relationship",
            name="fk_tier_limit_notice_relationship",
        ),
    )

    _ = op.create_table(
        "capture_period",
        *_keys(),
        _text("capture_code"),
        _text("report_code"),
        _text("period_key"),
        _date("period_start"),
        _date("period_end"),
        _enum("status", default="OPEN"),
        _counter("event_count"),
        sa.Column("reconciled_count", sa.Integer(), nullable=True),
        _at("reconciled_at"),
        _jsonb("discrepancies"),
        _at("closed_at"),
        _at("reopened_at"),
        _counter("change_count"),
        _pk("capture_period"),
        sa.UniqueConstraint(
            "tenant_id",
            "capture_code",
            "period_key",
            name="uq_capture_period_tenant_id_capture_code_period_key",
        ),
    )


def _counterparty_subjects() -> None:
    op.add_column(
        "screening_subject", sa.Column("counterparty_id", sa.Uuid(), nullable=True)
    )
    op.create_foreign_key(
        "fk_screening_subject_counterparty",
        "screening_subject",
        "counterparty",
        ["tenant_id", "counterparty_id"],
        ["tenant_id", "id"],
    )
    op.create_unique_constraint(
        "uq_screening_subject_counterparty",
        "screening_subject",
        ["tenant_id", "counterparty_id"],
    )
    op.drop_constraint(op.f(SUBJECT_CHECK_0010), "screening_subject", type_="check")
    op.create_check_constraint(
        op.f(SUBJECT_CHECK), "screening_subject", SUBJECT_KIND_0013
    )
    op.create_foreign_key(
        "fk_counterparty_subject",
        "counterparty",
        "screening_subject",
        ["tenant_id", "screening_subject_id"],
        ["tenant_id", "id"],
    )

    _ = op.create_table(
        "counterparty_name_index",
        *_keys(),
        _uuid("counterparty_id"),
        _text("norm_name"),
        _texts("tokens"),
        _texts("phonetic_keys"),
        _text("matcher_version"),
        _now("updated_at"),
        _pk("counterparty_name_index"),
        sa.UniqueConstraint(
            "tenant_id",
            "counterparty_id",
            name="uq_counterparty_name_index_tenant_id_counterparty_id",
        ),
        _fk(
            "counterparty_name_index",
            "counterparty_id",
            "counterparty",
            name="fk_counterparty_name_index_counterparty",
        ),
    )
    op.create_index(
        "ix_cni_phonetic",
        "counterparty_name_index",
        ["phonetic_keys"],
        postgresql_using="gin",
    )
    op.create_index(
        "ix_cni_trgm",
        "counterparty_name_index",
        ["norm_name"],
        postgresql_using="gin",
        postgresql_ops={"norm_name": "gin_trgm_ops"},
    )
    enable_rls("counterparty_name_index")


def upgrade() -> None:
    _templates()
    _rules()
    _hits()
    _captures()
    for table in TENANT_TABLES:
        enable_rls(table)
    _counterparty_subjects()


def downgrade() -> None:
    # Dropping a table drops its indexes, policy and grants with it.
    op.drop_table("counterparty_name_index")
    op.drop_constraint("fk_counterparty_subject", "counterparty", type_="foreignkey")
    op.drop_constraint(op.f(SUBJECT_CHECK), "screening_subject", type_="check")
    op.create_check_constraint(
        op.f(SUBJECT_CHECK_0010), "screening_subject", SUBJECT_KIND_0010
    )
    op.drop_constraint(
        "uq_screening_subject_counterparty", "screening_subject", type_="unique"
    )
    op.drop_constraint(
        "fk_screening_subject_counterparty", "screening_subject", type_="foreignkey"
    )
    op.drop_column("screening_subject", "counterparty_id")
    op.drop_constraint(
        "fk_rule_version_simulation", "monitoring_rule_version", type_="foreignkey"
    )
    for table in reversed(TENANT_TABLES):
        op.drop_table(table)
    for table in reversed(SHARED):
        op.drop_table(table)
