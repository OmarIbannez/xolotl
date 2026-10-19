"""The audit hash chain: canonical JSON, row digest, append and verification."""

import hashlib
import json
from datetime import UTC
from typing import cast
from uuid import UUID

from pydantic import BaseModel, JsonValue, field_validator
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from xolotl.platform import clock
from xolotl.platform.audit.models import AuditEventRow
from xolotl.platform.db import bound_context
from xolotl.platform.errors import ContextMissingError
from xolotl.platform.ids import uuid7

GENESIS_HASH = bytes(32)

_LOCK_SQL = text(
    "SELECT pg_advisory_xact_lock(hashtext(CAST(:tenant_id AS text))::bigint)"
)


def _contains_float(value: JsonValue) -> bool:
    pending: list[JsonValue] = [value]
    while pending:
        item = pending.pop()
        if isinstance(item, float):
            return True
        if isinstance(item, dict):
            pending.extend(item.values())
        elif isinstance(item, list):
            pending.extend(item)
    return False


class AuditEvent(BaseModel):
    """One event to append to a tenant's chain.

    ``detail`` must not contain floats, at any depth. The digest is recomputed from
    the JSONB value Postgres returns, and JSONB stores numbers as ``numeric``, which
    does not round-trip every float: ``1e16`` comes back as the integer
    ``10000000000000000`` and ``-0.0`` comes back as ``0.0``. The recomputed digest
    would then differ from the stored one and an untouched row would fail
    verification. Use ints or strings instead, such as an amount in minor units or a
    decimal string.
    """

    action: str
    entity_type: str | None = None
    entity_id: str | None = None
    detail: dict[str, JsonValue] | None = None

    @field_validator("detail")
    @classmethod
    def _reject_floats(
        cls, detail: dict[str, JsonValue] | None
    ) -> dict[str, JsonValue] | None:
        if _contains_float(detail):
            msg = "audit detail must not contain floats; use ints or strings"
            raise ValueError(msg)
        return detail


class ChainVerification(BaseModel):
    tenant_id: UUID
    valid: bool
    entries_checked: int
    first_broken_seq: int | None = None
    reason: str | None = None


def canonical_json(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode()


def digest(row: AuditEventRow) -> bytes:
    """SHA-256 over the canonical JSON of the row's content plus ``prev_hash``.

    Returns:
        The 32-byte digest. ``row.hash`` itself is not part of the input.

    """
    payload: list[object] = [
        str(row.tenant_id),
        row.seq,
        row.at.astimezone(UTC).isoformat(),
        row.actor_type,
        row.actor_id,
        row.action,
        row.entity_type,
        row.entity_id,
        row.detail,
        row.prev_hash.hex(),
    ]
    return hashlib.sha256(canonical_json(payload)).digest()


def record(session: Session, event: AuditEvent) -> AuditEventRow:
    """Append an event for the session's tenant inside the caller's transaction.

    The tenant's advisory lock is transaction-scoped: it is held until the caller's
    transaction commits or rolls back, and every other writer for that tenant waits
    until then. Record last, keep the transaction short, and when one transaction
    writes to several tenants, take them in a consistent order.

    The gap-free ``seq`` assumes READ COMMITTED: the last link is read after the lock
    is granted, so each statement sees the previous writer's committed row. Under
    REPEATABLE READ or SERIALIZABLE the snapshot can predate that commit and two
    writers would claim the same ``seq``.

    Returns:
        The flushed row, with ``seq``, ``prev_hash`` and ``hash`` filled in.

    """
    ctx = bound_context(session)
    tenant_id = ctx.require_tenant_id()
    _ = session.execute(_LOCK_SQL, {"tenant_id": str(tenant_id)})
    last = session.execute(
        select(AuditEventRow.seq, AuditEventRow.hash)
        .where(AuditEventRow.tenant_id == tenant_id)
        .order_by(AuditEventRow.seq.desc())
        .limit(1)
    ).one_or_none()
    seq, prev_hash = (last[0] + 1, last[1]) if last is not None else (1, GENESIS_HASH)
    row = AuditEventRow(
        tenant_id=tenant_id,
        at=clock.now(),
        id=uuid7(),
        seq=seq,
        actor_type=ctx.actor_type.value,
        actor_id=ctx.actor_id,
        action=event.action,
        entity_type=event.entity_type,
        entity_id=event.entity_id,
        detail=cast("dict[str, object] | None", event.detail),
        prev_hash=prev_hash,
        hash=b"",
    )
    row.hash = digest(row)
    session.add(row)
    session.flush()
    return row


def verify_chain(session: Session, tenant_id: UUID | None = None) -> ChainVerification:
    """Walk the session's tenant chain in ``seq`` order and report the first break.

    Row-level security shows a session only its own tenant's rows, so this always
    verifies the tenant the session is bound to. ``tenant_id`` is a consistency
    check: any other tenant raises, because verifying it would see no rows and
    report an empty chain as valid.

    Returns:
        The verdict: ``valid``, how many entries were checked and, on a break, the
        ``seq`` of the first bad entry with the reason.

    Raises:
        ContextMissingError: When the session's context has no tenant, or is bound
            to a tenant other than ``tenant_id``.

    """
    bound = bound_context(session).require_tenant_id()
    if tenant_id is not None and tenant_id != bound:
        msg = f"session is bound to tenant {bound}, cannot verify tenant {tenant_id}"
        raise ContextMissingError(msg)
    expected_prev = GENESIS_HASH
    checked = 0
    rows = session.scalars(
        select(AuditEventRow)
        .where(AuditEventRow.tenant_id == bound)
        .order_by(AuditEventRow.seq)
    )
    for expected_seq, row in enumerate(rows, start=1):
        checked += 1
        reason: str | None = None
        if row.seq != expected_seq:
            reason = "sequence gap or duplicate"
        elif row.prev_hash != expected_prev:
            reason = "prev_hash does not match the previous entry"
        elif digest(row) != row.hash:
            reason = "content does not match its hash"
        if reason is not None:
            return ChainVerification(
                tenant_id=bound,
                valid=False,
                entries_checked=checked,
                first_broken_seq=row.seq,
                reason=reason,
            )
        expected_prev = row.hash
    return ChainVerification(tenant_id=bound, valid=True, entries_checked=checked)
