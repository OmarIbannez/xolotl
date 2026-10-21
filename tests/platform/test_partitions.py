"""The generic monthly-partition helper and its registry (SP-04 spec §2)."""

import multiprocessing
import time
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import date

import pytest
from sqlalchemy import Engine, text

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
