"""``Encrypted`` values, the ``EncryptedStr`` column and ``reveal_for_response``.

(spec 8.1 and 8.2, plan 1b task B3.) The value object is pinned first, without a
database. Everything after it runs against the real database through a throwaway
tenant table: real commits, real row-level security, a real key provider. The table
lives on a metadata of its own, so neither the schema-sync test nor the isolation
harness (which read ``Base.metadata``) ever see it, and it is dropped when the module
ends.

Every plaintext a test uses comes from ``secret_text``, which remembers it; the last
test scans the whole module's log capture for all of them.
"""

import ast
import dataclasses
import inspect
import json
import logging
import traceback
from collections.abc import Generator, Iterable
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from typing import Any, ClassVar, override
from uuid import UUID, uuid4

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from pydantic import TypeAdapter, create_model
from pydantic.errors import PydanticSchemaGenerationError
from pydantic_core import PydanticSerializationError, to_jsonable_python
from sqlalchemy import (
    Engine,
    LargeBinary,
    MetaData,
    Text,
    insert,
    select,
    text,
    update,
)
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
    staff_ctx,
    system_ctx,
)
from xolotl.platform import audit
from xolotl.platform import logging as platform_logging
from xolotl.platform.audit import VIEW_PII, verify_chain
from xolotl.platform.audit.models import AuditEventRow
from xolotl.platform.crypto.blind_index import blind_index
from xolotl.platform.crypto.encrypted import (
    STR_CODEC,
    Encrypted,
    EncryptedStr,
    StrCodec,
    encrypt,
    reveal_for_response,
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
from xolotl.platform.enums import KeyPurpose, Role
from xolotl.platform.errors import ContextMissingError
from xolotl.platform.logging import redact
from xolotl.platform.tenancy.context import TenantContext, bind_context
from xolotl.platform.tenancy.ddl import TenantTableMixin, rls_statements

pytestmark = pytest.mark.usefixtures("module_log")

KEK = bytes(range(32, 64))
DATA, INDEX, BIOMETRIC = KeyPurpose.DATA, KeyPurpose.INDEX, KeyPurpose.BIOMETRIC
CIPHERTEXT = b"\x00\x01" + bytes(range(40))
DIALECT = postgresql.dialect()

PLAINTEXTS: list[str] = []


# A unique plaintext that looks like personal data; the last test looks for every
# one of them in the logs.
def secret_text(label: str) -> str:
    value = f"{label}-{uuid4().hex[:12]}@secret.example"
    PLAINTEXTS.append(value)
    return value


# The column also accepts plain text where an Encrypted is declared.
def as_text(value: str) -> Any:
    return value


# An Encrypted that needs no database and opens with no key.
def sealed(
    ciphertext: bytes = CIPHERTEXT, tenant_id: UUID | None = None
) -> Encrypted[str]:
    return Encrypted(
        ciphertext=ciphertext,
        tenant_id=tenant_id or uuid4(),
        purpose=DATA,
        version=1,
        _codec=STR_CODEC,
    )


# ---- the value object (no database) ---------------------------------------------


def test_repr_and_str_say_nothing_about_the_value():
    value = sealed()
    assert repr(value) == str(value) == "Encrypted(***)"
    assert f"{value}" == f"{value!r}" == format(value) == "Encrypted(***)"
    assert repr([value]) == "[Encrypted(***)]"  # containers print members with repr
    assert repr({"secret": value}) == "{'secret': Encrypted(***)}"


def test_equality_and_hash_follow_the_ciphertext_alone():
    value = sealed()
    same = dataclasses.replace(value, tenant_id=uuid4(), purpose=BIOMETRIC, version=2)
    assert value == same
    assert hash(value) == hash(same)
    assert len({value, same}) == 1
    assert value != sealed(CIPHERTEXT + b"x")
    assert value != CIPHERTEXT  # not interchangeable with its raw bytes


def test_a_value_is_immutable_and_carries_no_dict():
    value = sealed()
    for name in ("ciphertext", "tenant_id", "purpose", "version", "_codec"):
        with pytest.raises(dataclasses.FrozenInstanceError):
            setattr(value, name, None)
    assert not hasattr(value, "__dict__")


def test_json_and_pydantic_refuse_an_encrypted_value():
    value = sealed()
    with pytest.raises(TypeError, match="not JSON serializable"):
        _ = json.dumps({"secret": value})
    with pytest.raises(PydanticSerializationError):
        _ = to_jsonable_python({"secret": value})
    # Pydantic cannot validate the codec protocol the dataclass carries, so no model
    # can declare the field, parametrised or not, and no adapter can be built.
    for annotation in (Encrypted[str], Encrypted):
        with pytest.raises(PydanticSchemaGenerationError, match="Unable to generate"):
            _ = create_model("Leaky", secret=(annotation, ...))
    with pytest.raises(PydanticSchemaGenerationError, match="Unable to generate"):
        _ = TypeAdapter(Encrypted[str])


def test_redact_masks_an_encrypted_value_wherever_it_nests():
    value = sealed()
    assert redact(value) == "***"
    nested = {"a": value, "b": [value, {"c": (value,)}], "d": {value}}
    assert redact(nested) == {
        "a": "***",
        "b": ["***", {"c": ["***"]}],
        "d": ["***"],
    }


def test_logging_never_imports_the_crypto_package():
    # The ledger ruling: logging stays importable first, so it must not depend on
    # crypto, not even lazily. It recognises an Encrypted by what the class declares.
    tree = ast.parse(inspect.getsource(platform_logging))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
        elif isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
    assert imported  # the walk found the imports it is meant to judge
    assert not {name for name in imported if name.startswith("xolotl.platform.crypto")}


def test_the_str_codec_round_trips_text_and_refuses_bytes_that_are_not_text():
    assert STR_CODEC.decode(STR_CODEC.encode("pérez ñandú 日本")) == "pérez ñandú 日本"
    with pytest.raises(UnicodeDecodeError):
        _ = STR_CODEC.decode(b"\xff\xfe")


def test_view_pii_is_exported_by_the_audit_package():
    assert VIEW_PII == "VIEW_PII"
    assert "VIEW_PII" in audit.__all__


# ---- keys and providers ---------------------------------------------------------


class CountingFactory:
    """Counts the sessions the provider opens for itself."""

    def __init__(self) -> None:
        self.opened: int = 0

    def __call__(self) -> Session:
        self.opened += 1
        return get_sessionmaker()()


class SpyProvider(KeyProvider):
    """A real provider that remembers which session each ``data_key`` call got."""

    def __init__(self, inner: LocalKeyProvider) -> None:
        super().__init__()
        self.inner: LocalKeyProvider = inner
        self.calls: list[tuple[UUID, Session | None]] = []

    @override
    def create_tenant_keys(self, tenant_id, *, session=None):
        self.inner.create_tenant_keys(tenant_id, session=session)

    @override
    def data_key(self, tenant_id, purpose, version=None, *, session=None):
        self.calls.append((tenant_id, session))
        return self.inner.data_key(tenant_id, purpose, version, session=session)

    @override
    def index_key(self, tenant_id, *, session=None):
        return self.inner.index_key(tenant_id, session=session)

    @override
    def rotate(self, tenant_id, purpose, *, session=None):
        return self.inner.rotate(tenant_id, purpose, session=session)

    @override
    def destroy(self, tenant_id, *, session=None):
        self.inner.destroy(tenant_id, session=session)


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


def keys_of(provider: LocalKeyProvider, tenant_id: UUID) -> dict[KeyPurpose, bytes]:
    return {purpose: provider.data_key(tenant_id, purpose)[1] for purpose in KeyPurpose}


# ---- encrypt and reveal ---------------------------------------------------------


def test_encrypt_then_reveal_returns_the_text(provider: LocalKeyProvider):
    alpha = make_tenant_with_keys("alpha")
    plain = secret_text("round-trip")
    value = encrypt(plain, tenant_id=alpha)
    assert value.reveal() == plain
    assert (value.tenant_id, value.purpose, value.version) == (alpha, DATA, 1)


def test_the_ciphertext_follows_the_layout_and_hides_the_text(
    provider: LocalKeyProvider,
):
    alpha = make_tenant_with_keys("alpha")
    plain = secret_text("layout")
    value = encrypt(plain, tenant_id=alpha)
    raw = value.ciphertext
    assert raw[:HEADER_LEN] == b"\x00\x01"
    assert len(raw) == HEADER_LEN + NONCE_LEN + len(plain.encode()) + TAG_LEN
    assert plain.encode() not in raw
    # Opened with the primitives and the specified authenticated data, not via reveal.
    dek = provider.data_key(alpha, DATA)[1]
    opened = decrypt_value(dek, ciphertext=raw, aad=alpha.bytes + b"DATA")
    assert opened == plain.encode()
    assert aad_for(alpha, DATA) == alpha.bytes + b"DATA"


def test_every_encryption_of_the_same_text_is_different(provider: LocalKeyProvider):
    alpha = make_tenant_with_keys("alpha")
    plain = secret_text("nonce")
    first, second = encrypt(plain, tenant_id=alpha), encrypt(plain, tenant_id=alpha)
    assert first != second
    start, end = HEADER_LEN, HEADER_LEN + NONCE_LEN
    assert first.ciphertext[start:end] != second.ciphertext[start:end]
    assert first.reveal() == second.reveal() == plain


def test_text_is_sealed_under_the_purpose_it_is_given(provider: LocalKeyProvider):
    alpha = make_tenant_with_keys("alpha")
    plain = secret_text("biometric")
    value = encrypt(plain, tenant_id=alpha, purpose=BIOMETRIC)
    assert value.purpose is BIOMETRIC
    assert value.reveal() == plain
    # The purpose is authenticated, and it selects a different key as well.
    with pytest.raises(DecryptionError):
        _ = dataclasses.replace(value, purpose=DATA).reveal()


def test_the_index_purpose_is_never_used_to_encrypt(provider: LocalKeyProvider):
    alpha = make_tenant_with_keys("alpha")
    with pytest.raises(ValueError, match="INDEX"):
        _ = encrypt(secret_text("index"), tenant_id=alpha, purpose=INDEX)


def test_a_tenant_without_keys_cannot_encrypt(provider: LocalKeyProvider):
    alpha = make_tenant("alpha")
    with pytest.raises(KeyNotFoundError):
        _ = encrypt(secret_text("no-keys"), tenant_id=alpha)


def test_a_value_does_not_open_for_another_tenant(provider: LocalKeyProvider):
    alpha, beta = make_tenant_with_keys("alpha"), make_tenant_with_keys("beta")
    plain = secret_text("cross-tenant")
    value = encrypt(plain, tenant_id=alpha)
    with pytest.raises(DecryptionError) as refused:
        _ = dataclasses.replace(value, tenant_id=beta).reveal()
    assert str(refused.value) == "decryption failed"
    assert plain not in repr(refused.value) + repr(refused.value.args)


def test_a_version_the_tenant_never_had_is_a_missing_key(provider: LocalKeyProvider):
    alpha = make_tenant_with_keys("alpha")
    value = encrypt(secret_text("version"), tenant_id=alpha)
    with pytest.raises(KeyNotFoundError):
        _ = dataclasses.replace(value, version=9).reveal()


def test_an_altered_value_fails_authentication(provider: LocalKeyProvider):
    alpha = make_tenant_with_keys("alpha")
    plain = secret_text("tamper")
    value = encrypt(plain, tenant_id=alpha)
    raw = value.ciphertext
    flip_nonce = raw[:HEADER_LEN] + bytes([raw[HEADER_LEN] ^ 1]) + raw[HEADER_LEN + 1 :]
    flip_body = raw[:-TAG_LEN] + bytes([raw[-TAG_LEN] ^ 1]) + raw[-TAG_LEN + 1 :]
    flip_tag = raw[:-1] + bytes([raw[-1] ^ 1])
    for altered in (flip_nonce, flip_body, flip_tag, raw[:-1], raw + b"\x00", raw[:20]):
        with pytest.raises(DecryptionError):
            _ = dataclasses.replace(value, ciphertext=altered).reveal()


def test_text_that_is_not_utf8_is_a_decryption_failure_that_keeps_nothing_of_it(
    provider: LocalKeyProvider,
):
    alpha = make_tenant_with_keys("alpha")
    dek = provider.data_key(alpha, DATA)[1]
    odd = b"\xff\xfe secret-bytes"
    raw = encrypt_value(dek, version=1, plaintext=odd, aad=aad_for(alpha, DATA))
    value = Encrypted(
        ciphertext=raw, tenant_id=alpha, purpose=DATA, version=1, _codec=STR_CODEC
    )
    with pytest.raises(DecryptionError) as refused:
        _ = value.reveal()
    error = refused.value
    assert str(error) == "decryption failed"
    assert "secret-bytes" not in "".join(traceback.format_exception(error))
    # Hiding the context from the traceback is not enough: the UnicodeDecodeError keeps
    # every decrypted byte in .object, and anything that walks __context__ finds it.
    assert error.__context__ is None
    assert error.__cause__ is None
    # Nor may a frame an error tracker prints (Sentry, rich, pytest -l) hold them.
    report = locals_report(value.reveal)
    assert "secret-bytes" not in report
    assert leaked(report, odd, dek) == []


# ---- the column -----------------------------------------------------------------


class ScratchBase(DeclarativeBase):
    metadata: ClassVar[MetaData] = MetaData(naming_convention=NAMING_CONVENTION)


class Secret(TenantTableMixin, ScratchBase):
    """A tenant table with two encrypted columns and a blind index, for these tests."""

    __tablename__: str = "scratch_secret"

    label: Mapped[str] = mapped_column(Text)
    secret: Mapped[Encrypted[str] | None] = mapped_column(EncryptedStr)
    email: Mapped[Encrypted[str] | None] = mapped_column(EncryptedStr)
    email_tag: Mapped[bytes | None] = mapped_column(LargeBinary, index=True)


class Orphan(ScratchBase):
    """No tenant_id column: an encrypted value here has no tenant to seal it for."""

    __tablename__: str = "scratch_orphan"

    id: Mapped[int] = mapped_column(primary_key=True)
    secret: Mapped[Encrypted[str] | None] = mapped_column(EncryptedStr)


@pytest.fixture(scope="module")
def scratch_table(engines: tuple[Engine, Engine]) -> Generator[None]:
    admin = engines[1]
    ScratchBase.metadata.drop_all(admin)  # whatever an interrupted run left behind
    ScratchBase.metadata.create_all(admin)
    with admin.begin() as conn:
        for statement in rls_statements("scratch_secret"):
            _ = conn.execute(text(statement))
    yield
    ScratchBase.metadata.drop_all(admin)


# The secret column of every row as the database holds it, in label order.
def stored(admin: Engine) -> list[bytes | None]:
    with admin.connect() as conn:
        rows = conn.execute(text("SELECT secret FROM scratch_secret ORDER BY label"))
        return [row[0] for row in rows]


def add_secret(tenant_id: UUID, label: str, value: Encrypted[str] | None) -> None:
    with tenant_session(system_ctx(tenant_id)) as s:
        s.add(Secret(tenant_id=tenant_id, label=label, secret=value))


def only_the_scratch_tables(
    name: str | None, type_: str, _parent_names: object
) -> bool:
    return type_ != "table" or name in {"scratch_secret", "scratch_orphan"}


def test_the_column_is_a_plain_bytea_to_alembic_and_the_schema_sync_test(
    scratch_table: None, admin_engine: Engine
):
    # B4 turns a real column into an EncryptedStr; the schema-sync test must not see
    # a difference between the ORM type and the bytea a migration creates.
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
        c["name"]: c for c in sa_inspect(admin_engine).get_columns("scratch_secret")
    }
    assert str(columns["secret"]["type"]) == str(columns["email"]["type"]) == "BYTEA"
    assert columns["secret"]["nullable"] is True


