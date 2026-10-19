"""Encrypted values, the column that stores them, and the only way to show them.

``Encrypted[T]`` is the ciphertext of one value plus what is needed to open it: the
tenant, the key purpose and the key version read from the value's header. It is
opaque. ``repr`` and ``str`` print ``Encrypted(***)``, the redaction filter masks it
wherever it reaches a log record, ``json`` and Pydantic refuse it (Pydantic builds a
dataclass schema from the fields, and ``_codec`` is a ``Protocol`` it cannot validate,
so a model that declares an ``Encrypted`` field fails when the class is created), and
it compares and hashes by its ciphertext alone. The text of an error raised while
opening one never carries the plaintext, a key or the ciphertext. A failed decode
raises from a frame that holds neither the key nor the plaintext, with no exception
chained to it: a ``UnicodeDecodeError`` would keep every decrypted byte in its
``object`` and a ``JSONDecodeError`` the whole document in its ``doc``, for anything
that walks ``__context__`` or prints locals.

``reveal()`` decrypts and records nothing: it is for processing, such as checking a
TOTP code. ``reveal_for_response`` is the only path that hands plaintext to a
person. It refuses a value of another tenant than its session's, records a
``VIEW_PII`` event in the caller's transaction, and decrypts only if that succeeded;
if the decryption then fails, the exception rolls the event back with the rest of
the transaction.

``EncryptedStr`` and ``EncryptedJson`` are the column types: ``LargeBinary`` underneath,
the DATA purpose, holding text and a JSON object (a ``dict``, stored as canonical JSON)
respectively. They hold DATA values only: an ``Encrypted`` of another purpose would load
as DATA and never open, so binding one is refused. What is said below of text holds for
an object in an ``EncryptedJson`` column, which is the plain form that column takes.

* Writing takes an ``Encrypted`` (stored as it is, whatever its tenant: whether the
  row may exist is the database policy's decision, not this type's) or plain text.
  Plain text assigned to a mapped attribute is sealed before the flush builds its
  statements, in the flushing session, under the key of the tenant *the row names*:
  its ``tenant_id``, the loaded attribute or else the identity key of a row whose
  attribute is expired. The ambient context plays no part, so a flush that spans
  tenants (a migration or a script on the owner engine, an autoflush after a context
  switch) seals each value under its owner's key, with one key read per tenant. A row
  with no ``tenant_id`` yet, a table with no ``tenant_id`` column, and a row of
  another tenant than a tenant-bound session's all fail with ``TenantScopeError``
  before any key is read or any value is changed. The keys are read in the
  transaction that is running (which also sees keys it has just created and not yet
  committed) and no second pooled connection is taken. The instance holds an
  ``Encrypted`` afterwards, not the text.
* Plain text in a statement that never passes through a flush (``insert(...).values``,
  a bulk statement) is sealed when the parameter is bound, through the provider's own
  session and its cache, under the tenant of the *current* context, because there is
  no row to ask: use it for single-tenant statements. A key failure there surfaces
  wrapped in SQLAlchemy's ``StatementError``; ``encrypt`` and the flush path raise
  key errors as they are. An ORM bulk UPDATE with text also makes the ORM write the
  raw text into the loaded instances (its default synchronisation), so the encrypted
  attributes it left holding text are expired as soon as the statement has run: the
  identity map never keeps the text, whether or not the session expires on commit.
* Reading binds each value to the tenant of the *current* context, the one
  ``bind_context`` set, because a result processor has no session. A session whose
  context was changed with ``apply_context`` alone does not change it; code that
  switches tenants that way must also ``bind_context``, or loading a row raises
  ``ContextMissingError``.
* An encrypted column cannot be searched or compared: every encryption of a value
  differs. Search a blind index (``blind_index``) stored beside it.

Key errors are the provider's: ``KeyNotFoundError`` for a version the tenant never
had, ``KeyDestroyedError`` (410) after crypto-shredding, ``DecryptionError`` for a
value that fails authentication, belongs to another tenant or purpose, or is not
what its column holds (text, a JSON object).
"""

