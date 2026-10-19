"""The ``party.*`` events: ids and codes only, never a personal value (D8)."""

from uuid import UUID

from pydantic import JsonValue
from sqlalchemy.orm import Session

from xolotl.platform import clock
from xolotl.platform.db import bound_context
from xolotl.platform.events import Event, publish
from xolotl.platform.ids import uuid7

EVENT_SOURCE = "xolotl.party"
PARTY_CREATED = "party.created.v1"
PARTY_UPDATED = "party.updated.v1"
IDENTIFIER_ADDED = "party.identifier_added.v1"
RELATIONSHIP_OPENED = "party.relationship_opened.v1"
RELATIONSHIP_CLOSED = "party.relationship_closed.v1"
PARTY_PURGED = "party.purged.v1"


def publish_party_event(
    s: Session,
    event_type: str,
    *,
    entity: str,
    entity_id: UUID,
    data: dict[str, JsonValue],
    policy_snapshot_id: UUID | None = None,
) -> Event:
    """Publish a party event about one entity in the caller's transaction.

    Keyed by the entity's id, so each consumer gets one entity's events in order;
    the subject names the entity. Inside an HTTP request the request id becomes the
    correlation id.

    Returns:
        The event as published.

    """
    ctx = bound_context(s)
    event = Event(
        id=uuid7(),
        type=event_type,
        source=EVENT_SOURCE,
        tenant_id=ctx.require_tenant_id(),
        subject=f"{entity}/{entity_id}",
        time=clock.now(),
        key=str(entity_id),
        correlation_id=ctx.request_id or None,
        policy_snapshot_id=policy_snapshot_id,
        data=data,
    )
    publish(s, event)
    return event
