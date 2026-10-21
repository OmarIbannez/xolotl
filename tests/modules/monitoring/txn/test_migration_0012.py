"""Migration 0012: accounts, counterparties, partitioned transactions and aggregates.

The tests that move the session database back to 0011 and forward again commit for
real; the ``database`` fixture always brings it back to head afterwards, so the rest
of the session still sees a database at head.
"""

from collections.abc import Generator
from datetime import UTC, datetime

import pytest
from alembic import command
from alembic.script import ScriptDirectory
from sqlalchemy import Engine, insert, inspect, select, text
from sqlalchemy.exc import IntegrityError, ProgrammingError

from tests.helpers import make_tenant, system_ctx
from tests.isolation.factories import counterparty, transaction
from xolotl.models import metadata
from xolotl.modules.monitoring.txn.models import Transaction
from xolotl.modules.screening.counterparty.models import Counterparty
from xolotl.platform import migrate
from xolotl.platform.db import APP_ROLE, tenant_session
from xolotl.platform.ids import uuid7
from xolotl.platform.partitions import next_month, partition_name

REVISION, BEFORE = "0012_transactions", "0011_screening_hit_run_index"
TABLES = (
    "account",
    "counterparty",
    "transaction_batch",
    "transaction_file",
    "transaction",
    "transaction_ref",
    "transaction_parked",
    "agg_bucket",
    "agg_distinct",
    "agg_declaration",
)
FORCED = text(
    """
    SELECT relname, relkind, relrowsecurity, relforcerowsecurity FROM pg_class
    WHERE relname = ANY(:names) ORDER BY relname
    """
)


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


def test_0012_follows_0011():
    script = ScriptDirectory.from_config(migrate.alembic_config())
    revision = script.get_revision(REVISION)
    assert revision is not None
    assert revision.down_revision == BEFORE


def test_downgrade_drops_the_ten_tables_and_upgrade_recreates_them_with_forced_rls(
    database: Engine,
):
    go_down(BEFORE)
    assert tables_present(database) == set()
    with database.connect() as conn:
        left = conn.scalars(
            text("SELECT relname FROM pg_class WHERE relname LIKE 'transaction_%'")
        ).all()
    assert left == []
    migrate.upgrade()
    assert tables_present(database) == set(TABLES)
    with database.connect() as conn:
        rows = conn.execute(FORCED, {"names": list(TABLES)}).all()
    assert [(row[0], row[2], row[3]) for row in rows] == [
        (name, True, True) for name in sorted(TABLES)
    ]


def test_transaction_is_partitioned_with_a_hardened_default_and_three_months_ahead(
    database: Engine,
):
    month = datetime.now(UTC).date().replace(day=1)
    expected = ["transaction_default"]
    for _ in range(4):
        expected.append(partition_name("transaction", month))
        month = next_month(month)
    with database.connect() as conn:
        parent = conn.execute(FORCED, {"names": ["transaction"]}).one()
        partitions = conn.execute(FORCED, {"names": expected}).all()
        granted = conn.scalars(
            text(
                """
                SELECT c.relname FROM pg_class c
                CROSS JOIN unnest(ARRAY['SELECT', 'INSERT', 'UPDATE', 'DELETE']) p
                WHERE c.relname = ANY(:names)
                  AND has_table_privilege(:role, c.oid, p)
                """
            ),
            {"names": expected, "role": APP_ROLE},
        ).all()
    assert parent[1] == "p"
    assert sorted(row[0] for row in partitions) == sorted(expected)
    assert all(row[2] and row[3] for row in partitions)
    assert granted == []


def test_a_row_outside_every_monthly_partition_lands_in_the_default_partition_under_rls(
    engine: Engine,
):
    a, b = make_tenant("alpha"), make_tenant("beta")
    occurred_at = datetime(2019, 6, 1, 12, tzinfo=UTC)
    table = metadata().tables["transaction"]
    with tenant_session(system_ctx(a)) as s:
        transaction(s, a)
        # A second row like the factory's, dated before every monthly partition.
        row = dict(s.execute(select(table)).mappings().one())
        row.update(id=uuid7(), external_id="old", occurred_at=occurred_at)
        _ = s.execute(insert(table).values(row))
        home = s.scalar(
            select(text("tableoid::regclass::text"))
            .select_from(table)
            .where(table.c.external_id == "old")
        )
    assert home == "transaction_default"
    with tenant_session(system_ctx(b)) as s:
        assert s.scalars(select(Transaction.id)).all() == []
    with (
        pytest.raises(ProgrammingError, match="permission denied"),
        tenant_session(system_ctx(a)) as s,
    ):
        _ = s.execute(text("SELECT count(*) FROM transaction_default"))


