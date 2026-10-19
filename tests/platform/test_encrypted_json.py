"""``EncryptedJson``: a JSON object stored encrypted, built like ``EncryptedStr``.

(Plan 1b task B8.) The idempotency store keeps a whole response, which is a JSON object,
so the column type takes an object where ``EncryptedStr`` takes text. Everything that
makes the text column safe applies to it, and ``test_encrypted.py`` pins that for text:
the value is sealed in the flushing session under the key of the tenant *the row names*,
text bound outside a flush is sealed through the provider's own session, a value of
another purpose is refused, a bulk UPDATE leaves no plain value in the identity map and
a pending assignment survives one. These tests pin that the object column is wired to
the same code, and the parts that are its own: the codec, which Python values the
column seals, and that the two column types never seal each other's values.

Everything runs against the real database through a throwaway tenant table on a
metadata of its own (so neither the schema-sync test nor the isolation harness sees
it): real commits, real row-level security, a real key provider. Every secret a test
uses comes from ``secret_value``, which remembers it; the last test scans the whole
module's log capture for all of them.
"""

import dataclasses
import json
import logging
import traceback
from collections.abc import Generator, Iterable
from contextlib import contextmanager
from typing import Any, ClassVar, override
from uuid import UUID, uuid4

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import Engine, MetaData, Text, insert, select, text, update
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import StatementError
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from tests.helpers import (
    leaked,
    locals_report,
    make_tenant,
    make_tenant_with_keys,
    platform_ctx,
    system_ctx,
)
from xolotl.platform.audit import canonical_json
from xolotl.platform.crypto.encrypted import (
    JSON_CODEC,
    Encrypted,
    EncryptedJson,
    EncryptedStr,
    JsonCodec,
    encrypt_json,
)
from xolotl.platform.crypto.errors import (
    DecryptionError,
    KeyDestroyedError,
    KeyNotFoundError,
    TenantScopeError,
)
from xolotl.platform.crypto.primitives import (
    HEADER_LEN,
    NONCE_LEN,
    TAG_LEN,
    aad_for,
    decrypt_value,
    encrypt_value,
)
from xolotl.platform.crypto.provider import (
    KeyProvider,
    LocalKeyProvider,
    get_key_provider,
    set_key_provider,
)
from xolotl.platform.db import (
    NAMING_CONVENTION,
    apply_context,
    get_admin_engine,
    get_sessionmaker,
    tenant_session,
)
from xolotl.platform.enums import KeyPurpose
from xolotl.platform.errors import ContextMissingError
from xolotl.platform.tenancy.context import TenantContext, bind_context
from xolotl.platform.tenancy.ddl import TenantTableMixin, rls_statements

pytestmark = pytest.mark.usefixtures("module_log")

KEK = bytes(range(64, 96))
DATA, INDEX, BIOMETRIC = KeyPurpose.DATA, KeyPurpose.INDEX, KeyPurpose.BIOMETRIC
DIALECT = postgresql.dialect()

SECRETS: list[str] = []


# A unique secret that looks like a one-time credential; the last test looks for every
# one of them in the logs.
def secret_value(label: str) -> str:
    value = f"{label}-{uuid4().hex[:12]}"
    SECRETS.append(value)
    return value


# A JSON object shaped like a stored response, with a secret in it.
def a_document(label: str) -> dict[str, object]:
    return {
        "status": 201,
        "media_type": "application/json",
        "body": {"key": secret_value(label), "scopes": ["tenants:read"], "ñ": None},
        "raw": None,
    }


# The column also accepts a plain object where an Encrypted is declared ...
def as_object(value: dict[str, object]) -> Any:
    return value


# ... and what it must refuse, text where an object is declared, gets through the type
# checker the same way.
def as_text(value: str) -> Any:
    return value


# ---- the codec ------------------------------------------------------------------


