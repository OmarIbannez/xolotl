"""The key provider port and its local adapter (spec §8.1, program spec D6).

A key provider hands out the per-tenant data-encryption keys (DEKs) that every
encrypted column and blind index derives from. ``LocalKeyProvider`` wraps each DEK
with a key-encryption key (KEK, the ``MASTER_KEY``) and keeps the wrapped copy in
``tenant_key``; a KMS adapter can replace it behind the same port.

Transactions. With ``session=None`` the provider opens a short session of its own,
bound to a system context for the tenant, and commits it. A caller that already
holds a session (tenant creation, rotation) passes it and the provider works in the
caller's transaction, which then decides whether the work is kept. The provider
never audits: the caller records ``tenant.key_rotated`` and the like.

Destruction is final. ``destroy`` nulls every wrapped key of the tenant, and
``create_tenant_keys`` refuses a tenant that has any destroyed key, so nothing brings
a destroyed tenant back, not even the backfill for a purpose added later. (A tenant
that never had keys has nothing to destroy: marking it is up to the caller.)

The master key's reference. Every row records which key-encryption key wrapped it in
``kek_ref``: ``local:v1:`` and 16 hex characters, the first 8 bytes of an HMAC of a
fixed label under the master key (``kek_ref_for``). The reference is not secret and
cannot be turned back into the key. A read compares the row's reference with this
provider's before it unwraps anything, and a write (a new version, or a purpose that
had no key) compares it with the tenant's current keys, those neither retired nor
destroyed, before it writes anything, so a process started with another
``XOLOTL_MASTER_KEY`` gets ``KekMismatchError`` instead of leaving a version nobody
else can read. A tenant that has no current key yet takes the reference of whoever
writes first. A write needs only one current key of the tenant to carry this
provider's reference, which is how a chain broken by an earlier mistake is mended by
rotating under the right master key (the INDEX key is never rotated, so the tenant's
own reference stays current). A retired row counts for nothing: a stale one under
another master key, left by a chain that was mended since, must not let that key write
again. ``destroy`` needs no key at all and is not checked. Rows written before
references carried a fingerprint hold the bare scheme, ``local:v1``: a read takes the
authenticated unwrap as their check, and a write proves a master key by unwrapping one
of them.

Cache. Unwrapped DEKs stay in memory for ``cache_ttl``, measured on a monotonic clock
so that a step of the wall clock cannot stretch it, keyed by tenant, purpose and
version, plus a pointer to each purpose's current version. Only reads through the
provider's own sessions are cached, so uncommitted work of a caller can never end up
in the cache. Rotation and destruction evict the tenant once their transaction has
committed (the outermost one, when the caller works in savepoints), and a read that
was already under way when that happened is not stored (see ``KeyCache.epoch``). This
holds within one process. Another process notices a rotation or a destruction after
its cache entries expire, so shredding reaches the rows of the live database
everywhere at most ``cache_ttl`` after it commits. It does not reach copies taken
earlier: a base backup, archived WAL, or a heap tuple that VACUUM has not removed yet
still holds the wrapped keys, and while ``MASTER_KEY`` is unchanged, restoring any of
them recovers every key of that tenant. Shredding is complete only once every such
copy has expired, or the master key has been rotated away. Old versions stay
readable, so a late rotation is harmless.

Every key the provider holds or returns is a ``SecretBytes``, and ``wrapped_dek``
comes out of the database as one, so none of them shows up in a repr, a log line or a
traceback that prints local variables. Python cannot reliably erase bytes from
memory, though: DEKs live in process memory for as long as they are cached and until
the garbage collector reclaims the copies.

The process-wide provider is installed once at startup with ``set_key_provider`` and
read with ``get_key_provider``; code that needs keys calls the latter.
"""

import hmac
import threading
import time
from collections.abc import Callable, Generator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Protocol, cast, override
from uuid import UUID

from sqlalchemy import Row, event, func, or_, select, update
from sqlalchemy.dialects.postgresql import Insert
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from xolotl.platform.crypto.errors import (
    DecryptionError,
    KekMismatchError,
    KeyDestroyedError,
    KeyNotFoundError,
)
from xolotl.platform.crypto.models import TenantKey
from xolotl.platform.crypto.primitives import (
    KEY_LEN,
    MAX_VERSION,
    SecretBytes,
    new_dek,
    unwrap_dek,
    wrap_dek,
)
from xolotl.platform.db import SessionFactory, tenant_session
from xolotl.platform.enums import KeyPurpose
from xolotl.platform.errors import DomainError
from xolotl.platform.tenancy.context import TenantContext

