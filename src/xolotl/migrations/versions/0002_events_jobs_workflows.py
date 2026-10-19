"""events, jobs, workflows: outbox, webhooks, jobs, schedules, runs, notifications

Revision ID: 0002_events_jobs_workflows
Revises: 0001_kernel
Create Date: 2026-10-19

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from xolotl.platform.tenancy.ddl import enable_rls

revision: str = "0002_events_jobs_workflows"
down_revision: str | None = "0001_kernel"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    _ = op.create_table(
        "outbox",
        # An identity column, not a bigserial: the application role may insert
        # without a grant on an identity column's sequence.
        sa.Column("seq", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("type", sa.Text(), nullable=False),
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column(
            "status", sa.String(length=32), nullable=False, server_default="PENDING"
        ),
        sa.Column(
            "attempts", sa.Integer(), nullable=False, server_default=sa.text("0")
        ),
        sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("tenant_id", "id", name="pk_outbox"),
        sa.UniqueConstraint("seq", name="uq_outbox_seq"),
    )
    op.create_index(
        "ix_outbox_pending",
        "outbox",
        ["status", "created_at"],
        postgresql_where=sa.text("status = 'PENDING'"),
    )
    enable_rls("outbox")

    _ = op.create_table(
        "event_consumption",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("consumer", sa.Text(), nullable=False),
        sa.Column("event_id", sa.Uuid(), nullable=False),
        sa.Column(
            "consumed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint(
            "tenant_id", "consumer", "event_id", name="pk_event_consumption"
        ),
    )
    enable_rls("event_consumption")

    _ = op.create_table(
        "webhook_endpoint",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        # bytea; plan 1b flips the model type to EncryptedStr, which needs no migration.
        sa.Column("secret", sa.LargeBinary(), nullable=False),
        sa.Column("event_types", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("tenant_id", "id", name="pk_webhook_endpoint"),
    )
    enable_rls("webhook_endpoint")

    _ = op.create_table(
        "webhook_delivery",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("endpoint_id", sa.Uuid(), nullable=False),
        sa.Column("event_id", sa.Uuid(), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("response_code", sa.Integer(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "endpoint_id"],
            ["webhook_endpoint.tenant_id", "webhook_endpoint.id"],
            name="fk_webhook_delivery_tenant_id_endpoint_id_webhook_endpoint",
        ),
        sa.PrimaryKeyConstraint("tenant_id", "id", name="pk_webhook_delivery"),
        sa.UniqueConstraint(
            "tenant_id",
            "endpoint_id",
            "event_id",
            name="uq_webhook_delivery_tenant_id_endpoint_id_event_id",
        ),
    )
    op.create_index(
        "ix_webhook_delivery_next", "webhook_delivery", ["status", "next_attempt_at"]
    )
    enable_rls("webhook_delivery")

    _ = op.create_table(
        "job",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("key", sa.Text(), nullable=True),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column(
            "status", sa.String(length=32), nullable=False, server_default="PENDING"
        ),
        sa.Column(
            "attempts", sa.Integer(), nullable=False, server_default=sa.text("0")
        ),
        sa.Column(
            "max_attempts", sa.Integer(), nullable=False, server_default=sa.text("8")
        ),
        sa.Column(
            "run_after",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("locked_by", sa.Text(), nullable=True),
        # Beyond the spec: lease fencing for the job runner (plan 1c, E3).
        sa.Column("lock_token", sa.Uuid(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("tenant_id", "id", name="pk_job"),
    )
    op.create_index(
        "job_dedup",
        "job",
        ["tenant_id", "kind", "key"],
        unique=True,
        postgresql_where=sa.text("key IS NOT NULL AND status IN ('PENDING','RUNNING')"),
    )
    op.create_index("ix_job_claim", "job", ["status", "run_after"])
    enable_rls("job")

    _ = op.create_table(
        "job_schedule",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("cron", sa.Text(), nullable=False),
        sa.Column("tz", sa.Text(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column(
            "enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")
        ),
        sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("tenant_id", "id", name="pk_job_schedule"),
        sa.UniqueConstraint("tenant_id", "name", name="uq_job_schedule_tenant_id_name"),
    )
    enable_rls("job_schedule")

    _ = op.create_table(
        "workflow_run",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("definition", sa.Text(), nullable=False),
        sa.Column("business_key", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("current_step", sa.Text(), nullable=True),
        sa.Column("state", postgresql.JSONB(), nullable=False),
        sa.Column("wake_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("tenant_id", "id", name="pk_workflow_run"),
        sa.UniqueConstraint(
            "tenant_id",
            "definition",
            "business_key",
            name="uq_workflow_run_tenant_id_definition_business_key",
        ),
    )
    op.create_index("ix_workflow_run_wake", "workflow_run", ["status", "wake_at"])
    enable_rls("workflow_run")

    _ = op.create_table(
        "workflow_step",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("step_id", sa.Text(), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("output", postgresql.JSONB(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["tenant_id", "run_id"],
            ["workflow_run.tenant_id", "workflow_run.id"],
            name="fk_workflow_step_tenant_id_run_id_workflow_run",
        ),
        sa.PrimaryKeyConstraint(
            "tenant_id", "run_id", "step_id", "attempt", name="pk_workflow_step"
        ),
    )
    enable_rls("workflow_step")

    _ = op.create_table(
        "workflow_signal",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column(
            "received_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["tenant_id", "run_id"],
            ["workflow_run.tenant_id", "workflow_run.id"],
            name="fk_workflow_signal_tenant_id_run_id_workflow_run",
        ),
        sa.PrimaryKeyConstraint("tenant_id", "id", name="pk_workflow_signal"),
    )
    op.create_index(
        "ix_workflow_signal_pending",
        "workflow_signal",
        ["tenant_id", "run_id", "name", "received_at", "id"],
        postgresql_where=sa.text("consumed_at IS NULL"),
    )
    enable_rls("workflow_signal")

    _ = op.create_table(
        "notification",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("recipient_user_id", sa.Uuid(), nullable=True),
        sa.Column("recipient_role", sa.Text(), nullable=True),
        sa.Column("template", sa.Text(), nullable=False),
        sa.Column("data", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("tenant_id", "id", name="pk_notification"),
    )
    enable_rls("notification")


def downgrade() -> None:
    # Dropping a table drops its indexes, policies and grants with it.
    op.drop_table("notification")
    op.drop_table("workflow_signal")
    op.drop_table("workflow_step")
    op.drop_table("workflow_run")
    op.drop_table("job_schedule")
    op.drop_table("job")
    op.drop_table("webhook_delivery")
    op.drop_table("webhook_endpoint")
    op.drop_table("event_consumption")
    op.drop_table("outbox")