def test_the_json_codec_writes_canonical_json_in_utf8():
    value: dict[str, object] = {
        "b": [1, {"y": None, "x": True}],
        "a": "pérez ñandú 日本",
    }
    reordered: dict[str, object] = {
        "a": "pérez ñandú 日本",
        "b": [1, {"x": True, "y": None}],
    }
    expected = '{"a":"pérez ñandú 日本","b":[1,{"x":true,"y":null}]}'.encode()
    assert JSON_CODEC.encode(value) == JSON_CODEC.encode(reordered) == expected
    assert JSON_CODEC.encode(value) == canonical_json(value)
    assert isinstance(JSON_CODEC, JsonCodec)


def test_the_json_codec_round_trips_an_object():
    document = a_document("codec")
    assert JSON_CODEC.decode(JSON_CODEC.encode(document)) == document
    assert JSON_CODEC.decode(b"{}") == {}


@pytest.mark.parametrize("data", [b"[1]", b'"x"', b"1", b"null", b"true"])
def test_the_json_codec_refuses_json_that_is_not_an_object(data: bytes):
    with pytest.raises(ValueError, match="JSON object"):
        _ = JSON_CODEC.decode(data)


@pytest.mark.parametrize(
    "data", [b"{", b"", b'{"a":1} trailing', b"\xff\xfe{}", b"{'a': 1}"]
)
def test_the_json_codec_refuses_bytes_that_are_not_json(data: bytes):
    with pytest.raises((json.JSONDecodeError, UnicodeDecodeError)) as refused:
        _ = JSON_CODEC.decode(data)
    assert isinstance(refused.value, ValueError)  # the Codec contract


# ---- keys and providers ---------------------------------------------------------


class CountingFactory:
    """Counts the sessions the provider opens for itself."""

    def __init__(self) -> None:
        self.opened: int = 0

    def __call__(self) -> Session:
        self.opened += 1
        return get_sessionmaker()()


@pytest.fixture
def reads() -> CountingFactory:
    return CountingFactory()


@pytest.fixture
def provider(engine: Engine, reads: CountingFactory) -> Generator[LocalKeyProvider]:
    installed = LocalKeyProvider(KEK, reads)
    try:
        previous: KeyProvider | None = get_key_provider()
    except RuntimeError:
        previous = None
    set_key_provider(installed)
    yield installed
    set_key_provider(previous)


# ---- encrypt_json and reveal ----------------------------------------------------


def test_encrypt_json_then_reveal_returns_the_object(provider: LocalKeyProvider):
    alpha = make_tenant_with_keys("alpha")
    document = a_document("round-trip")
    value = encrypt_json(document, tenant_id=alpha)
    assert value.reveal() == document
    assert (value.tenant_id, value.purpose, value.version) == (alpha, DATA, 1)
    assert repr(value) == str(value) == "Encrypted(***)"
    # The layout is the text value's: version, nonce, ciphertext and tag, and the
    # tenant and purpose as authenticated data. The plaintext is the canonical JSON.
    raw, canonical = value.ciphertext, JSON_CODEC.encode(document)
    assert raw[:HEADER_LEN] == b"\x00\x01"
    assert len(raw) == HEADER_LEN + NONCE_LEN + len(canonical) + TAG_LEN
    assert canonical not in raw
    dek = provider.data_key(alpha, DATA)[1]
    opened = decrypt_value(dek, ciphertext=raw, aad=alpha.bytes + b"DATA")
    assert opened == canonical
    assert encrypt_json(document, tenant_id=alpha) != value  # a fresh nonce each time


def test_encrypt_json_takes_the_keys_of_the_tenant_it_is_given(
    provider: LocalKeyProvider,
):
    alpha = make_tenant_with_keys("alpha")
    with pytest.raises(ValueError, match="INDEX"):
        _ = encrypt_json({}, tenant_id=alpha, purpose=INDEX)
    biometric = encrypt_json({"a": 1}, tenant_id=alpha, purpose=BIOMETRIC)
    assert (biometric.purpose, biometric.reveal()) == (BIOMETRIC, {"a": 1})
    keyless = make_tenant("keyless")
    with pytest.raises(KeyNotFoundError):
        _ = encrypt_json({"a": 1}, tenant_id=keyless)


