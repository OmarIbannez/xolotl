"""The reference a wrapped key carries to the master key that wrapped it (plan 1b, B7).

``tenant_key.kek_ref`` is ``local:v1:`` and 16 hex characters derived from the master
key. Reading a key checks it before anything is unwrapped, and writing a key checks it
before anything is written, so a process started with another ``XOLOTL_MASTER_KEY``
gets a clear error instead of a version nobody else can read.
"""

import hashlib
import hmac
import re
from datetime import datetime
from itertools import starmap
from pathlib import Path
from typing import NamedTuple
from uuid import UUID

import pytest
from fastapi import APIRouter
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text

from tests.helpers import (
    first_login,
    key_states,
    leaked,
    make_tenant,
    make_tenant_with_keys,
    new_tenant,
    system_ctx,
    wrapped_keys,
)
from xolotl.app import build_app
from xolotl.platform.auth.principal import StaffDep
from xolotl.platform.config import DEV_MASTER_KEY_BYTES, get_settings
from xolotl.platform.crypto import (
    DecryptionError,
    KekMismatchError,
    KeyDestroyedError,
    LocalKeyProvider,
    TenantKey,
    encrypt,
    get_key_provider,
    new_dek,
    set_key_provider,
    wrap_dek,
)
from xolotl.platform.crypto.blind_index import blind_index
from xolotl.platform.crypto.provider import KEK_SCHEME, kek_ref_for
from xolotl.platform.db import get_sessionmaker, tenant_session
from xolotl.platform.enums import KeyPurpose
from xolotl.platform.tenancy.context import TenantContext

DATA, BIOMETRIC = KeyPurpose.DATA, KeyPurpose.BIOMETRIC
KEK = bytes(range(32, 64))
OTHER_KEK = bytes(range(64, 96))
LABEL = b"xolotl:kek-ref:v1"
README = Path(__file__).parents[2] / "README.md"


class KeyRow(NamedTuple):
    purpose: str
    version: int
    wrapped_dek: bytes | None
    kek_ref: str
    retired_at: datetime | None
    destroyed_at: datetime | None


# Every key row of a tenant, read as the owner (outside row-level security).
def rows_of(admin: Engine, tenant_id: UUID) -> list[KeyRow]:
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


def relabel(admin: Engine, tenant_id: UUID, reference: str) -> None:
    with admin.begin() as conn:
        _ = conn.execute(
            text("UPDATE tenant_key SET kek_ref = :ref WHERE tenant_id = :tenant_id"),
            {"ref": reference, "tenant_id": tenant_id},
        )


@pytest.fixture
def provider(engine: Engine) -> LocalKeyProvider:
    return LocalKeyProvider(KEK, get_sessionmaker())


# A process that was started with another master key.
@pytest.fixture
def stranger(engine: Engine) -> LocalKeyProvider:
    return LocalKeyProvider(OTHER_KEK, get_sessionmaker())


# ---- the reference --------------------------------------------------------------


def test_the_reference_is_the_scheme_and_a_short_fingerprint_of_the_master_key():
    reference = kek_ref_for(KEK)
    assert reference == "local:v1:04a0e00102c26950"  # frozen: stored rows depend on it
    assert KEK_SCHEME == "local:v1"
    tag = hmac.new(KEK, LABEL, hashlib.sha256).hexdigest()[:16]
    assert reference == f"local:v1:{tag}"  # the first 8 bytes of HMAC-SHA-256, in hex
    assert re.fullmatch(r"local:v1:[0-9a-f]{16}", reference)
    assert kek_ref_for(OTHER_KEK) == "local:v1:15d893b9c7194203"
    assert kek_ref_for(bytes(KEK)) == reference


def test_the_reference_gives_nothing_of_the_key_away():
    reference = kek_ref_for(KEK)
    assert leaked(reference, KEK) == []
    # Not a bare hash of the key either: such a digest could be somebody's derived key.
    assert hashlib.sha256(KEK).hexdigest()[:16] not in reference


def test_the_public_dev_master_key_has_a_known_reference():
    assert kek_ref_for(DEV_MASTER_KEY_BYTES) == "local:v1:ccda9faa1d7fd54d"


