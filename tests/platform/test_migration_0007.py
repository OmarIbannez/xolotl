"""Migration 0007: the ``connector_credential`` and ``connector_call`` tables.

The tests move the session database back to 0006 and forward again with real
commits; the ``database`` fixture always brings it back to head afterwards, so the
rest of the session still sees a database at head.
"""

from collections.abc import Generator

import pytest
from alembic import command
from alembic.script import ScriptDirectory
from sqlalchemy import Engine, inspect, text

from xolotl.platform import migrate

REVISION = "0007_connectors"
BEFORE = "0006_policy_units_calendar"
TABLES = ("connector_credential", "connector_call")


def go_down(revision: str) -> None:
    command.downgrade(migrate.alembic_config(), revision)


def tables_present(engine: Engine) -> set[str]:
    with engine.connect() as conn:
        return set(inspect(conn).get_table_names()) & set(TABLES)


# The test database, which a test may move back in time; it always ends at head.
@pytest.fixture
def database(admin_engine: Engine) -> Generator[Engine]:
    try:
        yield admin_engine
    finally:
        migrate.upgrade()


def test_0007_follows_0006():
    script = ScriptDirectory.from_config(migrate.alembic_config())
    revision = script.get_revision(REVISION)
    assert revision is not None
    assert revision.down_revision == BEFORE


def test_downgrade_drops_both_tables_and_upgrade_recreates_them_with_forced_rls(
    database: Engine,
):
    go_down(BEFORE)
    assert tables_present(database) == set()
    migrate.upgrade()
    assert tables_present(database) == set(TABLES)
    with database.connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT relname, relrowsecurity, relforcerowsecurity FROM pg_class
                WHERE relname = ANY(:names)
                """
            ),
            {"names": list(TABLES)},
        ).all()
    assert sorted(tuple(row) for row in rows) == [
        ("connector_call", True, True),
        ("connector_credential", True, True),
    ]
    with database.connect() as conn:
        index = conn.scalar(
            text(
                """
                SELECT indexdef FROM pg_indexes
                WHERE indexname = 'ix_connector_call_tenant_day'
                """
            )
        )
    assert index is not None
    assert "(tenant_id, created_at)" in index