def test_a_json_value_does_not_open_for_another_tenant_or_purpose(
    provider: LocalKeyProvider,
):
    alpha, beta = make_tenant_with_keys("alpha"), make_tenant_with_keys("beta")
    document = a_document("cross-tenant")
    value = encrypt_json(document, tenant_id=alpha)
    for stranger in (
        dataclasses.replace(value, tenant_id=beta),
        dataclasses.replace(value, purpose=BIOMETRIC),
    ):
        with pytest.raises(DecryptionError) as refused:
            _ = stranger.reveal()
        assert str(refused.value) == "decryption failed"
        assert json.dumps(document["body"]) not in repr(refused.value.args)


@pytest.mark.parametrize(
    ("plain", "hidden"),
    [
        (b'["secret-list-member"]', "secret-list-member"),
        (b'{"secret-broken-member": ', "secret-broken-member"),
        (b'\xff\xfe{"secret-odd-member": 1}', "secret-odd-member"),
    ],
    ids=["not-an-object", "malformed", "not-utf8"],
)
def test_json_that_cannot_be_read_is_a_decryption_failure_that_keeps_nothing_of_it(
    provider: LocalKeyProvider, plain: bytes, hidden: str
):
    alpha = make_tenant_with_keys("alpha")
    dek = provider.data_key(alpha, DATA)[1]
    raw = encrypt_value(dek, version=1, plaintext=plain, aad=aad_for(alpha, DATA))
    value = Encrypted(
        ciphertext=raw, tenant_id=alpha, purpose=DATA, version=1, _codec=JSON_CODEC
    )
    with pytest.raises(DecryptionError) as refused:
        _ = value.reveal()
    error = refused.value
    assert str(error) == "decryption failed"
    assert hidden not in "".join(traceback.format_exception(error))
    # A JSONDecodeError keeps the whole document in .doc, and anything that walks
    # __context__ finds it: nothing may be chained, nor held by a frame that an error
    # tracker prints.
    assert error.__context__ is None
    assert error.__cause__ is None
    report = locals_report(value.reveal)
    assert hidden not in report
    assert leaked(report, plain, dek) == []


# ---- the column -----------------------------------------------------------------


class ScratchBase(DeclarativeBase):
    metadata: ClassVar[MetaData] = MetaData(naming_convention=NAMING_CONVENTION)


class Document(TenantTableMixin, ScratchBase):
    """A tenant table with an object column and a text column, for these tests."""

    __tablename__: str = "scratch_document"

    label: Mapped[str] = mapped_column(Text)
    body: Mapped[Encrypted[dict[str, object]] | None] = mapped_column(EncryptedJson)
    note: Mapped[Encrypted[str] | None] = mapped_column(EncryptedStr)


class Loose(ScratchBase):
    """No tenant_id column: an encrypted value here has no tenant to seal it for."""

    __tablename__: str = "scratch_loose"

    id: Mapped[int] = mapped_column(primary_key=True)
    body: Mapped[Encrypted[dict[str, object]] | None] = mapped_column(EncryptedJson)


@pytest.fixture(scope="module")
def scratch_table(engines: tuple[Engine, Engine]) -> Generator[None]:
    admin = engines[1]
    ScratchBase.metadata.drop_all(admin)  # whatever an interrupted run left behind
    ScratchBase.metadata.create_all(admin)
    with admin.begin() as conn:
        for statement in rls_statements("scratch_document"):
            _ = conn.execute(text(statement))
    yield
    ScratchBase.metadata.drop_all(admin)


# The body column of every row as the database holds it, in label order.
def stored(admin: Engine) -> list[bytes | None]:
    with admin.connect() as conn:
        rows = conn.execute(text("SELECT body FROM scratch_document ORDER BY label"))
        return [row[0] for row in rows]


# The tenant and body of every row as the database holds it, by label.
def stored_by_label(admin: Engine) -> dict[str, tuple[UUID, bytes | None]]:
    with admin.connect() as conn:
        rows = conn.execute(text("SELECT label, tenant_id, body FROM scratch_document"))
        return {row[0]: (row[1], row[2]) for row in rows}