def test_a_value_round_trips_through_the_column(
    provider: LocalKeyProvider, scratch_table: None, admin_engine: Engine
):
    alpha = make_tenant_with_keys("alpha")
    plain = secret_text("column")
    value = encrypt(plain, tenant_id=alpha)
    add_secret(alpha, "one", value)
    with tenant_session(system_ctx(alpha)) as s:
        loaded = s.scalars(select(Secret)).one().secret
        assert isinstance(loaded, Encrypted)
        assert loaded == value
        assert (loaded.tenant_id, loaded.purpose, loaded.version) == (alpha, DATA, 1)
        assert loaded.reveal() == plain
    assert stored(admin_engine) == [value.ciphertext]  # stored as is
    assert plain.encode() not in value.ciphertext


def test_null_stays_null_in_both_directions(
    provider: LocalKeyProvider, scratch_table: None, admin_engine: Engine
):
    alpha = make_tenant_with_keys("alpha")
    add_secret(alpha, "none", None)
    assert stored(admin_engine) == [None]
    with tenant_session(system_ctx(alpha)) as s:
        assert s.scalars(select(Secret)).one().secret is None


def test_text_assigned_to_an_attribute_is_sealed_in_the_flushing_session(
    provider: LocalKeyProvider, reads: CountingFactory, scratch_table: None
):
    spy = SpyProvider(provider)
    set_key_provider(spy)
    alpha = make_tenant("alpha")  # no keys yet
    plain, other = secret_text("flush"), secret_text("flush-email")
    with tenant_session(system_ctx(alpha)) as s:
        spy.create_tenant_keys(alpha, session=s)  # uncommitted: nobody else sees them
        opened = reads.opened
        row = Secret(tenant_id=alpha, label="text", secret=as_text(plain))
        row.email = as_text(other)
        s.add(row)
        s.flush()
        assert spy.calls == [(alpha, s)]  # one read for both columns, via this session
        assert reads.opened == opened  # no second connection
        assert isinstance(row.secret, Encrypted)  # the instance holds no plain text
        assert isinstance(row.email, Encrypted)
        assert (row.secret.version, row.secret.tenant_id) == (1, alpha)
    with tenant_session(system_ctx(alpha)) as s:
        loaded = s.scalars(select(Secret)).one()
        assert loaded.secret is not None
        assert loaded.email is not None
        assert (loaded.secret.reveal(), loaded.email.reveal()) == (plain, other)


