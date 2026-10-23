"""The policy a screening run resolves (spec Decision 14).

"Today" for policy resolution is the calendar day in Mexico City, whatever the
server's zone. A run or a disposition resolves the strictest merge of every pack in
force for the tenant that day, whatever its product (``strictest_policy``), and stores
its snapshot: screening is per party, not per product (Decision 14), and D21 leaves a
tenant whose packs are assigned per product without a ``product=None`` policy. The
bands a hit is cut with depend on its list's kind (spec §8.3): the sanctions bands for
every kind but ``PEP``, whose review band sits ``PEP_REVIEW_OFFSET`` below its alert
band (spec Decision 6).
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from xolotl.modules.screening.enums import ListKind
from xolotl.modules.screening.matching import BandThresholds, ScoringConfig
from xolotl.platform import clock
from xolotl.platform.calendar.deadlines import MEXICO_CITY
from xolotl.platform.policy import (
    EffectivePolicy,
    ScreeningBands,
    snapshot_policy,
    strictest_policy,
)

PEP_REVIEW_OFFSET = Decimal("0.15")
# Candidates retrieved per kind group (TS-03 §5).
PEP_TOP_K = 25
SANCTIONS_TOP_K = 50


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
    """Resolve today's policy across products, snapshot it and derive the config.

    A tenant without a pack in force today gets ``POLICY_NOT_ASSIGNED``.

    Returns:
        The snapshot id, the effective policy and the scoring configuration.

    """
    effective = strictest_policy(s, on=today_mx())
    return ScreeningPolicyView(
        snapshot_id=snapshot_policy(s, effective),
        effective=effective,
        config=scoring_config(effective.screening.bands),
    )


def scoring_config(bands: ScreeningBands) -> ScoringConfig:
    """Derive the matcher configuration of a policy's bands.

    A run and a replay of it both derive the configuration, and so its hash, here.

    Returns:
        The scoring configuration with the bands' ID floor and conflict cap.

    """
    return ScoringConfig(
        id_match_floor=bands.id_match_floor, id_conflict_cap=bands.id_conflict_cap
    )


def thresholds_for(list_kind: str, bands: ScreeningBands) -> BandThresholds:
    """Tell the alert and review thresholds of a list kind.

    Returns:
        ``pep_alert`` and ``pep_alert - 0.15`` for ``PEP``; the sanctions bands for
        ``SANCTION``, ``BLOCKED``, ``INTERNAL`` and ``FISCAL``.

    """
    if list_kind == ListKind.PEP:
        return BandThresholds(
            alert=bands.pep_alert, review=bands.pep_alert - PEP_REVIEW_OFFSET
        )
    return BandThresholds(alert=bands.sanctions_alert, review=bands.sanctions_review)


def top_k_for(list_kind: str) -> int:
    """Tell how many name candidates a list kind retrieves.

    Returns:
        25 for ``PEP``, 50 for every other kind.

    """
    return PEP_TOP_K if list_kind == ListKind.PEP else SANCTIONS_TOP_K
