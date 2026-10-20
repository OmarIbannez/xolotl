"""The content-keyed whitelist (SP-03 spec §9; FR-SC-007, DR-306).

A false-positive decision whitelists the exact entry content for the subject: the
key is ``(subject, list, entry key, content hash)``, so a changed entry has another
hash and alerts again. An MLRO may revoke a row; a later false positive on the same
content reactivates it.
"""

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from xolotl.modules.screening.engine.models import ScreeningMatch
from xolotl.modules.screening.review.models import MatchDisposition, ScreeningWhitelist
from xolotl.platform import clock
from xolotl.platform.audit import AuditEvent, record
from xolotl.platform.db import bound_context
from xolotl.platform.enums import Role
from xolotl.platform.errors import ForbiddenError, NotFoundError
from xolotl.platform.ids import uuid7

_KEY = [
    "tenant_id",
    "subject_id",
    "list_code",
    "source_entry_key",
    "entry_content_hash",
]


def active_whitelist_keys(s: Session, subject_id: UUID) -> set[tuple[str, str, bytes]]:
    """Read the entry contents whitelisted for a subject and not revoked.

    Returns:
        ``(list_code, source_entry_key, entry_content_hash)`` of each active row.

    """
    rows = s.execute(
        select(
            ScreeningWhitelist.list_code,
            ScreeningWhitelist.source_entry_key,
            ScreeningWhitelist.entry_content_hash,
        ).where(
            ScreeningWhitelist.subject_id == subject_id,
            ScreeningWhitelist.revoked_at.is_(None),
        )
    )
    return {(code, key, content) for code, key, content in rows}


def whitelist_entry(
    s: Session, match: ScreeningMatch, disposition: MatchDisposition
) -> ScreeningWhitelist:
    """Whitelist the finding's exact entry content, reactivating a revoked row.

    Returns:
        The active whitelist row, pointing at ``disposition``.

    """
    statement = (
        insert(ScreeningWhitelist)
        .values(
            tenant_id=match.tenant_id,
            id=uuid7(),
            subject_id=match.subject_id,
            list_code=match.list_code,
            source_entry_key=match.source_entry_key,
            entry_content_hash=match.entry_content_hash,
            disposition_id=disposition.id,
        )
        .on_conflict_do_update(
            index_elements=_KEY,
            set_={
                "disposition_id": disposition.id,
                "revoked_at": None,
                "revoked_by": None,
                "created_at": func.now(),
            },
        )
        .returning(ScreeningWhitelist)
    )
    return s.scalars(statement, execution_options={"populate_existing": True}).one()


def revoke_whitelist(s: Session, whitelist_id: UUID) -> ScreeningWhitelist:
    """Revoke a whitelist row, so the next run may alert on that content again.

    Revoking a revoked row changes nothing and records nothing.

    Returns:
        The row, revoked.

    Raises:
        ForbiddenError: When the actor is not an MLRO (``ROLE_FORBIDDEN``).
        NotFoundError: When the tenant has no such row.

    """
    ctx = bound_context(s)
    if Role.MLRO not in ctx.roles:
        msg = "only an MLRO revokes a whitelist entry"
        raise ForbiddenError(msg, code="ROLE_FORBIDDEN")
    row = s.scalars(
        select(ScreeningWhitelist)
        .where(ScreeningWhitelist.id == whitelist_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).one_or_none()
    if row is None:
        msg = "whitelist entry not found"
        raise NotFoundError(msg)
    if row.revoked_at is not None:
        return row
    row.revoked_at = clock.now()
    row.revoked_by = UUID(ctx.actor_id)
    s.flush()
    _ = record(
        s,
        AuditEvent(
            action="screening.whitelist_revoked",
            entity_type="screening_whitelist",
            entity_id=str(row.id),
            detail={"subject_id": str(row.subject_id)},
        ),
    )
    return row