import dataclasses
import json
from abc import ABC, abstractmethod
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from functools import cache
from typing import ClassVar, NamedTuple, Protocol, cast, final, override
from uuid import UUID

from sqlalchemy import LargeBinary, event
from sqlalchemy.engine import Dialect, Result
from sqlalchemy.orm import Mapper, ORMExecuteState, Session, object_mapper
from sqlalchemy.orm.attributes import instance_state
from sqlalchemy.types import TypeDecorator

from xolotl.platform.audit import VIEW_PII, AuditEvent, canonical_json, record
from xolotl.platform.crypto.errors import DecryptionError, TenantScopeError
from xolotl.platform.crypto.primitives import (
    aad_for,
    decrypt_value,
    encrypt_value,
    parse_header,
)
from xolotl.platform.crypto.provider import get_key_provider
from xolotl.platform.db import bound_context
from xolotl.platform.enums import KeyPurpose
from xolotl.platform.tenancy.context import require_context

_MASKED = "Encrypted(***)"


class Codec[T](Protocol):
    """How a type becomes the bytes that are encrypted, and back."""

    def encode(self, value: T) -> bytes:
        """Turn a value into the bytes to encrypt."""
        ...

    def decode(self, data: bytes) -> T:
        """Turn decrypted bytes back into a value, or raise ``ValueError``."""
        ...


class StrCodec(Codec[str]):
    """Text as UTF-8."""

    @override
    def encode(self, value: str) -> bytes:
        return value.encode()

    @override
    def decode(self, data: bytes) -> str:
        return data.decode()


STR_CODEC = StrCodec()


class JsonCodec(Codec[dict[str, object]]):
    """A JSON object as canonical JSON in UTF-8: sorted keys, no spaces."""

    @override
    def encode(self, value: dict[str, object]) -> bytes:
        return canonical_json(value)

    @override
    def decode(self, data: bytes) -> dict[str, object]:
        parsed = cast("object", json.loads(data.decode()))
        if isinstance(parsed, dict):
            return cast("dict[str, object]", parsed)
        msg = "not a JSON object"
        raise ValueError(msg)


JSON_CODEC = JsonCodec()


def _decode[T](codec: Codec[T], plaintext: bytes) -> list[T]:
    # The value in a one-item list, or an empty list when the bytes are not a T. The
    # caller raises: raising in here, inside the handler, would chain the ValueError,
    # and a UnicodeDecodeError keeps every decrypted byte in its ``object``.
    try:
        value = codec.decode(plaintext)
    except ValueError:
        return []
    return [value]


@dataclass(frozen=True, slots=True, repr=False)
class Encrypted[T]:
    """One encrypted value, opaque until :meth:`reveal` is called on it.

    Equality and hashing use ``ciphertext`` only: it carries a random nonce, so equal
    ciphertexts are the same stored value, and an ORM that compares an attribute with
    the value it loaded sees no change. ``log_opaque`` tells the redaction filter to
    mask the whole object without importing this module.
    """

    log_opaque: ClassVar[bool] = True

    ciphertext: bytes
    tenant_id: UUID = dataclasses.field(compare=False)
    purpose: KeyPurpose = dataclasses.field(compare=False)
    version: int = dataclasses.field(compare=False)
    _codec: Codec[T] = dataclasses.field(compare=False)

    @override
    def __repr__(self) -> str:
        return _MASKED

    @override
    def __str__(self) -> str:
        return _MASKED

    def reveal(self, *, session: Session | None = None) -> T:
        """Decrypt the value without recording that anyone saw it.

        Use this to process a secret (a TOTP check), never to show it: showing it to
        a person is :func:`reveal_for_response`. The key is read from the provider's
        cache, or, with ``session``, inside that session's transaction, which sees
        keys it has not committed and takes no second connection (and always asks
        the database, so a destruction committed elsewhere is seen at once). Fails
        with ``KeyNotFoundError`` when the tenant never had its key version and
        ``KeyDestroyedError`` once the tenant's keys were destroyed.

        Returns:
            The decrypted value.

        Raises:
            DecryptionError: When the value does not authenticate, or is not what
                its column holds (text, a JSON object).

        """
        # No local holds the key, and the plaintext is gone before this can raise: a
        # traceback printed with its locals (Sentry, rich, pytest -l) must not show it.
        plaintext = decrypt_value(
            get_key_provider().data_key(
                self.tenant_id, self.purpose, self.version, session=session
            )[1],
            ciphertext=self.ciphertext,
            aad=aad_for(self.tenant_id, self.purpose),
        )
        decoded = _decode(self._codec, plaintext)
        del plaintext
        if not decoded:
            raise DecryptionError
        return decoded[0]


