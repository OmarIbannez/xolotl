"""The generic monthly-partition helper and its registry (SP-04 spec §2)."""

import multiprocessing
import time
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime
from uuid import uuid4

import pytest
from sqlalchemy import Engine, column, func, select, table, text
from sqlalchemy.exc import DBAPIError

from tests.platform.fresh_process_support import tables_registered_after_importing
from xolotl.platform import clock, migrate, partitions
from xolotl.platform.audit.partitions import IMMUTABLE_FUNCTION
from xolotl.platform.db import APP_ROLE
from xolotl.platform.partitions import (
    LOCK_TIMEOUT,
    PartitionedTable,
    ensure_all_partitions,
    ensure_partitions,
    is_partition_name,
    next_month,
    partition_name,
    partitioned_tables,
    register_partitioned_table,
    retire_partition,
    retirement_lock_set,
)

PROBE = "pt_probe"


def _isolate_registry(monkeypatch) -> None:
    # Registrations made by the test vanish with it.
    monkeypatch.setattr(
        partitions, "_REGISTRY", {spec.table: spec for spec in partitioned_tables()}
    )


@pytest.fixture
def probe(admin_engine: Engine, monkeypatch) -> Iterator[PartitionedTable]:
    # A throwaway partitioned table, registered for this test only.
    _isolate_registry(monkeypatch)
    spec = PartitionedTable(PROBE, "at", None)
    register_partitioned_table(spec)
    with admin_engine.begin() as conn:
        _ = conn.execute(
            text(
                f"""
                CREATE TABLE {PROBE} (tenant_id uuid, at timestamptz, id uuid)
                PARTITION BY RANGE (at)
                """
            )
        )
    try:
        yield spec
    finally:
        with admin_engine.begin() as conn:
            _ = conn.execute(text(f"DROP TABLE IF EXISTS {PROBE} CASCADE"))


def _present(admin_engine: Engine, names: list[str]) -> list[str]:
    with admin_engine.connect() as conn:
        return sorted(
            conn.scalars(
                text(
                    """
                    SELECT relname FROM pg_class
                    WHERE relname = ANY(:names) AND relispartition
                      AND relrowsecurity AND relforcerowsecurity
                    """
                ),
                {"names": names},
            ).all()
        )


def test_audit_event_is_registered_with_its_truncate_guard():
    assert IMMUTABLE_FUNCTION == "audit_event_immutable"
    assert (
        PartitionedTable("audit_event", "at", "audit_event_immutable")
        in partitioned_tables()
    )


@pytest.mark.parametrize("module", ["xolotl.app", "xolotl.cli"])
def test_a_fresh_process_registers_audit_event(module: str):
    # The worker and the API never import xolotl.models, so the daily partition
    # job relies on the platform's own imports registering audit_event.
    with multiprocessing.get_context("spawn").Pool(1) as pool:
        tables = pool.apply(tables_registered_after_importing, (module,))
    assert "audit_event" in tables


def test_registering_an_identical_spec_twice_keeps_one_entry(monkeypatch):
    _isolate_registry(monkeypatch)
    before = partitioned_tables()
    register_partitioned_table(
        PartitionedTable("audit_event", "at", "audit_event_immutable")
    )
    assert partitioned_tables() == before


def test_a_conflicting_registration_raises(monkeypatch):
    _isolate_registry(monkeypatch)
    with pytest.raises(ValueError, match="already registered"):
        register_partitioned_table(PartitionedTable("audit_event", "at", "x"))


@pytest.mark.parametrize(
    "spec",
    [
        PartitionedTable("Bad", "at"),
        PartitionedTable("t; DROP TABLE x", "at"),
        PartitionedTable("t", "at)"),
        PartitionedTable("t", "at", "guard()"),
    ],
)
def test_registration_rejects_names_that_are_not_plain_identifiers(
    monkeypatch, spec: PartitionedTable
):
    _isolate_registry(monkeypatch)
    with pytest.raises(ValueError, match="plain lower-case identifier"):
        register_partitioned_table(spec)
    assert spec not in partitioned_tables()


