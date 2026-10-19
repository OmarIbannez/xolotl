"""Connector usage: the metered calls of the session's tenant, per UTC day.

Row-level security scopes the read to the session's tenant. A day is ``created_at``
in UTC; the range bounds are whole days so the index on ``(tenant_id, created_at)``
serves the filter.
"""

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta

from sqlalchemy import Date, cast, func, select
from sqlalchemy.orm import Session

from xolotl.platform.connectors.models import ConnectorCall
from xolotl.platform.enums import CallOutcome
from xolotl.platform.errors import ValidationFailedError

MAX_USAGE_DAYS = 366


@dataclass(frozen=True, slots=True)
class UsageRow:
    """The calls one connector made on one UTC day."""

    day: date
    connector: str
    calls: int
    cost_micros: int
    outcomes: dict[str, int]


def _midnight(day: date) -> datetime:
    return datetime.combine(day, time.min, tzinfo=UTC)


def usage(session: Session, *, start: date, end: date) -> list[UsageRow]:
    """Aggregate the session tenant's connector calls from ``start`` to ``end``.

    Both days are included.

    Returns:
        One row per ``(day, connector)`` with calls, cost and a count per outcome,
        ordered by day, then connector.

    Raises:
        ValidationFailedError: When ``start`` is after ``end`` or the range spans
            more than 366 days.

    """
    if start > end:
        msg = "from must be on or before to"
        raise ValidationFailedError(msg)
    if (end - start).days > MAX_USAGE_DAYS:
        msg = f"the range may span at most {MAX_USAGE_DAYS} days"
        raise ValidationFailedError(msg)
    day = cast(func.timezone("UTC", ConnectorCall.created_at), Date)
    rows = session.execute(
        select(
            day,
            ConnectorCall.connector,
            ConnectorCall.outcome,
            func.count(),
            func.coalesce(func.sum(ConnectorCall.cost_micros), 0),
        )
        .where(
            ConnectorCall.created_at >= _midnight(start),
            ConnectorCall.created_at < _midnight(end + timedelta(days=1)),
        )
        .group_by(day, ConnectorCall.connector, ConnectorCall.outcome)
        .order_by(day, ConnectorCall.connector, ConnectorCall.outcome)
    )
    folded: dict[tuple[date, str], UsageRow] = {}
    for on, connector, outcome, calls, cost in rows:
        key = (on, connector)
        found = folded.get(key)
        if found is None:
            found = UsageRow(
                day=on, connector=connector, calls=0, cost_micros=0, outcomes={}
            )
        outcomes = {**found.outcomes, CallOutcome(outcome).value: int(calls)}
        folded[key] = UsageRow(
            day=on,
            connector=connector,
            calls=found.calls + int(calls),
            cost_micros=found.cost_micros + int(cost),
            outcomes=outcomes,
        )
    return list(folded.values())