def add_document(
    tenant_id: UUID, label: str, value: Encrypted[dict[str, object]] | None
) -> None:
    with tenant_session(system_ctx(tenant_id)) as s:
        s.add(Document(tenant_id=tenant_id, label=label, body=value))


# A session on the owner engine, which migrations, the CLI and data-fix scripts use: the
# test database's owner is a superuser, so row-level security does not bind it. The
# session's context is applied; the context variable is left to the caller.
@contextmanager
def owner_session(session_ctx: TenantContext) -> Generator[Session]:
    session = sessionmaker(bind=get_admin_engine(), expire_on_commit=False)()
    try:
        apply_context(session, session_ctx)
        yield session
        session.commit()
    except BaseException:
        session.rollback()
        raise
    finally:
        session.close()


# The object opens with its owner's key, and with nobody else's.
def assert_readable_only_by(
    provider: LocalKeyProvider,
    owner: UUID,
    others: Iterable[UUID],
    sealed_body: bytes | None,
    document: dict[str, object],
) -> None:
    assert sealed_body is not None
    opened = decrypt_value(
        provider.data_key(owner, DATA)[1],
        ciphertext=sealed_body,
        aad=aad_for(owner, DATA),
    )
    assert opened == JSON_CODEC.encode(document)
    for other in others:
        stranger = provider.data_key(other, DATA)[1]
        for aad in (aad_for(other, DATA), aad_for(owner, DATA)):
            with pytest.raises(DecryptionError):
                _ = decrypt_value(stranger, ciphertext=sealed_body, aad=aad)
        value = Encrypted(
            ciphertext=sealed_body,
            tenant_id=other,
            purpose=DATA,
            version=1,
            _codec=JSON_CODEC,
        )
        with pytest.raises(DecryptionError):
            _ = value.reveal()


def only_the_scratch_tables(
    name: str | None, type_: str, _parent_names: object
) -> bool:
    return type_ != "table" or name in {"scratch_document", "scratch_loose"}


def test_the_column_is_a_plain_bytea_to_alembic_and_the_schema_sync_test(
    scratch_table: None, admin_engine: Engine
):
    # 0005 turns a real column into an EncryptedJson; the schema-sync test must not see
    # a difference between the ORM type and the bytea the migration leaves.
    with admin_engine.connect() as conn:
        context = MigrationContext.configure(
            conn,
            opts={
                "compare_type": True,
                "compare_server_default": True,
                "include_name": only_the_scratch_tables,
            },
        )
        assert compare_metadata(context, ScratchBase.metadata) == []
    columns = {
        c["name"]: c for c in sa_inspect(admin_engine).get_columns("scratch_document")
    }
    assert str(columns["body"]["type"]) == str(columns["note"]["type"]) == "BYTEA"
    assert columns["body"]["nullable"] is True


def test_an_object_round_trips_through_the_column(
    provider: LocalKeyProvider, scratch_table: None, admin_engine: Engine
):
    alpha = make_tenant_with_keys("alpha")
    document = a_document("column")
    value = encrypt_json(document, tenant_id=alpha)
    add_document(alpha, "one", value)
    with tenant_session(system_ctx(alpha)) as s:
        loaded = s.scalars(select(Document)).one().body
        assert isinstance(loaded, Encrypted)
        assert loaded == value
        assert (loaded.tenant_id, loaded.purpose, loaded.version) == (alpha, DATA, 1)
        assert loaded.reveal() == document
    assert stored(admin_engine) == [value.ciphertext]  # stored as is
    assert JSON_CODEC.encode(document) not in value.ciphertext


def test_null_stays_null_in_both_directions(
    provider: LocalKeyProvider, scratch_table: None, admin_engine: Engine
):
    alpha = make_tenant_with_keys("alpha")
    add_document(alpha, "none", None)
    assert stored(admin_engine) == [None]
    with tenant_session(system_ctx(alpha)) as s:
        assert s.scalars(select(Document)).one().body is None


