"""Tables behind the job queue and its cron schedules (spec §9.1)."""

from datetime import datetime
from uuid import UUID

from pydantic import JsonValue
from sqlalchemy import (
    Boolean,
    DateTime,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql.schema import SchemaItem

from xolotl.platform.db import Base
from xolotl.platform.enums import JobStatus
from xolotl.platform.sqltypes import enum_column
from xolotl.platform.tenancy.ddl import TenantTableMixin, tenant_table_args


class JobRow(TenantTableMixin, Base):
    """One unit of background work, claimed with a lease.

    ``lock_token`` is a fresh UUID written on every claim; heartbeat, completion and
    failure match on it, so a worker whose lease was reclaimed cannot overwrite the
    state of the run that replaced it.
    """

    __tablename__: str = "job"
    __table_args__: tuple[SchemaItem, ...] = tenant_table_args(
        "job",
        Index(
            "job_dedup",
            "tenant_id",
            "kind",
            "key",
            unique=True,
            postgresql_where=text(
                "key IS NOT NULL AND status IN ('PENDING','RUNNING')"
            ),
        ),
        Index("ix_job_claim", "status", "run_after"),
    )

    kind: Mapped[str] = mapped_column(Text)
    key: Mapped[str | None] = mapped_column(Text)
    payload: Mapped[dict[str, JsonValue]] = mapped_column(JSONB)
    status: Mapped[JobStatus] = mapped_column(
        enum_column(JobStatus),
        default=JobStatus.PENDING,
        server_default=JobStatus.PENDING.value,
    )
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    max_attempts: Mapped[int] = mapped_column(
        Integer, default=8, server_default=text("8")
    )
    run_after: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    locked_by: Mapped[str | None] = mapped_column(Text)
    lock_token: Mapped[UUID | None] = mapped_column(Uuid)
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class JobSchedule(TenantTableMixin, Base):
    """A cron expression, read in ``tz``, that enqueues ``kind`` at each slot."""

    __tablename__: str = "job_schedule"
    __table_args__: tuple[SchemaItem, ...] = tenant_table_args(
        "job_schedule", UniqueConstraint("tenant_id", "name")
    )

    name: Mapped[str] = mapped_column(Text)
    cron: Mapped[str] = mapped_column(Text)
    tz: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(Text)
    payload: Mapped[dict[str, JsonValue]] = mapped_column(JSONB)
    enabled: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default=text("true")
    )
    next_run_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
