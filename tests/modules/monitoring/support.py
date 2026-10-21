"""Tenants with policy, holidays and unit values for the monitoring tests.

Plain functions: the module's ``conftest.py`` (A4) wraps them in fixtures. Plans 4b
and 4c use these names (reconciliation finding 19).
"""

from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import Engine, func, select, update
from sqlalchemy.orm import Session, sessionmaker

from tests.helpers import platform_ctx, staff_session, system_ctx
from tests.platform.policy_support import install_both_packs
from tests.platform.units_support import UNIT_GOLDEN, load_unit_values, seed_mx_holidays
from xolotl.modules.monitoring.aggregates.declarations import AggregateDeclaration
from xolotl.modules.monitoring.aggregates.models import AggDeclaration
from xolotl.modules.monitoring.aggregates.postgres import ensure_declarations
from xolotl.modules.monitoring.enums import BatchSource
from xolotl.modules.monitoring.kinds import PROCESS_KIND
from xolotl.modules.monitoring.txn.ingest import BatchReport, ingest_batch
from xolotl.modules.monitoring.txn.schema import (
    Channel,
    Direction,
    EnrichedTransaction,
    Instrument,
    PeriodKeys,
    Rail,
    UnitAmount,
    validate_batch,
)
from xolotl.modules.party import service
from xolotl.modules.party.enums import CustomerRelationshipStatus, PartyType
from xolotl.modules.party.models import CustomerRelationship
from xolotl.modules.party.schemas import PartyCreateRequest, PersonInput
from xolotl.modules.screening.counterparty.schema import CounterpartyScreeningStatus
from xolotl.modules.screening.matching.evaluation import Draw
from xolotl.modules.tenants.service import CreatedTenant
from xolotl.platform import clock
from xolotl.platform.config import get_settings
from xolotl.platform.db import tenant_session
from xolotl.platform.enums import JobStatus
from xolotl.platform.jobs.models import JobRow
from xolotl.platform.jobs.queue import PostgresJobQueue
from xolotl.platform.policy import assign_pack
from xolotl.platform.services import PlatformServices

ITF = "MX/ITF-IFPE"
AVI = "MX/LFPIORPI-AVI"
FROM = date(2026, 1, 1)
CUSTOMER = PartyCreateRequest(
    party_type=PartyType.PERSON,
    person=PersonInput(given_names="Lucía", first_surname="Ramos"),
)


def _reference_data(admin_engine: Engine) -> dict[str, UUID]:
    """Install both packs, seed Mexico's holidays and load the fixture unit values.

    Returns:
        The pack version ids by pack code.

    """
    versions = install_both_packs(admin_engine)
    seed_mx_holidays(admin_engine)
    owner = sessionmaker(bind=admin_engine, expire_on_commit=False)
    with tenant_session(platform_ctx(), factory=owner) as s:
        _ = load_unit_values(s, UNIT_GOLDEN / "values.yaml")
    return versions


def _assign(tenant: CreatedTenant, assignments: list[tuple[UUID, str | None]]) -> None:
    with staff_session(tenant.tenant_id, tenant.admin_user_id) as s:
        for version_id, product in assignments:
            _ = assign_pack(
                s,
                pack_version_id=version_id,
                product=product,
                effective_from=FROM,
                approved_by=tenant.admin_user_id,
            )


def itf_tenant(tenant: CreatedTenant, admin_engine: Engine) -> UUID:
    """Assign the ITF pack to every product of ``tenant`` from 2026-01-01.

    Mexico's holidays and ``tests/golden/units/values.yaml`` are loaded too.

    Returns:
        The tenant's id.

    """
    versions = _reference_data(admin_engine)
    _assign(tenant, [(versions[ITF], None)])
    return tenant.tenant_id


def both_packs_tenant(tenant: CreatedTenant, admin_engine: Engine) -> UUID:
    """Assign ITF to product ``EMONEY`` and AVI to ``CRYPTO`` from 2026-01-01.

    Mexico's holidays and ``tests/golden/units/values.yaml`` are loaded too.

    Returns:
        The tenant's id.

    """
    versions = _reference_data(admin_engine)
    _assign(tenant, [(versions[ITF], "EMONEY"), (versions[AVI], "CRYPTO")])
    return tenant.tenant_id


def declare(
    session: Session, declaration: AggregateDeclaration, *, queue: PostgresJobQueue
) -> str:
    """Declare an aggregate through ``ensure_declarations`` and mark it backfilled.

    A test that declares before it streams has no history to walk, so its reads
    are complete at once; call ``ensure_declarations`` itself for an incomplete
    one.

    Returns:
        The declaration's ``agg_id``.

    """
    _ = ensure_declarations(session, [declaration], queue=queue)
    agg_id = declaration.agg_id()
    _ = session.execute(
        update(AggDeclaration)
        .where(AggDeclaration.agg_id == agg_id)
        .values(backfilled_through=datetime.now(UTC))
    )
    return agg_id


