"""The counterparties the periodic re-screen walks (SP-04 spec §11.3, §21 Q8).

After its party pages, the ``PERIODIC`` walk visits the tenant's counterparties in
id order, ``SCREENING_RESCREEN_BATCH`` per page. A counterparty no transaction has
named in the last ``DORMANT_MONTHS`` months (``last_seen_at`` before the same day
twelve months back, Mexico City time) is dormant and skipped, for cost: a list delta
still screens it, since blocking reads every counterparty.
"""

from datetime import date, datetime, time
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from xolotl.modules.screening.counterparty.models import Counterparty
from xolotl.platform.calendar.deadlines import MEXICO_CITY
from xolotl.platform.db import bound_context

DORMANT_MONTHS = 12


def _active_since(today: date) -> datetime:
    # The same calendar day DORMANT_MONTHS back; 29 February falls to the 28th.
    months = today.year * 12 + today.month - 1 - DORMANT_MONTHS
    year, month = divmod(months, 12)
    try:
        day = today.replace(year=year, month=month + 1)
    except ValueError:
        day = today.replace(year=year, month=month + 1, day=28)
    return datetime.combine(day, time(), tzinfo=ZoneInfo(MEXICO_CITY))


def counterparty_ids_after(
    s: Session, *, after: UUID | None, limit: int, today: date
) -> list[UUID]:
    """Page through the tenant's active counterparty ids in id order.

    Returns:
        Up to ``limit`` ids greater than ``after`` (every id when it is None) of
        counterparties seen since the same day ``DORMANT_MONTHS`` months back.

    """
    query = select(Counterparty.id).where(
        Counterparty.tenant_id == bound_context(s).require_tenant_id(),
        Counterparty.last_seen_at >= _active_since(today),
    )
    if after is not None:
        query = query.where(Counterparty.id > after)
    return list(s.scalars(query.order_by(Counterparty.id).limit(limit)))


def active_counterparty_count(s: Session, *, today: date) -> int:
    """Count the counterparties the periodic walk visits, part of its ``total``.

    Returns:
        The count of counterparties that are not dormant.

    """
    query = select(func.count()).where(
        Counterparty.tenant_id == bound_context(s).require_tenant_id(),
        Counterparty.last_seen_at >= _active_since(today),
    )
    return s.scalar(query) or 0