KEY_PROVIDER_ACTOR = "key-provider"
DEFAULT_CACHE_TTL = timedelta(minutes=5)
# The adapter and the version of its wrapping scheme. Rows written before references
# carried a fingerprint hold just this.
KEK_SCHEME = "local:v1"
_KEK_REF_LABEL = b"xolotl:kek-ref:v1"
_KEK_REF_BYTES = 8


def kek_ref_for(kek: bytes) -> str:
    """Return the reference stored beside every key wrapped under ``kek``.

    ``local:v1:`` and the first 8 bytes of ``HMAC-SHA-256(kek, "xolotl:kek-ref:v1")``
    in hex. A keyed hash under a fixed label, not a bare digest of the key, so that it
    cannot double as a key someone else derives with ``SHA-256(kek)``. Eight bytes are
    plenty: the reference only has to tell apart master keys by accident, and the
    wrapped keys are authenticated anyway.

    Returns:
        The reference, e.g. ``local:v1:04a0e00102c26950``. It is not secret.

    """
    tag = hmac.digest(kek, _KEK_REF_LABEL, "sha256")
    return f"{KEK_SCHEME}:{tag[:_KEK_REF_BYTES].hex()}"


# Rows of (version, wrapped_dek, kek_ref, retired_at, destroyed_at), newest version
# first.
type KeyRows = Sequence[Row[int, bytes | None, str, datetime | None, datetime | None]]
# The current keys of a tenant, as (purpose, version, wrapped_dek, kek_ref).
type CurrentKeys = Sequence[Row[KeyPurpose, int, bytes | None, str]]

# A key row is destroyed when its key is gone or it is marked so (fail closed).
_DESTROYED = or_(TenantKey.wrapped_dek.is_(None), TenantKey.destroyed_at.is_not(None))
# Session.info key under which rotate and destroy queue the evictions of a commit.
_QUEUED_EVICTIONS = "xolotl.key_cache_evictions"


class KeyProvider(Protocol):
    """Where a tenant's data keys come from: the port a KMS adapter will implement.

    Every method takes an optional ``session``. Pass the caller's session to work
    inside its transaction, or leave it out for a short transaction of the provider's
    own. A tenant has one key per purpose and version; a retired version stays
    readable, a destroyed one never is.
    """

    def create_tenant_keys(
        self, tenant_id: UUID, *, session: Session | None = None
    ) -> None:
        """Give the tenant version 1 of every purpose that has no key yet.

        A tenant whose keys were destroyed is refused: destruction is final.
        """
        ...

    def data_key(
        self,
        tenant_id: UUID,
        purpose: KeyPurpose,
        version: int | None = None,
        *,
        session: Session | None = None,
    ) -> tuple[int, bytes]:
        """Return ``(version, dek)``: the current version, or the pinned ``version``.

        A pinned version is returned even when retired, so values written under it
        stay readable. ``KeyDestroyedError`` and ``KeyNotFoundError`` say why not.
        """
        ...

    def index_key(self, tenant_id: UUID, *, session: Session | None = None) -> bytes:
        """Return the key blind indexes are computed under (never rotated)."""
        ...

    def rotate(
        self, tenant_id: UUID, purpose: KeyPurpose, *, session: Session | None = None
    ) -> int:
        """Add the next version of a purpose's key, retire the old one, return it."""
        ...

    def destroy(self, tenant_id: UUID, *, session: Session | None = None) -> None:
        """Make every key of the tenant unreadable for good (crypto-shredding).

        Records nothing: the caller audits.
        """
        ...


