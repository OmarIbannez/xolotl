"""``tenant_key`` and the local key provider (spec 8.1, plan 1b task B2).

The first part pins what the migrated table must look like: column order, types,
nullability, the ``now()`` default, primary-key order and grants, so that a change to
the model or the migration cannot slip through a schema comparison. Then the key
cache is exercised on its own, and finally the provider runs against the real
database: real commits, real row-level security, and a second transaction for every
race.
"""

import logging
import re
import time
from collections.abc import Generator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timedelta
from inspect import signature
from itertools import starmap
from typing import Any, NamedTuple, override
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Engine, inspect, select, text
from sqlalchemy.exc import IntegrityError, ProgrammingError
from sqlalchemy.orm import Session

from tests.helpers import (
    leaked,
    locals_report,
    make_tenant,
    make_tenant_with_keys,
    platform_ctx,
    system_ctx,
)
from xolotl.platform.crypto.errors import (
    DecryptionError,
    KeyDestroyedError,
    KeyNotFoundError,
)
from xolotl.platform.crypto.models import TenantKey
from xolotl.platform.crypto.primitives import (
    KEY_LEN,
    MAX_VERSION,
    NONCE_LEN,
    TAG_LEN,
    SecretBytes,
)
from xolotl.platform.crypto.provider import (
    KeyCache,
    KeyProvider,
    LocalKeyProvider,
    get_key_provider,
    kek_ref_for,
    set_key_provider,
)
from xolotl.platform.db import (
    APP_ROLE,
    bound_context,
    get_engine,
    get_sessionmaker,
    tenant_session,
)
from xolotl.platform.enums import ActorType, KeyPurpose
from xolotl.platform.errors import DomainError

DATA, INDEX, BIOMETRIC = KeyPurpose.DATA, KeyPurpose.INDEX, KeyPurpose.BIOMETRIC
KEK = bytes(range(32, 64))
OTHER_KEK = bytes(range(64, 96))
WRONG_SIZED_KEK = bytes(range(100, 116))
TTL = timedelta(minutes=5)
START = 1_000.0  # a monotonic clock has no meaningful origin
WRAPPED_LEN = NONCE_LEN + KEY_LEN + TAG_LEN
# What a locals-capturing traceback shows for a provider: its reference, never its key.
SELF_REPR = r"self = LocalKeyProvider\(kek_ref='local:v1:[0-9a-f]{16}'\)"

# ---- the table ------------------------------------------------------------------


def a_key(tenant_id: UUID, purpose: KeyPurpose = DATA, version: int = 1) -> TenantKey:
    return TenantKey(
        tenant_id=tenant_id,
        purpose=purpose,
        version=version,
        wrapped_dek=b"\x00" * WRAPPED_LEN,
        kek_ref="local:v1",
    )


def add_keys(tenant_id: UUID, *keys: TenantKey) -> None:
    with tenant_session(system_ctx(tenant_id)) as s:
        s.add_all(keys)


def test_columns_are_the_specs_in_order(admin_engine: Engine):
    with admin_engine.connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT column_name, data_type, character_maximum_length,
                       is_nullable, column_default
                FROM information_schema.columns
                WHERE table_schema = 'public' AND table_name = 'tenant_key'
                ORDER BY ordinal_position
                """
            )
        ).all()
    assert [tuple(row) for row in rows] == [
        ("tenant_id", "uuid", None, "NO", None),
        ("id", "uuid", None, "NO", None),
        ("purpose", "character varying", 32, "NO", None),
        ("version", "integer", None, "NO", None),
        ("wrapped_dek", "bytea", None, "YES", None),
        ("kek_ref", "text", None, "NO", None),
        ("created_at", "timestamp with time zone", None, "NO", "now()"),
        ("retired_at", "timestamp with time zone", None, "YES", None),
        ("destroyed_at", "timestamp with time zone", None, "YES", None),
    ]


def test_keys_and_constraints_are_the_specs(admin_engine: Engine):
    insp = inspect(admin_engine)
    pk = insp.get_pk_constraint("tenant_key")
    assert (pk["name"], pk["constrained_columns"]) == (
        "pk_tenant_key",
        ["tenant_id", "id"],
    )
    assert [
        (u["name"], u["column_names"])
        for u in insp.get_unique_constraints("tenant_key")
    ] == [
        ("uq_tenant_key_tenant_id_purpose_version", ["tenant_id", "purpose", "version"])
    ]
    assert [
        (
            fk["name"],
            fk["constrained_columns"],
            fk["referred_table"],
            fk["referred_columns"],
        )
        for fk in insp.get_foreign_keys("tenant_key")
    ] == [("fk_tenant_key_tenant_id_tenant", ["tenant_id"], "tenant", ["id"])]


def test_the_application_role_may_read_and_write_but_not_truncate(
    admin_engine: Engine,
):
    privileges = (
        "SELECT",
        "INSERT",
        "UPDATE",
        "DELETE",
        "TRUNCATE",
        "REFERENCES",
        "TRIGGER",
    )
    with admin_engine.connect() as conn:
        granted = {
            privilege: conn.scalar(
                text("SELECT has_table_privilege(:role, 'tenant_key', :privilege)"),
                {"role": APP_ROLE, "privilege": privilege},
            )
            for privilege in privileges
        }
    assert granted == {
        "SELECT": True,
        "INSERT": True,
        "UPDATE": True,
        "DELETE": True,
        "TRUNCATE": False,
        "REFERENCES": False,
        "TRIGGER": False,
    }


def test_a_new_row_gets_its_defaults_and_stores_the_purpose_by_value(engine: Engine):
    tenant_id = make_tenant("alpha")
    add_keys(tenant_id, a_key(tenant_id, BIOMETRIC))
    with tenant_session(system_ctx(tenant_id)) as s:
        row = s.scalars(select(TenantKey)).one()
        assert row.purpose is BIOMETRIC
        assert row.id.version == 7
        assert row.created_at is not None
        assert (row.retired_at, row.destroyed_at) == (None, None)
        assert s.scalar(text("SELECT purpose FROM tenant_key")) == "BIOMETRIC"


def test_a_wrapped_key_loaded_from_the_database_prints_as_a_mask(
    provider: LocalKeyProvider, admin_engine: Engine
):
    tenant = make_tenant_with_keys("alpha", provider=provider)
    wrapped = wrapped_keys(admin_engine, tenant)
    assert len(wrapped) == len(KeyPurpose)
    with tenant_session(system_ctx(tenant)) as s:
        rows = s.execute(select(TenantKey.version, TenantKey.wrapped_dek)).all()
        entity = s.scalars(select(TenantKey)).first()
    assert entity is not None
    assert all(type(blob) is SecretBytes for _, blob in rows)
    assert type(entity.wrapped_dek) is SecretBytes
    assert sorted(bytes(blob) for _, blob in rows if blob is not None) == sorted(
        wrapped
    )
    assert leaked(repr(rows) + str(rows) + repr(tuple(rows[0])), *wrapped) == []
    provider.destroy(tenant)
    with tenant_session(system_ctx(tenant)) as s:
        assert s.scalars(select(TenantKey.wrapped_dek)).all() == [None] * len(
            KeyPurpose
        )


def test_one_tenant_holds_one_row_per_purpose_and_version(engine: Engine):
    tenant_id = make_tenant("alpha")
    add_keys(tenant_id, a_key(tenant_id))
    with pytest.raises(IntegrityError, match="uq_tenant_key_tenant_id_purpose_version"):
        add_keys(tenant_id, a_key(tenant_id))


def test_purposes_versions_and_tenants_do_not_collide(engine: Engine):
    alpha, beta = make_tenant("alpha"), make_tenant("beta")
    add_keys(alpha, a_key(alpha, DATA, 1), a_key(alpha, DATA, 2), a_key(alpha, INDEX))
    add_keys(beta, a_key(beta, DATA, 1))
    with tenant_session(system_ctx(alpha)) as s:
        assert len(s.scalars(select(TenantKey)).all()) == 3
    with tenant_session(system_ctx(beta)) as s:
        assert len(s.scalars(select(TenantKey)).all()) == 1


def test_a_key_needs_an_existing_tenant(engine: Engine):
    unknown = uuid4()
    with pytest.raises(IntegrityError, match="fk_tenant_key_tenant_id_tenant"):
        add_keys(unknown, a_key(unknown))


# ---- helpers for the cache and the provider -------------------------------------


class FakeClock:
    """A monotonic clock, in seconds, that the test moves by hand."""

    def __init__(self) -> None:
        self.current: float = START

    def __call__(self) -> float:
        return self.current

    def advance(self, by: timedelta) -> None:
        self.current += by.total_seconds()


class CountingFactory:
    """Counts the sessions the provider opens for itself: one per cache miss."""

    def __init__(self) -> None:
        self.opened: int = 0

    def __call__(self) -> Session:
        self.opened += 1
        return get_sessionmaker()()


class KeyRow(NamedTuple):
    purpose: str
    version: int
    wrapped_dek: bytes | None
    kek_ref: str
    retired_at: datetime | None
    destroyed_at: datetime | None


# Every key row of a tenant, read as the owner (outside row-level security).
def key_rows(admin: Engine, tenant_id: UUID) -> list[KeyRow]:
    with admin.connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT purpose, version, wrapped_dek, kek_ref, retired_at, destroyed_at
                FROM tenant_key WHERE tenant_id = :tenant_id
                ORDER BY purpose, version
                """
            ),
            {"tenant_id": tenant_id},
        ).all()
    return list(starmap(KeyRow, rows))


