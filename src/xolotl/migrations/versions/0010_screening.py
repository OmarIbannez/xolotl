"""screening: the party name index, subjects, list sets, runs, findings, hits, dispositions, whitelist and batches

Revision ID: 0010_screening
Revises: 0009_lists
Create Date: 2026-10-19

Ten tenant tables with the kernel's conventions: ``(tenant_id, id)`` primary key
(``screening_list_set_member`` is keyed naturally), composite foreign keys into the
tenant, forced row-level security. ``match_disposition`` is append-only for the
application role, and its constraint trigger ``match_disposition_four_eyes`` refuses an
answer by the user who made the disposition it answers (DR-305). Every other table
gets the default grant, which covers the hit redaction and the name-index purge of
plan 3c. ``screening_subject.input`` and ``match_disposition.rationale`` hold
ciphertext under the tenant's DATA key; ``party_name_index`` holds normalized tokens
only (program spec D7), indexed with ``pg_trgm`` from 0009.
"""  # ruff: ignore[line-too-long]

from collections.abc import Sequence
from datetime import datetime
from uuid import UUID

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql
from sqlalchemy.sql.schema import SchemaItem

from xolotl.platform.tenancy.ddl import APPEND_ONLY_PRIVILEGES, enable_rls

revision: str = "0010_screening"
down_revision: str | None = "0009_lists"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLES = (
    "party_name_index",
    "screening_subject",
    "screening_list_set",
    "screening_list_set_member",
    "screening_run",
    "screening_match",
    "screening_hit",
    "match_disposition",
    "screening_whitelist",
    "screening_batch",
)
ONE_ENTRY_CHECK = "(list_entry_id IS NULL) <> (private_list_entry_id IS NULL)"
FOUR_EYES_FUNCTION = """
CREATE FUNCTION match_disposition_four_eyes() RETURNS trigger AS $$
BEGIN
  IF NEW.prior_disposition_id IS NOT NULL AND EXISTS (
       SELECT 1 FROM match_disposition p
       WHERE p.tenant_id = NEW.tenant_id AND p.id = NEW.prior_disposition_id
         AND p.decided_by IS NOT DISTINCT FROM NEW.decided_by
         AND NEW.decided_by IS NOT NULL) THEN
    RAISE EXCEPTION 'four-eyes: the prior disposition was made by the same user'
      USING ERRCODE = 'check_violation', CONSTRAINT = 'match_disposition_four_eyes';
  END IF;
  RETURN NEW;
END $$ LANGUAGE plpgsql
"""
FOUR_EYES_TRIGGER = """
CREATE CONSTRAINT TRIGGER match_disposition_four_eyes AFTER INSERT ON match_disposition
  DEFERRABLE INITIALLY IMMEDIATE FOR EACH ROW
  EXECUTE FUNCTION match_disposition_four_eyes()
"""


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


def _at(name: str) -> sa.Column[datetime]:
    return sa.Column(name, sa.DateTime(timezone=True), nullable=True)


def _enum(name: str, *, nullable: bool = False) -> sa.Column[str]:
    return sa.Column(name, sa.String(length=32), nullable=nullable)


def _text(name: str, *, nullable: bool = False) -> sa.Column[str]:
    return sa.Column(name, sa.Text(), nullable=nullable)


def _uuid(name: str, *, nullable: bool = False) -> sa.Column[UUID]:
    return sa.Column(name, sa.Uuid(), nullable=nullable)


def _int(name: str, *, nullable: bool = False) -> sa.Column[int]:
    return sa.Column(name, sa.Integer(), nullable=nullable)


def _counter(name: str) -> sa.Column[int]:
    return sa.Column(name, sa.Integer(), nullable=False, server_default=sa.text("0"))


def _bytes(name: str, *, nullable: bool = False) -> sa.Column[bytes]:
    return sa.Column(name, sa.LargeBinary(), nullable=nullable)


