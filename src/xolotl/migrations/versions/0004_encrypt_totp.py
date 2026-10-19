"""encrypt staff_credential.totp_secret

Revision ID: 0004_encrypt_totp
Revises: 0003_tenant_key
Create Date: 2026-10-19

The TOTP secret was base32 text in a ``bytea`` column. It becomes the ciphertext of
``EncryptedStr`` (``version || nonce || AES-256-GCM`` under the tenant's DATA key, the
tenant and purpose as authenticated data), still ``bytea``, so the model and the
catalog agree without a type change. The conversion runs in the migration's own
transaction, one tenant at a time, on the owner's connection. Each tenant is a session
of its own (``tenant_session`` on that connection, which joins the migration's
transaction: its commit commits nothing, and a failure rolls everything back), scoped
with the platform context named for that tenant, because the owner of a managed server
is not a superuser and row-level security binds it: a statement that named no tenant
would find no row. A session never holds another tenant's rows, and the current
context is bound to the tenant being worked on.

Upgrade: every tenant gets the keys it lacks (``create_tenant_keys`` keeps the ones it
has), every non-null secret is sealed into ``totp_secret_enc`` and read back (what the
database holds must open again, under the tenant's key and authenticated data, to the
secret it replaces), and only then is the plaintext column dropped and the new one
renamed in its place. A tenant whose keys were destroyed is skipped and logged:
destruction is final, so it gets no key, and a secret nobody can open is not kept. If
any secret cannot be sealed and read back the migration stops and the transaction takes
everything back.

``DROP COLUMN`` only hides a column: its bytes stay in the table's heap pages, in the
live rows and in the dead versions the sealing updates leave. So the table is rewritten
in the same transaction (``ALTER COLUMN ... TYPE bytea USING totp_secret || ''``, which
changes no value and keeps the type), and the live pages of the database no longer hold
the plaintext. Copies made before the upgrade still do: a base backup, a replica's disk,
a volume snapshot and the archived WAL carry the plaintext TOTP secrets until their
retention expires. Nothing in the database can change that; the operator has to know.

Downgrade: every secret is opened with the key version named in its header and the
plaintext column comes back. Secrets of a destroyed tenant come back as NULL. Any other
failure, a wrong ``MASTER_KEY`` among them, stops the migration with nothing changed.

Nothing here prints or logs a secret. The plaintext is read through ``SecretBinary``,
so SQLAlchemy's DEBUG row log shows a mask, and it is held as ``SecretBytes``.
"""

import logging
from collections.abc import Sequence
from uuid import UUID

import sqlalchemy as sa
from alembic import op
from sqlalchemy.orm import Session, sessionmaker

from xolotl.platform.config import get_settings
from xolotl.platform.crypto.errors import DecryptionError, KeyDestroyedError
from xolotl.platform.crypto.models import SecretBinary
from xolotl.platform.crypto.primitives import (
    SecretBytes,
    aad_for,
    decrypt_value,
    encrypt_value,
    parse_header,
)
from xolotl.platform.crypto.provider import LocalKeyProvider
from xolotl.platform.db import SET_CONTEXT_SQL, SessionFactory, tenant_session
from xolotl.platform.enums import KeyPurpose
from xolotl.platform.tenancy.context import TenantContext

revision: str = "0004_encrypt_totp"
down_revision: str | None = "0003_tenant_key"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ACTOR = "migration:0004_encrypt_totp"
LOGGER = logging.getLogger("xolotl.migrations.0004_encrypt_totp")

# The tables as this migration sees them, whatever the models say later.
TENANT = sa.table(
    "tenant", sa.column("id", sa.Uuid()), sa.column("created_at", sa.DateTime())
)
TENANT_KEY = sa.table(
    "tenant_key", sa.column("tenant_id", sa.Uuid()), sa.column("purpose", sa.String())
)
CREDENTIAL = sa.table(
    "staff_credential",
    sa.column("tenant_id", sa.Uuid()),
    sa.column("id", sa.Uuid()),
    sa.column("totp_secret", SecretBinary()),
    sa.column("totp_secret_enc", sa.LargeBinary()),
    sa.column("totp_secret_plain", sa.LargeBinary()),
)
DATA = KeyPurpose.DATA