def test_a_flush_reads_the_key_once_however_much_text_it_seals(
    provider: LocalKeyProvider, scratch_table: None
):
    spy = SpyProvider(provider)
    set_key_provider(spy)
    alpha = make_tenant("alpha")
    with tenant_session(system_ctx(alpha)) as s:
        spy.create_tenant_keys(alpha, session=s)
        for number in range(3):
            plain, other = secret_text("many"), secret_text("many-email")
            row = Secret(tenant_id=alpha, label=f"r{number}", secret=as_text(plain))
            row.email = as_text(other)
            s.add(row)
        s.flush()
        assert spy.calls == [(alpha, s)]  # three rows, six values, one read
        s.add(Secret(tenant_id=alpha, label="no-text", secret=None))
        s.flush()
        assert spy.calls == [(alpha, s)]  # nothing to seal, nothing read
    with tenant_session(system_ctx(alpha)) as s:
        rows = s.scalars(select(Secret).where(Secret.label.like("r%"))).all()
        assert len(rows) == 3
        assert all(row.secret is not None and row.secret.reveal() for row in rows)


def test_text_assigned_to_a_loaded_row_is_sealed_at_the_update(
    provider: LocalKeyProvider, scratch_table: None, admin_engine: Engine
):
    alpha = make_tenant_with_keys("alpha")
    before, after = secret_text("before"), secret_text("after")
    add_secret(alpha, "one", encrypt(before, tenant_id=alpha))
    with tenant_session(system_ctx(alpha)) as s:
        row = s.scalars(select(Secret)).one()
        row.secret = as_text(after)
        s.flush()
        assert isinstance(row.secret, Encrypted)
    (raw,) = stored(admin_engine)
    assert raw is not None
    assert after.encode() not in raw
    assert before.encode() not in raw
    with tenant_session(system_ctx(alpha)) as s:
        loaded = s.scalars(select(Secret)).one().secret
        assert loaded is not None
        assert loaded.reveal() == after


