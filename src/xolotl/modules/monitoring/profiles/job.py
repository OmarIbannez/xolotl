"""Monthly behaviour profiles (SP-04 spec §5.2, §13.3, FR-TM-013; plan 4c C8).

``monitoring.profiles`` runs per tenant on ``XOLOTL_MONITORING_PROFILE_CRON`` (the
2nd, Mexico City, so the 1st's late rows are in) and profiles the previous Mexico
City month; the CLI names a month in the payload. For every relationship with a
``PROCESSED`` row in the month it sums the month into ``behaviour_profile.metrics``
in one grouped read, copies the relationship's declared profile, derives the
actual-over-declared ratios and flags the month ``deviated`` when either ratio is at
or above ``XOLOTL_MONITORING_PROFILE_DEVIATION_RATIO``.

- Every amount is the row's ``unit_amounts["MXN/SAME_DAY"]``, as is the declared
  profile (Deviation 12: it is refused in any other currency), so actual and declared
  compare without a conversion. Amounts are strings of whole MXN minor units.
- ``cash_share``, ``international_share`` and ``crypto_share`` are shares of the
  month's MXN total, in plus out (Deviation 13): the amounts of ``instrument = CASH``,
  of cross-border rows (a counterparty country other than the pack's, or
  ``rail = SWIFT``, as the aggregates read it) and of ``instrument = CRYPTO``, with
  four places. ``INTERNAL`` movements between a customer's own accounts are left out.
- A ratio is shown with one place, rounded down, so a shown ratio at or above the
  threshold is exactly a deviated one.

A row written or changed publishes the staff-only
``monitoring.behaviour_profile_updated.v1`` (ids, the month and the flag), which
risk scoring (SP-07) consumes to re-score; a recomputation that changes nothing
writes and publishes nothing. The month is computed in one transaction: one grouped
read over the month's partition, however many relationships it has.
"""

import logging
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from decimal import ROUND_FLOOR, ROUND_HALF_UP, Decimal
from uuid import UUID

from pydantic import JsonValue
from sqlalchemy import Numeric, and_, cast, func, or_, select
from sqlalchemy.orm import Session

from xolotl.modules.monitoring.aggregates.windows import MEXICO_CITY_ZONE
from xolotl.modules.monitoring.enrich.policy import policy_date
from xolotl.modules.monitoring.enums import TransactionStatus
from xolotl.modules.monitoring.events import (
    BEHAVIOUR_PROFILE_UPDATED,
    publish_monitoring_event,
)
from xolotl.modules.monitoring.profiles.models import BehaviourProfile
from xolotl.modules.monitoring.txn.models import Transaction
from xolotl.modules.monitoring.txn.schema import Direction, Instrument, Rail
from xolotl.modules.party.service import relationship_ref, relationship_refs
from xolotl.platform import clock
from xolotl.platform.config import get_settings
from xolotl.platform.db import bound_context
from xolotl.platform.jobs.queue import Job
from xolotl.platform.policy.models import PolicySnapshot

logger = logging.getLogger(__name__)

PROFILES_KIND = "monitoring.profiles"
# How many months GET /v1/monitoring/profiles/{relationship_id} returns.
HISTORY_MONTHS = 12
CURRENCY = "MXN"
_UNIT_KEY = "MXN/SAME_DAY"
_MONTH = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")
_MINOR_PER_MAJOR = Decimal(100)
_SHARE_PLACES = Decimal("0.0001")
_RATIO_PLACES = Decimal("0.1")
_DECLARED = (
    ("monthly_inflow", "monthly_inflow_minor"),
    ("monthly_outflow", "monthly_outflow_minor"),
)


def profile_month(today: date) -> str:
    """Return the month before ``today``'s, as ``YYYY-MM``.

    Returns:
        The previous month.

    """
    return f"{today.replace(day=1) - timedelta(days=1):%Y-%m}"


def parse_month(value: str) -> str:
    """Check a month written ``YYYY-MM``.

    Returns:
        The month, unchanged.

    Raises:
        ValueError: When it is not a ``YYYY-MM`` month.

    """
    if not _MONTH.fullmatch(value):
        msg = f"month must be YYYY-MM, not {value!r}"
        raise ValueError(msg)
    return value


