"""The exact list versions a run screened against (SP-03 spec §8.3, FR-SC-001).

A list set is named by the hash of its members' codes and version ids, so every run
over the same versions shares one ``screening_list_set`` row, and the hash also keys
the holder's scoring IDF table of that set (Deviation 2).
"""

import hashlib
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from xolotl.modules.screening.engine.models import (
    ScreeningListSet,
    ScreeningListSetMember,
)
from xolotl.platform.db import bound_context
from xolotl.platform.ids import uuid7


@dataclass(frozen=True, slots=True)
class ListSetMemberRef:
    """One list of a run: a shared version or a private one, never both."""

    list_code: str
    list_kind: str
    list_version_id: UUID | None
    private_list_version_id: UUID | None
    seq: int


def list_set_hash(members: Iterable[ListSetMemberRef]) -> bytes:
    """Hash a list set: SHA-256 of its sorted ``code:version_id`` lines.

    Returns:
        The 32-byte digest.

    """
    lines = sorted(
        f"{m.list_code}:{m.list_version_id or m.private_list_version_id}"
        for m in members
    )
    return hashlib.sha256("\n".join(lines).encode()).digest()


def upsert_list_set(
    s: Session, members: Sequence[ListSetMemberRef]
) -> ScreeningListSet:
    """Return the tenant's list set of exactly these versions, creating it once.

    The members are written only with a new set; an existing one already has them.

    Returns:
        The list set.

    """
    tenant_id = bound_context(s).require_tenant_id()
    content_hash = list_set_hash(members)
    created = s.scalars(
        insert(ScreeningListSet)
        .values(tenant_id=tenant_id, id=uuid7(), content_hash=content_hash)
        .on_conflict_do_nothing(index_elements=["tenant_id", "content_hash"])
        .returning(ScreeningListSet)
    ).one_or_none()
    if created is None:
        return s.scalars(
            select(ScreeningListSet).where(
                ScreeningListSet.content_hash == content_hash
            )
        ).one()
    s.add_all(
        ScreeningListSetMember(
            tenant_id=tenant_id,
            list_set_id=created.id,
            list_code=m.list_code,
            list_version_id=m.list_version_id,
            private_list_version_id=m.private_list_version_id,
        )
        for m in members
    )
    s.flush()
    return created