def test_text_in_an_orm_bulk_update_does_not_stay_in_the_identity_map(
    provider: LocalKeyProvider, scratch_table: None, admin_engine: Engine
):
    # The ORM applies the SET values of a bulk UPDATE to the loaded instances (the
    # default synchronisation), and the platform's sessions do not expire on commit.
    alpha = make_tenant_with_keys("alpha")
    after = secret_text("bulk-after")
    add_secret(alpha, "one", encrypt(secret_text("bulk-before"), tenant_id=alpha))
    session = get_sessionmaker()()
    try:
        apply_context(session, system_ctx(alpha))
        with bind_context(system_ctx(alpha)):
            row = session.scalars(select(Secret)).one()
            assert isinstance(row.secret, Encrypted)
            # The label is a SET that is not idempotent, and the WHERE still matches
            # after it: a statement that ran twice would show.
            statement = (
                update(Secret)
                .where(Secret.tenant_id == alpha)
                .values(secret=after, label=Secret.label + "!")
            )
            _ = session.execute(statement)
            session.commit()
            assert not isinstance(vars(row).get("secret"), str)  # no raw text kept
            assert row.secret is not None  # and it reads back as the stored value
            assert row.secret.reveal() == after
            # An UPDATE that matches nothing leaves the loaded instance alone.
            held = row.secret
            nothing = update(Secret).where(Secret.label == "none").values(secret=after)
            _ = session.execute(nothing)
            assert vars(row).get("secret") is held
    finally:
        session.close()
    (raw,) = stored(admin_engine)
    assert raw is not None
    assert after.encode() not in raw
    assert set(stored_by_label(admin_engine)) == {"one!"}  # run exactly once


@pytest.mark.parametrize("target", ["same-mapper", "other-mapper"])
def test_a_pending_assignment_survives_an_orm_update_that_changes_nothing(
    provider: LocalKeyProvider, scratch_table: None, admin_engine: Engine, target: str
):
    # With autoflush off the assignment is still pending when the UPDATE runs. The hook
    # that expires the text a bulk UPDATE leaves behind must not expire this one, or
    # the write is lost: the commit succeeds and the old value stays.
    alpha = make_tenant_with_keys("alpha")
    add_secret(alpha, "one", encrypt(secret_text("old"), tenant_id=alpha))
    pending, elsewhere = secret_text("pending"), secret_text("elsewhere")
    if target == "same-mapper":
        nothing = (
            update(Secret).where(Secret.label == "no-such").values(secret=elsewhere)
        )
    else:
        nothing = update(Orphan).where(Orphan.id == -1).values(secret=elsewhere)
    with bind_context(system_ctx(alpha)), owner_session(system_ctx(alpha)) as s:
        row = s.scalars(select(Secret)).one()
        with s.no_autoflush:
            row.secret = as_text(pending)
            _ = s.execute(nothing)
            assert vars(row).get("secret") == pending  # still there, still unflushed
            assert vars(row).get("label") == "one"  # and nothing else was expired
    # The commit at the end of the block flushed it: sealed, and under alpha's key.
    (raw,) = stored(admin_engine)
    assert_readable_only_by(provider, alpha, [], raw, pending)


def test_the_column_stores_values_of_the_data_purpose_only(
    provider: LocalKeyProvider, scratch_table: None, admin_engine: Engine
):
    column_type = EncryptedStr()
    assert column_type.process_bind_param(sealed(), DIALECT) == CIPHERTEXT
    for purpose in (INDEX, BIOMETRIC):
        stranger = dataclasses.replace(sealed(), purpose=purpose)
        with pytest.raises(ValueError, match="DATA values only") as refused:
            _ = column_type.process_bind_param(stranger, DIALECT)
        assert CIPHERTEXT.hex() not in str(refused.value)
    alpha = make_tenant_with_keys("alpha")
    biometric = encrypt(secret_text("template"), tenant_id=alpha, purpose=BIOMETRIC)
    with (
        pytest.raises(StatementError) as refused,
        tenant_session(system_ctx(alpha)) as s,
    ):
        s.add(Secret(tenant_id=alpha, label="x", secret=biometric))
        s.flush()
    assert isinstance(refused.value.orig, ValueError)
    assert stored(admin_engine) == []  # it can never be opened as DATA, so not stored


def test_sealing_at_flush_needs_the_keys_of_the_rows_tenant(
    provider: LocalKeyProvider, scratch_table: None
):
    alpha = make_tenant("alpha")  # no keys
    with pytest.raises(KeyNotFoundError), tenant_session(system_ctx(alpha)) as s:
        s.add(Secret(tenant_id=alpha, label="x", secret=as_text(secret_text("none"))))
        s.flush()
    with tenant_session(system_ctx(alpha)) as s:
        assert s.scalars(select(Secret)).all() == []  # nothing was written


# ---- whose key a value is sealed under ------------------------------------------


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


# The tenant and secret of every row as the database holds them, by label.
def stored_by_label(admin: Engine) -> dict[str, tuple[UUID, bytes | None]]:
    with admin.connect() as conn:
        rows = conn.execute(text("SELECT label, tenant_id, secret FROM scratch_secret"))
        return {row[0]: (row[1], row[2]) for row in rows}


# The text opens with its owner's key, and with nobody else's, whichever authenticated
# data is tried.
def assert_readable_only_by(
    provider: LocalKeyProvider,
    owner: UUID,
    others: Iterable[UUID],
    sealed_text: bytes | None,
    plain: str,
) -> None:
    assert sealed_text is not None
    opened = decrypt_value(
        provider.data_key(owner, DATA)[1],
        ciphertext=sealed_text,
        aad=aad_for(owner, DATA),
    )
    assert opened == plain.encode()
    for other in others:
        stranger = provider.data_key(other, DATA)[1]
        for aad in (aad_for(other, DATA), aad_for(owner, DATA)):
            with pytest.raises(DecryptionError):
                _ = decrypt_value(stranger, ciphertext=sealed_text, aad=aad)
        value = Encrypted(
            ciphertext=sealed_text,
            tenant_id=other,
            purpose=DATA,
            version=1,
            _codec=STR_CODEC,
        )
        with pytest.raises(DecryptionError):
            _ = value.reveal()