def row_of(admin: Engine, tenant_id: UUID, purpose: KeyPurpose, version: int) -> KeyRow:
    (found,) = (
        row
        for row in key_rows(admin, tenant_id)
        if (row.purpose, row.version) == (purpose.value, version)
    )
    return found


def wait_until_a_statement_is_blocked(admin: Engine) -> None:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        with admin.connect() as conn:
            blocked = conn.scalar(
                text(
                    """
                    SELECT count(*) FROM pg_stat_activity
                    WHERE wait_event_type = 'Lock' AND datname = current_database()
                    """
                )
            )
        if blocked:
            return
        time.sleep(0.02)
    pytest.fail("no statement is waiting for a lock")


@pytest.fixture
def fake_clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def reads() -> CountingFactory:
    return CountingFactory()


@pytest.fixture
def provider(
    engine: Engine, reads: CountingFactory, fake_clock: FakeClock
) -> LocalKeyProvider:
    return LocalKeyProvider(KEK, reads, clock_fn=fake_clock)


# ---- the key cache (no database) ------------------------------------------------

ALPHA, BETA = uuid4(), uuid4()
DEK1, DEK2 = bytes(range(32)), bytes(range(1, 33))


def new_cache(ttl: timedelta = TTL) -> tuple[KeyCache, FakeClock]:
    clock = FakeClock()
    return KeyCache(ttl, clock), clock


def put(
    cache: KeyCache,
    tenant_id: UUID,
    version: int,
    dek: bytes,
    purpose: KeyPurpose = DATA,
    *,
    current: bool = True,
) -> None:
    cache.put(tenant_id, purpose, version, dek, current=current, epoch=cache.epoch())


def test_a_cached_key_is_served_until_the_ttl_has_passed():
    cache, clock = new_cache()
    put(cache, ALPHA, 1, DEK1)
    assert cache.get(ALPHA, DATA, 1) == (1, DEK1)
    assert cache.get(ALPHA, DATA, None) == (1, DEK1)
    clock.advance(TTL - timedelta(seconds=1))
    assert cache.get(ALPHA, DATA, 1) == (1, DEK1)
    assert cache.get(ALPHA, DATA, None) == (1, DEK1)
    clock.advance(timedelta(seconds=1))
    assert cache.get(ALPHA, DATA, 1) is None
    assert cache.get(ALPHA, DATA, None) is None


def test_a_zero_ttl_caches_nothing():
    cache, _ = new_cache(timedelta(0))
    put(cache, ALPHA, 1, DEK1)
    assert cache.get(ALPHA, DATA, 1) is None
    assert cache.get(ALPHA, DATA, None) is None


def test_a_pinned_version_is_not_the_current_one():
    cache, _ = new_cache()
    put(cache, ALPHA, 1, DEK1, current=False)
    assert cache.get(ALPHA, DATA, 1) == (1, DEK1)
    assert cache.get(ALPHA, DATA, None) is None


def test_the_current_pointer_follows_the_latest_put():
    cache, _ = new_cache()
    put(cache, ALPHA, 1, DEK1)
    put(cache, ALPHA, 2, DEK2)
    assert cache.get(ALPHA, DATA, None) == (2, DEK2)
    assert cache.get(ALPHA, DATA, 1) == (1, DEK1)


def test_entries_are_keyed_by_tenant_purpose_and_version():
    cache, _ = new_cache()
    put(cache, ALPHA, 1, DEK1)
    assert cache.get(BETA, DATA, 1) is None
    assert cache.get(BETA, DATA, None) is None
    assert cache.get(ALPHA, INDEX, 1) is None
    assert cache.get(ALPHA, INDEX, None) is None
    assert cache.get(ALPHA, DATA, 2) is None


def test_a_put_drops_what_has_expired():
    cache, clock = new_cache()
    put(cache, ALPHA, 1, DEK1)
    put(cache, BETA, 1, DEK2)
    assert len(cache) == 2
    clock.advance(TTL)
    put(cache, ALPHA, 1, DEK1, INDEX)
    assert len(cache) == 1


def test_evict_drops_every_entry_of_one_tenant_and_no_other():
    cache, _ = new_cache()
    for version, dek in ((1, DEK1), (2, DEK2)):
        put(cache, ALPHA, version, dek)
    put(cache, ALPHA, 1, DEK1, INDEX)
    put(cache, BETA, 1, DEK2)
    cache.evict(ALPHA)
    assert cache.get(ALPHA, DATA, None) is None
    assert cache.get(ALPHA, DATA, 1) is None
    assert cache.get(ALPHA, DATA, 2) is None
    assert cache.get(ALPHA, INDEX, 1) is None
    assert cache.get(BETA, DATA, None) == (1, DEK2)
    assert len(cache) == 1


def test_evict_also_forgets_which_version_was_current():
    cache, _ = new_cache()
    put(cache, ALPHA, 2, DEK2)  # version 2 was the current one
    cache.evict(ALPHA)
    # Version 2 is read again by pin, after a rotation made version 3 the current one:
    # a surviving pointer would now make it look current again.
    put(cache, ALPHA, 2, DEK2, current=False)
    assert cache.get(ALPHA, DATA, 2) == (2, DEK2)
    assert cache.get(ALPHA, DATA, None) is None


def test_a_put_that_started_before_an_eviction_is_dropped():
    cache, _ = new_cache()
    started = cache.epoch()
    cache.evict(BETA)  # an eviction of any tenant moves the epoch on
    cache.put(ALPHA, DATA, 1, DEK1, current=True, epoch=started)
    assert cache.get(ALPHA, DATA, 1) is None
    assert len(cache) == 0
    cache.put(ALPHA, DATA, 1, DEK1, current=True, epoch=cache.epoch())
    assert cache.get(ALPHA, DATA, 1) == (1, DEK1)


