"""Frozen, content-addressed effective policies (spec §5.3, program spec D4).

A snapshot is the canonical JSON of an ``EffectivePolicy``; its SHA-256 is unique
per tenant, so resolving the same policy twice returns the same snapshot id, and
every later decision stores that id.
"""

import hashlib
from datetime import datetime
from typing import ClassVar, cast
from uuid import UUID

from pydantic import BaseModel, ConfigDict, JsonValue
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from xolotl.platform.audit import canonical_json
from xolotl.platform.db import bound_context
from xolotl.platform.errors import NotFoundError
from xolotl.platform.ids import uuid7
from xolotl.platform.policy.models import PolicySnapshot
from xolotl.platform.policy.resolver import EffectivePolicy
from xolotl.platform.sqltypes import contains_float


class PolicySnapshotContent(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    snapshot_id: UUID
    content_hash: str
    created_at: datetime
    policy: EffectivePolicy


def snapshot_policy(session: Session, effective: EffectivePolicy) -> UUID:
    """Store ``effective`` once per tenant and content.

    Returns:
        The id of the snapshot holding exactly this content, new or existing.

    Raises:
        ValueError: When the dump holds a float (it would not hash back).

    """
    content = cast("dict[str, JsonValue]", effective.model_dump(mode="json"))
    if contains_float(content):
        msg = "an effective policy must not dump a float"
        raise ValueError(msg)
    digest = hashlib.sha256(canonical_json(content)).digest()
    tenant_id = bound_context(session).require_tenant_id()
    created = session.scalar(
        insert(PolicySnapshot)
        .values(tenant_id=tenant_id, id=uuid7(), content_hash=digest, content=content)
        .on_conflict_do_nothing(index_elements=["tenant_id", "content_hash"])
        .returning(PolicySnapshot.id)
    )
    if created is not None:
        return created
    return session.scalars(
        select(PolicySnapshot.id).where(
            PolicySnapshot.tenant_id == tenant_id,
            PolicySnapshot.content_hash == digest,
        )
    ).one()


def get_snapshot(session: Session, snapshot_id: UUID) -> PolicySnapshotContent:
    """Read a snapshot of the session's tenant.

    Returns:
        The frozen policy with its id, hex content hash and creation time.

    Raises:
        NotFoundError: When the tenant has no such snapshot.

    """
    tenant_id = bound_context(session).require_tenant_id()
    row = session.get(PolicySnapshot, (tenant_id, snapshot_id))
    if row is None:
        msg = "policy snapshot not found"
        raise NotFoundError(msg)
    return PolicySnapshotContent(
        snapshot_id=row.id,
        content_hash=row.content_hash.hex(),
        created_at=row.created_at,
        policy=EffectivePolicy.model_validate(row.content),
    )
