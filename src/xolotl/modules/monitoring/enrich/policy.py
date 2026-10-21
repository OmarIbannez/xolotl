"""The effective policy a transaction is judged under (SP-04 spec §7 step 1).

The policy date is the transaction's calendar date in Mexico City. A drain meets
the same ``(date, product)`` many times, so the snapshot is resolved and stored
once per pair and memoised in a cache the caller owns.
"""

from dataclasses import dataclass
from datetime import date, datetime
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from xolotl.platform.calendar import MEXICO_CITY
from xolotl.platform.policy import EffectivePolicy, effective_policy
from xolotl.platform.policy.snapshots import snapshot_policy

_MEXICO_CITY = ZoneInfo(MEXICO_CITY)


@dataclass(frozen=True, slots=True)
class SnapshotRef:
    """A stored policy snapshot and the policy it holds."""

    snapshot_id: UUID
    policy: EffectivePolicy


type SnapshotCache = dict[tuple[date, str | None], SnapshotRef]


def policy_date(occurred_at: datetime) -> date:
    """Return the calendar date of an aware instant in Mexico City.

    Returns:
        The local date.

    """
    return occurred_at.astimezone(_MEXICO_CITY).date()


def snapshot_for(
    session: Session, *, on: date, product: str | None, cache: SnapshotCache
) -> SnapshotRef:
    """Resolve and store the tenant's policy for ``on`` and ``product``, once.

    ``effective_policy``'s ``PolicyNotAssignedError`` propagates when no pack is
    in force on ``on``; the processing job parks the row for it.

    Returns:
        The snapshot, from ``cache`` when this pair was resolved before.

    """
    key = (on, product)
    found = cache.get(key)
    if found is None:
        policy = effective_policy(session, on=on, product=product)
        found = SnapshotRef(snapshot_id=snapshot_policy(session, policy), policy=policy)
        cache[key] = found
    return found
