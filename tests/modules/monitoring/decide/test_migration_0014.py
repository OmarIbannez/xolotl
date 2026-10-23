"""Migration 0014: rail settings, partitioned decisions and behaviour profiles.

Rows are built with the isolation factories inside the tenant's own session, so
every check is the one the application role meets. The round trip moves the
session database back to 0013 and forward again with real commits; the
``database`` fixture always brings it back to head afterwards.
"""

from collections.abc import Generator
from uuid import UUID

import pytest
from alembic import command
from alembic.script import ScriptDirectory
from sqlalchemy import Engine, delete, inspect, select, text, update
from sqlalchemy.exc import IntegrityError

from tests.helpers import make_tenant, system_ctx
from tests.isolation import factories
from tests.platform.test_migration_chain import CHAIN
from xolotl.modules.monitoring.decide.enums import FailMode
from xolotl.modules.monitoring.decide.models import RailSetting, TxnDecision
from xolotl.modules.monitoring.profiles.models import BehaviourProfile
from xolotl.modules.monitoring.txn.models import Transaction
from xolotl.platform import clock, migrate
from xolotl.platform.db import APP_ROLE, tenant_session
from xolotl.platform.partitions import (
    PartitionedTable,
    ensure_all_partitions,
    next_month,
    partition_name,
    partitioned_tables,
)

REVISION, BEFORE = "0014_decide_profiles", "0013_rules_captures"
TABLES = ("rail_setting", "txn_decision", "behaviour_profile")
FORCED = text(
    """
    SELECT relname, relkind, relrowsecurity, relforcerowsecurity FROM pg_class
    WHERE relname = ANY(:names) ORDER BY relname
    """
)
GRANTED = text(
    """
    SELECT c.relname, p FROM pg_class c
    CROSS JOIN unnest(ARRAY['SELECT', 'INSERT', 'UPDATE', 'DELETE']) p
    WHERE c.relname = ANY(:names) AND has_table_privilege(:role, c.oid, p)
    """
)
INDEXES = text(
    "SELECT indexname, indexdef FROM pg_indexes WHERE indexname = ANY(:names)"
)
# Postgres clones the constraint onto each partition; the parent's is the one.
CONSTRAINT = text(
    """
    SELECT conname FROM pg_constraint
    WHERE conname = :name AND conrelid = 'transaction'::regclass
    """
)
DECLARED_PROFILE = text(
    """
    SELECT column_name FROM information_schema.columns
    WHERE table_name = 'customer_relationship' AND column_name = 'declared_profile'
    """
)
PARTITIONS = text(
    """
    SELECT relname FROM pg_class
    WHERE relname LIKE 'txn_decision_%' AND relkind = 'r'
    """
)
INDEX_NAMES = [
    "ux_txn_decision_external",
    "ix_txn_decision_relationship",
    "ix_txn_decision_id",
    "ix_counterparty_identifier_hash",
]


def go_down(revision: str) -> None:
    command.downgrade(migrate.alembic_config(), revision)


def _expected_partitions() -> list[str]:
    month = clock.now().date().replace(day=1)
    names = ["txn_decision_default"]
    for _ in range(4):
        names.append(partition_name("txn_decision", month))
        month = next_month(month)
    return names


# The test database, which a test may move back in time; it always ends at head.
@pytest.fixture
def database(admin_engine: Engine) -> Generator[Engine]:
    try:
        yield admin_engine
    finally:
        migrate.upgrade()


def test_0014_follows_0013_in_the_chain():
    assert CHAIN[CHAIN.index(REVISION) - 1] == BEFORE
    script = ScriptDirectory.from_config(migrate.alembic_config())
    revision = script.get_revision(REVISION)
    assert revision is not None
    assert revision.down_revision == BEFORE


def test_txn_decision_has_a_default_and_three_months_of_partitions_with_forced_rls(
    admin_engine: Engine,
):
    expected = _expected_partitions()
    with admin_engine.connect() as conn:
        tables = conn.execute(FORCED, {"names": list(TABLES)}).all()
        partitions = conn.execute(FORCED, {"names": expected}).all()
        granted = conn.execute(GRANTED, {"names": expected, "role": APP_ROLE}).all()
    assert [(row[0], row[1], row[2], row[3]) for row in tables] == [
        ("behaviour_profile", "r", True, True),
        ("rail_setting", "r", True, True),
        ("txn_decision", "p", True, True),
    ]
    assert sorted(row[0] for row in partitions) == sorted(expected)
    assert all(row[2] and row[3] for row in partitions)
    assert granted == []