def _tenant_ids(factory: SessionFactory) -> list[UUID]:
    with tenant_session(TenantContext.platform(ACTOR), factory=factory) as session:
        return list(
            session.scalars(
                sa.select(TENANT.c.id).order_by(TENANT.c.created_at, TENANT.c.id)
            )
        )


def _clear_context(connection: sa.Connection) -> None:
    # Transaction-local settings outlive the sessions that set them, and later
    # migrations of the same run share this transaction.
    empty = {"tenant_id": "", "actor_type": "", "actor_id": "", "roles": ""}
    _ = connection.execute(SET_CONTEXT_SQL, empty)


# The tenant's credentials that hold a secret, as ``(id, secret)``, held as SecretBytes.
def _credentials(session: Session, tenant_id: UUID) -> list[tuple[UUID, SecretBytes]]:
    rows = session.execute(
        sa
        .select(CREDENTIAL.c.id, CREDENTIAL.c.totp_secret)
        .where(
            CREDENTIAL.c.tenant_id == tenant_id, CREDENTIAL.c.totp_secret.is_not(None)
        )
        .order_by(CREDENTIAL.c.id)
    ).all()
    return [(credential_id, SecretBytes.of(secret)) for credential_id, secret in rows]


def _store(
    session: Session, tenant_id: UUID, credential_id: UUID, **column: bytes
) -> None:
    _ = session.execute(
        sa
        .update(CREDENTIAL)
        .where(CREDENTIAL.c.tenant_id == tenant_id, CREDENTIAL.c.id == credential_id)
        .values(**column)
    )


def _ensure_keys(provider: LocalKeyProvider, session: Session, tenant_id: UUID) -> bool:
    """Give the tenant the keys it lacks and say what was done.

    Returns:
        Whether the tenant has usable keys; ``False`` for a tenant whose keys were
        destroyed, which no key is added to.

    """
    had = set(
        session.scalars(
            sa.select(TENANT_KEY.c.purpose).where(TENANT_KEY.c.tenant_id == tenant_id)
        )
    )
    try:
        provider.create_tenant_keys(tenant_id, session=session)
    except KeyDestroyedError:
        return False
    added = sorted({purpose.value for purpose in KeyPurpose} - had)
    if added:
        LOGGER.info("tenant %s: created keys for %s", tenant_id, ", ".join(added))
    else:
        LOGGER.info("tenant %s already has keys: kept", tenant_id)
    return True


def _seal_tenant(provider: LocalKeyProvider, session: Session, tenant_id: UUID) -> None:
    if not _ensure_keys(provider, session, tenant_id):
        dropped = len(_credentials(session, tenant_id))
        LOGGER.warning(
            "tenant %s: its keys were destroyed, so its %d TOTP secret(s) are dropped",
            tenant_id,
            dropped,
        )
        return
    credentials = _credentials(session, tenant_id)
    if credentials:
        version, dek = provider.data_key(tenant_id, DATA, session=session)
        aad = aad_for(tenant_id, DATA)
        for credential_id, secret in credentials:
            sealed = encrypt_value(dek, version=version, plaintext=secret, aad=aad)
            _store(session, tenant_id, credential_id, totp_secret_enc=sealed)
        _check_recoverable(session, tenant_id, credentials, dek, aad)
    LOGGER.info("tenant %s: sealed %d TOTP secret(s)", tenant_id, len(credentials))