def _seal[T](
    value: T,
    codec: Codec[T],
    tenant_id: UUID,
    purpose: KeyPurpose,
    key: tuple[int, bytes],
) -> Encrypted[T]:
    version, dek = key
    ciphertext = encrypt_value(
        dek,
        version=version,
        plaintext=codec.encode(value),
        aad=aad_for(tenant_id, purpose),
    )
    return Encrypted(
        ciphertext=ciphertext,
        tenant_id=tenant_id,
        purpose=purpose,
        version=version,
        _codec=codec,
    )


def _encrypt[T](
    value: T,
    codec: Codec[T],
    *,
    tenant_id: UUID,
    purpose: KeyPurpose,
    session: Session | None,
) -> Encrypted[T]:
    if purpose is KeyPurpose.INDEX:
        msg = "INDEX keys only feed blind indexes: they never encrypt a value"
        raise ValueError(msg)
    key = get_key_provider().data_key(tenant_id, purpose, session=session)
    return _seal(value, codec, tenant_id, purpose, key)


def encrypt(
    value: str,
    *,
    tenant_id: UUID,
    purpose: KeyPurpose = KeyPurpose.DATA,
    session: Session | None = None,
) -> Encrypted[str]:
    """Encrypt ``value`` under the tenant's current key for ``purpose``.

    Pass the caller's ``session`` to read the key inside its transaction, which sees
    keys it has just created and takes no second connection. The INDEX purpose is
    refused with a ``ValueError``: its key only feeds blind indexes.

    Returns:
        The encrypted value, bound to the tenant, the purpose and the key version.

    """
    return _encrypt(
        value, STR_CODEC, tenant_id=tenant_id, purpose=purpose, session=session
    )


def encrypt_json(
    value: dict[str, object],
    *,
    tenant_id: UUID,
    purpose: KeyPurpose = KeyPurpose.DATA,
    session: Session | None = None,
) -> Encrypted[dict[str, object]]:
    """Encrypt a JSON object, as canonical JSON, the way :func:`encrypt` encrypts text.

    The arguments, the key it is read with and the refusal of the INDEX purpose are
    those of :func:`encrypt`.

    Returns:
        The encrypted object, bound to the tenant, the purpose and the key version.

    """
    return _encrypt(
        value, JSON_CODEC, tenant_id=tenant_id, purpose=purpose, session=session
    )


type Sealer[T] = Callable[[UUID, tuple[int, bytes]], Encrypted[T]]