def test_an_object_assigned_to_an_attribute_is_sealed_in_the_flushing_session(
    provider: LocalKeyProvider,
    reads: CountingFactory,
    scratch_table: None,
    monkeypatch: pytest.MonkeyPatch,
):
    alpha = make_tenant("alpha")  # no keys yet
    document, note = a_document("flush"), secret_value("flush-note")
    calls: list[tuple[UUID, Session | None]] = []
    real = provider.data_key

    def spy(tenant_id, purpose, version=None, *, session=None):
        calls.append((tenant_id, session))
        return real(tenant_id, purpose, version, session=session)

    monkeypatch.setattr(provider, "data_key", spy)
    with tenant_session(system_ctx(alpha)) as s:
        provider.create_tenant_keys(
            alpha, session=s
        )  # uncommitted: nobody else sees them
        calls.clear()
        opened = reads.opened
        row = Document(tenant_id=alpha, label="object", body=as_object(document))
        row.note = as_text(note)  # the text column of the same row, in the same flush
        s.add(row)
        s.flush()
        assert calls == [(alpha, s)]  # one read for both columns, via this session
        assert reads.opened == opened  # no second connection
        assert isinstance(row.body, Encrypted)  # the instance holds no plain object
        assert isinstance(row.note, Encrypted)
        assert (row.body.version, row.body.tenant_id) == (1, alpha)
    with tenant_session(system_ctx(alpha)) as s:
        loaded = s.scalars(select(Document)).one()
        assert loaded.body is not None
        assert loaded.note is not None
        assert (loaded.body.reveal(), loaded.note.reveal()) == (document, note)


def test_each_column_type_seals_only_its_own_kind_of_value(
    provider: LocalKeyProvider, scratch_table: None, admin_engine: Engine
):
    # Text where an object is declared, and an object where text is: neither is sealed
    # with the other's codec, and nothing is stored.
    alpha = make_tenant_with_keys("alpha")
    wrong = {
        "text-in-body": Document(tenant_id=alpha, label="x", body=as_text("text")),
        "object-in-note": Document(
            tenant_id=alpha, label="y", note=as_object({"a": 1})
        ),
    }
    for row in wrong.values():
        with (
            pytest.raises(StatementError) as refused,
            tenant_session(system_ctx(alpha)) as s,
        ):
            s.add(row)
            s.flush()
        assert isinstance(refused.value.orig, TypeError)
    assert stored(admin_engine) == []


def test_one_flush_seals_each_row_under_its_own_tenants_key(
    provider: LocalKeyProvider,
    scratch_table: None,
    admin_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
):
    alpha, beta = make_tenant_with_keys("alpha"), make_tenant_with_keys("beta")
    a_body, b_body = a_document("alpha-owner"), a_document("beta-owner")
    calls: list[tuple[UUID, Session | None]] = []
    real = provider.data_key

    def spy(tenant_id, purpose, version=None, *, session=None):
        calls.append((tenant_id, session))
        return real(tenant_id, purpose, version, session=session)

    monkeypatch.setattr(provider, "data_key", spy)
    # The ambient context is alpha's, which is where every row would be sealed if the
    # tenant came from the context and not from the row.
    with bind_context(system_ctx(alpha)), owner_session(platform_ctx()) as s:
        s.add(Document(tenant_id=alpha, label="a", body=as_object(a_body)))
        s.add(Document(tenant_id=beta, label="b", body=as_object(b_body)))
        s.add(Document(tenant_id=beta, label="c", body=as_object(b_body)))
        s.flush()
        # One read per tenant, each through this session.
        assert sorted(calls, key=lambda call: call[0].int) == sorted(
            [(alpha, s), (beta, s)], key=lambda call: call[0].int
        )
    rows = stored_by_label(admin_engine)
    assert_readable_only_by(provider, alpha, [beta], rows["a"][1], a_body)
    assert_readable_only_by(provider, beta, [alpha], rows["b"][1], b_body)
    assert_readable_only_by(provider, beta, [alpha], rows["c"][1], b_body)