def _check_recoverable(
    session: Session,
    tenant_id: UUID,
    credentials: list[tuple[UUID, SecretBytes]],
    dek: bytes,
    aad: bytes,
) -> None:
    """Open again what the database now holds and compare it with what it replaces.

    The plaintext column is dropped next, so it is only safe to drop once what was
    stored for each secret gives it back: present, authentic under the tenant's key and
    authenticated data, and equal. Anything else stops the migration, whose
    transaction takes everything back.

    Raises:
        RuntimeError: When a secret has no sealed copy, or its copy does not open to it.

    """
    stored = dict(
        session.execute(
            sa.select(CREDENTIAL.c.id, CREDENTIAL.c.totp_secret_enc).where(
                CREDENTIAL.c.tenant_id == tenant_id
            )
        ).all()
    )
    missing = unreadable = 0
    for credential_id, secret in credentials:
        sealed = stored.get(credential_id)
        if sealed is None:
            missing += 1
            continue
        try:
            opened = SecretBytes.of(decrypt_value(dek, ciphertext=sealed, aad=aad))
        except DecryptionError:
            opened = None
        if opened != secret:
            unreadable += 1
    if missing:
        msg = f"tenant {tenant_id}: {missing} TOTP secret(s) were not sealed"
        raise RuntimeError(msg)
    if unreadable:
        msg = f"tenant {tenant_id}: {unreadable} TOTP secret(s) cannot be opened again"
        raise RuntimeError(msg)


def _open_tenant(provider: LocalKeyProvider, session: Session, tenant_id: UUID) -> None:
    credentials = _credentials(session, tenant_id)
    if not credentials:
        return
    aad = aad_for(tenant_id, DATA)
    keys: dict[int, SecretBytes] = {}
    try:
        for credential_id, sealed in credentials:
            version = parse_header(sealed)
            if version not in keys:
                keys[version] = SecretBytes.of(
                    provider.data_key(tenant_id, DATA, version, session=session)[1]
                )
            plain = SecretBytes.of(
                decrypt_value(keys[version], ciphertext=sealed, aad=aad)
            )
            _store(session, tenant_id, credential_id, totp_secret_plain=plain)
    except KeyDestroyedError:
        LOGGER.warning(
            "tenant %s: keys destroyed, so its %d TOTP secret(s) come back NULL",
            tenant_id,
            len(credentials),
        )
        return
    LOGGER.info("tenant %s: opened %d TOTP secret(s)", tenant_id, len(credentials))


def upgrade() -> None:
    op.add_column(
        "staff_credential",
        sa.Column("totp_secret_enc", sa.LargeBinary(), nullable=True),
    )
    connection = op.get_bind()
    factory = sessionmaker(bind=connection)
    provider = LocalKeyProvider(get_settings().master_key_bytes, factory)
    for tenant_id in _tenant_ids(factory):
        ctx = TenantContext.platform(ACTOR, tenant_id=tenant_id)
        with tenant_session(ctx, factory=factory) as session:
            _seal_tenant(provider, session, tenant_id)
    _clear_context(connection)
    op.drop_column("staff_credential", "totp_secret")
    op.alter_column(
        "staff_credential", "totp_secret_enc", new_column_name="totp_secret"
    )
    # DROP COLUMN only hides the plaintext: its bytes stay in the table's heap pages, in
    # the live rows and in the dead versions the sealing updates left. A rewrite of the
    # table drops them, and any expression other than the bare column forces one.
    # Appending the empty bytea changes no value and keeps the type (and so the catalog
    # and the model agree), and NULL stays NULL.
    op.execute(
        "ALTER TABLE staff_credential ALTER COLUMN totp_secret "
        "TYPE bytea USING totp_secret || ''::bytea"
    )


def downgrade() -> None:
    op.add_column(
        "staff_credential",
        sa.Column("totp_secret_plain", sa.LargeBinary(), nullable=True),
    )
    connection = op.get_bind()
    factory = sessionmaker(bind=connection)
    provider = LocalKeyProvider(get_settings().master_key_bytes, factory)
    for tenant_id in _tenant_ids(factory):
        ctx = TenantContext.platform(ACTOR, tenant_id=tenant_id)
        with tenant_session(ctx, factory=factory) as session:
            _open_tenant(provider, session, tenant_id)
    _clear_context(connection)
    op.drop_column("staff_credential", "totp_secret")
    op.alter_column(
        "staff_credential", "totp_secret_plain", new_column_name="totp_secret"
    )
