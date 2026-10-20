"""The action matrix of a finding (SP-03 spec §9, program spec D15 and D20).

For one finding, one user and that user's roles, ``evaluate_actions`` tells which
staff decisions are allowed and, for every other one, every reason it is blocked, in
a fixed order: ``STATE``, ``ROLE_REQUIRED``, ``SAME_REVIEWER``, ``NOT_DELISTED``. The
same evaluation feeds the 409 of a refused disposition and the ``allowed_actions``
and ``blocked_reasons`` of the staff match view.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from xolotl.modules.screening.engine.enums import (
    BlockedReason,
    Decision,
    MatchStatus,
    SuggestedDisposition,
)
from xolotl.modules.screening.engine.models import ScreeningMatch
from xolotl.modules.screening.review.models import MatchDisposition
from xolotl.platform.enums import Role

STAFF_DECISIONS = (
    Decision.FALSE_POSITIVE,
    Decision.POSSIBLE_TRUE_MATCH,
    Decision.TRUE_MATCH,
    Decision.DELISTED,
)
FIRST_REVIEW = frozenset({Role.ANALYST, Role.SENIOR_ANALYST, Role.MLRO})
SECOND_REVIEW = frozenset({Role.SENIOR_ANALYST, Role.MLRO})

# The states each decision may be taken from.
_FROM_STATES: dict[Decision, frozenset[MatchStatus]] = {
    Decision.FALSE_POSITIVE: frozenset({MatchStatus.OPEN, MatchStatus.ESCALATED}),
    Decision.POSSIBLE_TRUE_MATCH: frozenset({MatchStatus.OPEN}),
    Decision.TRUE_MATCH: frozenset({MatchStatus.ESCALATED}),
    Decision.DELISTED: frozenset({MatchStatus.OPEN, MatchStatus.ESCALATED}),
}


@dataclass(frozen=True, slots=True)
class ActionSet:
    """The decisions a user may take, and why each other one is blocked."""

    allowed: tuple[Decision, ...]
    blocked: Mapping[Decision, tuple[BlockedReason, ...]]


def answers_escalation(decision: Decision, status: MatchStatus) -> bool:
    """Tell whether a decision answers an escalation (Deviation 11).

    These are the two-person decisions: confirming a true match, and clearing an
    escalated finding as a false positive.

    Returns:
        True when ``decision``, taken from ``status``, answers an escalation.

    """
    return decision is Decision.TRUE_MATCH or (
        decision is Decision.FALSE_POSITIVE and status is MatchStatus.ESCALATED
    )


def _required_roles(decision: Decision, status: MatchStatus) -> frozenset[Role]:
    # A first clearance or an escalation is a first review; confirming, clearing
    # an escalation and delisting are second reviews.
    if decision is Decision.POSSIBLE_TRUE_MATCH or (
        decision is Decision.FALSE_POSITIVE and status is not MatchStatus.ESCALATED
    ):
        return FIRST_REVIEW
    return SECOND_REVIEW


def _reasons(
    match: ScreeningMatch,
    decision: Decision,
    *,
    escalated_by: UUID | None,
    roles: frozenset[Role],
    user_id: UUID,
) -> tuple[BlockedReason, ...]:
    reasons: list[BlockedReason] = []
    if match.status not in _FROM_STATES[decision]:
        reasons.append(BlockedReason.STATE)
    if not roles & _required_roles(decision, match.status):
        reasons.append(BlockedReason.ROLE_REQUIRED)
    if (
        answers_escalation(decision, match.status)
        and escalated_by is not None
        and escalated_by == user_id
    ):
        reasons.append(BlockedReason.SAME_REVIEWER)
    if (
        decision is Decision.DELISTED
        and match.suggested_disposition is not SuggestedDisposition.DELISTED
    ):
        reasons.append(BlockedReason.NOT_DELISTED)
    return tuple(reasons)


def evaluate_actions(
    match: ScreeningMatch,
    *,
    escalated_by: UUID | None,
    roles: frozenset[Role],
    user_id: UUID,
) -> ActionSet:
    """Evaluate every staff decision on a finding for one user.

    ``escalated_by`` is the user who escalated the finding, if anyone did; that user
    may not answer the escalation.

    Returns:
        The allowed decisions in ``STAFF_DECISIONS`` order, and the reasons of every
        blocked one.

    """
    allowed: list[Decision] = []
    blocked: dict[Decision, tuple[BlockedReason, ...]] = {}
    for decision in STAFF_DECISIONS:
        reasons = _reasons(
            match, decision, escalated_by=escalated_by, roles=roles, user_id=user_id
        )
        if reasons:
            blocked[decision] = reasons
        else:
            allowed.append(decision)
    return ActionSet(allowed=tuple(allowed), blocked=blocked)


def escalated_by(s: Session, match_id: UUID) -> tuple[UUID | None, UUID | None]:
    """Find who escalated a finding most recently, and with which disposition.

    Returns:
        ``(decided_by, disposition id)`` of the latest ``POSSIBLE_TRUE_MATCH``
        disposition of the finding, or ``(None, None)`` when it was never escalated.

    """
    row = s.execute(
        select(MatchDisposition.decided_by, MatchDisposition.id)
        .where(
            MatchDisposition.match_id == match_id,
            MatchDisposition.decision == Decision.POSSIBLE_TRUE_MATCH,
        )
        .order_by(MatchDisposition.decided_at.desc(), MatchDisposition.id.desc())
        .limit(1)
    ).first()
    if row is None:
        return None, None
    decided_by, disposition_id = row
    return decided_by, disposition_id