def test_the_readme_documents_the_reference_it_derives():
    readme = " ".join(README.read_text(encoding="utf-8").split())  # lines unwrapped
    assert kek_ref_for(DEV_MASTER_KEY_BYTES) in readme  # the dev key's, quoted
    assert 'HMAC-SHA256(master key, "xolotl:kek-ref:v1")' in readme
    assert "SELECT DISTINCT kek_ref FROM tenant_key" in readme


def test_a_provider_knows_its_reference(
    provider: LocalKeyProvider, stranger: LocalKeyProvider
):
    assert provider.kek_ref == kek_ref_for(KEK)
    assert stranger.kek_ref == kek_ref_for(OTHER_KEK)
    assert repr(provider) == "LocalKeyProvider(kek_ref='local:v1:04a0e00102c26950')"
    assert leaked(repr(provider) + str(provider), KEK) == []


def test_a_created_tenant_has_keys_labelled_for_the_configured_master_key(
    engine: Engine, admin_engine: Engine
):
    created = new_tenant()
    reference = kek_ref_for(get_settings().master_key_bytes)
    rows = rows_of(admin_engine, created.tenant_id)
    assert len(rows) == len(KeyPurpose)
    assert {row.kek_ref for row in rows} == {reference}


def test_every_key_a_provider_writes_carries_its_reference(
    provider: LocalKeyProvider, admin_engine: Engine
):
    tenant = make_tenant_with_keys("alpha", provider=provider)
    assert provider.rotate(tenant, DATA) == 2
    assert provider.rotate(tenant, BIOMETRIC) == 2
    rows = rows_of(admin_engine, tenant)
    assert len(rows) == len(KeyPurpose) + 2
    assert {row.kek_ref for row in rows} == {provider.kek_ref}


# ---- reading a key --------------------------------------------------------------


def test_keys_wrapped_under_another_master_key_are_refused_with_a_clear_error(
    provider: LocalKeyProvider, stranger: LocalKeyProvider
):
    tenant = make_tenant_with_keys("alpha", provider=provider)
    assert provider.rotate(tenant, DATA) == 2
    reads = [
        lambda: stranger.data_key(tenant, DATA),
        lambda: stranger.data_key(tenant, DATA, 1),
        lambda: stranger.data_key(tenant, DATA, 2),
        lambda: stranger.data_key(tenant, BIOMETRIC),
        lambda: stranger.index_key(tenant),
    ]
    for read in reads:
        with pytest.raises(KekMismatchError) as caught:
            _ = read()
        error = caught.value
        assert (error.status, error.code) == (500, "KEK_MISMATCH")
        assert isinstance(error, DecryptionError)  # still a failure to decrypt
    assert provider.data_key(tenant, DATA)[0] == 2  # the right master key is unharmed


def test_the_reference_is_checked_before_anything_is_unwrapped(
    provider: LocalKeyProvider,
    stranger: LocalKeyProvider,
    monkeypatch: pytest.MonkeyPatch,
):
    tenant = make_tenant_with_keys("alpha", provider=provider)

    def unwrap(*args: object, **kwargs: object) -> None:
        pytest.fail("a key was unwrapped under the wrong master key")

    monkeypatch.setattr("xolotl.platform.crypto.provider.unwrap_dek", unwrap)
    with pytest.raises(KekMismatchError):
        _ = stranger.data_key(tenant, DATA)


def test_a_read_in_a_callers_transaction_is_checked_too(
    provider: LocalKeyProvider, stranger: LocalKeyProvider
):
    tenant = make_tenant_with_keys("alpha", provider=provider)
    with tenant_session(system_ctx(tenant)) as session:
        with pytest.raises(KekMismatchError):
            _ = stranger.data_key(tenant, DATA, session=session)
        assert provider.data_key(tenant, DATA, session=session)[0] == 1


