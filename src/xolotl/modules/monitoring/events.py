"""The monitoring events: ids and counts only (SP-04 spec §14).

Plan 4a's batch and file events may reach a customer's webhook.
``STAFF_ONLY_EVENT_TYPES`` never does: the composition root registers each with
``register_staff_only``. ``TRANSACTION_PROCESSED`` is among them by plan 4a
Deviation 7.

Plan 4b's types: every one but ``TIER_LIMIT_REACHED`` is staff-only (a hit, a
capture, a period or a rule change must never reach a webhook, PRV-502), and
``RULES_STAFF_ONLY_EVENT_TYPES`` is the tuple ``register_rules`` marks with
``register_staff_only``. ``TIER_LIMIT_REACHED`` is webhook-safe: ids and codes,
never an amount.
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

RULE_HIT_CREATED = "monitoring.rule_hit_created.v1"
TIER_LIMIT_REACHED = "monitoring.tier_limit_reached.v1"
REPORTABLE_EVENT_CAPTURED = "monitoring.reportable_event_captured.v1"
REPORTABLE_EVENT_SUPERSEDED = "monitoring.reportable_event_superseded.v1"
CAPTURE_PERIOD_CLOSED = "monitoring.capture_period_closed.v1"
CAPTURE_PERIOD_CHANGED = "monitoring.capture_period_changed.v1"
RULE_VERSION_ACTIVATED = "monitoring.rule_version_activated.v1"
RULE_VERSION_RETIRED = "monitoring.rule_version_retired.v1"
SIMULATION_COMPLETED = "monitoring.simulation_completed.v1"

# A separate tuple beside plan 4a's STAFF_ONLY_EVENT_TYPES (reconciliation finding
# 14): B7's register_rules registers it, register_monitoring registers 4a's.
RULES_STAFF_ONLY_EVENT_TYPES: tuple[str, ...] = (
    RULE_HIT_CREATED,
    REPORTABLE_EVENT_CAPTURED,
    REPORTABLE_EVENT_SUPERSEDED,
    CAPTURE_PERIOD_CLOSED,
    CAPTURE_PERIOD_CHANGED,
    RULE_VERSION_ACTIVATED,
    RULE_VERSION_RETIRED,
    SIMULATION_COMPLETED,
)


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
