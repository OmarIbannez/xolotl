"""The counterparty row as the synchronous status cache (SP-04 spec §11.2, DR-308).

The decide path and the enricher read ``counterparty.screening_status`` without
waiting for a screening, so the row must hold the status its subject holds. The
write-back follows every recomputation of a ``COUNTERPARTY`` subject's status, after a
run and after a disposition alike (plan 4c Deviation 4): a ``TRUE_MATCH`` confirmed
days after the run reaches the row before the next transaction is evaluated. This
module imports nothing from ``screening.engine``, which calls it.
"""

from uuid import UUID

from sqlalchemy import update
from sqlalchemy.orm import Session

from xolotl.modules.screening.counterparty.models import Counterparty
from xolotl.modules.screening.counterparty.schema import CounterpartyScreeningStatus
from xolotl.modules.screening.events import (
    COUNTERPARTY_STATUS_CHANGED,
    publish_screening_event,
)
from xolotl.platform import clock
from xolotl.platform.db import bound_context


def sync_counterparty_status(
    s: Session, *, counterparty_id: UUID, subject_id: UUID, status: str
) -> bool:
    """Write a subject's status to its counterparty row when the row differs.

    A change publishes the staff-only ``screening.counterparty_status_changed.v1``,
    keyed by the subject so it orders with the subject's other events. The event
    carries ids and the status only.

    Returns:
        Whether the row changed.

    """
    value = CounterpartyScreeningStatus(status)
    changed = s.execute(
        update(Counterparty)
        .where(
            Counterparty.tenant_id == bound_context(s).require_tenant_id(),
            Counterparty.id == counterparty_id,
            Counterparty.screening_status != value,
        )
        .values(screening_status=value)
        .returning(Counterparty.id)
        .execution_options(synchronize_session="fetch")
    ).first()
    if changed is None:
        return False
    _ = publish_screening_event(
        s,
        COUNTERPARTY_STATUS_CHANGED,
        entity="counterparty",
        entity_id=counterparty_id,
        key=str(subject_id),
        data={
            "counterparty_id": str(counterparty_id),
            "subject_id": str(subject_id),
            "status": value.value,
        },
    )
    return True


def mark_screened(s: Session, counterparty_id: UUID) -> None:
    """Stamp a counterparty's ``screened_at`` with the application clock."""
    _ = s.execute(
        update(Counterparty)
        .where(
            Counterparty.tenant_id == bound_context(s).require_tenant_id(),
            Counterparty.id == counterparty_id,
        )
        .values(screened_at=clock.now())
        .execution_options(synchronize_session="fetch")
    )