def test_the_cache_survives_concurrent_use():
    cache, _ = new_cache()
    tenants = [uuid4() for _ in range(8)]

    def hammer(tenant_id: UUID) -> None:
        for round_ in range(300):
            put(cache, tenant_id, 1, DEK1)
            found = cache.get(tenant_id, DATA, None)
            assert found in {None, (1, DEK1)}
            if round_ % 7 == 0:
                cache.evict(tenant_id)

    with ThreadPoolExecutor(max_workers=8) as pool:
        for future in [pool.submit(hammer, tenant_id) for tenant_id in tenants]:
            future.result()
    assert len(cache) <= len(tenants)


def test_the_cache_hands_back_masked_keys_even_when_given_plain_ones():
    cache, _ = new_cache()
    put(cache, ALPHA, 1, DEK1)  # plain bytes in
    found = cache.get(ALPHA, DATA, 1)
    assert found is not None
    assert type(found[1]) is SecretBytes


def test_the_cache_prints_no_keys():
    cache, _ = new_cache()
    put(cache, ALPHA, 1, DEK1)
    put(cache, ALPHA, 2, DEK2, current=False)
    assert leaked(repr(cache) + str(cache), DEK1, DEK2) == []


# ---- creating keys --------------------------------------------------------------


def test_create_tenant_keys_inserts_version_one_for_every_purpose(
    provider: LocalKeyProvider, admin_engine: Engine
):
    tenant = make_tenant("alpha")
    provider.create_tenant_keys(tenant)
    rows = key_rows(admin_engine, tenant)
    assert [(row.purpose, row.version) for row in rows] == [
        ("BIOMETRIC", 1),
        ("DATA", 1),
        ("INDEX", 1),
    ]
    assert provider.kek_ref == kek_ref_for(KEK)
    for row in rows:
        assert row.kek_ref == provider.kek_ref
        assert row.wrapped_dek is not None
        assert len(row.wrapped_dek) == WRAPPED_LEN
        assert (row.retired_at, row.destroyed_at) == (None, None)
    assert len({row.wrapped_dek for row in rows}) == 3


def test_create_tenant_keys_is_idempotent(
    provider: LocalKeyProvider, admin_engine: Engine
):
    tenant = make_tenant("alpha")
    provider.create_tenant_keys(tenant)
    before = key_rows(admin_engine, tenant)
    provider.create_tenant_keys(tenant)
    assert key_rows(admin_engine, tenant) == before  # nothing added, nothing replaced


def test_create_tenant_keys_fills_in_only_the_purposes_that_are_missing(
    provider: LocalKeyProvider, admin_engine: Engine
):
    tenant = make_tenant_with_keys("alpha", provider=provider)
    before = {row.purpose: row for row in key_rows(admin_engine, tenant)}
    with admin_engine.begin() as conn:
        _ = conn.execute(text("DELETE FROM tenant_key WHERE purpose = 'INDEX'"))
    provider.create_tenant_keys(tenant)
    after = {row.purpose: row for row in key_rows(admin_engine, tenant)}
    assert set(after) == {"BIOMETRIC", "DATA", "INDEX"}
    assert after["DATA"] == before["DATA"]
    assert after["BIOMETRIC"] == before["BIOMETRIC"]
    assert after["INDEX"].wrapped_dek != before["INDEX"].wrapped_dek


def test_create_tenant_keys_leaves_a_purpose_alone_whatever_versions_it_has(
    provider: LocalKeyProvider, admin_engine: Engine
):
    tenant = make_tenant_with_keys("alpha", provider=provider)
    assert provider.rotate(tenant, DATA) == 2
    with admin_engine.begin() as conn:
        _ = conn.execute(
            text("DELETE FROM tenant_key WHERE version = 1 AND purpose = 'DATA'")
        )
    before = key_rows(admin_engine, tenant)
    provider.create_tenant_keys(tenant)
    assert key_rows(admin_engine, tenant) == before  # no version 1 appears for DATA


def test_create_tenant_keys_leaves_a_rotated_purpose_alone(
    provider: LocalKeyProvider, admin_engine: Engine
):
    tenant = make_tenant_with_keys("alpha", provider=provider)
    assert provider.rotate(tenant, DATA) == 2
    before = key_rows(admin_engine, tenant)
    provider.create_tenant_keys(tenant)
    assert key_rows(admin_engine, tenant) == before


def test_create_tenant_keys_refuses_a_tenant_whose_keys_were_destroyed(
    provider: LocalKeyProvider, admin_engine: Engine
):
    tenant = make_tenant_with_keys("alpha", provider=provider)
    assert provider.rotate(tenant, DATA) == 2
    provider.destroy(tenant)
    before = key_rows(admin_engine, tenant)
    with pytest.raises(KeyDestroyedError):
        provider.create_tenant_keys(tenant)
    assert key_rows(admin_engine, tenant) == before  # no live key appeared
    for purpose in KeyPurpose:
        with pytest.raises(KeyDestroyedError):
            _ = provider.data_key(tenant, purpose)


def test_a_destroyed_tenant_gets_no_key_for_a_purpose_it_never_had(
    provider: LocalKeyProvider, admin_engine: Engine
):
    # A later plan adds a purpose and backfills every tenant: a tenant that was
    # destroyed in the meantime must not come back to life through it.
    tenant = make_tenant_with_keys("alpha", provider=provider)
    with admin_engine.begin() as conn:
        _ = conn.execute(text("DELETE FROM tenant_key WHERE purpose = 'INDEX'"))
    provider.destroy(tenant)
    with pytest.raises(KeyDestroyedError):
        provider.create_tenant_keys(tenant)
    assert {row.purpose for row in key_rows(admin_engine, tenant)} == {
        "BIOMETRIC",
        "DATA",
    }
    with pytest.raises(KeyNotFoundError):
        _ = provider.data_key(tenant, INDEX)


@pytest.mark.parametrize(
    "damage",
    [
        "UPDATE tenant_key SET destroyed_at = now() WHERE purpose = 'DATA'",
        "UPDATE tenant_key SET wrapped_dek = NULL WHERE purpose = 'INDEX'",
    ],
    ids=["only-destroyed-at", "only-wrapped-dek-null"],
)
def test_one_destroyed_row_is_enough_for_create_tenant_keys_to_refuse(
    provider: LocalKeyProvider, admin_engine: Engine, damage: str
):
    tenant = make_tenant_with_keys("alpha", provider=provider)
    with admin_engine.begin() as conn:
        _ = conn.execute(text(damage))
    before = key_rows(admin_engine, tenant)
    with pytest.raises(KeyDestroyedError):
        provider.create_tenant_keys(tenant)
    assert key_rows(admin_engine, tenant) == before


def test_create_tenant_keys_refuses_a_destroyed_tenant_in_the_callers_session(
    provider: LocalKeyProvider,
):
    tenant = make_tenant_with_keys("alpha", provider=provider)
    provider.destroy(tenant)
    with tenant_session(system_ctx(tenant)) as s, pytest.raises(KeyDestroyedError):
        provider.create_tenant_keys(tenant, session=s)


def test_the_keys_of_a_tenant_are_visible_only_to_that_tenant(
    provider: LocalKeyProvider,
):
    alpha = make_tenant_with_keys("alpha", provider=provider)
    beta = make_tenant_with_keys("beta", provider=provider)
    for tenant in (alpha, beta):
        with tenant_session(system_ctx(tenant)) as s:
            rows = s.scalars(select(TenantKey)).all()
            assert len(rows) == 3
            assert {row.tenant_id for row in rows} == {tenant}
    with tenant_session(platform_ctx()) as s:
        assert s.scalars(select(TenantKey)).all() == []


def test_a_session_bound_to_another_tenant_cannot_read_or_create_keys(
    provider: LocalKeyProvider,
):
    alpha = make_tenant_with_keys("alpha", provider=provider)
    beta = make_tenant("beta")
    with tenant_session(system_ctx(beta)) as s, pytest.raises(KeyNotFoundError):
        _ = provider.data_key(alpha, DATA, session=s)
    with (
        pytest.raises(ProgrammingError, match='policy for table "tenant_key"'),
        tenant_session(system_ctx(beta)) as s,
    ):
        provider.create_tenant_keys(alpha, session=s)