def test_the_error_is_fixed_text_that_names_no_reference_and_no_key(
    provider: LocalKeyProvider, stranger: LocalKeyProvider, admin_engine: Engine
):
    tenant = make_tenant_with_keys("alpha", provider=provider)
    secrets = [KEK, OTHER_KEK, *wrapped_keys(admin_engine, tenant)]
    with pytest.raises(KekMismatchError) as caught:
        _ = stranger.data_key(tenant, DATA)
    error = caught.value
    shown = repr(error) + str(error) + repr(error.args) + error.detail
    assert error.detail == KekMismatchError.default_detail
    assert "master key" in error.detail
    assert leaked(shown, *secrets) == []
    assert "local:v1" not in shown  # the references stay out of what an API shows


def test_a_matching_reference_does_not_replace_the_authenticated_unwrap(
    provider: LocalKeyProvider, admin_engine: Engine
):
    # A row that is labelled for this master key but was wrapped under another one (a
    # corrupted or tampered row) still fails, as an ordinary decryption failure.
    tenant = make_tenant_with_keys("alpha", provider=provider)
    forged = bytes(
        wrap_dek(OTHER_KEK, new_dek(), tenant_id=tenant, purpose=DATA, version=1)
    )
    with admin_engine.begin() as conn:
        _ = conn.execute(
            text(
                """
                UPDATE tenant_key SET wrapped_dek = :wrapped
                WHERE tenant_id = :tenant_id AND purpose = 'DATA'
                """
            ),
            {"wrapped": forged, "tenant_id": tenant},
        )
    cold = LocalKeyProvider(KEK, get_sessionmaker())
    with pytest.raises(DecryptionError) as caught:
        _ = cold.data_key(tenant, DATA)
    assert not isinstance(caught.value, KekMismatchError)


@pytest.mark.parametrize(
    "reference",
    [
        "kms:arn:aws:kms:eu-west-1:111122223333:key/1234",
        "local:v2:04a0e00102c26950",
        "",
    ],
    ids=["another-adapter", "another-scheme-version", "empty"],
)
def test_a_key_labelled_for_another_adapter_is_refused(
    provider: LocalKeyProvider, admin_engine: Engine, reference: str
):
    tenant = make_tenant_with_keys("alpha", provider=provider)
    relabel(admin_engine, tenant, reference)
    cold = LocalKeyProvider(KEK, get_sessionmaker())
    with pytest.raises(KekMismatchError):
        _ = cold.data_key(tenant, DATA)


def test_a_destroyed_tenant_reports_its_destruction_whatever_the_master_key(
    provider: LocalKeyProvider, stranger: LocalKeyProvider
):
    tenant = make_tenant_with_keys("alpha", provider=provider)
    provider.destroy(tenant)
    with pytest.raises(KeyDestroyedError):
        _ = stranger.data_key(tenant, DATA)
    with pytest.raises(KeyDestroyedError):
        _ = stranger.data_key(tenant, DATA, 1)
    with pytest.raises(KeyDestroyedError):
        _ = stranger.rotate(tenant, DATA)
    with pytest.raises(KeyDestroyedError):
        stranger.create_tenant_keys(tenant)


# ---- keys made before references carried a fingerprint --------------------------


def test_unlabelled_keys_are_checked_by_the_unwrap_alone(
    provider: LocalKeyProvider, stranger: LocalKeyProvider, admin_engine: Engine
):
    # Rows written before the reference carried a fingerprint hold the bare scheme.
    tenant = make_tenant_with_keys("alpha", provider=provider)
    relabel(admin_engine, tenant, KEK_SCHEME)
    cold = LocalKeyProvider(KEK, get_sessionmaker())
    assert cold.data_key(tenant, DATA)[0] == 1  # the right master key still reads them
    with pytest.raises(DecryptionError) as caught:
        _ = stranger.data_key(tenant, DATA)  # another one is refused by the unwrap
    assert not isinstance(caught.value, KekMismatchError)


# ---- writing a key --------------------------------------------------------------