def _insert(conn, sql: str, **params: object) -> None:
    _ = conn.execute(text(sql), params)


def _refused(engine: Engine, constraint: str, sql: str, **params: object) -> None:
    with (
        pytest.raises(IntegrityError, match=constraint),
        engine.begin() as conn,
    ):
        _insert(conn, sql, **params)


REF = """
INSERT INTO transaction_ref (tenant_id, external_id, transaction_id, occurred_at,
                             content_hash)
VALUES (:tenant, :external_id, :id, :at, '\\x00')
"""


def test_the_external_id_is_unique_per_tenant_and_the_ref_points_at_the_row(
    engine: Engine, admin_engine: Engine
):
    tenant = make_tenant("alpha")
    with tenant_session(system_ctx(tenant)) as s:
        transaction(s, tenant)
        row = s.scalars(select(Transaction)).one()
        key = {"tenant": tenant, "external_id": row.external_id}
        at, row_id = row.occurred_at, row.id
    with admin_engine.begin() as conn:
        _insert(conn, REF, **key, id=row_id, at=at)
    _refused(admin_engine, "pk_transaction_ref", REF, **key, id=row_id, at=at)
    _refused(
        admin_engine,
        "fk_transaction_ref_transaction",
        REF,
        tenant=tenant,
        external_id="other",
        id=uuid7(),
        at=at,
    )


COUNTERPARTY = """
INSERT INTO counterparty (tenant_id, id, kind, name_hash, dedup_hash)
VALUES (:tenant, :id, 'UNKNOWN', :name_hash, :dedup_hash)
"""


def test_a_counterparty_needs_one_hash_and_one_row_per_dedup_hash(
    engine: Engine, admin_engine: Engine
):
    tenant = make_tenant("alpha")
    with tenant_session(system_ctx(tenant)) as s:
        counterparty(s, tenant)
        dedup_hash = s.scalars(select(Counterparty.dedup_hash)).one()
    _refused(
        admin_engine,
        "ck_counterparty_one_component",
        COUNTERPARTY,
        tenant=tenant,
        id=uuid7(),
        name_hash=None,
        dedup_hash=b"\x02" * 32,
    )
    _refused(
        admin_engine,
        "uq_counterparty_tenant_id_dedup_hash",
        COUNTERPARTY,
        tenant=tenant,
        id=uuid7(),
        name_hash=b"\x01" * 32,
        dedup_hash=dedup_hash,
    )


BUCKET = """
INSERT INTO agg_bucket (tenant_id, key_kind, key, agg_id, bucket_start, bucket_kind)
VALUES (:tenant, 'PARTY', 'k', '0123456789abcdef', :start, 'DAY')
"""


def test_agg_bucket_is_keyed_by_key_kind_key_agg_id_and_bucket_start(
    admin_engine: Engine,
):
    tenant = uuid7()
    first = datetime(2026, 10, 1, tzinfo=UTC)
    with admin_engine.begin() as conn:
        _insert(conn, BUCKET, tenant=tenant, start=first)
        _insert(conn, BUCKET, tenant=tenant, start=datetime(2026, 10, 2, tzinfo=UTC))
        value, count = conn.execute(
            text("SELECT value, count FROM agg_bucket WHERE bucket_start = :start"),
            {"start": first},
        ).one()
    assert (value, count) == (0, 0)
    _refused(admin_engine, "pk_agg_bucket", BUCKET, tenant=tenant, start=first)


def test_the_pending_and_period_indexes_exist(admin_engine: Engine):
    with admin_engine.connect() as conn:
        definitions = dict(
            conn.execute(
                text(
                    """
                    SELECT indexname, indexdef FROM pg_indexes
                    WHERE tablename = 'transaction'
                    """
                )
            ).all()
        )
    pending = definitions["ix_transaction_pending"]
    assert "(tenant_id, party_id, occurred_at)" in pending
    assert "WHERE" in pending
    assert "'RECEIVED'" in pending
    assert "'PARKED'" in pending
    assert "period_keys ->> 'MONTH'" in definitions["ix_transaction_period"]
    assert "occurred_at DESC" in definitions["ix_transaction_party_time"]