class _EncryptedColumn[T](TypeDecorator[Encrypted[T]], ABC):
    """What ``EncryptedStr`` and ``EncryptedJson`` share: ``bytea`` holding DATA values.

    A subclass names its codec, which Python values it seals when a model holds them
    plain (the *plain form*: text, an object), and what to call them in an error.
    """

    @abstractmethod
    def codec(self) -> Codec[T]:
        """Give the codec of the values this column holds."""

    @abstractmethod
    def plain(self, value: object) -> T | None:
        """Give ``value`` as a ``T`` when it is this column's plain form.

        Returns:
            The value, or ``None`` for anything else (an ``Encrypted``, ``None``).

        """

    @abstractmethod
    def noun(self) -> str:
        """Name the plain form in the error that refuses anything else."""

    def sealer(self, value: object) -> Sealer[T] | None:
        """Prepare to seal ``value`` once its row's tenant and key are known.

        Returns:
            What turns the plain form into an ``Encrypted`` for a tenant and its key, or
            ``None`` when ``value`` is not the plain form.

        """
        plain = self.plain(value)
        if plain is None:
            return None
        codec = self.codec()
        return lambda tenant_id, key: _seal(
            plain, codec, tenant_id, KeyPurpose.DATA, key
        )

    @override
    def process_bind_param(self, value: object, dialect: Dialect) -> bytes | None:
        del dialect
        if value is None:
            return None
        if isinstance(value, Encrypted):
            if value.purpose != KeyPurpose.DATA:
                msg = f"{type(self).__name__} holds DATA values only"
                raise ValueError(msg)
            return value.ciphertext
        plain = self.plain(value)
        if plain is None:
            msg = f"{type(self).__name__} stores an Encrypted value or {self.noun()}"
            raise TypeError(msg)
        return _encrypt(
            plain,
            self.codec(),
            tenant_id=require_context().require_tenant_id(),
            purpose=KeyPurpose.DATA,
            session=None,
        ).ciphertext

    @override
    def process_result_value(
        self, value: bytes | None, dialect: Dialect
    ) -> Encrypted[T] | None:
        del dialect
        if value is None:
            return None
        return Encrypted(
            ciphertext=value,
            tenant_id=require_context().require_tenant_id(),
            purpose=KeyPurpose.DATA,
            version=parse_header(value),
            _codec=self.codec(),
        )


@final
class EncryptedStr(_EncryptedColumn[str]):
    """A text column stored encrypted (``bytea``), under the tenant's DATA key."""

    impl = LargeBinary
    cache_ok = True

    @override
    def codec(self) -> Codec[str]:
        return STR_CODEC

    @override
    def plain(self, value: object) -> str | None:
        return value if isinstance(value, str) else None

    @override
    def noun(self) -> str:
        return "text"


@final
class EncryptedJson(_EncryptedColumn[dict[str, object]]):
    """A JSON object column stored encrypted (``bytea``), under the tenant's DATA key.

    The object is stored as canonical JSON (``JsonCodec``). Everything said of
    ``EncryptedStr`` holds for it, with an object where it says text.
    """

    impl = LargeBinary
    cache_ok = True

    @override
    def codec(self) -> Codec[dict[str, object]]:
        return JSON_CODEC

    @override
    def plain(self, value: object) -> dict[str, object] | None:
        return cast("dict[str, object]", value) if isinstance(value, dict) else None

    @override
    def noun(self) -> str:
        return "a JSON object"


type EncryptedColumn = EncryptedStr | EncryptedJson


@cache
def _encrypted_columns(
    mapper: Mapper[object],
) -> tuple[tuple[str, EncryptedColumn], ...]:
    columns: list[tuple[str, EncryptedColumn]] = []
    for attribute in mapper.column_attrs:
        column_type = attribute.columns[0].type
        if isinstance(column_type, EncryptedStr | EncryptedJson):
            columns.append((attribute.key, column_type))
    return tuple(columns)


class _Pending(NamedTuple):
    instance: object
    name: str
    tenant_id: UUID
    seal: Callable[[UUID, tuple[int, bytes]], object]


def _owner(instance: object, mapper: Mapper[object]) -> UUID:
    # The tenant the row names, found without loading anything: its tenant_id attribute
    # when that is loaded, else the identity key of a row that exists (an expired
    # attribute is still part of the key). Never the context variable's tenant: that is
    # whoever is running, and a flush may hold rows of several tenants.
    column = mapper.columns.get("tenant_id")
    if column is not None:
        loaded = cast("dict[str, object]", vars(instance)).get("tenant_id")
        if isinstance(loaded, UUID):
            return loaded
        key = instance_state(instance).key
        position = next(
            (n for n, part in enumerate(mapper.primary_key) if part is column), None
        )
        if key is not None and position is not None:
            tenant_id = cast("tuple[object, ...]", key[1])[position]
            if isinstance(tenant_id, UUID):
                return tenant_id
    msg = "an encrypted value is assigned to a row that has no tenant_id"
    raise TenantScopeError(msg)