@pytest.mark.parametrize("kind", ["owner", "app"])
def test_a_session_bound_to_one_tenant_refuses_an_object_for_a_row_of_another(
    provider: LocalKeyProvider, scratch_table: None, admin_engine: Engine, kind: str
):
    alpha, beta = make_tenant_with_keys("alpha"), make_tenant_with_keys("beta")
    bound = system_ctx(alpha)
    session_scope = owner_session(bound) if kind == "owner" else tenant_session(bound)
    with (
        pytest.raises(TenantScopeError, match="another tenant") as refused,
        session_scope as s,
    ):
        s.add(Document(tenant_id=alpha, label="a", body=as_object(a_document("mine"))))
        s.add(Document(tenant_id=beta, label="b", body=as_object(a_document("theirs"))))
        s.flush()
    assert refused.value.status == 500
    assert stored_by_label(admin_engine) == {}  # refused before anything was written


def test_an_object_for_a_row_that_has_no_tenant_is_refused(
    provider: LocalKeyProvider, scratch_table: None
):
    alpha = make_tenant_with_keys("alpha")
    document = a_document("homeless")
    row = Document(label="x", body=as_object(document))  # tenant_id is still unset
    with (
        pytest.raises(TenantScopeError, match="tenant_id") as refused,
        owner_session(platform_ctx()) as s,
    ):
        s.add(row)
        s.flush()
    assert json.dumps(document["body"]) not in str(refused.value) + repr(refused.value)
    assert row.body == document  # untouched: nothing was sealed under a guess
    with (
        pytest.raises(TenantScopeError, match="tenant_id"),
        tenant_session(system_ctx(alpha)) as s,
    ):
        s.add(Loose(id=1, body=as_object(document)))  # a table with no tenant column
        s.flush()


def test_an_object_assigned_to_a_loaded_row_is_sealed_at_the_update(
    provider: LocalKeyProvider, scratch_table: None, admin_engine: Engine
):
    alpha = make_tenant_with_keys("alpha")
    before, after = a_document("before"), a_document("after")
    add_document(alpha, "one", encrypt_json(before, tenant_id=alpha))
    with tenant_session(system_ctx(alpha)) as s:
        row = s.scalars(select(Document)).one()
        row.body = as_object(after)
        s.flush()
        assert isinstance(row.body, Encrypted)
    assert_readable_only_by(provider, alpha, [], stored(admin_engine)[0], after)


def test_the_column_stores_values_of_the_data_purpose_only(
    provider: LocalKeyProvider, scratch_table: None, admin_engine: Engine
):
    alpha = make_tenant_with_keys("alpha")
    column_type = EncryptedJson()
    mine = encrypt_json(a_document("data"), tenant_id=alpha)
    assert column_type.process_bind_param(mine, DIALECT) == mine.ciphertext
    for purpose in (INDEX, BIOMETRIC):
        stranger = dataclasses.replace(mine, purpose=purpose)
        with pytest.raises(ValueError, match="DATA values only") as refused:
            _ = column_type.process_bind_param(stranger, DIALECT)
        assert mine.ciphertext.hex() not in str(refused.value)
    biometric = encrypt_json(a_document("template"), tenant_id=alpha, purpose=BIOMETRIC)
    with (
        pytest.raises(StatementError) as refused,
        tenant_session(system_ctx(alpha)) as s,
    ):
        s.add(Document(tenant_id=alpha, label="x", body=biometric))
        s.flush()
    assert isinstance(refused.value.orig, ValueError)
    assert stored(admin_engine) == []  # it can never be opened as DATA, so not stored


def test_the_column_refuses_values_that_are_neither_an_object_nor_encrypted():
    column_type = EncryptedJson()
    assert column_type.process_bind_param(None, DIALECT) is None
    for refused in (42, b"bytes", 1.5, ["x"], "text"):
        with pytest.raises(TypeError, match="Encrypted value or") as error:
            _ = column_type.process_bind_param(refused, DIALECT)
        assert repr(refused) not in str(error.value)


