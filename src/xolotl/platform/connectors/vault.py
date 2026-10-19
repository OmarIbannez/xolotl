"""The credential vault: a tenant's connector credentials, encrypted at rest.

One ``connector_credential`` row per ``(tenant, connector, mode)`` holds the fields
as JSON ciphertext under the tenant's DATA key. Row-level security scopes every read
and write to the session's tenant. The audit names the fields, never their values.
"""

import re
from collections.abc import Mapping
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from xolotl.platform import clock
from xolotl.platform.audit import AuditEvent, record
from xolotl.platform.connectors.models import ConnectorCredential
from xolotl.platform.crypto.encrypted import encrypt_json
from xolotl.platform.db import bound_context
from xolotl.platform.enums import ActorType, ConnectorMode
from xolotl.platform.errors import ForbiddenError, ValidationFailedError

FIELD_NAME = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
MAX_FIELD_VALUE_LEN = 4096


def _validate(fields: Mapping[str, str]) -> None:
    """Refuse a credential that is not a few short named strings.

    Raises:
        ValidationFailedError: When there is no field, a field name is not lowercase
            snake case of at most 64 characters, or a value is empty or longer than
            4096 characters.

    """
    if not fields:
        msg = "at least one field is required"
        raise ValidationFailedError(msg)
    for name, value in fields.items():
        if not FIELD_NAME.fullmatch(name):
            msg = "invalid field name"
            raise ValidationFailedError(msg)
        if not value:
            msg = "a field value is empty"
            raise ValidationFailedError(msg)
        if len(value) > MAX_FIELD_VALUE_LEN:
            msg = f"a field value exceeds {MAX_FIELD_VALUE_LEN} characters"
            raise ValidationFailedError(msg)


class CredentialVault:
    """Reads and writes the session tenant's connector credentials."""

    def get(  # ruff: ignore[no-self-use] -- a port method; connectors take a vault
        self, session: Session, connector: str, mode: ConnectorMode
    ) -> Mapping[str, str]:
        """Return the stored fields of ``(connector, mode)`` for the session's tenant.

        Returns:
            The decrypted fields with string values, or ``{}`` when none is stored.

        """
        row = session.scalars(
            select(ConnectorCredential).where(
                ConnectorCredential.connector == connector,
                ConnectorCredential.mode == mode,
            )
        ).one_or_none()
        if row is None:
            return {}
        secret = row.secret.reveal(session=session)
        return {k: v for k, v in secret.items() if isinstance(v, str)}

    def set(  # ruff: ignore[no-self-use] -- a port method; connectors take a vault
        self,
        session: Session,
        connector: str,
        mode: ConnectorMode,
        fields: Mapping[str, str],
    ) -> ConnectorCredential:
        """Store the fields of ``(connector, mode)``, replacing any stored ones.

        Only a staff member may set a credential. A replacement keeps the row and
        stamps ``rotated_at``. The fields are validated as ``_validate`` says, which
        raises ``ValidationFailedError``. The ``connector.credential_set`` audit event
        names the fields and never their values.

        Returns:
            The stored row, flushed.

        Raises:
            ForbiddenError: When the session's actor is not staff.

        """
        ctx = bound_context(session)
        if ctx.actor_type is not ActorType.STAFF:
            msg = "only staff may set connector credentials"
            raise ForbiddenError(msg)
        _validate(fields)
        tenant_id = ctx.require_tenant_id()
        payload: dict[str, object] = dict(fields)
        secret = encrypt_json(payload, tenant_id=tenant_id, session=session)
        row = session.scalars(
            select(ConnectorCredential)
            .where(
                ConnectorCredential.connector == connector,
                ConnectorCredential.mode == mode,
            )
            .with_for_update()
        ).one_or_none()
        existed = row is not None
        if row is None:
            row = ConnectorCredential(
                tenant_id=tenant_id,
                connector=connector,
                mode=mode,
                secret=secret,
                created_by=UUID(ctx.actor_id),
            )
            session.add(row)
        else:
            row.secret = secret
            row.rotated_at = clock.now()
        session.flush()
        _ = record(
            session,
            AuditEvent(
                action="connector.credential_set",
                entity_type="connector_credential",
                entity_id=str(row.id),
                detail={
                    "connector": connector,
                    "mode": mode.value,
                    "field_names": [*sorted(fields)],
                    "rotated": existed,
                },
            ),
        )
        return row
