"""The screening events: ids and codes only, never a name or a score (D8, spec §11).

``screening.run_completed.v1`` and ``screening.status_changed.v1`` may reach a
customer's webhook. The rest tell staff what a screening found and are staff-only
(FR-SC-013, PRV-303): ``register_screening`` marks every type of
``STAFF_ONLY_EVENT_TYPES`` with ``register_staff_only``, so no webhook carries them.
"""

from uuid import UUID

from pydantic import JsonValue
from sqlalchemy.orm import Session

from xolotl.platform import clock
from xolotl.platform.db import bound_context
from xolotl.platform.events import Event, publish
from xolotl.platform.ids import uuid7

EVENT_SOURCE = "xolotl.screening"
RUN_COMPLETED = "screening.run_completed.v1"
STATUS_CHANGED = "screening.status_changed.v1"
MATCH_CREATED = "screening.match_created.v1"
MATCH_DISPOSITIONED = "screening.match_dispositioned.v1"
TRUE_MATCH_CONFIRMED = "screening.true_match_confirmed.v1"
MATCH_DELISTING_SUGGESTED = "screening.match_delisting_suggested.v1"
PRIVATE_LIST_VERSION_ACTIVATED = "private_list.version_activated.v1"
# One per tenant per shared list version handed to re-screening (the catch-up of
# ``lists.poll``); webhook-safe.
LISTS_VERSION_PUBLISHED = "lists.version_published.v1"

STAFF_ONLY_EVENT_TYPES: tuple[str, ...] = (
    PRIVATE_LIST_VERSION_ACTIVATED,
    MATCH_CREATED,
    MATCH_DISPOSITIONED,
    TRUE_MATCH_CONFIRMED,
    MATCH_DELISTING_SUGGESTED,
)


def publish_screening_event(
    s: Session,
    event_type: str,
    *,
    entity: str,
    entity_id: UUID,
    key: str,
    data: dict[str, JsonValue],
    policy_snapshot_id: UUID | None = None,
) -> Event:
    """Publish a screening event about one entity in the caller's transaction.

    The subject names the entity; ``key`` orders a consumer's deliveries, so the
    engine passes the subject's id and one subject's events arrive in order. Inside an
    HTTP request the request id becomes the correlation id.

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