def test_one_flush_seals_each_row_under_its_own_tenants_key(
    provider: LocalKeyProvider, scratch_table: None, admin_engine: Engine
):
    spy = SpyProvider(provider)
    set_key_provider(spy)
    alpha, beta = make_tenant_with_keys("alpha"), make_tenant_with_keys("beta")
    spy.calls.clear()
    a_text, b_text = secret_text("alpha-owner"), secret_text("beta-owner")
    # The ambient context is alpha's, which is where every row used to be sealed.
    with bind_context(system_ctx(alpha)), owner_session(platform_ctx()) as s:
        s.add(Secret(tenant_id=alpha, label="a", secret=as_text(a_text)))
        s.add(Secret(tenant_id=beta, label="b", secret=as_text(b_text)))
        s.add(Secret(tenant_id=beta, label="c", secret=as_text(b_text)))
        s.flush()
        # One read per tenant, each through this session.
        assert sorted(spy.calls, key=lambda call: call[0].int) == sorted(
            [(alpha, s), (beta, s)], key=lambda call: call[0].int
        )
    rows = stored_by_label(admin_engine)
    assert_readable_only_by(provider, alpha, [beta], rows["a"][1], a_text)
    assert_readable_only_by(provider, beta, [alpha], rows["b"][1], b_text)
    assert_readable_only_by(provider, beta, [alpha], rows["c"][1], b_text)
    with tenant_session(system_ctx(alpha)) as s:  # and the owner reads it back
        (row,) = s.scalars(select(Secret)).all()
        assert row.secret is not None
        assert row.secret.reveal() == a_text


def test_an_autoflush_after_a_context_switch_seals_under_the_rows_own_tenant(
    provider: LocalKeyProvider, scratch_table: None, admin_engine: Engine
):
    # The shape of a per-tenant backfill: a row gets its text in one tenant's block and
    # is flushed by a query in the next one's.
    alpha, beta = make_tenant_with_keys("alpha"), make_tenant_with_keys("beta")
    a_text, b_text = secret_text("loop-alpha"), secret_text("loop-beta")
    with owner_session(platform_ctx()) as s:
        with bind_context(system_ctx(alpha)):
            s.add(Secret(tenant_id=alpha, label="a", secret=as_text(a_text)))
        with bind_context(system_ctx(beta)):
            assert s.scalars(select(Secret).where(Secret.tenant_id == beta)).all() == []
            s.add(Secret(tenant_id=beta, label="b", secret=as_text(b_text)))
            s.flush()
    rows = stored_by_label(admin_engine)
    assert_readable_only_by(provider, alpha, [beta], rows["a"][1], a_text)
    assert_readable_only_by(provider, beta, [alpha], rows["b"][1], b_text)


def test_a_flush_needs_no_ambient_context_to_seal_the_text_of_a_row(
    provider: LocalKeyProvider, scratch_table: None, admin_engine: Engine
):
    # The row names its tenant, so there is nothing to ask the context variable. The
    # commit at the end flushes with no context bound at all.
    alpha = make_tenant_with_keys("alpha")
    plain = secret_text("no-context")
    with owner_session(platform_ctx()) as s:
        s.add(Secret(tenant_id=alpha, label="a", secret=as_text(plain)))
    assert_readable_only_by(
        provider, alpha, [], stored_by_label(admin_engine)["a"][1], plain
    )


@pytest.mark.parametrize("kind", ["owner", "app"])
def test_a_session_bound_to_one_tenant_refuses_text_for_a_row_of_another(
    provider: LocalKeyProvider, scratch_table: None, admin_engine: Engine, kind: str
):
    spy = SpyProvider(provider)
    set_key_provider(spy)
    alpha, beta = make_tenant_with_keys("alpha"), make_tenant_with_keys("beta")
    spy.calls.clear()
    bound = system_ctx(alpha)
    session_scope = owner_session(bound) if kind == "owner" else tenant_session(bound)
    with (
        pytest.raises(TenantScopeError, match="another tenant") as refused,
        session_scope as s,
    ):
        s.add(Secret(tenant_id=alpha, label="a", secret=as_text(secret_text("mine"))))
        s.add(Secret(tenant_id=beta, label="b", secret=as_text(secret_text("theirs"))))
        s.flush()
    assert refused.value.status == 500
    assert spy.calls == []  # refused before any key was read, nothing was written
    assert stored_by_label(admin_engine) == {}


def test_a_session_that_takes_its_context_from_the_context_variable_is_checked_too(
    provider: LocalKeyProvider, scratch_table: None, admin_engine: Engine
):
    # No apply_context: the session binds the ambient context when its transaction
    # begins, which is after the hook starts. The check must still know it.
    alpha, beta = make_tenant_with_keys("alpha"), make_tenant_with_keys("beta")
    plain = secret_text("ambient")
    session = get_sessionmaker()()
    try:
        with bind_context(system_ctx(alpha)):
            session.add(Secret(tenant_id=alpha, label="a", secret=as_text(plain)))
            session.commit()
            session.add(Secret(tenant_id=beta, label="b", secret=as_text(plain)))
            with pytest.raises(TenantScopeError, match="another tenant"):
                session.flush()
    finally:
        session.rollback()
        session.close()
    rows = stored_by_label(admin_engine)
    assert set(rows) == {"a"}
    assert_readable_only_by(provider, alpha, [beta], rows["a"][1], plain)


def test_text_for_a_row_that_has_no_tenant_yet_is_refused(
    provider: LocalKeyProvider, scratch_table: None
):
    spy = SpyProvider(provider)
    set_key_provider(spy)
    alpha = make_tenant_with_keys("alpha")
    spy.calls.clear()
    plain = secret_text("homeless")
    row = Secret(label="x", secret=as_text(plain))  # tenant_id is still unset
    with (
        pytest.raises(TenantScopeError, match="tenant_id") as refused,
        owner_session(platform_ctx()) as s,
    ):
        s.add(row)
        s.flush()
    assert plain not in str(refused.value) + repr(refused.value)
    assert spy.calls == []
    assert row.secret == plain  # untouched: nothing was sealed under a guess
    assert alpha  # the tenant exists; the row simply never named it


def test_a_loaded_rows_tenant_comes_from_its_identity_when_the_attribute_is_expired(
    provider: LocalKeyProvider, scratch_table: None, admin_engine: Engine
):
    alpha, beta = make_tenant_with_keys("alpha"), make_tenant_with_keys("beta")
    add_secret(beta, "b", encrypt(secret_text("before"), tenant_id=beta))
    after = secret_text("after-expiry")
    with bind_context(system_ctx(alpha)), owner_session(platform_ctx()) as s:
        row = s.scalars(select(Secret)).one()
        s.expire(row, ["tenant_id"])
        assert "tenant_id" not in vars(row)
        row.secret = as_text(after)
        s.flush()
    assert_readable_only_by(
        provider, beta, [alpha], stored_by_label(admin_engine)["b"][1], after
    )


