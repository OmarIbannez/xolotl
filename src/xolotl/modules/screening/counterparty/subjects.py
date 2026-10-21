"""A counterparty's screening subject (SP-04 spec §11.2).

A ``COUNTERPARTY`` subject is created on first use, once per counterparty, and
stores no input: the ``counterparty`` row is the input, which the kind check of
migration 0013 enforces. The row points back at its subject through
``screening_subject_id``, which is also how the first-sight job tells a screened
counterparty from one never screened.
"""

from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from xolotl.modules.screening.counterparty.models import Counterparty
from xolotl.modules.screening.engine.enums import SubjectKind, SubjectStatus
from xolotl.modules.screening.engine.models import ScreeningSubject
from xolotl.platform.db import bound_context
from xolotl.platform.ids import uuid7


def subject_for_counterparty(
    s: Session, counterparty_id: UUID, *, for_update: bool = False
) -> ScreeningSubject:
    """Return the counterparty's subject, creating it and linking the row on first use.

    Returns:
        The subject, locked when ``for_update`` is set.

    """
    tenant_id = bound_context(s).require_tenant_id()
    _ = s.execute(
        insert(ScreeningSubject)
        .values(
            tenant_id=tenant_id,
            id=uuid7(),
            kind=SubjectKind.COUNTERPARTY,
            counterparty_id=counterparty_id,
            status=SubjectStatus.CLEAR,
        )
        .on_conflict_do_nothing(index_elements=["tenant_id", "counterparty_id"])
    )
    query = select(ScreeningSubject).where(
        ScreeningSubject.tenant_id == tenant_id,
        ScreeningSubject.counterparty_id == counterparty_id,
    )
    if for_update:
        query = query.with_for_update().execution_options(populate_existing=True)
    subject = s.scalars(query).one()
    _ = s.execute(
        update(Counterparty)
        .where(
            Counterparty.tenant_id == tenant_id,
            Counterparty.id == counterparty_id,
            Counterparty.screening_subject_id.is_(None),
        )
        .values(screening_subject_id=subject.id)
        .execution_options(synchronize_session="fetch")
    )
    return subject