def _fk(
    table: str, column: str, target: str, *, name: str | None = None
) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        ["tenant_id", column],
        [f"{target}.tenant_id", f"{target}.id"],
        name=name or f"fk_{table}_tenant_id_{column}_{target}",
    )


def _entry_columns(table: str) -> list[SchemaItem]:
    return [
        _uuid("list_entry_id", nullable=True),
        _uuid("private_list_entry_id", nullable=True),
        sa.ForeignKeyConstraint(
            ["list_entry_id"],
            ["list_entry.id"],
            name=f"fk_{table}_list_entry_id_list_entry",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "private_list_entry_id"],
            ["private_list_entry.tenant_id", "private_list_entry.id"],
            name=f"fk_{table}_private_entry",
        ),
        sa.CheckConstraint(ONE_ENTRY_CHECK, name=f"ck_{table}_one_entry"),
    ]


def _party_name_index() -> None:
    _ = op.create_table(
        "party_name_index",
        *_keys(),
        _uuid("party_id"),
        _enum("name_kind"),
        _text("norm_name"),
        sa.Column("tokens", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column("phonetic_keys", postgresql.ARRAY(sa.Text()), nullable=False),
        _text("matcher_version"),
        _now("updated_at"),
        sa.PrimaryKeyConstraint("tenant_id", "id", name="pk_party_name_index"),
        _fk("party_name_index", "party_id", "party"),
        sa.UniqueConstraint(
            "tenant_id",
            "party_id",
            "name_kind",
            name="uq_party_name_index_tenant_id_party_id_name_kind",
        ),
    )
    op.create_index(
        "ix_pni_phonetic",
        "party_name_index",
        ["phonetic_keys"],
        postgresql_using="gin",
    )
    op.create_index(
        "ix_pni_trgm",
        "party_name_index",
        ["norm_name"],
        postgresql_using="gin",
        postgresql_ops={"norm_name": "gin_trgm_ops"},
    )
    enable_rls("party_name_index")


def _subjects_and_list_sets() -> None:
    _ = op.create_table(
        "screening_subject",
        *_keys(),
        _enum("kind"),
        _uuid("party_id", nullable=True),
        _bytes("input", nullable=True),
        sa.Column(
            "status", sa.String(length=32), nullable=False, server_default="CLEAR"
        ),
        _at("last_screened_at"),
        _uuid("last_run_id", nullable=True),
        sa.Column("retention_until", sa.Date(), nullable=True),
        _now("created_at"),
        sa.PrimaryKeyConstraint("tenant_id", "id", name="pk_screening_subject"),
        _fk("screening_subject", "party_id", "party"),
        sa.UniqueConstraint(
            "tenant_id", "party_id", name="uq_screening_subject_tenant_id_party_id"
        ),
        sa.CheckConstraint(
            "(kind = 'PARTY' AND party_id IS NOT NULL AND input IS NULL) "
            "OR (kind = 'AD_HOC' AND party_id IS NULL AND input IS NOT NULL)",
            name="ck_screening_subject_kind_input",
        ),
    )
    enable_rls("screening_subject")

    _ = op.create_table(
        "screening_list_set",
        *_keys(),
        _bytes("content_hash"),
        _now("created_at"),
        sa.PrimaryKeyConstraint("tenant_id", "id", name="pk_screening_list_set"),
        sa.UniqueConstraint(
            "tenant_id",
            "content_hash",
            name="uq_screening_list_set_tenant_id_content_hash",
        ),
    )
    enable_rls("screening_list_set")

    _ = op.create_table(
        "screening_list_set_member",
        _uuid("tenant_id"),
        _uuid("list_set_id"),
        _text("list_code"),
        _uuid("list_version_id", nullable=True),
        _uuid("private_list_version_id", nullable=True),
        sa.PrimaryKeyConstraint(
            "tenant_id",
            "list_set_id",
            "list_code",
            name="pk_screening_list_set_member",
        ),
        _fk(
            "screening_list_set_member",
            "list_set_id",
            "screening_list_set",
            name="fk_screening_list_set_member_list_set",
        ),
        sa.ForeignKeyConstraint(
            ["list_version_id"],
            ["list_version.id"],
            name="fk_screening_list_set_member_list_version",
        ),
        _fk(
            "screening_list_set_member",
            "private_list_version_id",
            "private_list_version",
            name="fk_screening_list_set_member_private_version",
        ),
        sa.CheckConstraint(
            "(list_version_id IS NULL) <> (private_list_version_id IS NULL)",
            name="ck_screening_list_set_member_one_version",
        ),
    )
    enable_rls("screening_list_set_member")


def _runs() -> None:
    _ = op.create_table(
        "screening_run",
        *_keys(),
        _uuid("subject_id"),
        _enum("mode"),
        _enum("trigger_type"),
        _text("trigger_ref", nullable=True),
        _uuid("list_set_id"),
        _uuid("policy_snapshot_id"),
        _text("matcher_version"),
        _bytes("scoring_config_hash"),
        _bytes("query_fingerprint"),
        sa.Column(
            "restricted_to_delta",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        _int("candidate_count"),
        _int("hit_count"),
        _int("new_match_count"),
        _enum("outcome"),
        _int("latency_ms"),
        _text("created_by"),
        _now("created_at"),
        _uuid("batch_id", nullable=True),
        _uuid("run_id", nullable=True),
        sa.PrimaryKeyConstraint("tenant_id", "id", name="pk_screening_run"),
        _fk("screening_run", "subject_id", "screening_subject"),
        _fk("screening_run", "list_set_id", "screening_list_set"),
        _fk("screening_run", "policy_snapshot_id", "policy_snapshot"),
    )
    op.create_index(
        "ix_screening_run_subject",
        "screening_run",
        ["tenant_id", "subject_id", sa.text("created_at DESC")],
    )
    enable_rls("screening_run")


def _matches_and_hits() -> None:
    _ = op.create_table(
        "screening_match",
        *_keys(),
        _uuid("subject_id"),
        _text("list_code"),
        _enum("list_kind"),
        _text("source_entry_key"),
        _bytes("entry_content_hash"),
        _enum("band"),
        sa.Column("score", sa.Numeric(5, 4), nullable=False),
        _uuid("origin_run_id"),
        sa.Column(
            "status", sa.String(length=32), nullable=False, server_default="OPEN"
        ),
        _enum("suggested_disposition", nullable=True),
        _uuid("superseded_by_match_id", nullable=True),
        _now("created_at"),
        _now("updated_at"),
        _at("closed_at"),
        *_entry_columns("screening_match"),
        sa.PrimaryKeyConstraint("tenant_id", "id", name="pk_screening_match"),
        _fk("screening_match", "subject_id", "screening_subject"),
        _fk("screening_match", "origin_run_id", "screening_run"),
        _fk(
            "screening_match",
            "superseded_by_match_id",
            "screening_match",
            name="fk_screening_match_superseded_by",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "subject_id",
            "list_code",
            "source_entry_key",
            "entry_content_hash",
            name="uq_screening_match_finding",
        ),
    )
    op.create_index(
        "ix_screening_match_queue",
        "screening_match",
        ["tenant_id", "status", "band", "created_at"],
    )
    # Plan 3c's delisting lookup; spec §5.2 does not name it.
    op.create_index(
        "ix_screening_match_entry",
        "screening_match",
        ["tenant_id", "list_code", "source_entry_key"],
    )
    enable_rls("screening_match")

    _ = op.create_table(
        "screening_hit",
        *_keys(),
        _uuid("run_id"),
        _text("list_code"),
        _text("source_entry_key"),
        _bytes("entry_content_hash"),
        sa.Column("score", sa.Numeric(5, 4), nullable=False),
        _enum("band"),
        _enum("auto_reason", nullable=True),
        sa.Column("features", postgresql.JSONB(), nullable=False),
        _uuid("match_id", nullable=True),
        *_entry_columns("screening_hit"),
        sa.PrimaryKeyConstraint("tenant_id", "id", name="pk_screening_hit"),
        _fk("screening_hit", "run_id", "screening_run"),
        _fk("screening_hit", "match_id", "screening_match"),
    )
    # The hit history of one finding; spec §5.2 does not name it.
    op.create_index(
        "ix_screening_hit_match", "screening_hit", ["tenant_id", "match_id"]
    )
    enable_rls("screening_hit")


def _review() -> None:
    _ = op.create_table(
        "match_disposition",
        *_keys(),
        _uuid("match_id"),
        _enum("decision"),
        _bytes("rationale"),
        _enum("decided_by_type"),
        _uuid("decided_by", nullable=True),
        _uuid("prior_disposition_id", nullable=True),
        _uuid("policy_snapshot_id"),
        _now("decided_at"),
        sa.PrimaryKeyConstraint("tenant_id", "id", name="pk_match_disposition"),
        _fk("match_disposition", "match_id", "screening_match"),
        _fk("match_disposition", "decided_by", "staff_user"),
        _fk(
            "match_disposition",
            "prior_disposition_id",
            "match_disposition",
            name="fk_match_disposition_prior",
        ),
        _fk(
            "match_disposition",
            "policy_snapshot_id",
            "policy_snapshot",
            name="fk_match_disposition_policy_snapshot",
        ),
        sa.CheckConstraint(
            "(decided_by_type = 'STAFF') = (decided_by IS NOT NULL)",
            name="ck_match_disposition_decided_by",
        ),
        sa.CheckConstraint(
            "decision <> 'TRUE_MATCH' OR prior_disposition_id IS NOT NULL",
            name="ck_match_disposition_true_match_prior",
        ),
    )
    enable_rls("match_disposition", grant=APPEND_ONLY_PRIVILEGES)
    op.execute(FOUR_EYES_FUNCTION)
    op.execute(FOUR_EYES_TRIGGER)

    _ = op.create_table(
        "screening_whitelist",
        *_keys(),
        _uuid("subject_id"),
        _text("list_code"),
        _text("source_entry_key"),
        _bytes("entry_content_hash"),
        _uuid("disposition_id"),
        _now("created_at"),
        _at("revoked_at"),
        _uuid("revoked_by", nullable=True),
        sa.PrimaryKeyConstraint("tenant_id", "id", name="pk_screening_whitelist"),
        _fk("screening_whitelist", "subject_id", "screening_subject"),
        _fk(
            "screening_whitelist",
            "disposition_id",
            "match_disposition",
            name="fk_screening_whitelist_disposition",
        ),
        _fk("screening_whitelist", "revoked_by", "staff_user"),
        sa.UniqueConstraint(
            "tenant_id",
            "subject_id",
            "list_code",
            "source_entry_key",
            "entry_content_hash",
            name="uq_screening_whitelist_key",
        ),
    )
    enable_rls("screening_whitelist")


def _batches() -> None:
    _ = op.create_table(
        "screening_batch",
        *_keys(),
        _enum("kind"),
        _text("ref"),
        _enum("status"),
        _int("total", nullable=True),
        _counter("done"),
        _counter("runs"),
        _counter("new_matches"),
        _text("cursor", nullable=True),
        _now("started_at"),
        _at("finished_at"),
        _text("last_error", nullable=True),
        sa.PrimaryKeyConstraint("tenant_id", "id", name="pk_screening_batch"),
        sa.UniqueConstraint(
            "tenant_id", "kind", "ref", name="uq_screening_batch_tenant_id_kind_ref"
        ),
    )
    enable_rls("screening_batch")


def upgrade() -> None:
    _party_name_index()
    _subjects_and_list_sets()
    _runs()
    _matches_and_hits()
    _review()
    _batches()


def downgrade() -> None:
    # Dropping a table drops its indexes, policy, grants and triggers with it.
    for table in reversed(TABLES):
        op.drop_table(table)
    op.execute("DROP FUNCTION match_disposition_four_eyes()")