class KeyCache:
    """Unwrapped keys kept in memory for a while; safe to share between threads.

    An entry lives for ``ttl`` from the moment it is stored, as measured by
    ``clock_fn`` (seconds on a monotonic clock). Expired entries are dropped as soon as
    any call finds one is due, so a key does not outlast its time in memory just
    because nobody asked for it again.

    ``epoch`` closes a race. A reader notes the epoch, reads the database, and
    stores the result only if the epoch is still the same; ``evict`` moves it on.
    Without that, a read that finished just before a rotation or a destruction
    committed could store its stale key after the eviction and keep serving it.
    """

    def __init__(self, ttl: timedelta, clock_fn: Callable[[], float]) -> None:
        super().__init__()
        self._ttl: float = ttl.total_seconds()
        self._now: Callable[[], float] = clock_fn
        self._lock: threading.Lock = threading.Lock()
        self._epoch: int = 0
        self._deks: dict[tuple[UUID, KeyPurpose, int], tuple[SecretBytes, float]] = {}
        self._current: dict[tuple[UUID, KeyPurpose], tuple[int, float]] = {}
        self._next_expiry: float | None = None

    @override
    def __repr__(self) -> str:
        return f"KeyCache(entries={len(self)})"

    def __len__(self) -> int:
        with self._lock:
            return len(self._deks)

    def epoch(self) -> int:
        """Return the number of evictions so far.

        Returns:
            A counter that ``evict`` increases.

        """
        with self._lock:
            return self._epoch

    def get(
        self, tenant_id: UUID, purpose: KeyPurpose, version: int | None
    ) -> tuple[int, bytes] | None:
        """Look a key up; ``version=None`` means the purpose's current version.

        Returns:
            ``(version, dek)``, or ``None`` when the key is not cached or expired.

        """
        now = self._now()
        with self._lock:
            self._drop_expired(now)
            if version is None:
                pointer = self._current.get((tenant_id, purpose))
                if pointer is None:
                    return None
                version = pointer[0]
            entry = self._deks.get((tenant_id, purpose, version))
            return None if entry is None else (version, entry[0])

    def put(
        self,
        tenant_id: UUID,
        purpose: KeyPurpose,
        version: int,
        dek: bytes,
        *,
        current: bool,
        epoch: int,
    ) -> None:
        """Store a key read from the database, unless an eviction has happened since.

        ``current`` also records ``version`` as the purpose's current version.
        """
        dek = SecretBytes.of(dek)
        now = self._now()
        expires_at = now + self._ttl
        with self._lock:
            if epoch != self._epoch:
                return
            self._drop_expired(now)
            self._deks[tenant_id, purpose, version] = (dek, expires_at)
            if current:
                self._current[tenant_id, purpose] = (version, expires_at)
            if self._next_expiry is None or expires_at < self._next_expiry:
                self._next_expiry = expires_at

    def evict(self, tenant_id: UUID) -> None:
        """Forget every key of one tenant and invalidate reads that are under way."""
        with self._lock:
            self._epoch += 1
            self._deks = {k: v for k, v in self._deks.items() if k[0] != tenant_id}
            self._current = {
                k: v for k, v in self._current.items() if k[0] != tenant_id
            }

    def _drop_expired(self, now: float) -> None:
        # The lock must be held.
        if self._next_expiry is None or now < self._next_expiry:
            return
        self._deks = {k: v for k, v in self._deks.items() if v[1] > now}
        self._current = {k: v for k, v in self._current.items() if v[1] > now}
        remaining = [v[1] for v in self._deks.values()]
        remaining += [v[1] for v in self._current.values()]
        self._next_expiry = min(remaining, default=None)


def _evict_queued(session: Session) -> None:
    """Evict what rotate and destroy queued on ``session``, now that it has committed.

    ``after_commit`` also fires when a savepoint is released, and the outer transaction
    has not committed then: evicting now would let a read refill the cache with the old
    state and nothing would evict that refill later. Work that is rolled back leaves
    its entry behind, so the next commit of that session evicts once more, which costs
    a cache miss and nothing else.
    """
    if session.in_nested_transaction():
        return
    queued = cast(
        "set[tuple[KeyCache, UUID]] | None", session.info.get(_QUEUED_EVICTIONS)
    )
    if queued is None:
        return
    del session.info[_QUEUED_EVICTIONS]
    for cache, tenant_id in queued:
        cache.evict(tenant_id)


# One listener for every session, installed once. A listener per call would pile up on
# a session that rotates or destroys many times, and SQLAlchemy asks for listeners to be
# a configuration step, not something done per operation.
event.listen(Session, "after_commit", _evict_queued)


@dataclass(frozen=True, slots=True)
class _Found:
    """The row chosen to serve a read; its wrapped key stays out of the repr."""

    version: int
    wrapped_dek: SecretBytes = field(repr=False)
    kek_ref: str


