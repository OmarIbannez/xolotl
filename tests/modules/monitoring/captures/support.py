"""The stage of the capture tests (plan 4b B10).

The tenant holds ITF for product ``EMONEY`` and AVI for ``CRYPTO`` from 1 January
2026, with Mexico's holidays and the unit values a test names loaded. The shipped
AVI pack is in force only from 30 November 2026, so the stage installs it with
``effective_from`` moved to 1 June 2026: the accumulation scenarios of the plan
(one crypto operation a month from July) need it, and nothing else of the pack
changes. No rule is active, so the only writes are the captures'. Rows are
processed by the worker's jobs, as in the rules tests (``hook_support``).
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from uuid import UUID

import pytest
from sqlalchemy import Engine, select
from sqlalchemy.orm import sessionmaker

from tests.helpers import platform_ctx, staff_session, system_ctx
from tests.modules.monitoring.support import (
    CUSTOMER,
    FROM,
    ingest_items,
    make_item,
    run_processing,
)
from tests.platform.policy_support import AVI as AVI_FILE
from tests.platform.policy_support import ITF as ITF_FILE
from tests.platform.units_support import load_unit_values, seed_mx_holidays
from xolotl.app import wire_platform
from xolotl.modules.monitoring.aggregates.windows import MEXICO_CITY_ZONE
from xolotl.modules.monitoring.captures.models import (
    CaptureGroup,
    CapturePeriod,
    ReportableEvent,
)
from xolotl.modules.monitoring.txn.models import Transaction
from xolotl.modules.party import service
from xolotl.modules.party.enums import CustomerRelationshipStatus
from xolotl.modules.party.models import CustomerRelationship
from xolotl.modules.tenants.service import CreatedTenant
from xolotl.platform import clock
from xolotl.platform.config import get_settings
from xolotl.platform.db import get_sessionmaker, tenant_session
from xolotl.platform.events.event import Event, event_from_row
from xolotl.platform.events.models import OutboxRow
from xolotl.platform.policy import assign_pack
from xolotl.platform.policy.loader import install_pack, load_pack
from xolotl.platform.services import PlatformServices

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
UNIT_VALUES_2026 = FIXTURES / "unit_values_2026.yaml"
# June 2026 and January and February 2027, for the backfill scenarios.
UNIT_VALUES_EXTRA = FIXTURES / "unit_values_extra.yaml"
AVI_FROM = date(2026, 6, 1)
RECEIPT_DELAY = timedelta(hours=1)


def local(month: int, day: int, hour: int = 10) -> datetime:
    """Return an instant of 2026 in Mexico City, in UTC.

    Returns:
        The aware UTC instant.

    """
    return datetime(2026, month, day, hour, tzinfo=MEXICO_CITY_ZONE).astimezone(UTC)


@dataclass(frozen=True, slots=True)
class CaptureStage:
    """The tenant, its wired platform and the unit rows by ``(code, date)``."""

    tenant: CreatedTenant
    services: PlatformServices
    unit_ids: dict[tuple[str, date], int]

    @property
    def tenant_id(self) -> UUID:
        return self.tenant.tenant_id


def build_stage(
    tenant: CreatedTenant,
    admin_engine: Engine,
    units: Path = UNIT_VALUES_2026,
    *,
    more_units: Sequence[Path] = (),
) -> CaptureStage:
    """Install the packs, holidays, ``units`` and ``more_units``; wire the platform.

    Returns:
        The stage.

    """
    owner = sessionmaker(bind=admin_engine, expire_on_commit=False)
    with tenant_session(platform_ctx(), factory=owner) as s:
        itf = install_pack(
            s,
            load_pack(ITF_FILE, allow_unverified=True),
            source_path=str(ITF_FILE),
            loaded_by="test",
        )
        avi_config = load_pack(AVI_FILE, allow_unverified=True).model_copy(
            update={"effective_from": AVI_FROM}
        )
        avi = install_pack(s, avi_config, source_path=str(AVI_FILE), loaded_by="test")
        unit_ids = load_unit_values(s, units)
        for path in more_units:
            unit_ids |= load_unit_values(s, path)
    seed_mx_holidays(admin_engine)
    with staff_session(tenant.tenant_id, tenant.admin_user_id) as s:
        for version_id, product in ((itf, "EMONEY"), (avi, "CRYPTO")):
            _ = assign_pack(
                s,
                pack_version_id=version_id,
                product=product,
                effective_from=FROM,
                approved_by=tenant.admin_user_id,
            )
    services = wire_platform(get_sessionmaker(), get_settings())
    return CaptureStage(tenant, services, unit_ids)


def add_relationship(
    stage: CaptureStage, external_ref: str, *, product: str = "EMONEY", tier: str = "N3"
) -> UUID:
    """Create a person with a relationship ``external_ref`` of ``product``.

    Returns:
        The relationship's id.

    """
    with staff_session(stage.tenant_id, stage.tenant.admin_user_id) as s:
        party = service.create_party(s, CUSTOMER)
        row = CustomerRelationship(
            tenant_id=stage.tenant_id,
            party_id=party.id,
            external_ref=external_ref,
            product=product,
            account_tier=tier,
            status=CustomerRelationshipStatus.ACTIVE,
        )
        s.add(row)
        s.flush()
        return row.id


def crypto_item(
    external_id: str,
    at: datetime,
    amount: str,
    *,
    customer_ref: str = "crypto-1",
    fee: str | None = None,
    direction: str = "OUT",
) -> dict[str, object]:
    """Build an MXN crypto operation of ``customer_ref`` on account ``acct-c1``.

    Returns:
        The item as a client would send it.

    """
    item = make_item(
        external_id=external_id,
        occurred_at=at.isoformat(),
        customer_ref=customer_ref,
        account_ref=f"acct-{customer_ref}",
        direction=direction,
        rail="CRYPTO_ONCHAIN",
        instrument="CRYPTO",
        amount=amount,
        currency="MXN",
        channel="APP",
        crypto={"network": "bitcoin", "asset_code": "BTC", "asset_qty": "0.01"},
    )
    if fee is not None:
        item["fee_amount"] = fee
    return item


def ingest(
    stage: CaptureStage,
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
    stage: CaptureStage,
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


def events(stage: CaptureStage, code: str | None = None) -> list[ReportableEvent]:
    with tenant_session(system_ctx(stage.tenant_id)) as s:
        stmt = select(ReportableEvent).order_by(
            ReportableEvent.occurred_at, ReportableEvent.capture_code
        )
        if code is not None:
            stmt = stmt.where(ReportableEvent.capture_code == code)
        return list(s.scalars(stmt))


def groups(stage: CaptureStage) -> list[CaptureGroup]:
    with tenant_session(system_ctx(stage.tenant_id)) as s:
        return list(s.scalars(select(CaptureGroup).order_by(CaptureGroup.created_at)))


def periods(stage: CaptureStage) -> list[CapturePeriod]:
    with tenant_session(system_ctx(stage.tenant_id)) as s:
        return list(
            s.scalars(
                select(CapturePeriod).order_by(
                    CapturePeriod.capture_code, CapturePeriod.period_key
                )
            )
        )


def external_ids(stage: CaptureStage) -> dict[UUID, str]:
    with tenant_session(system_ctx(stage.tenant_id)) as s:
        rows = s.execute(select(Transaction.id, Transaction.external_id)).all()
        return {row.id: row.external_id for row in rows}


def outbox(stage: CaptureStage, event_type: str) -> list[Event]:
    with tenant_session(system_ctx(stage.tenant_id)) as s:
        rows = s.scalars(
            select(OutboxRow)
            .where(OutboxRow.type == event_type)
            .order_by(OutboxRow.seq)
        )
        return [event_from_row(row) for row in rows]
