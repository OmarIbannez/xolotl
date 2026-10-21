"""The daily partition job covers every registered table (A9)."""

from collections.abc import Iterator

import pytest
from sqlalchemy import Engine, text

from tests.helpers import system_ctx
from xolotl.modules.monitoring.kinds import PARTITIONS_KIND
from xolotl.modules.monitoring.process.partitions import PartitionsJob, describe
from xolotl.modules.tenants.service import CreatedTenant
from xolotl.platform import clock, partitions
from xolotl.platform.db import tenant_session
from xolotl.platform.ids import uuid7
from xolotl.platform.jobs.queue import Job
from xolotl.platform.partitions import (
    PartitionedTable,
    PartitionReport,
    next_month,
    partition_name,
    partitioned_tables,
    register_partitioned_table,
)

PROBE = "pt_job_probe"


@pytest.fixture
def probe(admin_engine: Engine, monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    """Register a throwaway partitioned table for this test only.

    Yields:
        Its name.

    """
    monkeypatch.setattr(
        partitions, "_REGISTRY", {spec.table: spec for spec in partitioned_tables()}
    )
    register_partitioned_table(PartitionedTable(PROBE, "at", None))
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
        yield PROBE
    finally:
        with admin_engine.begin() as conn:
            _ = conn.execute(text(f"DROP TABLE IF EXISTS {PROBE} CASCADE"))


def _present(admin_engine: Engine, names: list[str]) -> list[str]:
    with admin_engine.connect() as conn:
        return sorted(
            conn.scalars(
                text(
                    """
                    SELECT relname FROM pg_class WHERE relname = ANY(:names)
                      AND relispartition AND relforcerowsecurity
                    """
                ),
                {"names": names},
            ).all()
        )


def test_the_partitions_job_keeps_every_registered_table_ahead(
    tenant: CreatedTenant, admin_engine: Engine, probe: str
):
    month = clock.now().date().replace(day=1)
    months = [month]
    for _ in range(5):
        months.append(next_month(months[-1]))
    expected = [
        partition_name(table, m) for table in (probe, "transaction") for m in months
    ]
    job = Job(
        id=uuid7(),
        tenant_id=tenant.tenant_id,
        kind=PARTITIONS_KIND,
        key=None,
        payload={},
        attempts=1,
        max_attempts=1,
        lock_token=uuid7(),
    )
    with tenant_session(system_ctx(tenant.tenant_id)) as s:
        PartitionsJob(months_ahead=5).run(s, job)
    assert _present(admin_engine, expected) == sorted(expected)
    # A second run, from another tenant's schedule, finds them all there.
    with tenant_session(system_ctx(tenant.tenant_id)) as s:
        PartitionsJob(months_ahead=5).run(s, job)
    assert _present(admin_engine, expected) == sorted(expected)


def test_a_report_reads_as_one_line_per_table():
    assert (
        describe("transaction", PartitionReport(created=[], skipped={}))
        == "transaction: created none; skipped none"
    )
    assert describe(
        "transaction",
        PartitionReport(
            created=["transaction_y2027m02", "transaction_y2027m03"],
            skipped={"transaction_y2027m04": "55P03"},
        ),
    ) == (
        "transaction: created transaction_y2027m02, transaction_y2027m03; "
        "skipped transaction_y2027m04 (55P03)"
    )
