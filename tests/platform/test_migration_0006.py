"""Migration 0006: the policy, units, calendar, regulatory-profile and branch tables.

The tests move the session database back to 0005 and forward again with real
commits; the ``database`` fixture always brings it back to head afterwards, so the
rest of the session still sees a database at head.
"""

from collections.abc import Generator

import pytest
from alembic import command
from alembic.script import ScriptDirectory
from sqlalchemy import Engine, inspect, text

from xolotl.platform import migrate
from xolotl.platform.db import APP_ROLE

TABLES = {
    "policy_pack",
    "policy_pack_version",
    "unit_value",
    "holiday",
    "tenant_pack_assignment",
    "tenant_override_version",
    "policy_snapshot",
    "tenant_regulatory_profile",
    "tenant_branch",
}


def go_down(revision: str) -> None:
    command.downgrade(migrate.alembic_config(), revision)


# The test database, which a test may move back in time; it always ends at head.
@pytest.fixture
def database(admin_engine: Engine) -> Generator[Engine]:
    try:
        yield admin_engine
    finally:
        migrate.upgrade()


def test_0006_follows_0005():
    revision = ScriptDirectory.from_config(migrate.alembic_config()).get_revision(
        "0006_policy_units_calendar"
    )
    assert revision is not None
    assert revision.down_revision == "0005_encrypt_idempotency"


def test_0006_round_trips(database: Engine):
    go_down("0005_encrypt_idempotency")
    with database.connect() as conn:
        assert TABLES.isdisjoint(set(inspect(conn).get_table_names()))
    migrate.upgrade()
    with database.connect() as conn:
        assert set(inspect(conn).get_table_names()) >= TABLES


def test_0006_grants_follow_the_plan(database: Engine):
    # has_table_privilege, as test_migration_0005.py checks idempotency_key: it answers
    # for any role, where information_schema.role_table_grants lists only the caller's
    # own grants.
    def privileges(table: str) -> tuple[bool, ...]:  # SELECT, INSERT, UPDATE, DELETE
        with database.connect() as conn:
            row = conn.execute(
                text(
                    """
                    SELECT has_table_privilege(:app, :table, 'SELECT'),
                           has_table_privilege(:app, :table, 'INSERT'),
                           has_table_privilege(:app, :table, 'UPDATE'),
                           has_table_privilege(:app, :table, 'DELETE')
                    """
                ),
                {"app": APP_ROLE, "table": table},
            ).one()
        return tuple(row)

    assert privileges("unit_value") == (True, True, True, False)
    assert privileges("policy_pack") == (True, False, False, False)
    assert privileges("policy_pack_version") == (True, False, False, False)
    assert privileges("holiday") == (True, False, False, False)


def test_the_unit_value_lookup_index_skips_superseded_rows(database: Engine):
    # Alembic does not compare index predicates; test_event_model.py pins the model's.
    with database.connect() as conn:
        indexes = {i["name"]: i for i in inspect(conn).get_indexes("unit_value")}
    lookup = indexes["ix_unit_value_lookup"]
    assert lookup["column_names"] == ["unit_code", "valid_from"]
    assert not lookup["unique"]
    options = lookup.get("dialect_options", {})
    assert options.get("postgresql_where") == "(superseded_by IS NULL)"
