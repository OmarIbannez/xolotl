"""The monitoring events: ids and counts only (SP-04 spec §14).

The batch and file events may reach a customer's webhook. ``STAFF_ONLY_EVENT_TYPES``
never does: the composition root registers each with ``register_staff_only``.
``TRANSACTION_PROCESSED`` is among them by plan 4a Deviation 7.
"""

from uuid import UUID

from pydantic import JsonValue
from sqlalchemy.orm import Session

from xolotl.platform import clock
from xolotl.platform.db import bound_context
from xolotl.platform.events import Event, publish
from xolotl.platform.ids import uuid7

EVENT_SOURCE = "xolotl.monitoring"
TRANSACTION_BATCH_ACCEPTED = "monitoring.transaction_batch_accepted.v1"
TRANSACTION_FILE_COMPLETED = "monitoring.transaction_file_completed.v1"
TRANSACTION_PROCESSED = "monitoring.transaction_processed.v1"
TRANSACTION_PARKED = "monitoring.transaction_parked.v1"

STAFF_ONLY_EVENT_TYPES: tuple[str, ...] = (TRANSACTION_PARKED, TRANSACTION_PROCESSED)


def publish_monitoring_event(
    s: Session,
    event_type: str,
    *,
    entity: str,
    entity_id: UUID,
    key: str,
    data: dict[str, JsonValue],
    policy_snapshot_id: UUID | None = None,
) -> Event:
    """Publish a monitoring event about one entity in the caller's transaction.

    ``key`` orders a consumer's deliveries; inside an HTTP request the request id
    becomes the correlation id.

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
        key=key,
        correlation_id=ctx.request_id or None,
        policy_snapshot_id=policy_snapshot_id,
        data=data,
    )
    publish(s, event)
    return event