# ---- reading keys ---------------------------------------------------------------


def test_data_key_returns_version_one_and_a_32_byte_key(provider: LocalKeyProvider):
    tenant = make_tenant_with_keys("alpha", provider=provider)
    version, dek = provider.data_key(tenant, DATA)
    assert version == 1
    assert len(dek) == KEY_LEN
    assert provider.data_key(tenant, DATA, 1) == (1, dek)


def test_every_purpose_has_its_own_key(provider: LocalKeyProvider):
    tenant = make_tenant_with_keys("alpha", provider=provider)
    keys = {purpose: provider.data_key(tenant, purpose)[1] for purpose in KeyPurpose}
    assert len(set(keys.values())) == len(KeyPurpose)


def test_index_key_is_the_index_purpose_key(provider: LocalKeyProvider):
    tenant = make_tenant_with_keys("alpha", provider=provider)
    index = provider.index_key(tenant)
    assert index == provider.data_key(tenant, INDEX)[1]
    assert index != provider.data_key(tenant, DATA)[1]


def test_the_key_is_the_same_for_every_provider_with_the_same_kek(
    provider: LocalKeyProvider,
):
    tenant = make_tenant_with_keys("alpha", provider=provider)
    other = LocalKeyProvider(KEK, get_sessionmaker())
    for purpose in KeyPurpose:
        assert other.data_key(tenant, purpose) == provider.data_key(tenant, purpose)


def test_two_tenants_never_share_a_dek(
    provider: LocalKeyProvider, admin_engine: Engine
):
    alpha = make_tenant_with_keys("alpha", provider=provider)
    beta = make_tenant_with_keys("beta", provider=provider)
    for purpose in KeyPurpose:
        assert (
            provider.data_key(alpha, purpose)[1] != provider.data_key(beta, purpose)[1]
        )
    wrapped = [
        row.wrapped_dek for t in (alpha, beta) for row in key_rows(admin_engine, t)
    ]
    assert len(set(wrapped)) == 6


def test_a_key_that_does_not_exist_is_not_found(provider: LocalKeyProvider):
    tenant = make_tenant_with_keys("alpha", provider=provider)
    bare = make_tenant("bare")
    with pytest.raises(KeyNotFoundError):
        _ = provider.data_key(uuid4(), DATA)
    with pytest.raises(KeyNotFoundError):
        _ = provider.data_key(bare, DATA)
    with pytest.raises(KeyNotFoundError):
        _ = provider.data_key(tenant, DATA, 9)
    with pytest.raises(KeyNotFoundError):
        _ = provider.data_key(tenant, DATA, 0)
    with pytest.raises(KeyNotFoundError):
        _ = provider.index_key(bare)


def test_keys_wrapped_under_another_kek_cannot_be_unwrapped(
    provider: LocalKeyProvider,
):
    tenant = make_tenant_with_keys("alpha", provider=provider)
    stranger = LocalKeyProvider(OTHER_KEK, get_sessionmaker())
    with pytest.raises(DecryptionError):
        _ = stranger.data_key(tenant, DATA)
    assert provider.data_key(tenant, DATA)[0] == 1


@pytest.mark.parametrize(
    ("source", "target"),
    [
        (("alpha", DATA, 1), ("beta", DATA, 1)),
        (("alpha", INDEX, 1), ("alpha", DATA, 1)),
        (("alpha", DATA, 1), ("alpha", DATA, 2)),
    ],
    ids=["other-tenant", "other-purpose", "other-version"],
)
def test_a_wrapped_key_moved_to_another_row_does_not_unwrap(
    provider: LocalKeyProvider,
    admin_engine: Engine,
    source: tuple[str, KeyPurpose, int],
    target: tuple[str, KeyPurpose, int],
):
    tenants = {
        "alpha": make_tenant_with_keys("alpha", provider=provider),
        "beta": make_tenant_with_keys("beta", provider=provider),
    }
    assert provider.rotate(tenants["alpha"], DATA) == 2
    src_name, src_purpose, src_version = source
    dst_name, dst_purpose, dst_version = target
    with admin_engine.begin() as conn:
        _ = conn.execute(
            text(
                """
                UPDATE tenant_key SET wrapped_dek = (
                    SELECT wrapped_dek FROM tenant_key
                    WHERE tenant_id = :src AND purpose = :sp AND version = :sv)
                WHERE tenant_id = :dst AND purpose = :dp AND version = :dv
                """
            ),
            {
                "src": tenants[src_name],
                "sp": src_purpose.value,
                "sv": src_version,
                "dst": tenants[dst_name],
                "dp": dst_purpose.value,
                "dv": dst_version,
            },
        )
    with pytest.raises(DecryptionError):
        _ = provider.data_key(tenants[dst_name], dst_purpose, dst_version)


# ---- the cache inside the provider ---------------------------------------------


def test_a_key_is_read_from_the_database_once_per_ttl(
    provider: LocalKeyProvider, reads: CountingFactory, fake_clock: FakeClock
):
    tenant = make_tenant_with_keys("alpha", provider=provider)
    baseline = reads.opened
    assert provider.data_key(tenant, DATA)[0] == 1
    assert reads.opened == baseline + 1
    assert provider.data_key(tenant, DATA)[0] == 1
    assert provider.data_key(tenant, DATA, 1)[0] == 1  # the same entry, by version
    assert reads.opened == baseline + 1
    fake_clock.advance(TTL - timedelta(seconds=1))
    assert provider.data_key(tenant, DATA)[0] == 1
    assert reads.opened == baseline + 1
    fake_clock.advance(timedelta(seconds=1))  # five minutes in: expired
    assert provider.data_key(tenant, DATA)[0] == 1
    assert reads.opened == baseline + 2


def test_each_tenant_purpose_and_version_has_its_own_cache_entry(
    provider: LocalKeyProvider, reads: CountingFactory
):
    alpha = make_tenant_with_keys("alpha", provider=provider)
    beta = make_tenant_with_keys("beta", provider=provider)
    baseline = reads.opened
    for tenant in (alpha, beta):
        for purpose in KeyPurpose:
            _ = provider.data_key(tenant, purpose)
    assert reads.opened == baseline + 6
    for tenant in (alpha, beta):
        for purpose in KeyPurpose:
            _ = provider.data_key(tenant, purpose)
    assert reads.opened == baseline + 6


def test_cache_ttl_is_configurable(engine: Engine, reads: CountingFactory):
    clock = FakeClock()
    short = LocalKeyProvider(
        KEK, reads, cache_ttl=timedelta(seconds=30), clock_fn=clock
    )
    tenant = make_tenant_with_keys("alpha", provider=short)
    baseline = reads.opened
    _ = short.data_key(tenant, DATA)
    clock.advance(timedelta(seconds=29))
    _ = short.data_key(tenant, DATA)
    assert reads.opened == baseline + 1
    clock.advance(timedelta(seconds=1))
    _ = short.data_key(tenant, DATA)
    assert reads.opened == baseline + 2


def test_the_providers_own_sessions_run_as_the_key_provider_system_actor(
    engine: Engine,
):
    actors: list[tuple[UUID | None, ActorType, str]] = []

    class RecordingSession(Session):
        @override
        def commit(self) -> None:
            context = bound_context(self)
            actors.append((context.tenant_id, context.actor_type, context.actor_id))
            super().commit()

    def factory() -> Session:
        return RecordingSession(bind=get_engine(), expire_on_commit=False)

    provider = LocalKeyProvider(KEK, factory)
    tenant = make_tenant("alpha")
    provider.create_tenant_keys(tenant)
    _ = provider.data_key(tenant, DATA)
    _ = provider.rotate(tenant, DATA)
    provider.destroy(tenant)
    assert actors == [(tenant, ActorType.SYSTEM, "key-provider")] * 4