def _pending_plain(session: Session) -> Iterator[_Pending]:
    # Only values already loaded into the instance: looking must not trigger a load.
    new = cast("Iterable[object]", session.new)
    dirty = cast("Iterable[object]", session.dirty)
    for instance in (*new, *dirty):
        mapper = object_mapper(instance)
        loaded = cast("dict[str, object]", vars(instance))
        for name, column in _encrypted_columns(mapper):
            seal = column.sealer(loaded.get(name))
            if seal is not None:
                yield _Pending(instance, name, _owner(instance, mapper), seal)


def _require_session_scope(session: Session, tenants: Iterable[UUID]) -> None:
    # Beginning the transaction binds the session's context; a platform operator with
    # no tenant may flush rows of any tenant, a tenant-bound session only its own.
    _ = session.connection()
    bound = bound_context(session).tenant_id
    if bound is not None and any(tenant != bound for tenant in tenants):
        msg = (
            "an encrypted value is assigned to a row of another tenant than the session"
        )
        raise TenantScopeError(msg)


def _seal_pending_plain(
    session: Session, _flush_context: object, _instances: object
) -> None:
    pending = list(_pending_plain(session))
    if not pending:
        return
    tenants = list(dict.fromkeys(item.tenant_id for item in pending))
    _require_session_scope(session, tenants)
    provider = get_key_provider()
    keys = {
        tenant: provider.data_key(tenant, KeyPurpose.DATA, session=session)
        for tenant in tenants
    }
    for item in pending:
        sealed = item.seal(item.tenant_id, keys[item.tenant_id])
        setattr(item.instance, item.name, sealed)


event.listen(Session, "before_flush", _seal_pending_plain)


def _expire_raw_values(state: ORMExecuteState) -> Result[tuple[object, ...]] | None:
    if not state.is_update:
        return None
    mapper = cast("Mapper[object] | None", state.bind_mapper)
    if mapper is None or not _encrypted_columns(mapper):
        return None
    result = cast("Result[tuple[object, ...]]", state.invoke_statement())
    # The ORM has written the statement's literal SET values into the instances it
    # matched, raw text or objects included, and marked them as loaded and clean. Only
    # those: an attribute with an uncommitted change is the caller's own assignment, not
    # yet flushed (autoflush may be off), and expiring it would silently lose the write.
    session = state.session
    for instance in tuple(session.identity_map.values()):
        loaded = cast("dict[str, object]", vars(instance))
        plain = [
            name
            for name, column in _encrypted_columns(object_mapper(instance))
            if column.plain(loaded.get(name)) is not None
        ]
        if plain:
            clean = instance_state(instance).unmodified
            raw = [name for name in plain if name in clean]
            if raw:  # expire() with no names expires every attribute
                session.expire(instance, raw)
    return result


event.listen(Session, "do_orm_execute", _expire_raw_values)


def reveal_for_response(
    session: Session,
    value: Encrypted[str],
    *,
    entity_type: str,
    entity_id: str,
    field: str,
) -> str:
    """Decrypt ``value`` for a person and record that they were shown it.

    A value of another tenant than the session's is refused first, so the event
    always lands in the chain of the tenant whose data was shown (row-level security
    would not stop it on the owner engine). Then the ``VIEW_PII`` event
    (``entity_type`` and ``entity_id`` name the record, ``field`` goes in the event's
    detail) is written, in ``session``'s transaction. Only then is the value
    decrypted, with the keys read through the same session. If either step fails the
    plaintext is not returned, and the exception that propagates rolls the event back.

    Returns:
        The plaintext.

    Raises:
        TenantScopeError: When the session is bound to another tenant than the value's.

    """
    bound = bound_context(session).tenant_id
    if bound is not None and value.tenant_id != bound:
        msg = "the value belongs to another tenant than the session's"
        raise TenantScopeError(msg)
    _ = record(
        session,
        AuditEvent(
            action=VIEW_PII,
            entity_type=entity_type,
            entity_id=entity_id,
            detail={"field": field},
        ),
    )
    return value.reveal(session=session)