def test_rotating_under_another_master_key_refuses_before_writing(
    provider: LocalKeyProvider, stranger: LocalKeyProvider, admin_engine: Engine
):
    tenant = make_tenant_with_keys("alpha", provider=provider)
    before = rows_of(admin_engine, tenant)
    with pytest.raises(KekMismatchError):
        _ = stranger.rotate(tenant, DATA)
    assert rows_of(admin_engine, tenant) == before  # not even the retirement
    assert {state for _, _, state in key_states(admin_engine, tenant)} == {"current"}
    assert provider.rotate(tenant, DATA) == 2  # version 2 is still free for the owner


def test_adding_missing_keys_under_another_master_key_refuses_before_writing(
    provider: LocalKeyProvider, stranger: LocalKeyProvider, admin_engine: Engine
):
    tenant = make_tenant_with_keys("alpha", provider=provider)
    with admin_engine.begin() as conn:
        _ = conn.execute(text("DELETE FROM tenant_key WHERE purpose = 'INDEX'"))
    before = rows_of(admin_engine, tenant)
    with pytest.raises(KekMismatchError):
        stranger.create_tenant_keys(tenant)
    assert rows_of(admin_engine, tenant) == before
    provider.create_tenant_keys(tenant)
    assert {row.kek_ref for row in rows_of(admin_engine, tenant)} == {provider.kek_ref}
    assert len(rows_of(admin_engine, tenant)) == len(KeyPurpose)


def test_a_new_tenant_takes_the_reference_of_the_master_key_that_first_writes_for_it(
    stranger: LocalKeyProvider, admin_engine: Engine
):
    tenant = make_tenant("beta")
    stranger.create_tenant_keys(tenant)
    assert {row.kek_ref for row in rows_of(admin_engine, tenant)} == {stranger.kek_ref}


def test_a_tenant_that_has_every_key_needs_no_check_when_nothing_is_written(
    provider: LocalKeyProvider, stranger: LocalKeyProvider, admin_engine: Engine
):
    tenant = make_tenant_with_keys("alpha", provider=provider)
    before = rows_of(admin_engine, tenant)
    stranger.create_tenant_keys(tenant)
    assert rows_of(admin_engine, tenant) == before


def test_the_check_holds_in_a_callers_transaction(
    provider: LocalKeyProvider, stranger: LocalKeyProvider, admin_engine: Engine
):
    tenant = make_tenant_with_keys("alpha", provider=provider)
    before = rows_of(admin_engine, tenant)
    context = TenantContext.platform("cli:test", tenant_id=tenant)
    with tenant_session(context) as session:
        with pytest.raises(KekMismatchError):
            _ = stranger.rotate(tenant, DATA, session=session)
        assert provider.rotate(tenant, DATA, session=session) == 2
    assert len(rows_of(admin_engine, tenant)) == len(before) + 1


def _add_version_wrapped_under(
    admin: Engine, tenant_id: UUID, kek: bytes, purpose: KeyPurpose, version: int
) -> None:
    """Add the row an unchecked rotation under another master key used to leave."""
    wrapped = wrap_dek(
        kek, new_dek(), tenant_id=tenant_id, purpose=purpose, version=version
    )
    with admin.begin() as conn:
        _ = conn.execute(
            text(
                """
                UPDATE tenant_key SET retired_at = now()
                WHERE tenant_id = :tenant_id AND purpose = :purpose
                  AND version = :version - 1
                """
            ),
            {"tenant_id": tenant_id, "purpose": purpose.value, "version": version},
        )
    with tenant_session(system_ctx(tenant_id)) as session:
        session.add(
            TenantKey(
                tenant_id=tenant_id,
                purpose=purpose,
                version=version,
                wrapped_dek=wrapped,
                kek_ref=kek_ref_for(kek),
            )
        )


def test_a_chain_broken_by_another_master_key_is_mended_by_rotating_under_the_right_one(
    provider: LocalKeyProvider, admin_engine: Engine
):
    tenant = make_tenant_with_keys("alpha", provider=provider)
    _add_version_wrapped_under(admin_engine, tenant, OTHER_KEK, DATA, 2)
    cold = LocalKeyProvider(KEK, get_sessionmaker())
    with pytest.raises(KekMismatchError):
        _ = cold.data_key(tenant, DATA)  # the current version is the stranger's
    assert cold.data_key(tenant, DATA, 1)[0] == 1  # what the owner sealed still opens
    assert cold.rotate(tenant, DATA) == 3  # one of the live keys is the owner's
    assert cold.data_key(tenant, DATA)[0] == 3


