"""The counterparty name index (SP-04 spec §5.2 and §11.3; program spec D7).

One row per named counterparty, written when the row is created, so the list-delta
re-screen can block counterparties in Postgres beside parties. Like
``party_name_index`` it holds the library's normalized tokens and ``name_keys``
blocking keys only, never the name as the client sent it, and no route serializes
it. A person is normalized as a ``PERSON``; an organization or a counterparty of
unknown kind as an ``ORGANIZATION``.
"""

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from xolotl.modules.screening.counterparty.models import (
    Counterparty,
    CounterpartyNameIndex,
)
from xolotl.modules.screening.counterparty.schema import CounterpartyKind
from xolotl.modules.screening.lists import EntityType
from xolotl.modules.screening.matching import (
    MATCHER_VERSION,
    name_keys,
    normalize_name,
)
from xolotl.platform import clock
from xolotl.platform.ids import uuid7


def index_counterparty_name(
    s: Session, row: Counterparty
) -> CounterpartyNameIndex | None:
    """Upsert a counterparty's name index row, or delete it when it has no name.

    Decrypts the name for processing only (D6) and stores its normalized tokens.

    Returns:
        The row after the call, or ``None`` for a counterparty without a name.

    """
    entity_type = (
        EntityType.PERSON
        if row.kind is CounterpartyKind.PERSON
        else EntityType.ORGANIZATION
    )
    name = (
        None
        if row.name is None
        else normalize_name(row.name.reveal(session=s), entity_type=entity_type)
    )
    if name is None or not name.tokens:
        _ = s.execute(
            delete(CounterpartyNameIndex).where(
                CounterpartyNameIndex.tenant_id == row.tenant_id,
                CounterpartyNameIndex.counterparty_id == row.id,
            )
        )
        return None
    values = {
        "norm_name": name.folded,
        "tokens": [t.folded for t in name.tokens],
        "phonetic_keys": sorted(name_keys(name)),
        "matcher_version": MATCHER_VERSION,
    }
    _ = s.execute(
        insert(CounterpartyNameIndex)
        .values(tenant_id=row.tenant_id, id=uuid7(), counterparty_id=row.id, **values)
        .on_conflict_do_update(
            index_elements=["tenant_id", "counterparty_id"],
            set_={**values, "updated_at": clock.now()},
        )
    )
    return s.scalars(
        select(CounterpartyNameIndex)
        .where(
            CounterpartyNameIndex.tenant_id == row.tenant_id,
            CounterpartyNameIndex.counterparty_id == row.id,
        )
        .execution_options(populate_existing=True)
    ).one()