def _pick(rows: KeyRows, version: int | None) -> _Found:
    """Choose the row to use among a purpose's rows, newest version first.

    A pinned ``version`` is returned even when retired, since rows written under it
    must stay readable. Without one, the newest version that is neither retired nor
    destroyed wins. A row counts as destroyed when its key is gone or marked so.

    Returns:
        The chosen version and its wrapped key.

    Raises:
        KeyDestroyedError: When the wanted key, or every usable key, was destroyed.
        KeyNotFoundError: When there is no such key.

    """
    live: list[tuple[_Found, bool]] = []  # with whether the version is retired
    destroyed: set[int] = set()
    for number, wrapped, kek_ref, retired_at, destroyed_at in rows:
        if wrapped is None or destroyed_at is not None:
            destroyed.add(number)
        else:
            live.append((
                _Found(number, SecretBytes.of(wrapped), kek_ref),
                retired_at is not None,
            ))
    for found, retired in live:
        if found.version == version or (version is None and not retired):
            return found
    if (version is None and destroyed) or version in destroyed:
        raise KeyDestroyedError
    raise KeyNotFoundError


def _lock_rows(session: Session, tenant_id: UUID, purpose: KeyPurpose | None) -> None:
    """Lock a tenant's key rows, or one purpose's, until the transaction ends.

    Creation, rotation and destruction lock first and read second, in separate
    statements: under READ COMMITTED the second statement then sees whatever the
    transaction that held the lock committed, which a single statement started earlier
    would not. The rows are locked in id order by all of them, so two of them can never
    wait for each other in a cycle.
    """
    locking = select(TenantKey.id).where(TenantKey.tenant_id == tenant_id)
    if purpose is not None:
        locking = locking.where(TenantKey.purpose == purpose)
    _ = session.execute(locking.order_by(TenantKey.id).with_for_update()).all()