def test_loading_an_object_needs_a_tenant_scoped_context(provider: LocalKeyProvider):
    alpha = make_tenant_with_keys("alpha")
    raw = encrypt_json(a_document("load"), tenant_id=alpha).ciphertext
    column_type = EncryptedJson()
    with pytest.raises(ContextMissingError):
        _ = column_type.process_result_value(raw, DIALECT)
    with (
        bind_context(platform_ctx()),
        pytest.raises(ContextMissingError, match="tenant"),
    ):
        _ = column_type.process_result_value(raw, DIALECT)
    with bind_context(system_ctx(alpha)):
        loaded = column_type.process_result_value(raw, DIALECT)
    assert loaded is not None
    assert (loaded.tenant_id, loaded.purpose, loaded.version) == (alpha, DATA, 1)
    assert loaded.ciphertext == raw
    assert column_type.process_result_value(None, DIALECT) is None


def test_an_object_bound_outside_a_flush_is_sealed_with_a_session_of_the_providers_own(
    provider: LocalKeyProvider,
    reads: CountingFactory,
    scratch_table: None,
    admin_engine: Engine,
):
    alpha = make_tenant_with_keys("alpha")
    document = a_document("core")
    opened = reads.opened
    with tenant_session(system_ctx(alpha)) as s:
        _ = s.execute(
            insert(Document).values(tenant_id=alpha, label="c", body=document)
        )
    assert reads.opened == opened + 1  # one cache miss, one session of its own
    assert_readable_only_by(provider, alpha, [], stored(admin_engine)[0], document)
    with tenant_session(system_ctx(alpha)) as s:
        loaded = s.scalars(select(Document)).one().body
        assert loaded is not None
        assert loaded.reveal() == document


def test_keys_that_fail_while_binding_an_object_surface_inside_a_statement_error(
    provider: LocalKeyProvider, scratch_table: None
):
    alpha = make_tenant_with_keys("alpha")
    document = a_document("binding")
    provider.destroy(alpha)
    with (
        pytest.raises(StatementError) as refused,
        tenant_session(system_ctx(alpha)) as s,
    ):
        _ = s.execute(
            insert(Document).values(tenant_id=alpha, label="x", body=document)
        )
    assert isinstance(refused.value.orig, KeyDestroyedError)
    # The engines hide bound parameters, so the statement error does not print them.
    assert json.dumps(document["body"]) not in str(refused.value) + repr(refused.value)
    assert str(document["body"]) not in str(refused.value) + repr(refused.value)


def test_an_object_in_an_orm_bulk_update_does_not_stay_in_the_identity_map(
    provider: LocalKeyProvider, scratch_table: None, admin_engine: Engine
):
    # The ORM applies the SET values of a bulk UPDATE to the loaded instances (the
    # default synchronisation), and the platform's sessions do not expire on commit.
    alpha = make_tenant_with_keys("alpha")
    after = a_document("bulk-after")
    add_document(alpha, "one", encrypt_json(a_document("bulk-before"), tenant_id=alpha))
    session = get_sessionmaker()()
    try:
        apply_context(session, system_ctx(alpha))
        with bind_context(system_ctx(alpha)):
            row = session.scalars(select(Document)).one()
            assert isinstance(row.body, Encrypted)
            statement = (
                update(Document)
                .where(Document.tenant_id == alpha)
                .values(body=after, label=Document.label + "!")
            )
            _ = session.execute(statement)
            session.commit()
            assert not isinstance(vars(row).get("body"), dict)  # no raw object kept
            assert row.body is not None  # and it reads back as the stored value
            assert row.body.reveal() == after
            held = row.body
            nothing = (
                update(Document).where(Document.label == "none").values(body=after)
            )
            _ = session.execute(nothing)
            assert vars(row).get("body") is held  # an UPDATE of nothing leaves it alone
    finally:
        session.close()
    assert_readable_only_by(provider, alpha, [], stored(admin_engine)[0], after)
    assert set(stored_by_label(admin_engine)) == {"one!"}  # run exactly once


