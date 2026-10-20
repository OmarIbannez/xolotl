"""The action matrix is exactly the table of spec §9 (the acceptance test of DR-305).

The oracle below is written by hand from the spec table, not from the
implementation: every state x suggestion x role x same-or-other user x decision is
checked, and the blocked reasons must come in the documented order.
"""

import itertools
from uuid import uuid4

import pytest

from xolotl.modules.screening.engine.enums import (
    BlockedReason,
    Decision,
    MatchStatus,
    SuggestedDisposition,
)
from xolotl.modules.screening.engine.models import ScreeningMatch
from xolotl.modules.screening.review.actions import STAFF_DECISIONS, evaluate_actions
from xolotl.platform.enums import Role

STATES = list(MatchStatus)
SUGGESTIONS = [None, SuggestedDisposition.DELISTED]
ROLES = [Role.ANALYST, Role.SENIOR_ANALYST, Role.MLRO, Role.AUDITOR_RO, Role.ADMIN]
ANY_REVIEWER = frozenset({Role.ANALYST, Role.SENIOR_ANALYST, Role.MLRO})
SENIOR = frozenset({Role.SENIOR_ANALYST, Role.MLRO})

# The rows of the spec §9 table: (from, decision) -> (who, not the escalating user,
# needs the DELISTED suggestion).
SPEC: dict[tuple[MatchStatus, Decision], tuple[frozenset[Role], bool, bool]] = {
    (MatchStatus.OPEN, Decision.FALSE_POSITIVE): (ANY_REVIEWER, False, False),
    (MatchStatus.OPEN, Decision.POSSIBLE_TRUE_MATCH): (ANY_REVIEWER, False, False),
    (MatchStatus.ESCALATED, Decision.TRUE_MATCH): (SENIOR, True, False),
    (MatchStatus.ESCALATED, Decision.FALSE_POSITIVE): (SENIOR, True, False),
    (MatchStatus.OPEN, Decision.DELISTED): (SENIOR, False, True),
    (MatchStatus.ESCALATED, Decision.DELISTED): (SENIOR, False, True),
}
# From a state the table has no row for, the decision is blocked by STATE, and the
# rest of its row from its usual state still applies.
OFF_STATE: dict[Decision, tuple[frozenset[Role], bool, bool]] = {
    Decision.FALSE_POSITIVE: (ANY_REVIEWER, False, False),
    Decision.POSSIBLE_TRUE_MATCH: (ANY_REVIEWER, False, False),
    Decision.TRUE_MATCH: (SENIOR, True, False),
    Decision.DELISTED: (SENIOR, False, True),
}


def _expected(
    state: MatchStatus,
    suggestion: SuggestedDisposition | None,
    role: Role,
    *,
    same_user: bool,
    decision: Decision,
) -> list[BlockedReason]:
    row = SPEC.get((state, decision))
    who, four_eyes, needs_suggestion = row or OFF_STATE[decision]
    reasons: list[BlockedReason] = []
    if row is None:
        reasons.append(BlockedReason.STATE)
    if role not in who:
        reasons.append(BlockedReason.ROLE_REQUIRED)
    if four_eyes and same_user:
        reasons.append(BlockedReason.SAME_REVIEWER)
    if needs_suggestion and suggestion is not SuggestedDisposition.DELISTED:
        reasons.append(BlockedReason.NOT_DELISTED)
    return reasons


@pytest.mark.parametrize(
    ("state", "suggestion", "role", "same_user", "decision"),
    list(itertools.product(STATES, SUGGESTIONS, ROLES, [False, True], STAFF_DECISIONS)),
)
def test_the_disposition_matrix_is_exactly_the_spec(
    state, suggestion, role, same_user, decision
):
    user_id, other = uuid4(), uuid4()
    match = ScreeningMatch(status=state, suggested_disposition=suggestion)
    actions = evaluate_actions(
        match,
        escalated_by=user_id if same_user else other,
        roles=frozenset({role}),
        user_id=user_id,
    )
    expected = _expected(
        state, suggestion, role, same_user=same_user, decision=decision
    )
    if expected:
        assert decision not in actions.allowed
        assert list(actions.blocked[decision]) == expected
    else:
        assert decision in actions.allowed
        assert decision not in actions.blocked
    assert set(actions.allowed) | set(actions.blocked) == set(STAFF_DECISIONS)


def test_terminal_states_allow_nothing():
    for state in (
        MatchStatus.TRUE_MATCH,
        MatchStatus.FALSE_POSITIVE,
        MatchStatus.DELISTED,
        MatchStatus.SUPERSEDED,
    ):
        match = ScreeningMatch(
            status=state, suggested_disposition=SuggestedDisposition.DELISTED
        )
        actions = evaluate_actions(
            match,
            escalated_by=None,
            roles=frozenset({Role.MLRO, Role.ANALYST}),
            user_id=uuid4(),
        )
        assert actions.allowed == ()
        assert dict(actions.blocked) == dict.fromkeys(
            STAFF_DECISIONS, (BlockedReason.STATE,)
        )


def test_an_unescalated_finding_has_no_same_reviewer():
    # Nobody escalated the finding, so nobody is the escalating user.
    match = ScreeningMatch(status=MatchStatus.ESCALATED, suggested_disposition=None)
    actions = evaluate_actions(
        match, escalated_by=None, roles=frozenset({Role.MLRO}), user_id=uuid4()
    )
    assert actions.allowed == (Decision.FALSE_POSITIVE, Decision.TRUE_MATCH)
