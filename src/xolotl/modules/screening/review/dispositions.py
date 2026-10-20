"""Staff decisions on findings (SP-03 spec §9; FR-SC-007, DR-305).

A decision is evaluated against the action matrix for the current user, then stored
as an append-only ``match_disposition`` row with its rationale encrypted. A decision
that answers an escalation points at it, so the database's four-eyes trigger binds
it too. The writes run in the caller's transaction: rows, then audit, then ids-only
events; the subject's status follows last.
"""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from xolotl.modules.screening.engine.enums import (
    DecidedByType,
    Decision,
    MatchStatus,
)
from xolotl.modules.screening.engine.models import ScreeningMatch, ScreeningSubject
from xolotl.modules.screening.engine.policy import policy_for_run
from xolotl.modules.screening.engine.subjects import refresh_subject_status
from xolotl.modules.screening.errors import DispositionNotAllowedError
from xolotl.modules.screening.events import (
    MATCH_DISPOSITIONED,
    TRUE_MATCH_CONFIRMED,
    publish_screening_event,
)
from xolotl.modules.screening.review.actions import escalated_by, evaluate_actions
from xolotl.modules.screening.review.models import MatchDisposition
from xolotl.modules.screening.review.whitelist import whitelist_entry
from xolotl.platform import clock
from xolotl.platform.audit import AuditEvent, record
from xolotl.platform.crypto import encrypt
from xolotl.platform.db import bound_context
from xolotl.platform.enums import ActorType
from xolotl.platform.errors import (
    ForbiddenError,
    NotFoundError,
    ValidationFailedError,
)
from xolotl.platform.policy import get_snapshot

RATIONALE_MIN = 10
RATIONALE_MAX = 2000

# The finding's status after each staff decision.
_RESULT: dict[Decision, MatchStatus] = {
    Decision.FALSE_POSITIVE: MatchStatus.FALSE_POSITIVE,
    Decision.POSSIBLE_TRUE_MATCH: MatchStatus.ESCALATED,
    Decision.TRUE_MATCH: MatchStatus.TRUE_MATCH,
    Decision.DELISTED: MatchStatus.DELISTED,
}


def _rationale(rationale: str) -> str:
    text = rationale.strip()
    if not RATIONALE_MIN <= len(text) <= RATIONALE_MAX:
        msg = f"rationale must be {RATIONALE_MIN} to {RATIONALE_MAX} characters"
        raise ValidationFailedError(msg)
    return text


def _locked_match(s: Session, match_id: UUID) -> ScreeningMatch:
    match = s.scalars(
        select(ScreeningMatch)
        .where(ScreeningMatch.id == match_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).one_or_none()
    if match is None:
        msg = "match not found"
        raise NotFoundError(msg)
    return match


def disposition_match(
    s: Session, match_id: UUID, *, decision: Decision, rationale: str
) -> MatchDisposition:
    """Record a staff decision on a finding.

    An unknown finding is ``NOT_FOUND``, and a tenant without a policy in force is
    ``POLICY_NOT_ASSIGNED``.

    Returns:
        The new disposition, flushed.

    Raises:
        ForbiddenError: When the actor is not a staff user; API keys never decide.
        ValidationFailedError: For ``SUPERSEDED``, which only the system decides, or
            a rationale outside 10 to 2000 characters once stripped.
        DispositionNotAllowedError: When the action matrix blocks the decision for
            this user, with its ``blocked_reasons``.

    """
    ctx = bound_context(s)
    if ctx.actor_type is not ActorType.STAFF:
        msg = "only staff decide on screening matches"
        raise ForbiddenError(msg)
    if decision is Decision.SUPERSEDED:
        msg = "SUPERSEDED is not a staff decision"
        raise ValidationFailedError(msg)
    text = _rationale(rationale)
    match = _locked_match(s, match_id)
    user_id = UUID(ctx.actor_id)
    escalator, escalation_id = escalated_by(s, match.id)
    actions = evaluate_actions(
        match, escalated_by=escalator, roles=ctx.roles, user_id=user_id
    )
    if decision not in actions.allowed:
        raise DispositionNotAllowedError(actions.blocked[decision])
    answers = decision is Decision.TRUE_MATCH or (
        decision is Decision.FALSE_POSITIVE and match.status is MatchStatus.ESCALATED
    )
    policy = policy_for_run(s)
    disposition = MatchDisposition(
        tenant_id=match.tenant_id,
        match_id=match.id,
        decision=decision,
        rationale=encrypt(text, tenant_id=match.tenant_id, session=s),
        decided_by_type=DecidedByType.STAFF,
        decided_by=user_id,
        prior_disposition_id=escalation_id if answers else None,
        policy_snapshot_id=policy.snapshot_id,
    )
    s.add(disposition)
    s.flush()
    match.status = _RESULT[decision]
    if decision is not Decision.POSSIBLE_TRUE_MATCH:
        match.closed_at = clock.now()
    s.flush()
    if decision is Decision.FALSE_POSITIVE:
        _ = whitelist_entry(s, match, disposition)
    _ = record(
        s,
        AuditEvent(
            action="screening.match_dispositioned",
            entity_type="screening_match",
            entity_id=str(match.id),
            detail={"decision": decision.value, "disposition_id": str(disposition.id)},
        ),
    )
    _ = publish_screening_event(
        s,
        MATCH_DISPOSITIONED,
        entity="screening_match",
        entity_id=match.id,
        key=str(match.subject_id),
        data={
            "match_id": str(match.id),
            "decision": decision.value,
            "disposition_id": str(disposition.id),
        },
        policy_snapshot_id=policy.snapshot_id,
    )
    subject = s.get_one(ScreeningSubject, (match.tenant_id, match.subject_id))
    if decision is Decision.TRUE_MATCH:
        _publish_true_match(s, match, subject, policy.snapshot_id)
    _ = refresh_subject_status(s, subject)
    return disposition


def _publish_true_match(
    s: Session, match: ScreeningMatch, subject: ScreeningSubject, snapshot_id: UUID
) -> None:
    # The playbook action comes from the frozen snapshot, so SP-06 acts on exactly
    # the policy the decision was taken under.
    action = get_snapshot(s, snapshot_id).policy.screening.on_true_match.get(
        match.list_code
    )
    _ = publish_screening_event(
        s,
        TRUE_MATCH_CONFIRMED,
        entity="screening_match",
        entity_id=match.id,
        key=str(match.subject_id),
        data={
            "match_id": str(match.id),
            "subject_id": str(subject.id),
            "party_id": None if subject.party_id is None else str(subject.party_id),
            "list_code": match.list_code,
            "on_true_match": None if action is None else action.model_dump(mode="json"),
        },
        policy_snapshot_id=snapshot_id,
    )