@pytest.mark.parametrize("target", ["same-mapper", "other-mapper"])
def test_a_pending_object_survives_an_orm_update_that_changes_nothing(
    provider: LocalKeyProvider, scratch_table: None, admin_engine: Engine, target: str
):
    # With autoflush off the assignment is still pending when the UPDATE runs, and the
    # hook that expires what a bulk UPDATE leaves behind must not expire it.
    alpha = make_tenant_with_keys("alpha")
    add_document(alpha, "one", encrypt_json(a_document("old"), tenant_id=alpha))
    pending, elsewhere = a_document("pending"), a_document("elsewhere")
    if target == "same-mapper":
        nothing = (
            update(Document).where(Document.label == "no-such").values(body=elsewhere)
        )
    else:
        nothing = update(Loose).where(Loose.id == -1).values(body=elsewhere)
    with bind_context(system_ctx(alpha)), owner_session(system_ctx(alpha)) as s:
        row = s.scalars(select(Document)).one()
        with s.no_autoflush:
            row.body = as_object(pending)
            _ = s.execute(nothing)
            assert vars(row).get("body") == pending  # still there, still unflushed
            assert vars(row).get("label") == "one"  # and nothing else was expired
    # The commit at the end of the block flushed it: sealed, and under alpha's key.
    assert_readable_only_by(provider, alpha, [], stored(admin_engine)[0], pending)


def test_text_in_a_text_column_is_still_sealed_beside_an_object_column(
    provider: LocalKeyProvider, scratch_table: None, admin_engine: Engine
):
    # Two column types in one row, both given plain values: each is sealed by its own
    # codec, so the text column holds UTF-8 text, not JSON.
    alpha = make_tenant_with_keys("alpha")
    note, document = secret_value("note"), a_document("both")
    with tenant_session(system_ctx(alpha)) as s:
        s.add(
            Document(
                tenant_id=alpha,
                label="both",
                body=as_object(document),
                note=as_text(note),
            )
        )
    with tenant_session(system_ctx(alpha)) as s:
        row = s.scalars(select(Document)).one()
        assert row.body is not None
        assert row.note is not None
        assert row.body.reveal() == document
        assert row.note.reveal() == note
    with admin_engine.connect() as conn:
        raw_body, raw_note = conn.execute(
            text("SELECT body, note FROM scratch_document")
        ).one()
    dek, aad = provider.data_key(alpha, DATA)[1], aad_for(alpha, DATA)
    assert decrypt_value(dek, ciphertext=raw_body, aad=aad) == JSON_CODEC.encode(
        document
    )
    assert decrypt_value(dek, ciphertext=raw_note, aad=aad) == note.encode()


# ---- what the logs and the errors never hold ------------------------------------


class Collector(logging.Handler):
    """Formats every record, tracebacks included, into a list."""

    def __init__(self) -> None:
        super().__init__(logging.DEBUG)
        self.lines: list[str] = []

    @override
    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append(self.format(record))


@pytest.fixture(scope="module")
def module_log() -> Generator[Collector]:
    collector = Collector()
    root, engine_logger = logging.getLogger(), logging.getLogger("sqlalchemy.engine")
    levels = (root.level, engine_logger.level)
    root.addHandler(collector)
    root.setLevel(logging.DEBUG)
    engine_logger.setLevel(logging.INFO)  # statements are logged, parameters hidden
    yield collector
    root.removeHandler(collector)
    root.setLevel(levels[0])
    engine_logger.setLevel(levels[1])


def test_the_last_test_finds_no_secret_in_the_modules_log(
    module_log: Collector, provider: LocalKeyProvider, scratch_table: None
):
    # One more round through the column, so the capture holds statements for it.
    alpha = make_tenant_with_keys("alpha")
    last = a_document("last")
    add_document(alpha, "last", encrypt_json(last, tenant_id=alpha))
    with tenant_session(system_ctx(alpha)) as s:
        loaded = s.scalars(select(Document)).one().body
        assert loaded is not None
        assert loaded.reveal(session=s) == last
    captured = "\n".join(module_log.lines)
    assert "scratch_document" in captured  # the SQL was logged, so the scan can bite
    assert SECRETS  # and there is something to look for
    assert [secret for secret in SECRETS if secret in captured] == []
    dek = provider.data_key(alpha, DATA)[1]
    assert leaked(captured, KEK, dek, JSON_CODEC.encode(last)) == []
