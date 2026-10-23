"""Migration 0015: ``customer_relationship.first_processed_at``.

The tests move the session database back to 0014 and forward again with real
commits; the ``database`` fixture always brings it back to head afterwards, so the
rest of the session still sees a database at head.
"""

from collections.abc import Generator
from datetime import UTC, datetime
from uuid import UUID

import pytest
from alembic import command
from alembic.script import ScriptDirectory
from sqlalchemy import Engine, inspect, text, update

from tests.helpers import new_tenant, staff_session, system_ctx
from tests.modules.monitoring.support import add_customer, ingest_items, make_item
from xolotl.modules.monitoring.enums import TransactionStatus
from xolotl.modules.monitoring.txn.models import Transaction
from xolotl.modules.tenants.service import CreatedTenant
from xolotl.platform import migrate
from xolotl.platform.db import get_sessionmaker, tenant_session
from xolotl.platform.jobs.queue import PostgresJobQueue

REVISION = "0015_first_processed"
BEFORE = "0014_decide_profiles"
FIRST = datetime(2026, 3, 4, 15, tzinfo=UTC)
SECOND = datetime(2026, 5, 6, 15, tzinfo=UTC)


def go_down(revision: str) -> None:
    command.downgrade(migrate.alembic_config(), revision)


def has_column(engine: Engine) -> bool:
    with engine.connect() as conn:
        columns = inspect(conn).get_columns("customer_relationship")
    return "first_processed_at" in {column["name"] for column in columns}


# The test database, which a test may move back in time; it always ends at head.
@pytest.fixture
def database(admin_engine: Engine) -> Generator[Engine]:
    try:
        yield admin_engine
    finally:
        migrate.upgrade()


@pytest.fixture
def queue(engine: Engine) -> PostgresJobQueue:
    return PostgresJobQueue(get_sessionmaker())


def test_0015_follows_0014():
    script = ScriptDirectory.from_config(migrate.alembic_config())
    revision = script.get_revision(REVISION)
    assert revision is not None
    assert revision.down_revision == BEFORE


def _seed(tenant: CreatedTenant, queue: PostgresJobQueue, ref: str) -> UUID:
    """Give ``tenant`` a customer ``ref`` with two processed rows and a received one.

    Returns:
        The relationship's id.

    """
    relationship = add_customer(tenant, ref)
    items = [
        make_item(external_id=f"{ref}-1", occurred_at=SECOND.isoformat()),
        make_item(external_id=f"{ref}-2", occurred_at=FIRST.isoformat()),
        make_item(
            external_id=f"{ref}-3",
            occurred_at=datetime(2026, 1, 2, tzinfo=UTC).isoformat(),
        ),
    ]
    for item in items:
        item["customer_ref"] = ref
    with staff_session(tenant.tenant_id, tenant.admin_user_id) as s:
        report = ingest_items(s, items, queue=queue)
        processed = list(report.transaction_ids[:2])
        _ = s.execute(
            update(Transaction)
            .where(Transaction.id.in_(processed))
            .values(status=TransactionStatus.PROCESSED)
        )
    return relationship.id


def _first(tenant: CreatedTenant, relationship_id: UUID) -> datetime | None:
    with tenant_session(system_ctx(tenant.tenant_id)) as s:
        return s.scalar(
            text("SELECT first_processed_at FROM customer_relationship WHERE id = :id"),
            {"id": relationship_id},
        )


def test_upgrade_backfills_every_tenants_earliest_processed_row_and_downgrade_drops(
    database: Engine, tenant: CreatedTenant, queue: PostgresJobQueue
):
    other = new_tenant("otra", "admin@otra.mx")
    ours = _seed(tenant, queue, "cust-1")
    theirs = _seed(other, queue, "cust-1")
    idle = add_customer(tenant, "cust-idle").id

    go_down(BEFORE)
    assert not has_column(database)
    migrate.upgrade()
    assert has_column(database)

    # The earliest PROCESSED row; the older RECEIVED one is not processed yet.
    assert _first(tenant, ours) == FIRST
    assert _first(other, theirs) == FIRST
    assert _first(tenant, idle) is None