def _month_bounds(month: str) -> tuple[datetime, datetime]:
    # The Mexico City month as UTC instants, so the read prunes partitions.
    year, number = (int(part) for part in month.split("-"))
    first = date(year, number, 1)
    following = (first + timedelta(days=31)).replace(day=1)
    start, after = (
        datetime.combine(day, time(), tzinfo=MEXICO_CITY_ZONE)
        for day in (first, following)
    )
    return start.astimezone(UTC), after.astimezone(UTC)


@dataclass(frozen=True, slots=True)
class _Activity:
    """One relationship's month, as the grouped read sums it (MXN, major units)."""

    relationship_id: UUID
    party_id: UUID
    inflow: Decimal
    outflow: Decimal
    inflow_count: int
    outflow_count: int
    cash: Decimal
    international: Decimal
    crypto: Decimal
    counterparties: int
    days: int


def _activity(s: Session, month: str) -> list[_Activity]:
    start, end = _month_bounds(month)
    amount = cast(
        func.jsonb_extract_path_text(Transaction.unit_amounts, _UNIT_KEY, "amount"),
        Numeric(asdecimal=True),
    )
    pack_country = func.jsonb_extract_path_text(PolicySnapshot.content, "country")
    cross_border = or_(
        and_(
            Transaction.counterparty_country.is_not(None),
            Transaction.counterparty_country != pack_country,
        ),
        Transaction.rail == Rail.SWIFT,
    )
    inbound = Transaction.direction == Direction.IN
    outbound = Transaction.direction == Direction.OUT

    rows = s.execute(
        select(
            Transaction.relationship_id,
            Transaction.party_id,
            func.coalesce(func.sum(amount).filter(inbound), 0),
            func.coalesce(func.sum(amount).filter(outbound), 0),
            func.count().filter(inbound),
            func.count().filter(outbound),
            func.coalesce(
                func.sum(amount).filter(Transaction.instrument == Instrument.CASH), 0
            ),
            func.coalesce(func.sum(amount).filter(cross_border), 0),
            func.coalesce(
                func.sum(amount).filter(Transaction.instrument == Instrument.CRYPTO), 0
            ),
            func.count(Transaction.counterparty_id.distinct()),
            func.count(
                func.jsonb_extract_path_text(Transaction.period_keys, "DAY").distinct()
            ),
        )
        .select_from(Transaction)
        .outerjoin(
            PolicySnapshot,
            and_(
                PolicySnapshot.tenant_id == Transaction.tenant_id,
                PolicySnapshot.id == Transaction.policy_snapshot_id,
            ),
        )
        .where(
            Transaction.status == TransactionStatus.PROCESSED,
            Transaction.occurred_at >= start,
            Transaction.occurred_at < end,
            func.jsonb_extract_path_text(Transaction.period_keys, "MONTH") == month,
            Transaction.direction.in_((Direction.IN, Direction.OUT)),
        )
        .group_by(Transaction.relationship_id, Transaction.party_id)
        .order_by(Transaction.relationship_id)
    ).all()
    return [
        _Activity(
            relationship_id=row[0],
            party_id=row[1],
            inflow=Decimal(row[2]),
            outflow=Decimal(row[3]),
            inflow_count=int(row[4]),
            outflow_count=int(row[5]),
            cash=Decimal(row[6]),
            international=Decimal(row[7]),
            crypto=Decimal(row[8]),
            counterparties=int(row[9]),
            days=int(row[10]),
        )
        for row in rows
    ]


def _minor(pesos: Decimal) -> str:
    return str((pesos * _MINOR_PER_MAJOR).quantize(Decimal(1), ROUND_HALF_UP))


def _share(part: Decimal, whole: Decimal) -> str:
    if whole <= 0:
        return str(Decimal(0).quantize(_SHARE_PLACES))
    return str((part / whole).quantize(_SHARE_PLACES, ROUND_HALF_UP))


def _metrics(activity: _Activity) -> dict[str, JsonValue]:
    whole = activity.inflow + activity.outflow
    return {
        "inflow_minor": _minor(activity.inflow),
        "outflow_minor": _minor(activity.outflow),
        "inflow_count": activity.inflow_count,
        "outflow_count": activity.outflow_count,
        "cash_share": _share(activity.cash, whole),
        "international_share": _share(activity.international, whole),
        "crypto_share": _share(activity.crypto, whole),
        "distinct_counterparties": activity.counterparties,
        "active_days": activity.days,
        "currency": CURRENCY,
    }


