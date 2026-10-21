"""The child process of the index memory measurement (test_screening_index_memory).

It runs in a fresh interpreter (``spawn``), as the Render service does: it builds the
application, then reads the shared lists the parent ingested and reports what each
step cost. Peak RSS is the process's high-water mark (``ru_maxrss``), so a mode
measures one path from a clean start; ``tracemalloc`` slows the build and adds its
own bookkeeping, so the RSS headline comes from a mode that does not trace.
"""

import ctypes
import gc
import os
import resource
import sys
import tracemalloc
from collections.abc import Callable
from multiprocessing.connection import Connection
from pathlib import Path
from types import MappingProxyType

from sqlalchemy import func, select

from tests.modules.screening.catalog.support import make_version
from xolotl.app import build_app
from xolotl.modules.screening.catalog.models import ListEntryRow, ListVersion
from xolotl.modules.screening.engine.entries import (
    current_shared_entries,
    indexed_shared_entry,
)
from xolotl.modules.screening.engine.holder import get_index_holder
from xolotl.modules.screening.enums import VersionStatus
from xolotl.modules.screening.lists import ListEntry
from xolotl.modules.screening.matching import IdfTable, MemoryIndex
from xolotl.platform.db import get_admin_engine, tenant_session
from xolotl.platform.tenancy.context import TenantContext

type Measures = dict[str, float | int | str]

_MB = 1024 * 1024
_CTX = TenantContext.platform("perf:index-memory")
LIST_CODES = ("EU_FSF", "MX_SAT_69B", "OFAC_SDN", "UK_FCDO", "UN_CONSOLIDATED")


def peak_rss_mb() -> float:
    """Read the process's peak resident set size.

    Returns:
        Megabytes, rounded to one decimal.

    """
    if sys.platform == "darwin":
        usage = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return round(usage / _MB, 1)
    # Linux keeps ru_maxrss across exec, so a spawned child would start from its
    # parent's peak; the high-water mark of the process's own memory map does not.
    status = Path("/proc/self/status").read_text(encoding="ascii")
    kilobytes = next(
        int(line.split()[1])
        for line in status.splitlines()
        if line.startswith("VmHWM:")
    )
    return round(kilobytes * 1024 / _MB, 1)


def current_rss_mb() -> float:
    """Read the process's resident set size now, after a full collection.

    Returns:
        Megabytes, rounded to one decimal: what the process keeps, as opposed to
        the peak it passed through.

    """
    _ = gc.collect()
    if sys.platform == "darwin":
        # proc_pid_rusage(RUSAGE_INFO_V2): a 16-byte uuid, then 64-bit counters;
        # ri_resident_size is the seventh of them.
        info = (ctypes.c_uint64 * 40)()
        libc = ctypes.CDLL(None)
        _ = libc.proc_pid_rusage(os.getpid(), 2, ctypes.byref(info))
        resident = info[8]
    else:
        pages = Path("/proc/self/statm").read_text(encoding="ascii").split()[1]
        resident = int(pages) * os.sysconf("SC_PAGE_SIZE")
    return round(resident / _MB, 1)


def _traced() -> tuple[float, float]:
    current, peak = tracemalloc.get_traced_memory()
    return current / _MB, peak / _MB


def _step[T](measures: Measures, name: str, run: Callable[[], T]) -> T:
    # The process peak after a step and, when tracing, what it left allocated and
    # its own allocation peak above its start.
    if not tracemalloc.is_tracing():
        result = run()
        measures[f"{name}_rss_mb"] = peak_rss_mb()
        measures[f"{name}_kept_rss_mb"] = current_rss_mb()
        return result
    _ = gc.collect()
    before, _peak = _traced()
    tracemalloc.reset_peak()
    result = run()
    _ = gc.collect()
    after, peak = _traced()
    measures[f"{name}_retained_mb"] = round(after - before, 1)
    measures[f"{name}_peak_mb"] = round(peak - before, 1)
    measures[f"{name}_rss_mb"] = peak_rss_mb()
    return result


def _current_rows():
    return (
        select(ListEntryRow)
        .where(
            ListEntryRow.list_code.in_(
                select(ListVersion.list_code).where(
                    ListVersion.status == VersionStatus.ACTIVE
                )
            ),
            ListEntryRow.to_seq.is_(None),
            ListEntryRow.screenable,
        )
        .order_by(ListEntryRow.list_code, ListEntryRow.id)
    )


def _stages(measures: Measures) -> None:
    # The steps of the holder's read of the shared shard, one at a time.
    tracemalloc.start()
    with tenant_session(_CTX) as s:
        rows = _step(measures, "orm_rows", lambda: list(s.scalars(_current_rows())))
        measures["rows_read"] = len(rows)
        indexed = _step(
            measures, "convert", lambda: [indexed_shared_entry(r) for r in rows]
        )
        s.expunge_all()
        _ = _step(measures, "drop_orm_rows", rows.clear)
        indexed.clear()
        # The holder's read: three columns streamed, no ORM objects.
        indexed = _step(
            measures,
            "stream_read",
            lambda: list(current_shared_entries(s, LIST_CODES)),
        )
    blocking = _step(
        measures, "idf_build", lambda: IdfTable.build(i.entry for i in indexed)
    )
    _ = _step(measures, "index_build", lambda: MemoryIndex.build(indexed, idf=blocking))

    def copy() -> MappingProxyType[str, tuple[ListEntry, ...]]:
        entries: dict[str, list[ListEntry]] = {}
        for item in indexed:
            entries.setdefault(item.shard, []).append(item.entry)
        return MappingProxyType({c: tuple(e) for c, e in entries.items()})

    _ = _step(measures, "entries_copy", copy)
    tracemalloc.stop()


def _holder(measures: Measures) -> None:
    # The real path: warm the shared shard, then refresh it after a new version.
    holder = get_index_holder()
    _ = _step(measures, "warm", holder.warm)
    with tenant_session(_CTX) as s:
        code, seq = (
            s
            .execute(
                select(ListVersion.list_code, func.max(ListVersion.seq))
                .group_by(ListVersion.list_code)
                .order_by(ListVersion.list_code)
                .limit(1)
            )
            .one()
            .tuple()
        )
    _ = make_version(get_admin_engine(), code, seq + 1, added=0)

    def refresh() -> int:
        with tenant_session(_CTX) as s:
            return len(holder.shared(s, require={code: seq + 1}).index)

    measures["indexed"] = _step(measures, "refresh", refresh)


def probe(mode: str, conn: Connection) -> None:
    """Build the application, run one measurement mode and send its numbers back.

    ``stages`` traces each step of the shard read; ``holder-traced`` traces the
    holder's warm-up and refresh; ``holder`` runs them untraced for the RSS peak.
    """
    os.environ["XOLOTL_SCREENING_INDEX_WARM"] = "false"
    measures: Measures = {"mode": mode, "import_rss_mb": peak_rss_mb()}
    _ = build_app()
    measures["app_rss_mb"] = peak_rss_mb()
    measures["app_kept_rss_mb"] = current_rss_mb()
    if mode == "stages":
        _stages(measures)
    else:
        if mode == "holder-traced":
            tracemalloc.start()
        _holder(measures)
        tracemalloc.stop()
    measures["final_rss_mb"] = peak_rss_mb()
    conn.send(measures)
    conn.close()