def test_the_cache_clock_defaults_to_the_monotonic_clock():
    # A wall clock can be stepped back by NTP or a VM resume, which would stretch the
    # time a destroyed key is still served by the size of the step. A monotonic one
    # cannot go back.
    default = signature(LocalKeyProvider.__init__).parameters["clock_fn"].default
    assert default is time.monotonic


def test_a_provider_with_the_default_clock_caches(
    engine: Engine, reads: CountingFactory
):
    default = LocalKeyProvider(KEK, reads)
    tenant = make_tenant_with_keys("alpha", provider=default)
    baseline = reads.opened
    assert default.data_key(tenant, DATA)[0] == 1
    assert default.data_key(tenant, DATA)[0] == 1
    assert reads.opened == baseline + 1


def test_a_failed_read_is_not_cached(
    provider: LocalKeyProvider, reads: CountingFactory
):
    bare = make_tenant("bare")
    baseline = reads.opened
    for _ in range(2):
        with pytest.raises(KeyNotFoundError):
            _ = provider.data_key(bare, DATA)
    assert reads.opened == baseline + 2
    provider.create_tenant_keys(bare)
    assert provider.data_key(bare, DATA)[0] == 1


# ---- rotation -------------------------------------------------------------------


def test_rotate_adds_the_next_version_and_retires_the_previous_one(
    provider: LocalKeyProvider, admin_engine: Engine
):
    tenant = make_tenant_with_keys("alpha", provider=provider)
    version1, dek1 = provider.data_key(tenant, DATA)
    assert provider.rotate(tenant, DATA) == 2
    version2, dek2 = provider.data_key(tenant, DATA)
    assert (version1, version2) == (1, 2)
    assert dek1 != dek2
    assert provider.data_key(tenant, DATA, version=1) == (1, dek1)  # old rows read
    assert provider.data_key(tenant, DATA, version=2) == (2, dek2)
    first, second = (row_of(admin_engine, tenant, DATA, v) for v in (1, 2))
    assert first.retired_at is not None
    assert (second.retired_at, second.destroyed_at) == (None, None)
    assert second.kek_ref == provider.kek_ref
    assert second.wrapped_dek is not None
    assert len(second.wrapped_dek) == WRAPPED_LEN


def test_reading_an_old_version_does_not_make_it_the_current_one(
    provider: LocalKeyProvider,
):
    tenant = make_tenant_with_keys("alpha", provider=provider)
    _, dek1 = provider.data_key(tenant, DATA)
    assert provider.rotate(tenant, DATA) == 2
    other = LocalKeyProvider(KEK, get_sessionmaker())  # a cold cache
    assert other.data_key(tenant, DATA, version=1) == (1, dek1)
    assert other.data_key(tenant, DATA)[0] == 2
    assert other.data_key(tenant, DATA, version=1) == (1, dek1)


def test_rotate_keeps_counting_and_touches_nothing_else(
    provider: LocalKeyProvider, admin_engine: Engine
):
    tenant = make_tenant_with_keys("alpha", provider=provider)
    untouched = {
        purpose: row_of(admin_engine, tenant, purpose, 1)
        for purpose in (INDEX, BIOMETRIC)
    }
    assert [provider.rotate(tenant, DATA) for _ in range(3)] == [2, 3, 4]
    assert provider.data_key(tenant, DATA)[0] == 4
    retired = [row_of(admin_engine, tenant, DATA, v).retired_at for v in (1, 2, 3, 4)]
    assert [moment is not None for moment in retired] == [True, True, True, False]
    for purpose, before in untouched.items():
        assert row_of(admin_engine, tenant, purpose, 1) == before
        assert provider.data_key(tenant, purpose)[0] == 1


def test_biometric_keys_rotate_too(provider: LocalKeyProvider):
    tenant = make_tenant_with_keys("alpha", provider=provider)
    assert provider.rotate(tenant, BIOMETRIC) == 2
    assert provider.data_key(tenant, BIOMETRIC)[0] == 2
    assert provider.data_key(tenant, DATA)[0] == 1


def test_rotating_the_index_key_is_refused_before_touching_the_database(
    provider: LocalKeyProvider, reads: CountingFactory, admin_engine: Engine
):
    tenant = make_tenant_with_keys("alpha", provider=provider)
    before = key_rows(admin_engine, tenant)
    opened = reads.opened
    with pytest.raises(DomainError, match="INDEX") as refused:
        _ = provider.rotate(tenant, INDEX)
    assert (refused.value.status, refused.value.code) == (409, "CONFLICT")
    assert reads.opened == opened
    assert key_rows(admin_engine, tenant) == before


def test_rotating_index_is_refused_whatever_form_the_purpose_arrives_in(
    provider: LocalKeyProvider, reads: CountingFactory, admin_engine: Engine
):
    tenant = make_tenant_with_keys("alpha", provider=provider)
    before = key_rows(admin_engine, tenant)
    opened = reads.opened
    plain_string: Any = "INDEX"  # an untyped caller, say a command line argument
    with pytest.raises(DomainError, match="INDEX"):
        _ = provider.rotate(tenant, plain_string)
    assert reads.opened == opened
    assert key_rows(admin_engine, tenant) == before


def test_rotate_needs_keys_that_exist_and_are_not_destroyed(
    provider: LocalKeyProvider,
):
    bare = make_tenant("bare")
    with pytest.raises(KeyNotFoundError):
        _ = provider.rotate(bare, DATA)
    tenant = make_tenant_with_keys("alpha", provider=provider)
    provider.destroy(tenant)
    with pytest.raises(KeyDestroyedError):
        _ = provider.rotate(tenant, DATA)


def test_rotate_refuses_to_outgrow_the_two_byte_version_header(
    provider: LocalKeyProvider, admin_engine: Engine
):
    tenant = make_tenant_with_keys("alpha", provider=provider)
    with admin_engine.begin() as conn:
        _ = conn.execute(
            text(
                """
                UPDATE tenant_key SET version = :newest
                WHERE tenant_id = :tenant_id AND purpose = 'DATA'
                """
            ),
            {"newest": MAX_VERSION - 1, "tenant_id": tenant},
        )
    assert provider.rotate(tenant, DATA) == MAX_VERSION  # the last one still fits
    assert provider.data_key(tenant, DATA)[0] == MAX_VERSION
    before = key_rows(admin_engine, tenant)
    with pytest.raises(DomainError, match="version"):
        _ = provider.rotate(tenant, DATA)
    assert key_rows(admin_engine, tenant) == before  # nothing retired, nothing added


def test_every_row_lock_is_taken_in_id_order(
    provider: LocalKeyProvider, caplog: pytest.LogCaptureFixture
):
    # Creation, rotation and destruction lock overlapping rows. In one fixed order they
    # can never wait for each other in a cycle, whatever plan the database picks.
    tenant = make_tenant_with_keys("alpha", provider=provider)
    caplog.set_level(logging.INFO, logger="sqlalchemy.engine")
    provider.create_tenant_keys(tenant)
    assert provider.rotate(tenant, DATA) == 2
    provider.destroy(tenant)
    locking = [r.getMessage() for r in caplog.records if "FOR UPDATE" in r.getMessage()]
    assert len(locking) == 3
    assert all("ORDER BY tenant_key.id" in statement for statement in locking)


def test_rotation_is_seen_at_once_by_the_provider_that_did_it(
    provider: LocalKeyProvider,
):
    tenant = make_tenant_with_keys("alpha", provider=provider)
    assert provider.data_key(tenant, DATA)[0] == 1  # warms the cache
    assert provider.rotate(tenant, DATA) == 2
    assert provider.data_key(tenant, DATA)[0] == 2