_MEXICO_CITY = ZoneInfo("America/Mexico_City")
STREAM_START = datetime(2026, 9, 20, 3, 0, tzinfo=UTC)
STREAM_SNAPSHOT = UUID(int=4)
# Fixed rates: pesos per unit (USD, UDI, UMA).
_PER_UNIT = {"USD": Decimal("18.5"), "UDI": Decimal("8.2"), "UMA": Decimal("113.14")}
_PLACES = Decimal("0.0001")
_COUNTERPARTIES: tuple[tuple[str, str | None], ...] = (
    ("c1" * 32, "MX"),
    ("c2" * 32, "US"),
    ("c3" * 32, "KY"),
    ("c4" * 32, "MX"),
    ("c5" * 32, None),
)
_DEVICES = ("d1" * 32, "d2" * 32)
_CHANNELS = (Channel.APP, Channel.WEB, Channel.BRANCH)
_AREAS = ("CMX", "JAL", "BCN")
_SHAPES: tuple[tuple[Rail, Instrument, Direction], ...] = (
    (Rail.CASH, Instrument.CASH, Direction.IN),
    (Rail.SPEI, Instrument.TRANSFER, Direction.OUT),
    (Rail.SPEI, Instrument.TRANSFER, Direction.IN),
    (Rail.SWIFT, Instrument.TRANSFER, Direction.OUT),
    (Rail.CRYPTO_ONCHAIN, Instrument.CRYPTO, Direction.OUT),
)


def _period_keys(at: datetime) -> PeriodKeys:
    local = at.astimezone(_MEXICO_CITY).date()
    quarter = (local.month - 1) // 3 + 1
    return PeriodKeys(
        DAY=local.isoformat(),
        MONTH=f"{local.year}-{local.month:02d}",
        QUARTER=f"{local.year}-Q{quarter}",
        BUSINESS_DAY=local.weekday() < 5,
    )


def _unit_amounts(pesos: Decimal, on: date) -> dict[str, UnitAmount]:
    def unit(amount: Decimal) -> UnitAmount:
        return UnitAmount(
            amount=amount.quantize(_PLACES), unit_value_id=None, resolved_date=on
        )

    return {
        "USD/PRIOR_BANKING_DAY": unit(pesos / _PER_UNIT["USD"]),
        "UDI/SAME_DAY": unit(pesos / _PER_UNIT["UDI"]),
        "UDI/LAST_DAY_PREV_MONTH": unit(pesos / _PER_UNIT["UDI"]),
        "UMA/SAME_DAY": unit(pesos / _PER_UNIT["UMA"]),
        "MXN/SAME_DAY": unit(pesos),
    }


def stream(n: int, *, seed: int) -> list[EnrichedTransaction]:
    """Build a deterministic synthetic stream of enriched transactions.

    Three parties with one relationship and two accounts each, five
    counterparties, two devices, MXN and USD amounts, cash, transfers (SPEI and
    SWIFT) and crypto, one to nine hours apart from ``STREAM_START``, every
    ``unit_amounts`` key computed at a fixed rate.

    Returns:
        ``n`` transactions in ``occurred_at`` order.

    """
    draw = Draw(seed)
    parties = [UUID(int=100 + index) for index in range(3)]
    relationships = [UUID(int=200 + index) for index in range(3)]
    accounts = [
        [UUID(int=300 + 10 * index + slot) for slot in range(2)] for index in range(3)
    ]
    at = STREAM_START
    stream: list[EnrichedTransaction] = []
    for number in range(n):
        at += timedelta(hours=draw.randrange(1, 10))
        who = draw.below(3)
        rail, instrument, direction = _SHAPES[draw.below(len(_SHAPES))]
        currency = "USD" if draw.below(10) < 3 else "MXN"
        amount_minor = draw.randrange(10_000, 20_000_000)
        if draw.below(10) < 2:
            amount_minor -= amount_minor % 100_000
            amount_minor = max(amount_minor, 100_000)
        pesos = Decimal(amount_minor) / 100
        if currency == "USD":
            pesos *= _PER_UNIT["USD"]
        counterparty = (
            _COUNTERPARTIES[draw.below(len(_COUNTERPARTIES))]
            if draw.below(10) < 8
            else None
        )
        crypto = instrument is Instrument.CRYPTO
        stream.append(
            EnrichedTransaction(
                id=UUID(int=10_000 + number),
                external_id=f"s{seed}-{number}",
                occurred_at=at,
                received_at=at + timedelta(minutes=1),
                party_id=parties[who],
                relationship_id=relationships[who],
                account_id=accounts[who][draw.below(2)],
                product="EMONEY",
                direction=direction,
                rail=rail,
                instrument=instrument,
                channel=_CHANNELS[draw.below(len(_CHANNELS))],
                amount_minor=amount_minor,
                currency=currency,
                fee_minor=draw.randrange(0, 5_000) if crypto else None,
                asset_qty=Decimal(draw.randrange(1, 10**6)) / 10**4 if crypto else None,
                asset_code="BTC" if crypto else None,
                unit_amounts=_unit_amounts(pesos, at.astimezone(_MEXICO_CITY).date()),
                counterparty_id=None if counterparty is None else UUID(int=500),
                counterparty_key=None if counterparty is None else counterparty[0],
                counterparty_country=None if counterparty is None else counterparty[1],
                counterparty_screening_status=(
                    None
                    if counterparty is None
                    else CounterpartyScreeningStatus.UNSCREENED
                ),
                device_key=_DEVICES[draw.below(len(_DEVICES))]
                if draw.below(10) < 7
                else None,
                geo_country="MX",
                geo_admin_area=_AREAS[draw.below(len(_AREAS))],
                crypto=(
                    {"network": "bitcoin", "self_custody": draw.below(2) == 0}
                    if crypto
                    else None
                ),
                fx=None,
                purpose_code=None,
                period_keys=_period_keys(at),
                tier=None,
                risk_rating=None,
                monitoring_profile="STANDARD",
                policy_snapshot_id=STREAM_SNAPSHOT,
                policy_country="MX",
                late=False,
            )
        )
    return stream


