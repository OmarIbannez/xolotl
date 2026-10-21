"""The in-memory screening index of a process (SP-03 spec §8.1, Decision 2).

One ``ScreeningIndexHolder`` per process (API and worker) wraps the library's
``MemoryIndex``:

- the **shared shard** holds every screenable current entry of the shared lists with
  an ``ACTIVE`` version, read in one ``REPEATABLE READ`` snapshot. It is warmed in a
  background thread at start-up, and checked for newer versions at most every
  ``SCREENING_INDEX_REFRESH_SECONDS`` (at once when a screen requires a version the
  shard lacks); a rebuilt shard is swapped in whole. The rows are streamed, and a
  rebuild lets go of the old shard before it reads the new one, so the process never
  holds two (the 512 MB service could not). Meanwhile there is no shard: a screen
  answers 503, as during the warm-up, rather than wait on the lock holding a pooled
  connection (enough waiting screens would leave the rebuild's own session none);
  a screen that requires a version (a delta job, one at a time) waits for it. After
  a failed rebuild screens answer 503 until the refresh interval has passed, when
  the next one loads the shard again.
- a **private shard** per tenant holds its active private versions' entries,
  decrypted in that tenant's session. It is keyed by the tenant and its active
  version ids, so a new version is a new key, and the shards live in an LRU of
  ``SCREENING_PRIVATE_SHARDS``. A shard is only handed to a session of its tenant.
- **scoring IDF tables** are built per list set over the shard's entries of that set
  (Deviation 2) and kept in an LRU of 16 by the list set's content hash.
"""

import ctypes
import logging
import threading
import time
from collections import OrderedDict
from collections.abc import Callable, Collection, Mapping
from dataclasses import dataclass
from functools import cache
from types import MappingProxyType
from typing import cast
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from xolotl.modules.screening.catalog.models import (
    ListSource,
    ListVersion,
    PrivateListEntryRow,
)
from xolotl.modules.screening.catalog.private import active_private_versions
from xolotl.modules.screening.engine.entries import (
    current_shared_entries,
    indexed_private_entry,
)
from xolotl.modules.screening.enums import VersionStatus
from xolotl.modules.screening.errors import ScreeningIndexUnavailableError
from xolotl.modules.screening.lists import ListEntry
from xolotl.modules.screening.matching import IdfTable, IndexedEntry, MemoryIndex
from xolotl.platform.db import SessionFactory, bound_context
from xolotl.platform.tenancy.context import TenantContext, bind_context

logger = logging.getLogger(__name__)

SCORING_IDF_CACHE = 16
LOADER_ACTOR = "system:screening-index"


@dataclass(frozen=True, slots=True)
class HeldVersion:
    list_code: str
    version_id: UUID
    seq: int
    list_kind: str


@dataclass(frozen=True, slots=True)
class SharedShard:
    # By list code: what this index holds.
    versions: Mapping[str, HeldVersion]
    # Every shared screenable entry, built with blocking_idf.
    index: MemoryIndex
    blocking_idf: IdfTable
    # By list code, for the per-list-set scoring IDF: the entry objects the index
    # holds, not copies of them.
    entries: Mapping[str, tuple[ListEntry, ...]]


@dataclass(frozen=True, slots=True)
class PrivateShard:
    tenant_id: UUID
    # By private list code; version_id is the private_list_version id.
    versions: Mapping[str, HeldVersion]
    index: MemoryIndex


type _PrivateKey = tuple[UUID, frozenset[UUID]]


def _lacks(shard: SharedShard, require: Mapping[str, int] | None) -> bool:
    # A shard holding a newer version than the one required satisfies it: the newer
    # version is what the list says now.
    if not require:
        return False
    return any(
        (held := shard.versions.get(code)) is None or held.seq < seq
        for code, seq in require.items()
    )


@cache
def _glibc() -> ctypes.CDLL:
    return ctypes.CDLL("libc.so.6")


def _trim_heap() -> None:
    # A shard build frees most of what it allocates (rows, JSON, normalisation), but
    # glibc keeps the freed heap pages, so the process would stay at the build's
    # peak; malloc_trim hands them back to the system. Other C libraries have no
    # such library or call, and nothing is done.
    try:
        _ = cast("int", _glibc().malloc_trim(0))
    except (OSError, AttributeError):
        return


def _signature(versions: Mapping[str, HeldVersion]) -> dict[str, tuple[UUID, int]]:
    return {code: (v.version_id, v.seq) for code, v in versions.items()}