def test_concurrent_rotations_are_serialised(
    provider: LocalKeyProvider, admin_engine: Engine
):
    tenant = make_tenant_with_keys("alpha", provider=provider)
    with ThreadPoolExecutor(max_workers=1) as pool:
        with tenant_session(system_ctx(tenant)) as holder:
            assert provider.rotate(tenant, DATA, session=holder) == 2  # uncommitted
            second = pool.submit(provider.rotate, tenant, DATA)
            wait_until_a_statement_is_blocked(admin_engine)
            assert not second.done()
        assert second.result(timeout=10) == 3
    rows = [row_of(admin_engine, tenant, DATA, v) for v in (1, 2, 3)]
    assert [row.retired_at is not None for row in rows] == [True, True, False]


def test_destroy_waits_for_an_in_flight_rotation_and_destroys_its_new_key(
    provider: LocalKeyProvider, admin_engine: Engine
):
    tenant = make_tenant_with_keys("alpha", provider=provider)
    with ThreadPoolExecutor(max_workers=1) as pool:
        with tenant_session(system_ctx(tenant)) as holder:
            assert provider.rotate(tenant, DATA, session=holder) == 2
            destroying = pool.submit(provider.destroy, tenant)
            wait_until_a_statement_is_blocked(admin_engine)
            assert not destroying.done()
        destroying.result(timeout=10)
    rows = key_rows(admin_engine, tenant)
    assert [(row.purpose, row.version) for row in rows] == [
        ("BIOMETRIC", 1),
        ("DATA", 1),
        ("DATA", 2),
        ("INDEX", 1),
    ]
    assert all(row.wrapped_dek is None for row in rows)
    assert all(row.destroyed_at is not None for row in rows)


# ---- destruction ----------------------------------------------------------------


def test_destroy_makes_every_key_unreadable(
    provider: LocalKeyProvider, admin_engine: Engine
):
    tenant = make_tenant_with_keys("alpha", provider=provider)
    assert provider.rotate(tenant, DATA) == 2
    for purpose in KeyPurpose:  # warm every cache entry, by current and by version
        _ = provider.data_key(tenant, purpose)
        _ = provider.data_key(tenant, purpose, 1)
    _ = provider.data_key(tenant, DATA, 2)
    provider.destroy(tenant)
    for purpose in KeyPurpose:
        with pytest.raises(KeyDestroyedError):
            _ = provider.data_key(tenant, purpose)
        with pytest.raises(KeyDestroyedError):
            _ = provider.data_key(tenant, purpose, 1)
    with pytest.raises(KeyDestroyedError):
        _ = provider.data_key(tenant, DATA, 2)
    with pytest.raises(KeyDestroyedError):
        _ = provider.index_key(tenant)
    rows = key_rows(admin_engine, tenant)
    assert len(rows) == 4
    assert all(row.wrapped_dek is None for row in rows)
    assert all(row.destroyed_at is not None for row in rows)


def test_destroy_does_not_touch_other_tenants(
    provider: LocalKeyProvider, reads: CountingFactory
):
    alpha = make_tenant_with_keys("alpha", provider=provider)
    beta = make_tenant_with_keys("beta", provider=provider)
    kept = {purpose: provider.data_key(beta, purpose) for purpose in KeyPurpose}
    _ = provider.data_key(alpha, DATA)
    provider.destroy(alpha)
    with pytest.raises(KeyDestroyedError):
        _ = provider.data_key(alpha, DATA)
    opened = reads.opened
    for purpose in KeyPurpose:
        assert provider.data_key(beta, purpose) == kept[purpose]
    assert reads.opened == opened  # beta is still served from the cache


def test_destroying_twice_is_fine_and_keeps_the_first_timestamp(
    provider: LocalKeyProvider, admin_engine: Engine
):
    tenant = make_tenant_with_keys("alpha", provider=provider)
    provider.destroy(tenant)
    first = key_rows(admin_engine, tenant)
    provider.destroy(tenant)
    assert key_rows(admin_engine, tenant) == first


def test_destroying_a_tenant_without_keys_changes_nothing(
    provider: LocalKeyProvider, admin_engine: Engine
):
    bare = make_tenant("bare")
    provider.destroy(bare)
    assert key_rows(admin_engine, bare) == []


def test_a_destroyed_key_stays_destroyed_even_if_a_row_keeps_its_bytes(
    provider: LocalKeyProvider, admin_engine: Engine
):
    # Fail closed: destroyed_at alone is enough to refuse the key.
    tenant = make_tenant_with_keys("alpha", provider=provider)
    with admin_engine.begin() as conn:
        _ = conn.execute(
            text("UPDATE tenant_key SET destroyed_at = now() WHERE purpose = 'DATA'")
        )
    with pytest.raises(KeyDestroyedError):
        _ = provider.data_key(tenant, DATA)
    with pytest.raises(KeyDestroyedError):
        _ = provider.data_key(tenant, DATA, 1)


def test_a_read_that_loses_a_race_with_destroy_is_not_cached(engine: Engine):
    # The read finishes, then another transaction destroys the keys and evicts the
    # cache, then the read stores what it saw. That stale copy must not be kept.
    tenant = make_tenant("alpha")
    armed = [False]

    def race() -> None:
        if armed[0]:
            armed[0] = False
            provider.destroy(tenant)  # a second transaction, just before this commit

    class RacingSession(Session):
        @override
        def commit(self) -> None:
            race()
            super().commit()

    def factory() -> Session:
        return RacingSession(bind=get_engine(), expire_on_commit=False)

    provider = LocalKeyProvider(KEK, factory)
    provider.create_tenant_keys(tenant)
    armed[0] = True
    version, dek = provider.data_key(tenant, DATA)  # read before the destroy: legit
    assert (version, len(dek)) == (1, KEY_LEN)
    assert not armed[0]  # the race really ran
    with pytest.raises(KeyDestroyedError):  # a stale copy would have been cached
        _ = provider.data_key(tenant, DATA)


def test_a_backfill_waits_for_an_in_flight_destroy_and_then_refuses(
    provider: LocalKeyProvider, admin_engine: Engine
):
    tenant = make_tenant_with_keys("alpha", provider=provider)
    with admin_engine.begin() as conn:  # a purpose the backfill would add
        _ = conn.execute(text("DELETE FROM tenant_key WHERE purpose = 'INDEX'"))
    with ThreadPoolExecutor(max_workers=1) as pool:
        with tenant_session(system_ctx(tenant)) as holder:
            provider.destroy(tenant, session=holder)  # uncommitted
            backfill = pool.submit(provider.create_tenant_keys, tenant)
            wait_until_a_statement_is_blocked(admin_engine)
            assert not backfill.done()
        with pytest.raises(KeyDestroyedError):
            backfill.result(timeout=10)
    assert {row.purpose for row in key_rows(admin_engine, tenant)} == {
        "BIOMETRIC",
        "DATA",
    }


def test_destroy_waits_for_an_in_flight_backfill_and_destroys_the_key_it_adds(
    provider: LocalKeyProvider, admin_engine: Engine
):
    tenant = make_tenant_with_keys("alpha", provider=provider)
    with admin_engine.begin() as conn:  # a purpose the backfill adds
        _ = conn.execute(text("DELETE FROM tenant_key WHERE purpose = 'INDEX'"))
    with ThreadPoolExecutor(max_workers=1) as pool:
        with tenant_session(system_ctx(tenant)) as holder:
            provider.create_tenant_keys(
                tenant, session=holder
            )  # adds INDEX, uncommitted
            destroying = pool.submit(provider.destroy, tenant)
            wait_until_a_statement_is_blocked(admin_engine)
            assert not destroying.done()
        destroying.result(timeout=10)
    rows = key_rows(admin_engine, tenant)
    assert [(row.purpose, row.version) for row in rows] == [
        ("BIOMETRIC", 1),
        ("DATA", 1),
        ("INDEX", 1),
    ]
    assert all(row.wrapped_dek is None for row in rows)
    assert all(row.destroyed_at is not None for row in rows)