def test_partition_maintenance_keeps_txn_decision_months_ahead(admin_engine: Engine):
    assert PartitionedTable("txn_decision", "decided_at", None) in partitioned_tables()
    last = _expected_partitions()[-1]
    with admin_engine.connect() as conn, conn.begin() as txn:
        # fk_transaction_decision depends on every decision partition, so one is
        # detached before it can be dropped; the transaction is rolled back.
        _ = conn.execute(text(f"ALTER TABLE txn_decision DETACH PARTITION {last}"))
        _ = conn.execute(text(f"DROP TABLE {last}"))
        reports = ensure_all_partitions(conn, months_ahead=3)
        present = conn.scalars(PARTITIONS).all()
        hardened = conn.execute(FORCED, {"names": [last]}).one()
        txn.rollback()
    assert reports["txn_decision"].created == [last]
    assert sorted(present) == sorted(_expected_partitions())
    assert (hardened[2], hardened[3]) == (True, True)


def test_the_decision_and_counterparty_indexes_exist(admin_engine: Engine):
    with admin_engine.connect() as conn:
        definitions = dict(conn.execute(INDEXES, {"names": INDEX_NAMES}).all())
    expected = {
        "ux_txn_decision_external": (
            "UNIQUE INDEX",
            "(tenant_id, external_id, decided_at)",
        ),
        "ix_txn_decision_relationship": (
            "INDEX",
            "(tenant_id, relationship_id, decided_at DESC)",
        ),
        "ix_txn_decision_id": ("INDEX", "(tenant_id, id)"),
        "ix_counterparty_identifier_hash": ("INDEX", "(tenant_id, identifier_hash)"),
    }
    for name, (kind, tail) in expected.items():
        definition = definitions[name]
        assert definition.startswith(f"CREATE {kind} {name} "), definition
        assert definition.endswith(tail), definition


def test_the_app_role_may_write_the_three_tables(tenant_id: UUID):
    with tenant_session(system_ctx(tenant_id)) as s:
        factories.rail_setting(s, tenant_id)
        factories.txn_decision(s, tenant_id)
        factories.behaviour_profile(s, tenant_id)
    with tenant_session(system_ctx(tenant_id)) as s:
        conn = s.connection()
        updated = [
            conn.execute(update(RailSetting).values(fail_mode=FailMode.FAIL_CLOSED)),
            conn.execute(update(TxnDecision).values(db_ms=2)),
            conn.execute(update(BehaviourProfile).values(deviated=True)),
        ]
        assert [result.rowcount for result in updated] == [1, 1, 1]
        # A decision row lands in this month's partition, not the default one.
        home = s.scalar(
            select(text("tableoid::regclass::text")).select_from(TxnDecision)
        )
        assert home == _expected_partitions()[1]
        deleted = [
            conn.execute(delete(model))
            for model in (RailSetting, TxnDecision, BehaviourProfile)
        ]
        assert [result.rowcount for result in deleted] == [1, 1, 1]


def test_a_transaction_may_point_at_its_decision_and_not_at_another_tenants(
    tenant_id: UUID,
):
    other = make_tenant("beta")
    with tenant_session(system_ctx(tenant_id)) as s:
        factories.txn_decision(s, tenant_id)
        factories.transaction(s, tenant_id)
        decision = s.scalars(select(TxnDecision)).one()
        key = {"decision_id": decision.id, "decided_at": decision.decided_at}
        _ = s.execute(update(Transaction).values(**key))
    with tenant_session(system_ctx(tenant_id)) as s:
        linked = s.scalars(select(Transaction.decision_id)).one()
    assert linked == key["decision_id"]
    with (
        pytest.raises(IntegrityError, match="fk_transaction_decision"),
        tenant_session(system_ctx(other)) as s,
    ):
        factories.transaction(s, other)
        _ = s.execute(update(Transaction).values(**key))


def _present(engine: Engine) -> dict[str, list[str]]:
    """Name what 0014 adds that the database holds, each list sorted.

    Returns:
        The tables, the decision partitions, the indexes, the transaction's foreign
        key and the relationship's ``declared_profile`` column found.

    """
    with engine.connect() as conn:
        tables = set(inspect(conn).get_table_names()) & set(TABLES)
        found = {
            "partitions": conn.scalars(PARTITIONS).all(),
            "indexes": conn.scalars(
                text("SELECT indexname FROM pg_indexes WHERE indexname = ANY(:names)"),
                {"names": INDEX_NAMES},
            ).all(),
            "fk": conn.scalars(CONSTRAINT, {"name": "fk_transaction_decision"}).all(),
            "declared_profile": conn.scalars(DECLARED_PROFILE).all(),
        }
    return {"tables": sorted(tables)} | {
        key: sorted(str(name) for name in names) for key, names in found.items()
    }


def test_downgrade_removes_everything_0014_added(database: Engine):
    go_down(BEFORE)
    assert _present(database) == {
        "tables": [],
        "partitions": [],
        "indexes": [],
        "fk": [],
        "declared_profile": [],
    }
    migrate.upgrade()
    assert _present(database) == {
        "tables": sorted(TABLES),
        "partitions": sorted(_expected_partitions()),
        "indexes": sorted(INDEX_NAMES),
        "fk": ["fk_transaction_decision"],
        "declared_profile": ["declared_profile"],
    }