class LocalKeyProvider(KeyProvider):
    """Data keys wrapped by a local key-encryption key, stored in ``tenant_key``.

    ``cache_ttl`` is measured on ``clock_fn``, which returns seconds on a monotonic
    clock (``time.monotonic`` by default); tests pass a clock they move by hand.
    """

    def __init__(
        self,
        kek: bytes,
        session_factory: SessionFactory,
        *,
        cache_ttl: timedelta = DEFAULT_CACHE_TTL,
        clock_fn: Callable[[], float] = time.monotonic,
    ) -> None:
        super().__init__()
        kek = SecretBytes.of(kek)
        # Before the size check, so that a provider that refuses its key has a repr.
        self._kek_ref: str = kek_ref_for(kek)
        if len(kek) != KEY_LEN:
            msg = f"the key-encryption key must be {KEY_LEN} bytes"
            raise ValueError(msg)
        self._kek: SecretBytes = kek
        self._session_factory: SessionFactory = session_factory
        self._cache: KeyCache = KeyCache(cache_ttl, clock_fn)

    @override
    def __repr__(self) -> str:
        return f"{type(self).__name__}(kek_ref={self._kek_ref!r})"

    @property
    def kek_ref(self) -> str:
        """The reference this provider writes and expects: see ``kek_ref_for``."""
        return self._kek_ref

    @override
    def create_tenant_keys(
        self, tenant_id: UUID, *, session: Session | None = None
    ) -> None:
        """Give the tenant version 1 of every purpose that has no key yet.

        A purpose that already has a key, of any version, is left alone. Keys that have
        to be added to a tenant whose live keys were wrapped under another master key
        are refused with ``KekMismatchError``, before anything is written.

        Raises:
            KeyDestroyedError: When any key of the tenant was destroyed. Destruction is
                final: a destroyed tenant gets no new key, for no purpose.

        """
        with self._scope(tenant_id, session) as s:
            # Lock first, read second: a destroy or a rotation in flight has finished
            # when this reads, and one that starts later waits until this commits.
            _lock_rows(s, tenant_id, None)
            rows = s.execute(
                select(TenantKey.purpose, _DESTROYED.label("destroyed")).where(
                    TenantKey.tenant_id == tenant_id
                )
            ).all()
            if any(destroyed for _, destroyed in rows):
                raise KeyDestroyedError
            have = {purpose for purpose, _ in rows}
            missing = [purpose for purpose in KeyPurpose if purpose not in have]
            if missing:
                self._require_this_kek(s, tenant_id)
            for purpose in missing:
                new_key = self._new_key(tenant_id, purpose, 1)
                _ = s.execute(
                    new_key.on_conflict_do_nothing(
                        index_elements=["tenant_id", "purpose", "version"]
                    )
                )

    @override
    def data_key(
        self,
        tenant_id: UUID,
        purpose: KeyPurpose,
        version: int | None = None,
        *,
        session: Session | None = None,
    ) -> tuple[int, bytes]:
        if session is not None:
            return self._read(session, tenant_id, purpose, version)
        cached = self._cache.get(tenant_id, purpose, version)
        if cached is not None:
            return cached
        epoch = self._cache.epoch()
        with self._scope(tenant_id, None) as s:
            found_version, dek = self._read(s, tenant_id, purpose, version)
        self._cache.put(
            tenant_id,
            purpose,
            found_version,
            dek,
            current=version is None,
            epoch=epoch,
        )
        return found_version, dek

    @override
    def index_key(self, tenant_id: UUID, *, session: Session | None = None) -> bytes:
        return self.data_key(tenant_id, KeyPurpose.INDEX, session=session)[1]

    @override
    def rotate(
        self, tenant_id: UUID, purpose: KeyPurpose, *, session: Session | None = None
    ) -> int:
        """Add the next key version for ``purpose`` and retire the current one.

        A tenant whose live keys were wrapped under another master key than this
        provider's is refused with ``KekMismatchError``, before anything is written.

        Returns:
            The new version.

        Raises:
            DomainError: For ``INDEX`` (every blind index would need recomputing), or
                when the version no longer fits the two-byte header of a value.

        """
        if purpose == KeyPurpose.INDEX:
            msg = (
                "INDEX keys are never rotated: every blind index would need recomputing"
            )
            raise DomainError(msg)
        with self._scope(tenant_id, session, evict=True) as s:
            _lock_rows(s, tenant_id, purpose)
            self._require_this_kek(s, tenant_id)
            new_version = self._newest_version(s, tenant_id, purpose) + 1
            if new_version > MAX_VERSION:
                msg = f"key version {new_version} does not fit the version header"
                raise DomainError(msg)
            _ = s.execute(
                update(TenantKey)
                .where(
                    TenantKey.tenant_id == tenant_id,
                    TenantKey.purpose == purpose,
                    TenantKey.retired_at.is_(None),
                )
                .values(retired_at=func.now())
                .execution_options(synchronize_session=False)
            )
            _ = s.execute(self._new_key(tenant_id, purpose, new_version))
        return new_version

    @override
    def destroy(self, tenant_id: UUID, *, session: Session | None = None) -> None:
        with self._scope(tenant_id, session, evict=True) as s:
            _lock_rows(s, tenant_id, None)
            _ = s.execute(
                update(TenantKey)
                .where(TenantKey.tenant_id == tenant_id)
                .values(
                    wrapped_dek=None,
                    destroyed_at=func.coalesce(TenantKey.destroyed_at, func.now()),
                )
                .execution_options(synchronize_session=False)
            )

    @contextmanager
    def _scope(
        self, tenant_id: UUID, session: Session | None, *, evict: bool = False
    ) -> Generator[Session]:
        """Yield the caller's session, or a session of the provider's own.

        With ``evict`` the tenant leaves the cache once the session commits.

        Yields:
            The session to run the operation in.

        """
        if session is not None:
            if evict:
                self._evict_when_committed(session, tenant_id)
            yield session
            return
        context = TenantContext.system(tenant_id, KEY_PROVIDER_ACTOR)
        with tenant_session(context, factory=self._session_factory) as owned:
            if evict:
                self._evict_when_committed(owned, tenant_id)
            yield owned

    def _evict_when_committed(self, session: Session, tenant_id: UUID) -> None:
        # Queued, not evicted: before the commit a concurrent read would store the old
        # state again. A set, so fifty rotations in one session queue one eviction.
        queued = cast(
            "set[tuple[KeyCache, UUID]] | None", session.info.get(_QUEUED_EVICTIONS)
        )
        if queued is None:
            queued = set[tuple[KeyCache, UUID]]()
            session.info[_QUEUED_EVICTIONS] = queued
        queued.add((self._cache, tenant_id))

    def _read(
        self,
        session: Session,
        tenant_id: UUID,
        purpose: KeyPurpose,
        version: int | None,
    ) -> tuple[int, bytes]:
        rows = session.execute(
            select(
                TenantKey.version,
                TenantKey.wrapped_dek,
                TenantKey.kek_ref,
                TenantKey.retired_at,
                TenantKey.destroyed_at,
            )
            .where(TenantKey.tenant_id == tenant_id, TenantKey.purpose == purpose)
            .order_by(TenantKey.version.desc())
        ).all()
        found = _pick(rows, version)
        # Before anything is unwrapped: a key of another master key is not even tried.
        # A row from before references carried a fingerprint has none to compare, and
        # the authenticated unwrap below is its check.
        if found.kek_ref not in {self._kek_ref, KEK_SCHEME}:
            raise KekMismatchError
        dek = unwrap_dek(
            self._kek,
            found.wrapped_dek,
            tenant_id=tenant_id,
            purpose=purpose,
            version=found.version,
        )
        return found.version, dek

    def _require_this_kek(self, session: Session, tenant_id: UUID) -> None:
        """Refuse to add a key to a tenant whose current keys are not this provider's.

        Call it with the tenant's rows locked and before writing anything. Only
        current keys count, those that are neither retired nor destroyed: a retired row
        is history, and a stale one under another master key must not let that key in
        again. A tenant with no current key takes the reference of whoever writes
        first. Otherwise one current key has to carry this provider's reference, or be
        one of the unlabelled rows from before references carried a fingerprint and
        open under this provider's key. Requiring one such key, not all of them and not
        the newest, is what lets a rotation under the right master key mend a chain
        whose newest version was written under a wrong one: the INDEX key is never
        rotated, so the tenant's own reference is always among the current keys.

        Raises:
            KekMismatchError: When no current key of the tenant is this provider's.

        """
        current: CurrentKeys = session.execute(
            select(
                TenantKey.purpose,
                TenantKey.version,
                TenantKey.wrapped_dek,
                TenantKey.kek_ref,
            ).where(
                TenantKey.tenant_id == tenant_id,
                TenantKey.retired_at.is_(None),
                ~_DESTROYED,
            )
        ).all()
        if not current or any(ref == self._kek_ref for _, _, _, ref in current):
            return
        for purpose, version, wrapped, ref in current:
            if ref == KEK_SCHEME and wrapped is not None:
                try:
                    _ = unwrap_dek(
                        self._kek,
                        wrapped,
                        tenant_id=tenant_id,
                        purpose=purpose,
                        version=version,
                    )
                except DecryptionError:
                    continue
                return
        raise KekMismatchError

    @staticmethod
    def _newest_version(session: Session, tenant_id: UUID, purpose: KeyPurpose) -> int:
        newest = session.execute(
            select(TenantKey.version, _DESTROYED.label("destroyed"))
            .where(TenantKey.tenant_id == tenant_id, TenantKey.purpose == purpose)
            .order_by(TenantKey.version.desc())
            .limit(1)
        ).first()
        if newest is None:
            raise KeyNotFoundError
        version, destroyed = newest
        if destroyed:
            raise KeyDestroyedError
        return version

    def _new_key(self, tenant_id: UUID, purpose: KeyPurpose, version: int) -> Insert:
        wrapped = wrap_dek(
            self._kek, new_dek(), tenant_id=tenant_id, purpose=purpose, version=version
        )
        return pg_insert(TenantKey).values(
            tenant_id=tenant_id,
            purpose=purpose,
            version=version,
            wrapped_dek=wrapped,
            kek_ref=self._kek_ref,
        )


class _ProviderSlot:
    def __init__(self) -> None:
        self.provider: KeyProvider | None = None


_slot = _ProviderSlot()


def get_key_provider() -> KeyProvider:
    """Return the process-wide key provider.

    Returns:
        The provider installed by :func:`set_key_provider`.

    Raises:
        RuntimeError: When none was installed yet.

    """
    if _slot.provider is None:
        msg = "key provider not configured"
        raise RuntimeError(msg)
    return _slot.provider


def set_key_provider(provider: KeyProvider | None) -> None:
    """Install the process-wide key provider, or ``None`` to remove it."""
    _slot.provider = provider