def test_ensure_partitions_rejects_an_unregistered_table(admin_engine: Engine):
    with admin_engine.begin() as conn, pytest.raises(ValueError, match="registered"):
        _ = ensure_partitions(conn, table="unregistered", months_ahead=0)


def test_partition_names_follow_the_table():
    assert partition_name("transaction", date(2027, 2, 1)) == "transaction_y2027m02"
    assert next_month(date(2026, 12, 9)) == date(2027, 1, 1)


def test_is_partition_name_matches_only_registered_tables():
    assert is_partition_name("audit_event_default")
    assert is_partition_name("audit_event_y2026m10")
    assert not is_partition_name("transaction_ref")
    assert not is_partition_name("audit_event_x")
    assert not is_partition_name("audit_event")
    assert not is_partition_name("unregistered_y2026m10")


def test_ensure_partitions_creates_hardens_and_is_idempotent_for_a_registered_table(
    admin_engine: Engine, probe: PartitionedTable
):
    start = date(2031, 5, 1)
    names = [partition_name(PROBE, start), partition_name(PROBE, next_month(start))]
    with admin_engine.begin() as conn:
        report = ensure_partitions(conn, table=PROBE, start=start, months_ahead=1)
        again = ensure_partitions(conn, table=PROBE, start=start, months_ahead=1)
    assert report.created == names
    assert (again.created, again.skipped) == ([], {})
    assert _present(admin_engine, names) == names
    with admin_engine.connect() as conn:
        for name in names:
            readable = conn.scalar(
                text("SELECT has_table_privilege(:role, :name, 'SELECT')"),
                {"role": APP_ROLE, "name": name},
            )
            assert readable is False


def test_a_table_without_a_guard_gets_no_truncate_trigger(
    admin_engine: Engine, probe: PartitionedTable
):
    start = date(2031, 7, 1)
    with admin_engine.begin() as conn:
        _ = ensure_partitions(conn, table=PROBE, start=start, months_ahead=0)
    with admin_engine.connect() as conn:
        triggers = conn.scalars(
            text("SELECT tgname FROM pg_trigger WHERE tgrelid = :name ::regclass"),
            {"name": partition_name(PROBE, start)},
        ).all()
        audit_triggers = conn.scalars(
            text(
                """
                SELECT tgname FROM pg_trigger
                WHERE tgrelid = :name ::regclass AND NOT tgisinternal
                """
            ),
            {"name": partition_name("audit_event", clock.now().date().replace(day=1))},
        ).all()
    assert triggers == []
    assert any(name.endswith("_no_truncate") for name in audit_triggers)


def test_two_tables_take_turns_on_their_own_locks(
    admin_engine: Engine, probe: PartitionedTable
):
    start = date(2037, 1, 1)
    audit_name = partition_name("audit_event", start)

    def audit_ensure() -> tuple[list[str], float]:
        with admin_engine.begin() as conn:
            began = time.perf_counter()
            report = ensure_partitions(
                conn, table="audit_event", start=start, months_ahead=0
            )
            return report.created, time.perf_counter() - began

    try:
        with admin_engine.begin() as holder:
            _ = holder.execute(
                text(f"SELECT pg_advisory_xact_lock(hashtext('{PROBE}_partitions'))")
            )
            with ThreadPoolExecutor(max_workers=1) as pool:
                created, elapsed = pool.submit(audit_ensure).result(timeout=30)
        assert created == [audit_name]
        assert elapsed < int(LOCK_TIMEOUT.removesuffix("s"))
    finally:
        with admin_engine.begin() as conn:
            _ = conn.execute(text(f"DROP TABLE IF EXISTS {audit_name}"))


def test_ensure_all_partitions_covers_every_registered_table(
    admin_engine: Engine, probe: PartitionedTable
):
    with admin_engine.begin() as conn:
        reports = ensure_all_partitions(conn, months_ahead=1)
    assert list(reports) == [spec.table for spec in partitioned_tables()]
    assert PROBE in reports
    assert len(reports[PROBE].created) == 2


