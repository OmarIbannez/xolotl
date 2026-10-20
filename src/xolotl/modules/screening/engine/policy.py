"""The policy a screening run resolves (spec Decision 14).

"Today" for policy resolution is the calendar day in Mexico City, whatever the
server's zone. A run or a disposition resolves the tenant-wide policy of that day
(``product=None``) and stores its snapshot.
"""

from dataclasses import dataclass
from datetime import date
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from xolotl.modules.screening.matching import ScoringConfig
from xolotl.platform import clock
from xolotl.platform.calendar.deadlines import MEXICO_CITY
from xolotl.platform.policy import EffectivePolicy, effective_policy, snapshot_policy


def today_mx() -> date:
    """Tell the date in Mexico City now.

    Returns:
        The calendar day in ``America/Mexico_City``.

    """
    return clock.now().astimezone(ZoneInfo(MEXICO_CITY)).date()


@dataclass(frozen=True, slots=True)
class ScreeningPolicyView:
    """The policy a run or a disposition is taken under, frozen in a snapshot."""

    snapshot_id: UUID
    effective: EffectivePolicy
    config: ScoringConfig


def policy_for_run(s: Session) -> ScreeningPolicyView:
    """Resolve today's tenant-wide policy, snapshot it and derive the matcher config.

    A tenant without a pack in force today gets ``POLICY_NOT_ASSIGNED``.

    Returns:
        The snapshot id, the effective policy and the scoring configuration.

    """
    effective = effective_policy(s, on=today_mx(), product=None)
    bands = effective.screening.bands
    return ScreeningPolicyView(
        snapshot_id=snapshot_policy(s, effective),
        effective=effective,
        config=ScoringConfig(
            id_match_floor=bands.id_match_floor, id_conflict_cap=bands.id_conflict_cap
        ),
    )
