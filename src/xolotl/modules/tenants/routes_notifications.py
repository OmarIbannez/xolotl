"""The signed-in staff user's in-app notifications (plan 1c; the console's shell).

A user receives the notices addressed to them and to every role they hold (see
``xolotl.platform.events.notifier``). Machines have no inbox: both routes are for
staff only. Marking a notice read is a write, so ``AUDITOR_RO`` may list its inbox
but not mark it.
"""

from typing import Annotated
from uuid import UUID

from fastapi import Depends
from sqlalchemy import select

from xolotl.modules.tenants.schemas import NotificationResponse
from xolotl.platform.api.pagination import Page, PageDep, paginate
from xolotl.platform.api.router import v1_router
from xolotl.platform.auth.actor import Actor
from xolotl.platform.auth.principal import SessionDep, require_access
from xolotl.platform.events.models import Notification
from xolotl.platform.events.notifier import addressed_to, mark_read

router = v1_router(prefix="/notifications", tags=["notifications"])

# No scope: an API client is refused, whatever its scopes.
StaffOnly = Annotated[Actor, Depends(require_access())]


@router.get("")
def list_notifications(
    actor: StaffOnly, session: SessionDep, page: PageDep
) -> Page[NotificationResponse]:
    rows, next_cursor = paginate(
        session,
        select(Notification).where(
            addressed_to(UUID(actor.context.actor_id), actor.context.roles)
        ),
        ts_col=Notification.created_at,
        id_col=Notification.id,
        key=lambda row: (row.created_at, row.id),
        page=page,
    )
    return Page(
        items=[NotificationResponse.model_validate(row) for row in rows],
        next_cursor=next_cursor,
    )


@router.post("/{notification_id}/read")
def read_notification(
    notification_id: UUID, actor: StaffOnly, session: SessionDep
) -> NotificationResponse:
    row = mark_read(
        session,
        notification_id,
        user_id=UUID(actor.context.actor_id),
        roles=actor.context.roles,
    )
    return NotificationResponse.model_validate(row)