def test_upgrade_creates_the_partitions_of_every_registered_table(
    admin_engine: Engine, probe: PartitionedTable
):
    month = clock.now().date().replace(day=1)
    expected: list[str] = []
    for _ in range(4):
        expected.append(partition_name(PROBE, month))
        month = next_month(month)
    assert _present(admin_engine, expected) == []
    migrate.upgrade()
    assert _present(admin_engine, expected) == sorted(expected)


# Plan 4c C7: retiring a month's partition.
PAST = date(2015, 3, 1)


def _insert_probe_row(engine: Engine, month: date) -> None:
    probe = table(PROBE, column("tenant_id"), column("at"), column("id"))
    with engine.begin() as conn:
        _ = conn.execute(
            probe.insert().values(
                tenant_id=uuid4(),
                at=datetime(month.year, month.month, 15, 12, tzinfo=UTC),
                id=uuid4(),
            )
        )


def test_retire_partition_detaches_and_drops_under_the_lock(
    admin_engine: Engine, probe: PartitionedTable
):
    name = partition_name(PROBE, PAST)
    with admin_engine.begin() as conn:
        _ = ensure_partitions(conn, table=PROBE, start=PAST, months_ahead=0)
    assert _present(admin_engine, [name]) == [name]

    def retire() -> bool:
        with admin_engine.begin() as conn:
            return retire_partition(conn, table=PROBE, month=PAST)

    with ThreadPoolExecutor(max_workers=1) as pool:
        with admin_engine.begin() as holder:
            _ = holder.execute(
                text(f"SELECT pg_advisory_xact_lock(hashtext('{PROBE}_partitions'))")
            )
            pending = pool.submit(retire)
            time.sleep(0.5)
            # The retirement waits for the table's partition lock.
            assert not pending.done()
        assert pending.result(timeout=30) is True
    with admin_engine.connect() as conn:
        left = conn.scalar(
            text("SELECT count(*) FROM pg_class WHERE relname = :name"),
            {"name": name},
        )
    assert left == 0
    # A second run finds nothing to retire.
    with admin_engine.begin() as conn:
        assert retire_partition(conn, table=PROBE, month=PAST) is False


def test_retire_partition_refuses_a_month_that_still_holds_rows(
    admin_engine: Engine, probe: PartitionedTable
):
    name = partition_name(PROBE, PAST)
    with admin_engine.begin() as conn:
        _ = ensure_partitions(conn, table=PROBE, start=PAST, months_ahead=0)
    _insert_probe_row(admin_engine, PAST)
    with (
        pytest.raises(ValueError, match="still holds rows"),
        admin_engine.begin() as conn,
    ):
        _ = retire_partition(conn, table=PROBE, month=PAST)
    # The detach rolled back with the refusal: the partition is still attached.
    assert _present(admin_engine, [name]) == [name]


def test_retire_partition_refuses_a_referenced_month(admin_engine: Engine):
    # A transaction month whose rows other tables reference is never detached: the
    # caller deletes the month's rows first (the retention job, spec section 8.6).
    with admin_engine.begin() as conn:
        _ = ensure_partitions(conn, table="transaction", start=PAST, months_ahead=0)
    name = partition_name("transaction", PAST)
    try:
        with pytest.raises(ValueError, match="still holds rows"):
            _retire_with_a_row(admin_engine, name)
        assert _present(admin_engine, [name]) == [name]
    finally:
        with admin_engine.begin() as conn:
            _ = conn.execute(text(f"ALTER TABLE transaction DETACH PARTITION {name}"))
            _ = conn.execute(text(f"DROP TABLE {name}"))


def _retire_with_a_row(admin_engine: Engine, name: str) -> None:
    # A row the owner put straight into the partition, in the same transaction as
    # the retirement, so nothing outlives the test.
    with admin_engine.begin() as conn:
        _ = conn.execute(text("SET LOCAL session_replication_role = replica"))
        values: dict[str, object] = {
            "tenant_id": uuid4(),
            "occurred_at": datetime(PAST.year, PAST.month, 10, tzinfo=UTC),
            "id": uuid4(),
            "external_id": "x",
            "batch_id": uuid4(),
            "party_id": uuid4(),
            "relationship_id": uuid4(),
            "account_id": uuid4(),
            "direction": "IN",
            "rail": "CASH",
            "instrument": "CASH",
            "channel": "BRANCH",
            "amount_minor": 1,
            "currency": "MXN",
            "content_hash": b"\x00",
            "raw": b"\x00",
        }
        partition = table(name, *(column(key) for key in values))
        _ = conn.execute(partition.insert().values(values))
        _ = retire_partition(conn, table="transaction", month=PAST)


