"""The stage of the hits, hook and tier-limit tests (plan 4b B7).

Tenant A has both packs (ITF for ``EMONEY``, AVI for ``CRYPTO``), the library
loaded and ``CASH_STRUCTURING``, ``COUNTERPARTY_TRUE_MATCH`` and
``TIER_N1_MONTHLY_OPS`` activated from 30 June 2026 under a clock frozen on that
day (reconciliation finding 23), with their backfills run. The unit values of July
to December 2026 come from ``fixtures/unit_values_2026.yaml``. Deposits are
ingested as if received an hour after they occurred and processed by the worker's
jobs on the real clock.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from uuid import UUID

import pytest
from sqlalchemy import Engine, func, select, text
from sqlalchemy.orm import sessionmaker

from tests.helpers import platform_ctx, staff_session, system_ctx
from tests.modules.monitoring.rules.lifecycle_support import (
    activated,
    add_staff,
    freeze,
    load_library,
)
from tests.modules.monitoring.support import (
    CUSTOMER,
    both_packs_tenant,
    ingest_items,
    make_item,
    run_processing,
)
from tests.platform.units_support import load_unit_values
from xolotl.app import wire_platform
from xolotl.modules.monitoring.aggregates.windows import MEXICO_CITY_ZONE
from xolotl.modules.monitoring.kinds import AGG_BACKFILL_KIND
from xolotl.modules.monitoring.rules.models import (
    MonitoringRuleVersion,
    RuleHit,
    RuleHitTransaction,
)
from xolotl.modules.monitoring.txn.models import Transaction
from xolotl.modules.party import service
from xolotl.modules.party.enums import CustomerRelationshipStatus
from xolotl.modules.party.models import CustomerRelationship
from xolotl.modules.tenants.service import CreatedTenant
from xolotl.platform import clock
from xolotl.platform.config import get_settings
from xolotl.platform.db import get_sessionmaker, tenant_session
from xolotl.platform.enums import Role
from xolotl.platform.events.event import Event, event_from_row
from xolotl.platform.events.models import OutboxRow
from xolotl.platform.jobs.runner import JobRunner
from xolotl.platform.services import PlatformServices

UNIT_VALUES = Path(__file__).resolve().parents[1] / "fixtures/unit_values_2026.yaml"
CASH = "CASH_STRUCTURING"
TRUE_MATCH = "COUNTERPARTY_TRUE_MATCH"
TIER_N1 = "TIER_N1_MONTHLY_OPS"
ACTIVE_CODES = (CASH, TRUE_MATCH, TIER_N1)
ACTIVATION = datetime(2026, 6, 30, 15, tzinfo=UTC)
EFFECTIVE_FROM = date(2026, 6, 30)
RECEIPT_DELAY = timedelta(hours=1)


def local(day: int, hour: int = 10, minute: int = 0) -> datetime:
    """Return an instant of December 2026 in Mexico City, in UTC.

    Returns:
        The aware UTC instant.

    """
    return datetime(2026, 12, day, hour, minute, tzinfo=MEXICO_CITY_ZONE).astimezone(
        UTC
    )


@dataclass(frozen=True, slots=True)
class Stage:
    """The tenant, its wired platform and the active versions by rule code."""

    tenant: CreatedTenant
    services: PlatformServices
    approver: UUID
    versions: dict[str, UUID]

    @property
    def tenant_id(self) -> UUID:
        return self.tenant.tenant_id


def load_units_2026(admin_engine: Engine) -> None:
    owner = sessionmaker(bind=admin_engine, expire_on_commit=False)
    with tenant_session(platform_ctx(), factory=owner) as s:
        _ = load_unit_values(s, UNIT_VALUES)


def run_backfills(services: PlatformServices) -> int:
    """Run the pending aggregate backfills until none is left.

    Returns:
        How many jobs ran.

    """
    runner = JobRunner(
        services.queue,
        get_sessionmaker(),
        worker_id="w-backfill",
        kinds=[AGG_BACKFILL_KIND],
    )
    ran = 0
    while step := runner.run_once():
        ran += step
    return ran


def build_stage(
    tenant: CreatedTenant, admin_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> Stage:
    """Prepare tenant A with the three rules active from 30 June 2026.

    Returns:
        The stage.

    """
    _ = both_packs_tenant(tenant, admin_engine)
    load_units_2026(admin_engine)
    load_library(admin_engine)
    approver = add_staff(tenant, Role.MLRO)
    services = wire_platform(get_sessionmaker(), get_settings())
    versions: dict[str, UUID] = {}
    with monkeypatch.context() as patched:
        freeze(patched, ACTIVATION)
        for code in ACTIVE_CODES:
            version = activated(
                tenant, approver, services.queue, code, effective_from=EFFECTIVE_FROM
            )
            versions[code] = version.id
    _ = run_backfills(services)
    return Stage(tenant, services, approver, versions)


def add_relationship(tenant: CreatedTenant, external_ref: str, tier: str) -> UUID:
    """Create a person with an ``EMONEY`` relationship of ``tier``.

    Returns:
        The relationship's id.

    """
    with staff_session(tenant.tenant_id, tenant.admin_user_id) as s:
        party = service.create_party(s, CUSTOMER)
        row = CustomerRelationship(
            tenant_id=tenant.tenant_id,
            party_id=party.id,
            external_ref=external_ref,
            product="EMONEY",
            account_tier=tier,
            status=CustomerRelationshipStatus.ACTIVE,
        )
        s.add(row)
        s.flush()
        return row.id


def deposit(
    external_id: str,
    at: datetime,
    amount: str,
    *,
    customer_ref: str = "cust-1",
    currency: str = "USD",
) -> dict[str, object]:
    return make_item(
        external_id=external_id,
        occurred_at=at.isoformat(),
        customer_ref=customer_ref,
        amount=amount,
        currency=currency,
    )


def ingest(
    stage: Stage,
    items: Sequence[dict[str, object]],
    *,
    received: datetime | None = None,
) -> list[UUID]:
    """Ingest ``items`` as if received at ``received`` (an hour after the last).

    Returns:
        The ids of the accepted rows.

    """
    if received is None:
        received = (
            max(datetime.fromisoformat(str(item["occurred_at"])) for item in items)
            + RECEIPT_DELAY
        )
    with pytest.MonkeyPatch.context() as patched:
        patched.setattr(clock, "now", lambda: received)
        with staff_session(stage.tenant_id, stage.tenant.admin_user_id) as s:
            report = ingest_items(s, items, queue=stage.services.queue)
    return list(report.transaction_ids)


def feed(
    stage: Stage,
    items: Sequence[dict[str, object]],
    *,
    received: datetime | None = None,
) -> list[UUID]:
    """Ingest ``items`` one at a time, each processed before the next arrives.

    Returns:
        The ids of the rows, in the order of ``items``.

    """
    ids: list[UUID] = []
    for item in items:
        ids += ingest(stage, [item], received=received)
        _ = run_processing(stage.services, stage.tenant_id)
    return ids


def hits(stage: Stage, code: str) -> list[RuleHit]:
    with tenant_session(system_ctx(stage.tenant_id)) as s:
        return list(
            s.scalars(
                select(RuleHit)
                .where(RuleHit.rule_code == code)
                .order_by(RuleHit.created_at)
            )
        )


def links(stage: Stage, hit_id: UUID) -> dict[UUID, str]:
    with tenant_session(system_ctx(stage.tenant_id)) as s:
        rows = s.scalars(
            select(RuleHitTransaction).where(RuleHitTransaction.hit_id == hit_id)
        )
        return {row.transaction_id: row.role.value for row in rows}


def count_rows(stage: Stage, model: type[RuleHit | RuleHitTransaction]) -> int:
    with tenant_session(system_ctx(stage.tenant_id)) as s:
        return s.scalar(select(func.count()).select_from(model)) or 0


def statuses(stage: Stage) -> dict[UUID, str]:
    with tenant_session(system_ctx(stage.tenant_id)) as s:
        return {
            row.id: row.status.value for row in s.scalars(select(Transaction)).all()
        }


def events_of(tenant_id: UUID, event_type: str) -> list[Event]:
    with tenant_session(system_ctx(tenant_id)) as s:
        rows = s.scalars(
            select(OutboxRow)
            .where(OutboxRow.type == event_type)
            .order_by(OutboxRow.seq)
        )
        return [event_from_row(row) for row in rows]


def age_outbox(tenant_id: UUID) -> None:
    """Make every pending event due for the dispatcher (past its grace)."""
    with tenant_session(system_ctx(tenant_id)) as s:
        _ = s.execute(
            text("UPDATE outbox SET created_at = created_at - interval '1 minute'")
        )


def version(stage: Stage, code: str) -> MonitoringRuleVersion:
    with tenant_session(system_ctx(stage.tenant_id)) as s:
        return s.get_one(MonitoringRuleVersion, (stage.tenant_id, stage.versions[code]))