def test_text_in_a_table_without_a_tenant_column_is_refused(
    provider: LocalKeyProvider, scratch_table: None
):
    plain = secret_text("no-tenant-column")
    with (
        pytest.raises(TenantScopeError, match="tenant_id") as refused,
        tenant_session(system_ctx(make_tenant_with_keys("alpha"))) as s,
    ):
        s.add(Orphan(id=1, secret=as_text(plain)))
        s.flush()
    assert plain not in str(refused.value)


def test_the_tenant_scope_error_is_a_500_with_a_stable_code():
    error = TenantScopeError()
    assert (error.status, error.code) == (500, "TENANT_SCOPE")
    assert str(error) == error.detail


def test_text_bound_outside_a_flush_is_sealed_with_a_session_of_the_providers_own(
    provider: LocalKeyProvider,
    reads: CountingFactory,
    scratch_table: None,
    admin_engine: Engine,
):
    alpha = make_tenant_with_keys("alpha")
    plain = secret_text("core")
    opened = reads.opened
    with tenant_session(system_ctx(alpha)) as s:
        _ = s.execute(insert(Secret).values(tenant_id=alpha, label="c", secret=plain))
    assert reads.opened == opened + 1  # one cache miss, one session of its own
    (raw,) = stored(admin_engine)
    assert raw is not None
    assert plain.encode() not in raw
    with tenant_session(system_ctx(alpha)) as s:
        loaded = s.scalars(select(Secret)).one().secret
        assert loaded is not None
        assert loaded.reveal() == plain


def test_keys_that_fail_while_binding_surface_inside_a_statement_error(
    provider: LocalKeyProvider, scratch_table: None
):
    alpha = make_tenant_with_keys("alpha")
    plain = secret_text("binding")
    provider.destroy(alpha)
    with (
        pytest.raises(StatementError) as refused,
        tenant_session(system_ctx(alpha)) as s,
    ):
        _ = s.execute(insert(Secret).values(tenant_id=alpha, label="x", secret=plain))
    assert isinstance(refused.value.orig, KeyDestroyedError)
    # The engines hide bound parameters, so the statement error does not print them.
    assert plain not in str(refused.value) + repr(refused.value)


def test_the_column_refuses_values_that_are_neither_text_nor_encrypted():
    column_type = EncryptedStr()
    for refused in (42, b"bytes", 1.5, ["x"], {"a": 1}):
        with pytest.raises(TypeError, match="Encrypted value or text") as error:
            _ = column_type.process_bind_param(refused, DIALECT)
        assert repr(refused) not in str(error.value)


def test_loading_a_value_needs_a_tenant_scoped_context():
    column_type = EncryptedStr()
    with pytest.raises(ContextMissingError):
        _ = column_type.process_result_value(CIPHERTEXT, DIALECT)
    with (
        bind_context(platform_ctx()),
        pytest.raises(ContextMissingError, match="tenant"),
    ):
        _ = column_type.process_result_value(CIPHERTEXT, DIALECT)
    tenant = uuid4()
    with bind_context(system_ctx(tenant)):
        loaded = column_type.process_result_value(CIPHERTEXT, DIALECT)
    assert loaded is not None
    assert (loaded.tenant_id, loaded.purpose, loaded.version) == (tenant, DATA, 1)
    assert loaded.ciphertext == CIPHERTEXT
    assert column_type.process_result_value(None, DIALECT) is None


def test_a_session_whose_context_was_applied_but_never_bound_cannot_load_values(
    provider: LocalKeyProvider, scratch_table: None
):
    # apply_context sets the session's context, not the context variable the column
    # reads: code that switches tenants that way must also bind_context.
    alpha = make_tenant_with_keys("alpha")
    add_secret(alpha, "one", encrypt(secret_text("unbound"), tenant_id=alpha))
    session = get_sessionmaker()()
    try:
        apply_context(session, system_ctx(alpha))
        with pytest.raises(ContextMissingError):
            _ = session.scalars(select(Secret)).all()
    finally:
        session.rollback()
        session.close()


def test_a_value_forged_into_another_tenants_row_does_not_open(
    provider: LocalKeyProvider, scratch_table: None, admin_engine: Engine
):
    alpha, beta = make_tenant_with_keys("alpha"), make_tenant_with_keys("beta")
    plain = secret_text("forged")
    value = encrypt(plain, tenant_id=alpha)
    with admin_engine.begin() as conn:  # the owner is not bound by row-level security
        _ = conn.execute(
            text(
                """
                INSERT INTO scratch_secret (tenant_id, id, label, secret)
                VALUES (:tenant_id, :id, 'forged', :secret)
                """
            ),
            {"tenant_id": beta, "id": uuid4(), "secret": value.ciphertext},
        )
    with tenant_session(system_ctx(beta)) as s:
        forged = s.scalars(select(Secret)).one().secret
        assert forged is not None
        assert forged.tenant_id == beta  # it is bound to the row's tenant, not alpha
        with pytest.raises(DecryptionError) as refused:
            _ = forged.reveal()
        assert plain not in repr(refused.value)
    assert value.reveal() == plain  # alpha's own copy is unharmed


def test_rotation_keeps_old_rows_readable_and_new_writes_use_the_new_version(
    provider: LocalKeyProvider, scratch_table: None, admin_engine: Engine
):
    alpha = make_tenant_with_keys("alpha")
    first, second, third = (secret_text(name) for name in ("v1", "v2", "v2-text"))
    add_secret(alpha, "a", encrypt(first, tenant_id=alpha))
    assert provider.rotate(alpha, DATA) == 2
    fresh = encrypt(second, tenant_id=alpha)
    assert fresh.version == 2  # the value says which key it needs, before any reload
    assert fresh.reveal() == second
    add_secret(alpha, "b", fresh)
    with tenant_session(system_ctx(alpha)) as s:
        s.add(Secret(tenant_id=alpha, label="c", secret=as_text(third)))
    raw = stored(admin_engine)
    assert [item[:HEADER_LEN] for item in raw if item] == [
        b"\x00\x01",
        b"\x00\x02",
        b"\x00\x02",
    ]
    with tenant_session(system_ctx(alpha)) as s:
        rows = s.scalars(select(Secret).order_by(Secret.label)).all()
        loaded = [row.secret for row in rows]
        assert all(item is not None for item in loaded)
        assert [item.version for item in loaded if item] == [1, 2, 2]
        assert [item.reveal() for item in loaded if item] == [first, second, third]