def make_item(**overrides: object) -> dict[str, object]:
    """Build a valid MXN cash deposit for ``cust-1``, ten minutes ago.

    Returns:
        The item as a client would send it, with ``overrides`` applied.

    """
    occurred = clock.now() - timedelta(minutes=10)
    item: dict[str, object] = {
        "external_id": "tx-1",
        "occurred_at": occurred.isoformat(),
        "customer_ref": "cust-1",
        "direction": "IN",
        "rail": "CASH",
        "instrument": "CASH",
        "amount": "1500.00",
        "currency": "MXN",
        "channel": "BRANCH",
    }
    item.update(overrides)
    return item


def ingest_items(
    session: Session,
    items: Sequence[Mapping[str, object]],
    *,
    queue: PostgresJobQueue,
) -> BatchReport:
    """Validate ``items`` as the route does and ingest them as an API batch.

    Returns:
        The batch report.

    Raises:
        AssertionError: When an item does not validate.

    """
    accepted, errors = validate_batch(
        items,
        now=clock.now(),
        max_future_minutes=get_settings().monitoring_max_future_minutes,
    )
    if errors:
        msg = f"items do not validate: {errors}"
        raise AssertionError(msg)
    return ingest_batch(
        session,
        accepted,
        source=BatchSource.API,
        submitted_by="api_key:test",
        queue=queue,
    )


def add_customer(
    tenant: CreatedTenant,
    external_ref: str,
    *,
    status: CustomerRelationshipStatus = CustomerRelationshipStatus.ACTIVE,
) -> CustomerRelationship:
    """Create a person and an ``EMONEY`` relationship ``external_ref`` (tier N1).

    Returns:
        The relationship, detached.

    """
    with staff_session(tenant.tenant_id, tenant.admin_user_id) as s:
        party = service.create_party(s, CUSTOMER)
        row = CustomerRelationship(
            tenant_id=tenant.tenant_id,
            party_id=party.id,
            external_ref=external_ref,
            product="EMONEY",
            account_tier="N1",
            status=status,
        )
        s.add(row)
        s.flush()
        return row


PROCESSING_ROUNDS = 50


def _pending_processing(tenant_id: UUID) -> int:
    with tenant_session(system_ctx(tenant_id)) as s:
        return (
            s.scalar(
                select(func.count())
                .select_from(JobRow)
                .where(
                    JobRow.kind == PROCESS_KIND,
                    JobRow.status.in_((JobStatus.PENDING, JobStatus.RUNNING)),
                )
            )
            or 0
        )


def run_processing(services: PlatformServices, tenant_id: UUID) -> int:
    """Run the worker's jobs until the tenant has no ``monitoring.process`` job.

    At most 50 rounds; a round that runs nothing ends it too (a job waiting for
    its retry is not due yet).

    Returns:
        How many jobs ran, of every kind.

    """
    ran = 0
    for _ in range(PROCESSING_ROUNDS):
        if not _pending_processing(tenant_id):
            break
        count = services.job_runner.run_once()
        ran += count
        if count == 0:
            break
    return ran
