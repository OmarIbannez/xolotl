"""Customer relationships, the retention clock their closure starts, and legal hold.

Closing a relationship resolves the tenant's effective policy on the closing date,
snapshots it, and moves the party's ``retention_until`` to the end of the policy's
``kyc_after_relationship`` period, never earlier than it already was. Opening one
resolves the same policy (and discards it), so a relationship that could never be
closed is never opened.

The declared profile of a relationship (SP-04 spec §13.3) is what the customer said
it would move in a month; monitoring's behaviour profile and ``PROFILE_DEVIATION``
compare the actual volume with it. Its audit row names the currency, the fields
that changed and whether it was cleared, never an amount.
"""

from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from xolotl.modules.party.enums import CustomerRelationshipStatus, PartyStatus
from xolotl.modules.party.events import (
    RELATIONSHIP_CLOSED,
    RELATIONSHIP_OPENED,
    publish_party_event,
)
from xolotl.modules.party.models import CustomerRelationship, Party
from xolotl.modules.party.schemas import DeclaredProfile, RelationshipOpenRequest
from xolotl.modules.party.service import get_party
from xolotl.platform import clock
from xolotl.platform.audit import AuditEvent, record
from xolotl.platform.db import bound_context
from xolotl.platform.errors import DomainError, NotFoundError, ValidationFailedError
from xolotl.platform.policy import add_iso_duration, effective_policy, snapshot_policy

if TYPE_CHECKING:
    from pydantic import JsonValue

OPEN_STATUSES: frozenset[CustomerRelationshipStatus] = frozenset({
    CustomerRelationshipStatus.PENDING,
    CustomerRelationshipStatus.ACTIVE,
    CustomerRelationshipStatus.RESTRICTED,
    CustomerRelationshipStatus.BLOCKED,
})


def _aware(value: datetime | None, name: str) -> datetime:
    if value is None:
        return clock.now()
    if value.tzinfo is None:
        msg = f"{name} must carry a timezone"
        raise ValidationFailedError(msg)
    return value


def open_relationship(
    s: Session, body: RelationshipOpenRequest
) -> CustomerRelationship:
    """Open an ``ACTIVE`` customer relationship of a party.

    A tenant whose pack assignments do not cover ``body.product`` on the start
    date raises ``PolicyNotAssignedError`` (404 ``POLICY_NOT_ASSIGNED``) and opens
    nothing. A naive ``started_at`` raises ``ValidationFailedError``.

    Returns:
        The new relationship, flushed.

    Raises:
        DomainError: When the party is purged or the external reference is taken.

    """
    party = get_party(s, body.party_id)
    if party.status is PartyStatus.PURGED:
        msg = "party is purged"
        raise DomainError(msg)
    taken = s.scalar(
        select(CustomerRelationship.id).where(
            CustomerRelationship.external_ref == body.external_ref
        )
    )
    if taken is not None:
        msg = "external_ref already exists"
        raise DomainError(msg)
    started = _aware(body.started_at, "started_at")
    _ = effective_policy(s, on=started.date(), product=body.product)
    row = CustomerRelationship(
        tenant_id=party.tenant_id,
        party_id=party.id,
        external_ref=body.external_ref,
        product=body.product,
        status=CustomerRelationshipStatus.ACTIVE,
        account_tier=body.account_tier,
        started_at=started,
    )
    s.add(row)
    s.flush()
    _ = record(
        s,
        AuditEvent(
            action="customer_relationship.opened",
            entity_type="customer_relationship",
            entity_id=str(row.id),
            detail={"party_id": str(party.id)},
        ),
    )
    _ = publish_party_event(
        s,
        RELATIONSHIP_OPENED,
        entity="party",
        entity_id=party.id,
        data={"relationship_id": str(row.id), "party_id": str(party.id)},
    )
    return row