def test_destroyed_keys_leave_the_rows_loadable_but_unreadable(
    provider: LocalKeyProvider, scratch_table: None
):
    alpha = make_tenant_with_keys("alpha")
    plain = secret_text("shredded")
    add_secret(alpha, "one", encrypt(plain, tenant_id=alpha))
    provider.destroy(alpha)
    with tenant_session(system_ctx(alpha)) as s:
        loaded = s.scalars(select(Secret)).one().secret
        assert loaded is not None
        with pytest.raises(KeyDestroyedError) as refused:
            _ = loaded.reveal()
        assert refused.value.status == 410
        assert plain not in repr(refused.value) + str(refused.value)
        with pytest.raises(KeyDestroyedError):
            _ = loaded.reveal(session=s)
    with pytest.raises(KeyDestroyedError):
        _ = encrypt(secret_text("after"), tenant_id=alpha)
    with pytest.raises(KeyDestroyedError), tenant_session(system_ctx(alpha)) as s:
        s.add(Secret(tenant_id=alpha, label="x", secret=as_text(secret_text("late"))))
        s.flush()


def test_a_stored_value_that_was_altered_fails_authentication_on_reveal(
    provider: LocalKeyProvider, scratch_table: None, admin_engine: Engine
):
    alpha = make_tenant_with_keys("alpha")
    add_secret(alpha, "one", encrypt(secret_text("altered"), tenant_id=alpha))
    with admin_engine.begin() as conn:
        _ = conn.execute(
            text("UPDATE scratch_secret SET secret = set_byte(secret, 20, 255)")
        )
    with tenant_session(system_ctx(alpha)) as s:
        loaded = s.scalars(select(Secret)).one().secret
        assert loaded is not None
        with pytest.raises(DecryptionError):
            _ = loaded.reveal()


def test_a_stored_value_too_short_for_a_header_fails_on_load(
    provider: LocalKeyProvider, scratch_table: None, admin_engine: Engine
):
    alpha = make_tenant_with_keys("alpha")
    add_secret(alpha, "one", encrypt(secret_text("short"), tenant_id=alpha))
    with admin_engine.begin() as conn:
        _ = conn.execute(text("UPDATE scratch_secret SET secret = '\\x01'::bytea"))
    with pytest.raises(DecryptionError), tenant_session(system_ctx(alpha)) as s:
        _ = s.scalars(select(Secret)).all()


def test_a_stored_value_with_a_header_and_no_body_fails_on_reveal(
    provider: LocalKeyProvider, scratch_table: None, admin_engine: Engine
):
    alpha = make_tenant_with_keys("alpha")
    add_secret(alpha, "one", encrypt(secret_text("headless"), tenant_id=alpha))
    with admin_engine.begin() as conn:
        _ = conn.execute(text("UPDATE scratch_secret SET secret = '\\x000100'::bytea"))
    with tenant_session(system_ctx(alpha)) as s:
        loaded = s.scalars(select(Secret)).one().secret
        assert loaded is not None
        assert loaded.version == 1
        with pytest.raises(DecryptionError):
            _ = loaded.reveal()


def test_an_equal_value_is_not_a_change_and_a_fresh_encryption_is(
    provider: LocalKeyProvider, scratch_table: None
):
    alpha = make_tenant_with_keys("alpha")
    plain = secret_text("change")
    add_secret(alpha, "one", encrypt(plain, tenant_id=alpha))
    with tenant_session(system_ctx(alpha)) as s:
        row = s.scalars(select(Secret)).one()
        assert row.secret is not None
        row.secret = dataclasses.replace(row.secret)  # another object, same bytes
        assert not s.is_modified(row)
        row.secret = encrypt(plain, tenant_id=alpha)  # same text, new nonce
        assert s.is_modified(row)


def test_concurrent_flushes_each_seal_through_their_own_session(
    provider: LocalKeyProvider, scratch_table: None
):
    spy = SpyProvider(provider)
    set_key_provider(spy)
    tenants = [make_tenant(f"tenant-{n}") for n in range(8)]

    def seal(tenant_id: UUID) -> tuple[str, Session]:
        plain = secret_text("thread")
        with tenant_session(system_ctx(tenant_id)) as s:
            spy.create_tenant_keys(tenant_id, session=s)  # readable only through s
            s.add(Secret(tenant_id=tenant_id, label="t", secret=as_text(plain)))
            s.flush()
            return plain, s

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(seal, tenants))
    for tenant_id, (_plain, session) in zip(tenants, results, strict=True):
        assert [s for t, s in spy.calls if t == tenant_id] == [session]
    for tenant_id, (plain, _session) in zip(tenants, results, strict=True):
        with tenant_session(system_ctx(tenant_id)) as s:
            loaded = s.scalars(select(Secret)).one().secret
            assert loaded is not None
            assert loaded.reveal() == plain


def test_a_row_is_found_through_its_blind_index_and_then_revealed(
    provider: LocalKeyProvider, scratch_table: None
):
    alpha, beta = make_tenant_with_keys("alpha"), make_tenant_with_keys("beta")
    addresses = {"ana": "Ana.Lopez@Acme.MX", "luis": "luis@acme.mx"}
    with tenant_session(system_ctx(alpha)) as s:
        for label, address in addresses.items():
            s.add(
                Secret(
                    tenant_id=alpha,
                    label=label,
                    email=encrypt(address, tenant_id=alpha),
                    email_tag=blind_index("email", address, tenant_id=alpha),
                )
            )
    needle = blind_index("email", " ana.lopez@acme.mx ", tenant_id=alpha)
    with tenant_session(system_ctx(alpha)) as s:
        found = s.scalars(select(Secret).where(Secret.email_tag == needle)).one()
        assert found.label == "ana"
        assert found.email is not None
        assert found.email.reveal() == "Ana.Lopez@Acme.MX"
    # Row-level security hides the row from beta, and the tags share nothing anyway.
    with tenant_session(system_ctx(beta)) as s:
        assert s.scalars(select(Secret).where(Secret.email_tag == needle)).all() == []
    assert needle != blind_index("email", "ana.lopez@acme.mx", tenant_id=beta)


# ---- revealing to a person ------------------------------------------------------


def view_events(tenant_id: UUID) -> list[AuditEventRow]:
    with tenant_session(system_ctx(tenant_id)) as s:
        return list(
            s.scalars(
                select(AuditEventRow)
                .where(AuditEventRow.action == VIEW_PII)
                .order_by(AuditEventRow.seq)
            )
        )


