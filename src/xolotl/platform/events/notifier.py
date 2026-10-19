"""In-app notifications: the ``Notifier`` port and its adapter (program spec §4).

A notification is one ``notification`` row for one staff user or for every holder of
one role, written in the caller's transaction, so it exists exactly when the change
that caused it commits. ``template`` names what the console shows and ``data`` holds
what it needs to show it: ids, never personal data, the same rule as event payloads
(D8). The notifier stores the two as given and adds nothing to them; the log line it
writes names the notice and its recipient, never the data.

A notice addressed to a role is one shared notice, since the row has one
``read_at``: once a holder of the role marks it read, it is read for all of them.
Email and chat channels are later adapters of the same port; ``Notifier`` states the
contract they keep.
"""

import logging
from collections.abc import Iterable
from typing import Protocol
from uuid import UUID

from pydantic import JsonValue
from sqlalchemy import ColumnElement, or_, select
from sqlalchemy.orm import Session

from xolotl.platform import clock
from xolotl.platform.db import bound_context
from xolotl.platform.enums import Role
from xolotl.platform.errors import NotFoundError
from xolotl.platform.events.models import Notification

logger = logging.getLogger(__name__)


class Notifier(Protocol):
    """Tell a staff user, or every holder of a role, that something happened.

    The contract every adapter keeps:

    * Transactional. The notice belongs to the caller's transaction: it exists
      exactly when the caller's change commits and goes with it when that rolls back.
    * A failure is the caller's failure, by design. A notice with no recipient or
      two (``ValueError``), a session bound to no tenant (``ContextMissingError``)
      or a write that fails raises into the caller, and so fails the request or the
      job that called it: no change is committed without the notice it promised,
      and no bug in a caller is hidden. A caller that wants a best-effort notice
      says so itself, with a savepoint around the call.
    * No network inside ``notify``. An adapter that reaches the outside (email,
      chat) records the notice or enqueues a job in the same transaction and
      delivers from the worker, so a slow server never holds a request or its
      transaction.
    """

    def notify(
        self,
        session: Session,
        *,
        template: str,
        data: dict[str, JsonValue],
        recipient_user_id: UUID | None = None,
        recipient_role: Role | None = None,
    ) -> UUID: ...


class InAppNotifier:
    """The ``Notifier`` port as rows of the ``notification`` table, plus a log line."""

    @staticmethod
    def notify(
        session: Session,
        *,
        template: str,
        data: dict[str, JsonValue],
        recipient_user_id: UUID | None = None,
        recipient_role: Role | None = None,
    ) -> UUID:
        """Write a notice for the session's tenant in the caller's transaction.

        Exactly one of ``recipient_user_id`` (a ``staff_user.id``) and
        ``recipient_role`` names who receives it. ``created_at`` comes from the
        platform clock, like the ``read_at`` that ``mark_read`` sets later.

        Returns:
            The new notification's id.

        Raises:
            ValueError: When there is no recipient, or there are two.

        """
        if (recipient_user_id is None) == (recipient_role is None):
            msg = "a notification needs exactly one recipient: a user or a role"
            raise ValueError(msg)
        tenant_id = bound_context(session).require_tenant_id()
        row = Notification(
            tenant_id=tenant_id,
            recipient_user_id=recipient_user_id,
            recipient_role=None if recipient_role is None else recipient_role.value,
            template=template,
            data=data,
            created_at=clock.now(),
        )
        session.add(row)
        session.flush()
        logger.info(
            "notification created",
            extra={
                "notification_id": str(row.id),
                "template": template,
                "recipient_user_id": (
                    None if recipient_user_id is None else str(recipient_user_id)
                ),
                "recipient_role": row.recipient_role,
            },
        )
        return row.id


def addressed_to(user_id: UUID, roles: Iterable[Role]) -> ColumnElement[bool]:
    """Match the notices a staff user receives: their own and their roles' ones.

    Returns:
        A condition on ``Notification`` for a ``WHERE`` clause.

    """
    names = sorted(role.value for role in roles)
    return or_(
        Notification.recipient_user_id == user_id,
        Notification.recipient_role.in_(names),
    )


def mark_read(
    session: Session, notification_id: UUID, *, user_id: UUID, roles: Iterable[Role]
) -> Notification:
    """Mark a notice the user receives as read; marking it again changes nothing.

    The row is locked first, so two requests at once agree on the first ``read_at``.

    Returns:
        The notice, read.

    Raises:
        NotFoundError: When the session's tenant has no such notice, or the user
            does not receive it. The two are not told apart.

    """
    row = session.scalars(
        select(Notification)
        .where(Notification.id == notification_id, addressed_to(user_id, roles))
        .with_for_update()
    ).one_or_none()
    if row is None:
        raise NotFoundError
    if row.read_at is None:
        row.read_at = clock.now()
        session.flush()
    return row