def close_relationship(
    s: Session, relationship_id: UUID, *, ended_at: datetime | None = None
) -> CustomerRelationship:
    """Exit a customer relationship and start the party's retention clock.

    Closing an ``EXITED`` relationship returns it unchanged. A tenant with no pack
    assignment covering the closing date raises ``PolicyNotAssignedError`` (404
    ``POLICY_NOT_ASSIGNED``) and the relationship stays open; a naive ``ended_at``
    raises ``ValidationFailedError``.

    Returns:
        The relationship.

    Raises:
        NotFoundError: When the tenant has no such relationship.

    """
    tenant_id = bound_context(s).require_tenant_id()
    row = s.get(
        CustomerRelationship,
        (tenant_id, relationship_id),
        with_for_update=True,
        populate_existing=True,
    )
    if row is None:
        msg = "relationship not found"
        raise NotFoundError(msg)
    if row.status is CustomerRelationshipStatus.EXITED:
        return row
    ended = _aware(ended_at, "ended_at")
    effective = effective_policy(s, on=ended.date(), product=row.product)
    snapshot_id = snapshot_policy(s, effective)
    until = add_iso_duration(
        ended.date(), effective.retention.kyc_after_relationship.value
    )
    party = get_party(s, row.party_id, for_update=True)
    if party.retention_until is None or party.retention_until < until:
        party.retention_until = until
    row.status = CustomerRelationshipStatus.EXITED
    row.ended_at = ended
    s.flush()
    _ = record(
        s,
        AuditEvent(
            action="customer_relationship.closed",
            entity_type="customer_relationship",
            entity_id=str(row.id),
            detail={
                "party_id": str(party.id),
                "retention_until": until.isoformat(),
                "policy_snapshot_id": str(snapshot_id),
            },
        ),
    )
    _ = publish_party_event(
        s,
        RELATIONSHIP_CLOSED,
        entity="party",
        entity_id=party.id,
        data={
            "relationship_id": str(row.id),
            "party_id": str(party.id),
            "retention_until": until.isoformat(),
        },
        policy_snapshot_id=snapshot_id,
    )
    return row


def set_legal_hold(s: Session, party_id: UUID, *, legal_hold: bool) -> Party:
    """Put a party on legal hold, or release it; the sweep never purges a held one.

    Setting the value the party already has records nothing.

    Returns:
        The party.

    """
    party = get_party(s, party_id, for_update=True)
    if party.legal_hold is legal_hold:
        return party
    party.legal_hold = legal_hold
    party.updated_at = clock.now()
    s.flush()
    _ = record(
        s,
        AuditEvent(
            action="party.legal_hold_set",
            entity_type="party",
            entity_id=str(party.id),
            detail={"legal_hold": legal_hold},
        ),
    )
    return party


def set_declared_profile(
    s: Session, relationship_id: UUID, profile: DeclaredProfile | None
) -> CustomerRelationship:
    """Set or clear (None) a relationship's declared profile, audited on change.

    The audit row names the currency, the fields that changed and whether the
    profile was cleared, never a value; a write that changes nothing records
    nothing.

    Returns:
        The relationship.

    Raises:
        NotFoundError: When the tenant has no such relationship.

    """
    tenant_id = bound_context(s).require_tenant_id()
    row = s.get(
        CustomerRelationship,
        (tenant_id, relationship_id),
        with_for_update=True,
        populate_existing=True,
    )
    if row is None:
        msg = "relationship not found"
        raise NotFoundError(msg)
    before = row.declared_profile or {}
    after: dict[str, JsonValue] = (
        {} if profile is None else profile.model_dump(mode="json")
    )
    changed: list[JsonValue] = [
        name
        for name in sorted(before.keys() | after.keys())
        if before.get(name) != after.get(name)
    ]
    if not changed:
        return row
    row.declared_profile = None if profile is None else after
    s.flush()
    _ = record(
        s,
        AuditEvent(
            action="relationship.declared_profile_set",
            entity_type="customer_relationship",
            entity_id=str(row.id),
            detail={
                "currency": None if profile is None else profile.currency,
                "changed": changed,
                "cleared": profile is None,
            },
        ),
    )
    return row