class ScreeningIndexHolder:
    """The process's screening index: one shared shard and an LRU of private ones."""

    def __init__(
        self,
        session_factory: SessionFactory,
        *,
        refresh_seconds: int,
        private_shards: int,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._session_factory: SessionFactory = session_factory
        self._refresh_seconds: int = refresh_seconds
        self._private_capacity: int = private_shards
        self._monotonic: Callable[[], float] = monotonic
        self._lock: threading.Lock = threading.Lock()
        self._cache_lock: threading.Lock = threading.Lock()
        self._shared: SharedShard | None = None
        self._checked_at: float = 0.0
        self._warming: bool = False
        # No shard is installed while a rebuild runs, or after one failed.
        self._rebuilding: bool = False
        self._rebuild_failed: bool = False
        self._private: OrderedDict[_PrivateKey, PrivateShard] = OrderedDict()
        self._idf: OrderedDict[bytes, IdfTable] = OrderedDict()

    # The shared shard.

    def warm(self) -> None:
        """Load the shared shard; on failure log it and leave the holder unloaded.

        The next screen then loads it synchronously.
        """
        try:
            with self._lock:
                _ = self._install(self._load_shared())
        except Exception:
            logger.exception("screening index warm-up failed")
        finally:
            self._warming = False

    def start_warming(self) -> threading.Thread:
        """Run ``warm`` on a daemon thread; screens answer 503 until it ends.

        Returns:
            The started thread.

        """
        self._warming = True
        thread = threading.Thread(
            target=self.warm, name="screening-index-warm", daemon=True
        )
        thread.start()
        return thread

    def shared(
        self, s: Session, *, require: Mapping[str, int] | None = None
    ) -> SharedShard:
        """Return the shared shard, refreshed when it is due or lacks a version.

        ``require`` maps list codes to the version a caller is about to screen
        (a delta job): a shard holding an older one is refreshed at once.

        Returns:
            The shard to screen with.

        Raises:
            ScreeningIndexUnavailableError: While the start-up warm-up runs and
                nothing is loaded yet; while a rebuild runs, unless the screen
                requires a version; within the refresh interval after a failed
                rebuild; or when a required version is still missing after a
                refresh.

        """
        shard = self._shared
        if shard is None:
            if self._warming or self._unavailable(require):
                raise ScreeningIndexUnavailableError
            seen, wait = None, True
        else:
            wait = _lacks(shard, require)
            if not (self._due() or wait):
                return shard
            seen = shard.versions
            # A rebuild frees the shard it replaces before it reads the new one, which
            # it cannot while this frame still holds it.
            del shard
        shard = self._refresh(s, seen, wait=wait)
        if _lacks(shard, require):
            raise ScreeningIndexUnavailableError
        return shard

    def _due(self) -> bool:
        return self._monotonic() - self._checked_at >= self._refresh_seconds

    def _unavailable(self, require: Mapping[str, int] | None) -> bool:
        # With no shard installed: a screen does not queue behind a rebuild, and does
        # not retry a failed one before the interval, each a full load in series.
        if self._rebuilding:
            return not require
        return self._rebuild_failed and not self._due()

    def _refresh(
        self, s: Session, seen: Mapping[str, HeldVersion] | None, *, wait: bool
    ) -> SharedShard:
        # ``seen`` is the versions of the shard the caller found (they name it without
        # keeping it alive). Without wait, a screen that finds another one checking
        # keeps using the shard that is still installed; once a rebuild has let go of
        # it, there is none, and the screen answers 503 as it would on arriving then.
        if not wait and not self._lock.acquire(blocking=False):
            if (installed := self._shared) is not None:
                return installed
            raise ScreeningIndexUnavailableError
        if wait:
            _ = self._lock.acquire()
        try:
            current = self._shared
            if current is not None and current.versions is not seen:
                # Another screen swapped in a new shard while this one waited.
                return current
            active = self._active_signature(s)
            self._checked_at = self._monotonic()
            if current is not None and _signature(current.versions) == active:
                return current
            # Let go of the old shard first: building the new one beside it would
            # hold two shards at once. Screens arriving meanwhile answer 503 (or
            # wait, requiring a version); one already screening keeps the old shard
            # alive until it is done.
            self._shared = None
            self._rebuilding = True
            del current
            try:
                return self._install(self._load_shared())
            except Exception:
                self._rebuild_failed = True
                raise
            finally:
                self._rebuilding = False
        finally:
            self._lock.release()

    def _install(self, shard: SharedShard) -> SharedShard:
        self._shared = shard
        self._rebuild_failed = False
        self._checked_at = self._monotonic()
        return shard

    @staticmethod
    def _active_signature(s: Session) -> dict[str, tuple[UUID, int]]:
        rows = s.execute(
            select(ListVersion.list_code, ListVersion.id, ListVersion.seq).where(
                ListVersion.status == VersionStatus.ACTIVE
            )
        )
        return {code: (version_id, seq) for code, version_id, seq in rows}

    def _load_shared(self) -> SharedShard:
        # One fresh session in REPEATABLE READ: the versions and their entries come
        # from one snapshot even while a list is being ingested.
        session = self._session_factory()
        try:
            with bind_context(TenantContext.platform(LOADER_ACTOR)):
                _ = session.connection(
                    execution_options={"isolation_level": "REPEATABLE READ"}
                )
                return self._read_shared(session)
        finally:
            session.rollback()
            session.close()
            _trim_heap()

    @staticmethod
    def _read_shared(s: Session) -> SharedShard:
        versions = {
            code: HeldVersion(code, version_id, seq, kind)
            for code, version_id, seq, kind in s.execute(
                select(
                    ListVersion.list_code,
                    ListVersion.id,
                    ListVersion.seq,
                    ListSource.list_kind,
                )
                .join(ListSource, ListSource.code == ListVersion.list_code)
                .where(ListVersion.status == VersionStatus.ACTIVE)
            )
        }
        indexed = list(current_shared_entries(s, versions))
        entries: dict[str, list[ListEntry]] = {}
        for item in indexed:
            entries.setdefault(item.shard, []).append(item.entry)
        blocking_idf = IdfTable.build(item.entry for item in indexed)
        return SharedShard(
            versions=MappingProxyType(versions),
            index=MemoryIndex.build(indexed, idf=blocking_idf),
            blocking_idf=blocking_idf,
            entries=MappingProxyType({c: tuple(e) for c, e in entries.items()}),
        )

    # Private shards.

    def private(self, s: Session) -> PrivateShard | None:
        """Return the session tenant's private shard, building it on a miss.

        The entries are decrypted in ``s``, for processing only. The index blocks
        with the shared shard's IDF table; private entries never weigh tokens.

        Returns:
            The shard, or None when the tenant has no active private version.

        """
        tenant_id = bound_context(s).require_tenant_id()
        active = active_private_versions(s)
        if not active:
            return None
        key: _PrivateKey = (tenant_id, frozenset(v.id for _, v in active))
        with self._cache_lock:
            if (cached := self._private.get(key)) is not None:
                self._private.move_to_end(key)
                return cached
        # A cached shard keeps the blocking IDF of the shared shard it was built with
        # after the shared one is swapped; that changes only blocking, not scoring.
        blocking_idf = self.shared(s).blocking_idf
        indexed: list[IndexedEntry] = []
        versions: dict[str, HeldVersion] = {}
        for private_list, version in active:
            code = private_list.list_code
            versions[code] = HeldVersion(
                code, version.id, version.seq, private_list.list_kind
            )
            rows = s.scalars(
                select(PrivateListEntryRow)
                .where(
                    PrivateListEntryRow.list_id == private_list.id,
                    PrivateListEntryRow.to_seq.is_(None),
                )
                .order_by(PrivateListEntryRow.id)
            )
            indexed.extend(
                item
                for row in rows
                if (item := indexed_private_entry(s, row, code)).entry.screenable
            )
        shard = PrivateShard(
            tenant_id=tenant_id,
            versions=MappingProxyType(versions),
            index=MemoryIndex.build(indexed, idf=blocking_idf),
        )
        with self._cache_lock:
            # An older shard of the tenant will never be asked for again.
            for stale in [k for k in self._private if k[0] == tenant_id]:
                del self._private[stale]
            self._private[key] = shard
            while len(self._private) > self._private_capacity:
                _ = self._private.popitem(last=False)
        return shard

    # Scoring IDF.

    def scoring_idf(
        self, shard: SharedShard, list_codes: Collection[str], *, key: bytes
    ) -> IdfTable:
        """Return the scoring IDF table of a list set, built over its entries.

        ``key`` is the list set's content hash, which names the exact versions, so
        a cached table is always the one ``list_codes`` of ``shard`` give.

        Returns:
            The table, shared by every run over the same list set.

        """
        with self._cache_lock:
            if (cached := self._idf.get(key)) is not None:
                self._idf.move_to_end(key)
                return cached
        table = IdfTable.build(
            entry
            for code in sorted(list_codes)
            for entry in shard.entries.get(code, ())
        )
        with self._cache_lock:
            self._idf[key] = table
            while len(self._idf) > SCORING_IDF_CACHE:
                _ = self._idf.popitem(last=False)
        return table


class _Slot:
    def __init__(self) -> None:
        self.holder: ScreeningIndexHolder | None = None


_slot = _Slot()


def get_index_holder() -> ScreeningIndexHolder:
    """Return the process's holder.

    Returns:
        The holder ``register_screening`` installed.

    Raises:
        RuntimeError: When ``register_screening`` has not run in this process.

    """
    if _slot.holder is None:
        msg = "no screening index holder: register_screening has not run"
        raise RuntimeError(msg)
    return _slot.holder


def set_index_holder(holder: ScreeningIndexHolder | None) -> None:
    """Install the process's holder (composition and tests); None removes it."""
    _slot.holder = holder