# ---- keys inside the caller's transaction ---------------------------------------


def test_work_in_the_callers_session_never_opens_one_of_its_own(
    provider: LocalKeyProvider, reads: CountingFactory
):
    tenant = make_tenant("alpha")
    with tenant_session(system_ctx(tenant)) as s:
        before = reads.opened
        provider.create_tenant_keys(tenant, session=s)
        assert provider.data_key(tenant, DATA, session=s)[0] == 1
        assert provider.index_key(tenant, session=s)
        assert provider.rotate(tenant, DATA, session=s) == 2
        assert provider.data_key(tenant, DATA, session=s)[0] == 2
        provider.destroy(tenant, session=s)
        assert reads.opened == before


def test_the_callers_session_sees_its_own_uncommitted_keys(
    provider: LocalKeyProvider,
):
    tenant = make_tenant("alpha")
    with tenant_session(system_ctx(tenant)) as s:
        provider.create_tenant_keys(tenant, session=s)
        assert provider.data_key(tenant, DATA, session=s)[0] == 1
        with pytest.raises(KeyNotFoundError):  # nobody else does, yet
            _ = provider.data_key(tenant, DATA)
    assert provider.data_key(tenant, DATA)[0] == 1  # committed with the session


def test_work_rolled_back_in_the_callers_session_leaves_no_trace(
    provider: LocalKeyProvider, admin_engine: Engine
):
    tenant = make_tenant("alpha")
    msg = "abort the caller's transaction"
    with (
        pytest.raises(RuntimeError, match="abort"),
        tenant_session(system_ctx(tenant)) as s,
    ):
        provider.create_tenant_keys(tenant, session=s)
        assert provider.data_key(tenant, DATA, session=s)[0] == 1
        raise RuntimeError(msg)
    assert key_rows(admin_engine, tenant) == []
    with pytest.raises(KeyNotFoundError):  # no uncommitted key leaked into the cache
        _ = provider.data_key(tenant, DATA)


def test_a_commit_of_the_callers_session_drops_the_cached_current_version(
    provider: LocalKeyProvider,
):
    tenant = make_tenant_with_keys("alpha", provider=provider)
    assert provider.data_key(tenant, DATA)[0] == 1  # caches the version 1 pointer
    with tenant_session(system_ctx(tenant)) as s:
        assert provider.rotate(tenant, DATA, session=s) == 2
        assert provider.data_key(tenant, DATA, session=s)[0] == 2
        assert provider.data_key(tenant, DATA)[0] == 1  # not committed yet
    assert provider.data_key(tenant, DATA)[0] == 2


def test_a_commit_of_the_callers_session_drops_cached_keys_it_destroyed(
    provider: LocalKeyProvider,
):
    tenant = make_tenant_with_keys("alpha", provider=provider)
    assert provider.data_key(tenant, DATA)[0] == 1
    with tenant_session(system_ctx(tenant)) as s:
        provider.destroy(tenant, session=s)
        assert provider.data_key(tenant, DATA)[0] == 1  # not committed yet
    with pytest.raises(KeyDestroyedError):
        _ = provider.data_key(tenant, DATA)


AFTER_COMMIT = "after_commit"


# How many after_commit listeners a session would run, the class-level ones included.
def commit_listeners(session: Session) -> int:
    return len(getattr(session.dispatch, AFTER_COMMIT))


def test_fifty_rotations_in_one_session_register_no_listener_and_evict_once(
    provider: LocalKeyProvider, monkeypatch: pytest.MonkeyPatch
):
    evictions: list[UUID] = []
    evict = KeyCache.evict

    def counting(cache: KeyCache, tenant_id: UUID) -> None:
        evictions.append(tenant_id)
        evict(cache, tenant_id)

    monkeypatch.setattr(KeyCache, "evict", counting)
    tenant = make_tenant_with_keys("alpha", provider=provider)
    with tenant_session(system_ctx(tenant)) as s:
        listeners = commit_listeners(s)
        for expected in range(2, 52):
            assert provider.rotate(tenant, DATA, session=s) == expected
        assert commit_listeners(s) == listeners  # nothing registered per call
        assert evictions == []  # and nothing evicted before the commit
        s.commit()
        assert evictions == [tenant]  # one eviction, when the transaction commits
        _ = s.execute(text("SELECT 1"))
        s.commit()
        assert evictions == [tenant]  # the queue was emptied by the first commit
    assert provider.data_key(tenant, DATA)[0] == 51


def test_fifty_destroys_in_one_session_register_no_listener(
    provider: LocalKeyProvider,
):
    tenant = make_tenant_with_keys("alpha", provider=provider)
    with tenant_session(system_ctx(tenant)) as s:
        listeners = commit_listeners(s)
        for _ in range(50):
            provider.destroy(tenant, session=s)
        assert commit_listeners(s) == listeners
    with pytest.raises(KeyDestroyedError):
        _ = provider.data_key(tenant, DATA)


def test_a_released_savepoint_does_not_evict_before_the_outer_commit(
    provider: LocalKeyProvider,
):
    # after_commit also fires when a savepoint is released. Evicting then would let a
    # read refill the cache from the still-committed old state, and nothing would
    # evict that refill when the outer transaction finally commits.
    tenant = make_tenant_with_keys("alpha", provider=provider)
    assert provider.data_key(tenant, DATA)[0] == 1  # caches the version 1 pointer
    with tenant_session(system_ctx(tenant)) as s:
        with s.begin_nested():
            assert provider.rotate(tenant, DATA, session=s) == 2
        assert provider.data_key(tenant, DATA)[0] == 1  # savepoint released, no commit
    assert provider.data_key(tenant, DATA)[0] == 2


def test_a_rolled_back_rotation_changes_nothing(
    provider: LocalKeyProvider, admin_engine: Engine
):
    tenant = make_tenant_with_keys("alpha", provider=provider)
    before = key_rows(admin_engine, tenant)
    msg = "abort"
    with (
        pytest.raises(RuntimeError, match="abort"),
        tenant_session(system_ctx(tenant)) as s,
    ):
        assert provider.rotate(tenant, DATA, session=s) == 2
        raise RuntimeError(msg)
    assert key_rows(admin_engine, tenant) == before
    assert provider.data_key(tenant, DATA)[0] == 1
    assert provider.rotate(tenant, DATA) == 2  # the number is free again


# ---- sharing a provider ---------------------------------------------------------


def test_threads_sharing_a_provider_get_the_same_key(
    provider: LocalKeyProvider, admin_engine: Engine
):
    tenant = make_tenant_with_keys("alpha", provider=provider)
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(provider.data_key, tenant, DATA) for _ in range(24)]
        found = [future.result() for future in futures]
    assert len(set(found)) == 1
    assert found[0][0] == 1


def test_a_provider_needs_a_32_byte_kek():
    for kek in (b"", b"k" * 16, b"k" * 31, b"k" * 33):
        with pytest.raises(ValueError, match="32 bytes"):
            _ = LocalKeyProvider(kek, get_sessionmaker())


# ---- secrecy --------------------------------------------------------------------


# The key-encryption key and the tenant's data keys; reading them fills the cache.
def raw_secrets(provider: LocalKeyProvider, tenant: UUID) -> list[bytes]:
    return [KEK, *(provider.data_key(tenant, purpose)[1] for purpose in KeyPurpose)]


