"""Events through the transactional outbox, and in-app notifications (spec §9, D8).

A service publishes with ``publish(session, event)`` in its own transaction, the way
it records an audit event with ``audit.record``: the outbox row exists exactly when
the change that caused it commits. The outbox is the kernel's event store, whatever
carries events further later, so publishing needs no adapter instance. Subscribers
are registered at composition time and run in the worker, one transaction each.
"""

from xolotl.platform.events.bus import OutboxEventBus, publish
from xolotl.platform.events.event import Event
from xolotl.platform.events.notifier import InAppNotifier, Notifier
from xolotl.platform.events.registry import (
    EventTypeNotDeliverableError,
    Handler,
    is_staff_only,
    register_staff_only,
    register_subscriber,
    require_deliverable,
)

__all__ = [
    "Event",
    "EventTypeNotDeliverableError",
    "Handler",
    "InAppNotifier",
    "Notifier",
    "OutboxEventBus",
    "is_staff_only",
    "publish",
    "register_staff_only",
    "register_subscriber",
    "require_deliverable",
]