def test_unlabelled_keys_are_proven_by_unwrapping_one_before_a_write(
    provider: LocalKeyProvider, stranger: LocalKeyProvider, admin_engine: Engine
):
    tenant = make_tenant_with_keys("alpha", provider=provider)
    relabel(admin_engine, tenant, KEK_SCHEME)
    before = rows_of(admin_engine, tenant)
    with pytest.raises(KekMismatchError):
        _ = stranger.rotate(tenant, DATA)  # nothing says whose they are, and none opens
    assert rows_of(admin_engine, tenant) == before
    assert provider.rotate(tenant, DATA) == 2  # the owner's key opens one: proven


def test_destroying_needs_no_master_key(
    provider: LocalKeyProvider, stranger: LocalKeyProvider, admin_engine: Engine
):
    tenant = make_tenant_with_keys("alpha", provider=provider)
    stranger.destroy(tenant)
    assert {state for _, _, state in key_states(admin_engine, tenant)} == {"destroyed"}
    with pytest.raises(KeyDestroyedError):
        _ = provider.data_key(tenant, DATA)


def test_each_tenant_is_judged_by_its_own_rows(
    provider: LocalKeyProvider, stranger: LocalKeyProvider
):
    mine = make_tenant_with_keys("mine", provider=provider)
    theirs = make_tenant_with_keys("theirs", provider=stranger)
    assert provider.data_key(mine, DATA)[0] == 1
    assert stranger.data_key(theirs, DATA)[0] == 1
    with pytest.raises(KekMismatchError):
        _ = provider.data_key(theirs, DATA)
    with pytest.raises(KekMismatchError):
        _ = stranger.data_key(mine, DATA)


# ---- what callers of the provider see -------------------------------------------


def test_values_and_blind_indexes_fail_with_the_clear_error_under_another_master_key(
    provider: LocalKeyProvider, stranger: LocalKeyProvider, no_key_provider: None
):
    tenant = make_tenant_with_keys("alpha", provider=provider)
    set_key_provider(provider)
    sealed = encrypt("ana@acme.mx", tenant_id=tenant)
    assert sealed.reveal() == "ana@acme.mx"
    set_key_provider(stranger)
    with pytest.raises(KekMismatchError):
        _ = sealed.reveal()
    with pytest.raises(KekMismatchError):
        _ = encrypt("other", tenant_id=tenant)
    with pytest.raises(KekMismatchError):
        _ = blind_index("curp", "GAXX800101HDFRRR09", tenant_id=tenant)
    set_key_provider(provider)
    assert sealed.reveal() == "ana@acme.mx"


def _probe_router() -> APIRouter:
    router = APIRouter()

    @router.get("/probe/key")
    def key_version(staff: StaffDep) -> dict[str, int]:
        tenant_id = staff.context.require_tenant_id()
        return {"version": get_key_provider().data_key(tenant_id, DATA)[0]}

    return router


def test_a_mismatch_is_a_500_problem_with_a_stable_code_and_no_reference(
    engine: Engine, stranger: LocalKeyProvider, no_key_provider: None
):
    app = build_app()  # installs the provider the configuration names
    app.include_router(_probe_router(), prefix="/v1")
    created = new_tenant()
    with TestClient(app) as client:
        headers = first_login(
            client,
            "acme",
            created.admin_email,
            created.admin_password,
            "a-long-new-secret-phrase",
        )
        assert client.get("/v1/probe/key", headers=headers).json() == {"version": 1}
        set_key_provider(stranger)  # a replica that was started with another key
        refused = client.get("/v1/probe/key", headers=headers)
    assert refused.status_code == 500
    assert refused.headers["content-type"] == "application/problem+json"
    body = refused.json()
    assert (body["code"], body["title"]) == ("KEK_MISMATCH", "Internal Server Error")
    assert body["detail"] == KekMismatchError.default_detail
    assert "local:v1" not in refused.text