# The owner-engine reads of these helpers must not land in the SQL log under inspection.
@contextmanager
def quiet_sql() -> Generator[None]:
    logger = logging.getLogger("sqlalchemy.engine")
    level = logger.level
    logger.setLevel(logging.WARNING)
    try:
        yield
    finally:
        logger.setLevel(level)


def wrapped_keys(admin_engine: Engine, tenant: UUID) -> list[bytes]:
    with quiet_sql():
        wrapped = [row.wrapped_dek for row in key_rows(admin_engine, tenant)]
    return [blob for blob in wrapped if blob is not None]


def test_nothing_that_holds_key_bytes_prints_them(
    provider: LocalKeyProvider, admin_engine: Engine
):
    tenant = make_tenant_with_keys("alpha", provider=provider)
    secrets = [*raw_secrets(provider, tenant), *wrapped_keys(admin_engine, tenant)]
    assert leaked(repr(provider) + str(provider), *secrets) == []
    assert provider.kek_ref in repr(provider)
    with tenant_session(system_ctx(tenant)) as s:
        for row in s.scalars(select(TenantKey)):
            assert leaked(repr(row) + str(row), *secrets) == []


def test_errors_never_carry_key_bytes(provider: LocalKeyProvider, admin_engine: Engine):
    tenant = make_tenant_with_keys("alpha", provider=provider)
    secrets = [
        *raw_secrets(provider, tenant),
        *wrapped_keys(admin_engine, tenant),
        OTHER_KEK,
    ]
    stranger = LocalKeyProvider(OTHER_KEK, get_sessionmaker())
    failures = [
        lambda: stranger.data_key(tenant, DATA),
        lambda: provider.data_key(tenant, DATA, 9),
        lambda: provider.rotate(tenant, INDEX),
        lambda: provider.data_key(uuid4(), DATA),
    ]
    for failure in failures:
        with pytest.raises((DecryptionError, KeyNotFoundError, DomainError)) as caught:
            _ = failure()
        error = caught.value
        assert leaked(repr(error) + str(error) + repr(error.args), *secrets) == []
    provider.destroy(tenant)
    with pytest.raises(KeyDestroyedError) as destroyed:
        _ = provider.data_key(tenant, DATA)
    assert leaked(repr(destroyed.value) + str(destroyed.value), *secrets) == []


def test_tracebacks_that_capture_locals_never_show_key_bytes(
    provider: LocalKeyProvider, admin_engine: Engine
):
    # What a tracker such as Sentry sends when it captures local variables: every frame
    # of the failing call, with the repr of each local it holds.
    alpha = make_tenant_with_keys("alpha", provider=provider)
    beta = make_tenant_with_keys("beta", provider=provider)
    gone = make_tenant_with_keys("gone", provider=provider)
    secrets = [OTHER_KEK, WRONG_SIZED_KEK]
    for tenant in (alpha, beta, gone):
        secrets += raw_secrets(provider, tenant) + wrapped_keys(admin_engine, tenant)
    provider.destroy(gone)
    with admin_engine.begin() as conn:  # beta's wrapped DATA key moved into alpha's row
        _ = conn.execute(
            text(
                """
                UPDATE tenant_key SET wrapped_dek = (
                    SELECT wrapped_dek FROM tenant_key
                    WHERE tenant_id = :beta AND purpose = 'DATA')
                WHERE tenant_id = :alpha AND purpose = 'DATA'
                """
            ),
            {"alpha": alpha, "beta": beta},
        )
    cold = LocalKeyProvider(KEK, get_sessionmaker(), cache_ttl=timedelta(0))
    stranger = LocalKeyProvider(OTHER_KEK, get_sessionmaker(), cache_ttl=timedelta(0))
    holding_keys = {
        "wrong kek": lambda: stranger.data_key(beta, DATA),
        "moved wrapped key": lambda: cold.data_key(alpha, DATA),
        "unknown version": lambda: cold.data_key(beta, DATA, 9),
    }
    others = {
        "destroyed": lambda: cold.data_key(gone, DATA),
        "backfill of a destroyed tenant": lambda: cold.create_tenant_keys(gone),
        "index rotation": lambda: cold.rotate(beta, INDEX),
        "kek of the wrong size": lambda: LocalKeyProvider(
            WRONG_SIZED_KEK, get_sessionmaker()
        ),
    }
    for name, failure in {**holding_keys, **others}.items():
        report = locals_report(failure)
        assert re.search(SELF_REPR, report), name
        assert leaked(report, *secrets) == [], name
    for name, failure in holding_keys.items():
        assert "SecretBytes(***)" in locals_report(failure), name  # shown, but masked


def run_the_whole_lifecycle(
    provider: LocalKeyProvider, admin_engine: Engine
) -> tuple[list[bytes], list[bytes]]:
    """Create, read, rotate, destroy and fail, as the keys' only observer would.

    Returns:
        The raw secrets (key-encryption key and data keys) and the wrapped keys.

    """
    tenant = make_tenant("alpha")
    provider.create_tenant_keys(tenant)
    raw = raw_secrets(provider, tenant)
    assert provider.rotate(tenant, DATA) == 2
    raw.append(provider.data_key(tenant, DATA)[1])
    wrapped = wrapped_keys(admin_engine, tenant)
    provider.destroy(tenant)
    with pytest.raises(KeyDestroyedError):
        _ = provider.data_key(tenant, DATA)
    return raw, wrapped


def test_logs_never_carry_key_bytes(
    provider: LocalKeyProvider,
    admin_engine: Engine,
    caplog: pytest.LogCaptureFixture,
):
    caplog.set_level(logging.INFO)
    caplog.set_level(logging.INFO, logger="sqlalchemy.engine")
    raw, wrapped = run_the_whole_lifecycle(provider, admin_engine)
    assert "tenant_key" in caplog.text  # the statements are logged, minus parameters
    assert leaked(caplog.text, *raw, *wrapped) == []


def test_sql_debug_logging_never_carries_a_key_in_any_form(
    provider: LocalKeyProvider,
    admin_engine: Engine,
    caplog: pytest.LogCaptureFixture,
):
    # SQLAlchemy logs every result row at DEBUG; hide_parameters only hides bound
    # parameters. The wrapped_dek column hands out SecretBytes, which print as a mask,
    # so even the wrapped keys the provider reads stay out of the log.
    caplog.set_level(logging.DEBUG)
    caplog.set_level(logging.DEBUG, logger="sqlalchemy.engine")
    raw, wrapped = run_the_whole_lifecycle(provider, admin_engine)
    assert "Row (" in caplog.text  # result rows are being logged, so the scan bites
    assert "SecretBytes(***)" in caplog.text  # and the provider's own reads among them
    assert leaked(caplog.text, *raw, *wrapped) == []


# ---- the process-wide provider --------------------------------------------------


@pytest.fixture
def unconfigured() -> Generator[None]:
    try:
        previous: KeyProvider | None = get_key_provider()
    except RuntimeError:
        previous = None
    set_key_provider(None)
    yield
    set_key_provider(previous)


def test_get_key_provider_refuses_until_one_is_set(
    unconfigured: None, provider: LocalKeyProvider
):
    with pytest.raises(RuntimeError, match="key provider not configured"):
        _ = get_key_provider()
    set_key_provider(provider)
    assert get_key_provider() is provider
    set_key_provider(None)
    with pytest.raises(RuntimeError, match="key provider not configured"):
        _ = get_key_provider()


def test_make_tenant_with_keys_uses_the_configured_provider(
    unconfigured: None, provider: LocalKeyProvider, admin_engine: Engine
):
    set_key_provider(provider)
    tenant = make_tenant_with_keys("alpha")
    assert len(key_rows(admin_engine, tenant)) == len(KeyPurpose)
    assert provider.data_key(tenant, DATA)[0] == 1