def _deviation(
    activity: _Activity, declared: dict[str, JsonValue] | None, threshold: Decimal
) -> tuple[dict[str, JsonValue] | None, bool]:
    if declared is None:
        return None, False
    actual = {
        "monthly_inflow": activity.inflow * _MINOR_PER_MAJOR,
        "monthly_outflow": activity.outflow * _MINOR_PER_MAJOR,
    }
    ratios: dict[str, JsonValue] = {}
    deviated = False
    for name, field in _DECLARED:
        amount = declared.get(field)
        # A declared zero, or a value that is not an amount, gives no ratio.
        if isinstance(amount, bool) or not isinstance(amount, int) or amount <= 0:
            continue
        ratio = actual[name] / Decimal(amount)
        ratios[name] = str(ratio.quantize(_RATIO_PLACES, ROUND_FLOOR))
        deviated = deviated or ratio >= threshold
    return ratios, deviated


def compute_profiles(s: Session, *, month: str) -> int:
    """Profile every relationship with processed rows in ``month`` (``YYYY-MM``).

    Upserts one ``behaviour_profile`` per relationship and month and publishes
    ``monitoring.behaviour_profile_updated.v1`` for each row written or changed.

    Returns:
        How many relationships the month profiled.

    """
    month = parse_month(month)
    activities = _activity(s, month)
    if not activities:
        return 0
    threshold = get_settings().monitoring_profile_deviation_ratio
    refs = relationship_refs(s, [item.relationship_id for item in activities])
    existing = {
        row.relationship_id: row
        for row in s.scalars(
            select(BehaviourProfile)
            .where(BehaviourProfile.month == month)
            .with_for_update()
        )
    }
    tenant_id = bound_context(s).require_tenant_id()
    now = clock.now()
    for activity in activities:
        ref = refs.get(activity.relationship_id)
        declared = None if ref is None else ref.declared_profile
        metrics = _metrics(activity)
        deviation, deviated = _deviation(activity, declared, threshold)
        row = existing.get(activity.relationship_id)
        if row is None:
            row = BehaviourProfile(
                tenant_id=tenant_id,
                relationship_id=activity.relationship_id,
                party_id=activity.party_id,
                month=month,
            )
            s.add(row)
        elif (row.metrics, row.declared_profile, row.deviation, row.deviated) == (
            metrics,
            declared,
            deviation,
            deviated,
        ):
            continue
        row.metrics = metrics
        row.declared_profile = declared
        row.deviation = deviation
        row.deviated = deviated
        row.computed_at = now
        s.flush()
        _ = publish_monitoring_event(
            s,
            BEHAVIOUR_PROFILE_UPDATED,
            entity="behaviour_profile",
            entity_id=row.id,
            key=str(activity.relationship_id),
            data={
                "relationship_id": str(activity.relationship_id),
                "party_id": str(activity.party_id),
                "month": month,
                "deviated": deviated,
            },
        )
    return len(activities)


def handle_profiles(s: Session, job: Job) -> None:
    """Handle ``monitoring.profiles``: the payload's month, else the previous one.

    ``parse_month`` refuses a string that is no ``YYYY-MM`` month (``ValueError``).

    Raises:
        TypeError: When the payload's month is not a string.

    """
    named = job.payload.get("month")
    if named is None:
        month = profile_month(policy_date(clock.now()))
    elif isinstance(named, str):
        month = parse_month(named)
    else:
        msg = "month must be a YYYY-MM string"
        raise TypeError(msg)
    count = compute_profiles(s, month=month)
    logger.info(
        "behaviour profiles computed",
        extra={"tenant_id": str(job.tenant_id), "month": month, "count": count},
    )


def recent_profiles(s: Session, relationship_id: UUID) -> list[BehaviourProfile]:
    """Read a relationship's last ``HISTORY_MONTHS`` profiles, newest first.

    Returns:
        The profiles, possibly none.

    """
    _ = relationship_ref(s, relationship_id)
    return list(
        s.scalars(
            select(BehaviourProfile)
            .where(BehaviourProfile.relationship_id == relationship_id)
            .order_by(BehaviourProfile.month.desc())
            .limit(HISTORY_MONTHS)
        )
    )