@pytest.mark.parametrize(
    ("table", "month", "message"),
    [
        ("audit_event", PAST, "append-only"),
        ("unregistered", PAST, "not a registered"),
        (PROBE, None, "only a past month"),
    ],
)
def test_retire_partition_refuses_what_it_must_never_drop(
    admin_engine: Engine, probe: PartitionedTable, table, month, message
):
    when = month or clock.now().date().replace(day=1)
    with pytest.raises(ValueError, match=message), admin_engine.begin() as conn:
        _ = retire_partition(conn, table=table, month=when)


def test_retire_partition_sees_rows_row_level_security_hides_from_the_owner(
    bound_owner_database: tuple[Engine, Engine], monkeypatch
):
    # On a managed server the owner is bound by forced RLS: a count without a
    # tenant would read zero and the partition would be dropped with its rows.
    owner, root = bound_owner_database
    _isolate_registry(monkeypatch)
    register_partitioned_table(PartitionedTable(PROBE, "at", None))
    with owner.begin() as conn:
        _ = conn.execute(
            text(
                f"""
                CREATE TABLE {PROBE} (tenant_id uuid, at timestamptz, id uuid)
                PARTITION BY RANGE (at)
                """
            )
        )
        _ = ensure_partitions(conn, table=PROBE, start=PAST, months_ahead=0)
    _insert_probe_row(root, PAST)
    name = partition_name(PROBE, PAST)
    with owner.connect() as conn:
        seen = conn.scalar(select(func.count()).select_from(table(name)))
    assert seen == 0  # what a naive emptiness check would conclude
    with pytest.raises(ValueError, match="still holds rows"), owner.begin() as conn:
        _ = retire_partition(conn, table=PROBE, month=PAST)
    with root.connect() as conn:
        kept = conn.scalar(select(func.count()).select_from(table(PROBE)))
    assert kept == 1


def test_the_retirement_locks_the_table_and_every_neighbour(admin_engine: Engine):
    with admin_engine.connect() as conn:
        names = retirement_lock_set(conn, "transaction")
    assert names[0] == "transaction"
    # Detach locks the referencing tables, drop the referenced ones.
    assert {
        "txn_decision",
        "rule_hit_transaction",
        "reportable_event",
        "rule_hit",
        "transaction_parked",
        "transaction_ref",
        "party",
        "customer_relationship",
        "account",
        "counterparty",
        "transaction_batch",
        "policy_snapshot",
    } <= set(names)


def test_retire_partition_gives_up_at_once_when_a_neighbour_is_busy(
    admin_engine: Engine,
):
    with admin_engine.begin() as conn:
        _ = ensure_partitions(conn, table="transaction", start=PAST, months_ahead=0)
    name = partition_name("transaction", PAST)
    try:
        with admin_engine.connect() as reader:
            busy = reader.begin()
            _ = reader.execute(text("SELECT count(*) FROM party"))  # a long read
            began = time.perf_counter()
            with pytest.raises(DBAPIError) as caught, admin_engine.begin() as conn:
                _ = retire_partition(conn, table="transaction", month=PAST)
            elapsed = time.perf_counter() - began
            busy.rollback()
        assert getattr(caught.value.orig, "sqlstate", None) == "55P03"
        # It never queued behind the reader, so it never held up the reader's peers.
        assert elapsed < 1
        assert _present(admin_engine, [name]) == [name]
    finally:
        with admin_engine.begin() as conn:
            _ = conn.execute(text(f"ALTER TABLE transaction DETACH PARTITION {name}"))
            _ = conn.execute(text(f"DROP TABLE {name}"))
