"""The ``unit_value`` store: upserts with corrections, lookups and summaries.

A value that changes for the same ``(unit_code, valid_from)`` is a correction: a
new row, with the old row's ``superseded_by`` pointing at it. Lookups read only the
rows nothing supersedes, whatever their ``fetched_at``.
"""

from dataclasses import dataclass
from datetime import date, datetime

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from xolotl.platform.units.catalogue import UNITS
from xolotl.platform.units.models import UnitValueRow
from xolotl.platform.units.sources import UnitValue

HISTORY_LIMIT = 400


def upsert_value(
    session: Session, value: UnitValue, *, fetched_at: datetime
) -> int | None:
    """Store a value unless the current row already holds it.

    Returns:
        The new row's id, or ``None`` when the current row of the same unit and
        ``valid_from`` has the same value and ``valid_to``.

    """
    current = session.scalars(
        select(UnitValueRow).where(
            UnitValueRow.unit_code == value.unit_code,
            UnitValueRow.valid_from == value.valid_from,
            UnitValueRow.superseded_by.is_(None),
        )
    ).first()
    if (
        current is not None
        and current.value == value.value
        and current.valid_to == value.valid_to
    ):
        return None
    row = UnitValueRow(
        unit_code=value.unit_code,
        valid_from=value.valid_from,
        valid_to=value.valid_to,
        value=value.value,
        quote_ccy=value.quote_ccy,
        source=value.source,
        fetched_at=fetched_at,
    )
    session.add(row)
    session.flush()
    if current is not None:
        current.superseded_by = row.id
        session.flush()
    return row.id


def current_row(session: Session, unit_code: str, on: date) -> UnitValueRow | None:
    """Find the row in force on ``on`` that nothing supersedes.

    Returns:
        The latest such row by ``valid_from`` then ``fetched_at``, or ``None``.

    """
    return session.scalars(
        select(UnitValueRow)
        .where(
            UnitValueRow.unit_code == unit_code,
            UnitValueRow.valid_from <= on,
            or_(UnitValueRow.valid_to.is_(None), UnitValueRow.valid_to >= on),
            UnitValueRow.superseded_by.is_(None),
        )
        .order_by(UnitValueRow.valid_from.desc(), UnitValueRow.fetched_at.desc())
        .limit(1)
    ).first()


def history(
    session: Session, unit_code: str, *, start: date, end: date
) -> list[UnitValueRow]:
    """List the rows with ``valid_from`` in ``[start, end]``, superseded included.

    Returns:
        At most 400 rows, newest ``valid_from`` then ``fetched_at`` first.

    """
    return list(
        session.scalars(
            select(UnitValueRow)
            .where(
                UnitValueRow.unit_code == unit_code,
                UnitValueRow.valid_from >= start,
                UnitValueRow.valid_from <= end,
            )
            .order_by(UnitValueRow.valid_from.desc(), UnitValueRow.fetched_at.desc())
            .limit(HISTORY_LIMIT)
        ).all()
    )


def latest_valid_from(session: Session, unit_code: str) -> date | None:
    """Find the latest ``valid_from`` among the current rows of a unit.

    Returns:
        That date, or ``None`` when the unit has no rows.

    """
    return session.scalar(
        select(func.max(UnitValueRow.valid_from)).where(
            UnitValueRow.unit_code == unit_code,
            UnitValueRow.superseded_by.is_(None),
        )
    )


@dataclass(frozen=True, slots=True)
class UnitSummary:
    """A catalogue entry with the latest current row the store holds for it."""

    unit_code: str
    quote_ccy: str
    daily: bool
    series_id: str | None
    latest_valid_from: date | None
    latest_fetched_at: datetime | None
    latest_source: str | None


def unit_summaries(session: Session) -> list[UnitSummary]:
    """Summarise every catalogue unit.

    Returns:
        One summary per ``UNITS`` entry, in catalogue order.

    """
    summaries: list[UnitSummary] = []
    for spec in UNITS.values():
        latest = session.scalars(
            select(UnitValueRow)
            .where(
                UnitValueRow.unit_code == spec.code,
                UnitValueRow.superseded_by.is_(None),
            )
            .order_by(UnitValueRow.valid_from.desc(), UnitValueRow.fetched_at.desc())
            .limit(1)
        ).first()
        summaries.append(
            UnitSummary(
                unit_code=spec.code,
                quote_ccy=spec.quote_ccy,
                daily=spec.daily,
                series_id=spec.series_id,
                latest_valid_from=latest.valid_from if latest else None,
                latest_fetched_at=latest.fetched_at if latest else None,
                latest_source=latest.source if latest else None,
            )
        )
    return summaries