def test_reveal_for_response_returns_the_text_and_records_one_view_event(
    provider: LocalKeyProvider,
):
    alpha = make_tenant_with_keys("alpha")
    plain = secret_text("view")
    value = encrypt(plain, tenant_id=alpha)
    viewer = staff_ctx(alpha, actor_id="staff-7", roles=[Role.MLRO])
    with tenant_session(viewer) as s:
        shown = reveal_for_response(
            s, value, entity_type="staff_credential", entity_id="cred-1", field="totp"
        )
        assert shown == plain
    (event,) = view_events(alpha)
    assert event.action == VIEW_PII
    assert (event.entity_type, event.entity_id) == ("staff_credential", "cred-1")
    assert event.detail == {"field": "totp"}
    assert (event.actor_type, event.actor_id) == ("STAFF", "staff-7")
    assert (event.seq, event.tenant_id) == (1, alpha)
    assert plain not in repr(event.detail) + (event.entity_id or "")
    with tenant_session(system_ctx(alpha)) as s:
        assert verify_chain(s).valid


def test_each_reveal_is_its_own_event(provider: LocalKeyProvider):
    alpha = make_tenant_with_keys("alpha")
    value = encrypt(secret_text("twice"), tenant_id=alpha)
    with tenant_session(system_ctx(alpha)) as s:
        for field in ("first", "second"):
            _ = reveal_for_response(
                s, value, entity_type="thing", entity_id="1", field=field
            )
    assert [e.detail for e in view_events(alpha)] == [
        {"field": "first"},
        {"field": "second"},
    ]
    with tenant_session(system_ctx(alpha)) as s:
        chain = verify_chain(s)
        assert (chain.valid, chain.entries_checked) == (True, 2)


def test_nothing_is_revealed_when_the_audit_event_cannot_be_recorded(
    provider: LocalKeyProvider, monkeypatch: pytest.MonkeyPatch
):
    alpha = make_tenant_with_keys("alpha")
    decoded: list[bytes] = []

    class SpyCodec(StrCodec):
        @override
        def decode(self, data: bytes) -> str:
            decoded.append(data)
            return super().decode(data)

    value = dataclasses.replace(
        encrypt(secret_text("audit-down"), tenant_id=alpha), _codec=SpyCodec()
    )

    def refuse(_session: Session, _event: object) -> None:
        msg = "the audit store is down"
        raise RuntimeError(msg)

    monkeypatch.setattr("xolotl.platform.crypto.encrypted.record", refuse)
    with (
        pytest.raises(RuntimeError, match="audit store is down"),
        tenant_session(system_ctx(alpha)) as s,
    ):
        _ = reveal_for_response(s, value, entity_type="t", entity_id="1", field="f")
    assert decoded == []  # the audit event comes first; the text was never produced


def test_a_failed_reveal_leaves_no_view_event_behind(provider: LocalKeyProvider):
    alpha = make_tenant_with_keys("alpha")
    value = encrypt(secret_text("gone"), tenant_id=alpha)
    provider.destroy(alpha)
    with pytest.raises(KeyDestroyedError), tenant_session(system_ctx(alpha)) as s:
        _ = reveal_for_response(s, value, entity_type="t", entity_id="1", field="f")
    # The exception rolled the caller's transaction back, event included.
    assert view_events(alpha) == []


def test_a_session_without_a_tenant_reveals_nothing(provider: LocalKeyProvider):
    alpha = make_tenant_with_keys("alpha")
    value = encrypt(secret_text("platform"), tenant_id=alpha)
    with pytest.raises(ContextMissingError), tenant_session(platform_ctx()) as s:
        _ = reveal_for_response(s, value, entity_type="t", entity_id="1", field="f")


def test_another_tenants_value_is_not_revealed_through_this_session(
    provider: LocalKeyProvider,
):
    alpha, beta = make_tenant_with_keys("alpha"), make_tenant_with_keys("beta")
    plain = secret_text("foreign")
    value = encrypt(plain, tenant_id=alpha)
    # pytest.raises comes after the session: the refusal is caught inside the
    # transaction, which then commits, so a refusal that came after the event would
    # leave the event behind.
    with (
        tenant_session(system_ctx(beta)) as s,
        pytest.raises(TenantScopeError, match="another tenant"),
    ):
        _ = reveal_for_response(s, value, entity_type="t", entity_id="1", field="f")
    assert view_events(beta) == []
    assert view_events(alpha) == []
    assert value.reveal() == plain  # an Encrypted opens wherever the provider is


def test_a_session_on_the_owner_engine_does_not_reveal_another_tenants_value(
    provider: LocalKeyProvider,
):
    # Row-level security does not bind the owner engine, so nothing else would stop it:
    # the plaintext would come back and the VIEW_PII event would land in the viewing
    # tenant's chain, never in the chain of the tenant the data belongs to.
    alpha, beta = make_tenant_with_keys("alpha"), make_tenant_with_keys("beta")
    value = encrypt(secret_text("owner-engine"), tenant_id=beta)
    with (
        owner_session(system_ctx(alpha)) as s,
        pytest.raises(TenantScopeError, match="another tenant"),
    ):
        _ = reveal_for_response(s, value, entity_type="t", entity_id="1", field="f")
    assert view_events(alpha) == []
    assert view_events(beta) == []


def test_reveal_through_a_session_reads_the_keys_in_that_transaction(
    provider: LocalKeyProvider, reads: CountingFactory
):
    alpha = make_tenant("alpha")
    plain = secret_text("in-transaction")
    with tenant_session(system_ctx(alpha)) as s:
        provider.create_tenant_keys(alpha, session=s)  # uncommitted
        value = encrypt(plain, tenant_id=alpha, session=s)
        opened = reads.opened
        assert value.reveal(session=s) == plain
        assert (
            reveal_for_response(s, value, entity_type="t", entity_id="1", field="f")
            == plain
        )
        assert reads.opened == opened
        with pytest.raises(KeyNotFoundError):  # no other session can see the keys yet
            _ = value.reveal()


# ---- what the logs and the errors never hold -----------------------------------


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


def test_the_last_test_finds_no_plaintext_in_the_modules_log(
    module_log: Collector, provider: LocalKeyProvider, scratch_table: None
):
    # One more round through the column, so the capture holds statements for it.
    alpha = make_tenant_with_keys("alpha")
    last = secret_text("last")
    add_secret(alpha, "last", encrypt(last, tenant_id=alpha))
    with tenant_session(system_ctx(alpha)) as s:
        loaded = s.scalars(select(Secret)).one().secret
        assert loaded is not None
        _ = reveal_for_response(s, loaded, entity_type="t", entity_id="1", field="f")
    captured = "\n".join(module_log.lines)
    assert "scratch_secret" in captured  # the SQL was logged, so the scan can bite
    assert PLAINTEXTS  # and there is something to look for
    assert [plain for plain in PLAINTEXTS if plain in captured] == []
    keys = keys_of(provider, alpha)
    assert leaked(captured, KEK, *keys.values()) == []
