"""Who listens to which event type (spec §9.2).

Subscribers are registered at composition time (``app.py`` and ``worker.py``) on one
process-wide table, so the API process and the worker see the same subscribers
without any import having a side effect. A subscription is a ``(event type,
consumer)`` pair: the consumer name is what the dispatcher records in
``event_consumption`` to make redelivery a no-op, so one consumer holds at most one
handler for any given event type.

Some event types are staff-only (FR-SC-013, PRV-303): they tell staff what a screening
found, and a customer who heard of it could be tipped off. A module marks such a type
with ``register_staff_only`` at composition time. The ``webhooks`` consumer never fans
one out, not even to an endpoint that subscribes to ``"*"``, and creating an endpoint
that names one is refused with ``EVENT_TYPE_NOT_DELIVERABLE``
(``require_deliverable``). In-process subscribers receive them like any other type.
"""

from collections.abc import Callable, Iterable
from typing import ClassVar

from sqlalchemy.orm import Session

from xolotl.platform.errors import ValidationFailedError
from xolotl.platform.events.event import Event

Handler = Callable[[Session, Event], None]

WILDCARD = "*"

# (event type, consumer) -> handler. Dict order is registration order, and replacing
# a key keeps its place.
_subscriptions: dict[tuple[str, str], Handler] = {}

# Event types webhooks never deliver.
_staff_only: set[str] = set()


class EventTypeNotDeliverableError(ValidationFailedError):
    default_code: ClassVar[str] = "EVENT_TYPE_NOT_DELIVERABLE"
    default_detail: ClassVar[str] = "event type is not deliverable to webhooks"


def register_subscriber(event_type: str, handler: Handler, *, consumer: str) -> None:
    """Subscribe ``consumer`` to ``event_type``; ``"*"`` subscribes it to every type.

    Registering the same consumer for the same type again replaces its handler and
    keeps its place in the order, so composing the application twice in one process
    leaves one subscription. A handler runs inside the dispatcher's transaction, with
    its own copy of the event, and must leave that transaction as it found it: it
    must not commit it, roll it back, or swallow a database error and carry on
    (use a savepoint, ``session.begin_nested()``, to ignore an expected error). The
    dispatcher checks that its record of the delivery survived, and counts a handler
    that discarded the transaction as failed.

    Raises:
        ValueError: When ``consumer`` would hold both a ``"*"`` subscription and an
            exact one. Both would match some event, and the second handler would
            never run because the first records the consumer's consumption.

    """
    for registered_type, registered_consumer in _subscriptions:
        if (
            registered_consumer == consumer
            and registered_type != event_type
            and WILDCARD in {registered_type, event_type}
        ):
            msg = f"consumer {consumer!r} cannot subscribe to both '*' and exact types"
            raise ValueError(msg)
    _subscriptions[event_type, consumer] = handler


def subscribers_for(event_type: str) -> list[tuple[str, Handler]]:
    """List who receives events of ``event_type``.

    Returns:
        ``(consumer, handler)`` pairs in registration order, exact and ``"*"``
        subscriptions interleaved as they were registered.

    """
    return [
        (consumer, handler)
        for (registered_type, consumer), handler in tuple(_subscriptions.items())
        if registered_type in {event_type, WILDCARD}
    ]


def consumers() -> frozenset[str]:
    """List every consumer with at least one subscription.

    Returns:
        The registered consumer names.

    """
    return frozenset(consumer for _, consumer in tuple(_subscriptions))


def reset_subscribers() -> None:
    """Forget every subscription (tests)."""
    _subscriptions.clear()


def register_staff_only(event_type: str) -> None:
    """Mark ``event_type`` as staff-only: webhooks never deliver it.

    Marking a type again is harmless, so composing the application twice is too.

    Raises:
        ValueError: When ``event_type`` is ``"*"`` or blank. A wildcard cannot be
            staff-only, or no event would reach any webhook.

    """
    if not event_type.strip() or event_type == WILDCARD:
        msg = f"{event_type!r} cannot be a staff-only event type"
        raise ValueError(msg)
    _staff_only.add(event_type)


def is_staff_only(event_type: str) -> bool:
    """Tell whether ``event_type`` is staff-only.

    Returns:
        True when a module marked it with ``register_staff_only``.

    """
    return event_type in _staff_only


def require_deliverable(event_types: Iterable[str]) -> None:
    """Refuse a list of event types that names a staff-only one.

    Raises:
        EventTypeNotDeliverableError: Naming the first staff-only type, in sorted
            order (422).

    """
    for event_type in sorted(set(event_types)):
        if is_staff_only(event_type):
            msg = f"{event_type} is staff-only"
            raise EventTypeNotDeliverableError(msg)
