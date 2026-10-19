"""Tables behind the step-table workflow runner (spec §9.1)."""

from datetime import datetime
from uuid import UUID

from pydantic import JsonValue
from sqlalchemy import DateTime, Index, Integer, Text, UniqueConstraint, Uuid, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql.schema import SchemaItem

from xolotl.platform.db import Base
from xolotl.platform.enums import RunStatus, StepStatus
from xolotl.platform.sqltypes import enum_column
from xolotl.platform.tenancy.ddl import TenantTableMixin, tenant_fk, tenant_table_args


class WorkflowRunRow(TenantTableMixin, Base):
    """One execution of a workflow definition, unique per ``business_key``."""

    __tablename__: str = "workflow_run"
    __table_args__: tuple[SchemaItem, ...] = tenant_table_args(
        "workflow_run",
        UniqueConstraint("tenant_id", "definition", "business_key"),
        Index("ix_workflow_run_wake", "status", "wake_at"),
    )

    definition: Mapped[str] = mapped_column(Text)
    business_key: Mapped[str] = mapped_column(Text)
    status: Mapped[RunStatus] = mapped_column(enum_column(RunStatus))
    current_step: Mapped[str | None] = mapped_column(Text)
    state: Mapped[dict[str, JsonValue]] = mapped_column(JSONB)
    wake_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)


class WorkflowStepRow(Base):
    """One attempt at one step of a run.

    The key ``(tenant_id, run_id, step_id, attempt)`` is natural, so the table has no
    ``id`` and is not the target of any ``(tenant_id, id)`` foreign key.
    """

    __tablename__: str = "workflow_step"
    __table_args__: tuple[SchemaItem, ...] = tenant_table_args(
        "workflow_step", tenant_fk("run_id", "workflow_run")
    )

    tenant_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    run_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    step_id: Mapped[str] = mapped_column(Text, primary_key=True)
    attempt: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    status: Mapped[StepStatus] = mapped_column(enum_column(StepStatus))
    output: Mapped[dict[str, JsonValue] | None] = mapped_column(
        JSONB(none_as_null=True)
    )
    error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class WorkflowSignalRow(TenantTableMixin, Base):
    """A signal sent to a run; ``consumed_at`` is set when a step takes it."""

    __tablename__: str = "workflow_signal"
    __table_args__: tuple[SchemaItem, ...] = tenant_table_args(
        "workflow_signal",
        tenant_fk("run_id", "workflow_run"),
        # A run's unconsumed signals of one name, oldest first: what every wake and
        # every signal looks up. Consumed signals stay out of it.
        Index(
            "ix_workflow_signal_pending",
            "tenant_id",
            "run_id",
            "name",
            "received_at",
            "id",
            postgresql_where=text("consumed_at IS NULL"),
        ),
    )

    run_id: Mapped[UUID] = mapped_column(Uuid)
    name: Mapped[str] = mapped_column(Text)
    payload: Mapped[dict[str, JsonValue]] = mapped_column(JSONB)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
