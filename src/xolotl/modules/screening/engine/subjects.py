"""Screening subjects and their status (spec §8.2).

A ``PARTY`` subject is created on first use, once per party. An ``AD_HOC`` subject
stores its input encrypted, for reproducibility, until the policy's KYC retention
period has passed. A subject's status follows its findings, and a change is
published as ``screening.status_changed.v1``, except a ``COUNTERPARTY`` subject's,
which never reaches a webhook (tipping-off). A ``COUNTERPARTY`` subject's status is
written to its ``counterparty`` row whenever the row differs, the status
cache the decide path reads (SP-04 spec §11.2, plan 4c Deviation 4).
"""

from typing import cast
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from xolotl.modules.party.service import get_party
from xolotl.modules.screening.counterparty.status import sync_counterparty_status
from xolotl.modules.screening.engine.enums import (
    OPEN_MATCH_STATUSES,
    MatchStatus,
    SubjectKind,
    SubjectStatus,
)
from xolotl.modules.screening.engine.models import ScreeningMatch, ScreeningSubject
from xolotl.modules.screening.engine.policy import today_mx
from xolotl.modules.screening.engine.queries import AdHocSubjectInput
from xolotl.modules.screening.events import STATUS_CHANGED, publish_screening_event
from xolotl.platform.crypto import encrypt_json
from xolotl.platform.db import bound_context
from xolotl.platform.ids import uuid7
from xolotl.platform.policy import add_iso_duration, strictest_policy


def subject_for_party(
    s: Session, party_id: UUID, *, for_update: bool = False
) -> ScreeningSubject:
    """Return the party's subject, creating it on first use.

    Returns:
        The subject, locked when ``for_update`` is set.

    """
    party = get_party(s, party_id)
    _ = s.execute(
        insert(ScreeningSubject)
        .values(
            tenant_id=party.tenant_id,
            id=uuid7(),
            kind=SubjectKind.PARTY,
            party_id=party.id,
            status=SubjectStatus.CLEAR,
        )
        .on_conflict_do_nothing(index_elements=["tenant_id", "party_id"])
    )
    query = select(ScreeningSubject).where(ScreeningSubject.party_id == party.id)
    if for_update:
        query = query.with_for_update().execution_options(populate_existing=True)
    return s.scalars(query).one()


def create_ad_hoc_subject(s: Session, body: AdHocSubjectInput) -> ScreeningSubject:
    """Create an ad hoc subject whose input is kept encrypted until its retention.

    Returns:
        The new subject, flushed.

    """
    tenant_id = bound_context(s).require_tenant_id()
    today = today_mx()
    retention = strictest_policy(s, on=today).retention
    subject = ScreeningSubject(
        tenant_id=tenant_id,
        kind=SubjectKind.AD_HOC,
        input=encrypt_json(
            cast("dict[str, object]", body.model_dump(mode="json")),
            tenant_id=tenant_id,
            session=s,
        ),
        status=SubjectStatus.CLEAR,
        retention_until=add_iso_duration(today, retention.kyc_after_relationship.value),
    )
    s.add(subject)
    s.flush()
    return subject


def refresh_subject_status(s: Session, subject: ScreeningSubject) -> SubjectStatus:
    """Recompute a subject's status from its findings, publishing a change.

    ``TRUE_MATCH`` when any finding is a true match, else ``PENDING_REVIEW`` when
    any still needs a decision, else ``CLEAR``.

    Returns:
        The subject's status now.

    """
    statuses = set(
        s.scalars(
            select(ScreeningMatch.status)
            .where(ScreeningMatch.subject_id == subject.id)
            .distinct()
        )
    )
    if MatchStatus.TRUE_MATCH in statuses:
        status = SubjectStatus.TRUE_MATCH
    elif statuses & OPEN_MATCH_STATUSES:
        status = SubjectStatus.PENDING_REVIEW
    else:
        status = SubjectStatus.CLEAR
    if subject.counterparty_id is not None:
        # Before the early return: a first run that stays CLEAR still moves the row
        # from UNSCREENED.
        _ = sync_counterparty_status(
            s,
            counterparty_id=subject.counterparty_id,
            subject_id=subject.id,
            status=status.value,
        )
    if status == subject.status:
        return status
    subject.status = status
    s.flush()
    if subject.counterparty_id is not None:
        # A counterparty's status stays off webhooks (tipping-off, SP-04 spec §15);
        # the staff-only screening.counterparty_status_changed.v1 carries it.
        return status
    _ = publish_screening_event(
        s,
        STATUS_CHANGED,
        entity="screening_subject",
        entity_id=subject.id,
        key=str(subject.id),
        data={
            "subject_id": str(subject.id),
            "party_id": None if subject.party_id is None else str(subject.party_id),
            "status": status.value,
        },
    )
    return status
