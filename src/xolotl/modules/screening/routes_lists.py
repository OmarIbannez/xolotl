"""The shared lists API (SP-03 spec §12).

Staff of any role read the tenant's subscriptions, the lists' version history, their
deltas and entries (public data); API keys with ``lists:read`` read the overview only.
Only ADMIN switches a subscription. Routes hold no business rule.
"""

import base64
import binascii
from typing import Annotated
from uuid import UUID

from fastapi import Depends, Query
from sqlalchemy import select

from xolotl.modules.screening.catalog.models import ListEntryRow, ListVersion
from xolotl.modules.screening.catalog.queries import delta_page
from xolotl.modules.screening.catalog.service import (
    list_overview,
    set_subscription_enabled,
)
from xolotl.modules.screening.schemas import (
    DeltaRowResponse,
    ListEntryResponse,
    ListOverview,
    ListOverviewResponse,
    ListVersionResponse,
    SubscriptionUpdate,
)
from xolotl.platform import clock
from xolotl.platform.api.pagination import (
    DEFAULT_LIMIT,
    MAX_LIMIT,
    Page,
    PageDep,
    paginate,
)
from xolotl.platform.api.router import v1_router
from xolotl.platform.auth.actor import Actor
from xolotl.platform.auth.principal import SessionDep, require_access
from xolotl.platform.enums import Role
from xolotl.platform.errors import NotFoundError, ValidationFailedError

router = v1_router(prefix="/lists", tags=["lists"])

ListsReader = Annotated[Actor, Depends(require_access(scope="lists:read"))]
# Staff only: a route without a scope refuses every API client.
ListsStaff = Annotated[Actor, Depends(require_access())]
ListsAdmin = Annotated[Actor, Depends(require_access(roles=(Role.ADMIN,)))]


def _version_response(version: ListVersion) -> ListVersionResponse:
    return ListVersionResponse(
        id=version.id,
        seq=version.seq,
        status=version.status,
        published_at=version.published_at,
        source_version=version.source_version,
        parser_version=version.parser_version,
        fetched_at=version.fetched_at,
        loaded_by=version.loaded_by,
        file_sha256=version.file_sha256.hex(),
        file_size=version.file_size,
        entry_count=version.entry_count,
        added=version.added,
        changed=version.changed,
        removed=version.removed,
    )


def _encode_key(key: str) -> str:
    return base64.urlsafe_b64encode(key.encode()).decode().rstrip("=")


def _decode_key(cursor: str) -> str:
    padded = cursor + "=" * (-len(cursor) % 4)
    try:
        return base64.urlsafe_b64decode(padded).decode()
    except (binascii.Error, ValueError, UnicodeDecodeError):
        msg = "invalid cursor"
        raise ValidationFailedError(msg) from None


@router.get("")
def list_lists(_reader: ListsReader, session: SessionDep) -> ListOverviewResponse:
    return ListOverviewResponse(items=list_overview(session, now=clock.now()))


@router.get("/{code}/versions")
def list_list_versions(
    code: str, _staff: ListsStaff, session: SessionDep, page: PageDep
) -> Page[ListVersionResponse]:
    rows, next_cursor = paginate(
        session,
        select(ListVersion).where(ListVersion.list_code == code),
        ts_col=ListVersion.fetched_at,
        id_col=ListVersion.id,
        key=lambda row: (row.fetched_at, row.id),
        page=page,
    )
    return Page(items=[_version_response(row) for row in rows], next_cursor=next_cursor)


@router.get("/{code}/versions/{version_id}/delta")
def list_version_delta(
    code: str,
    version_id: UUID,
    _staff: ListsStaff,
    session: SessionDep,
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = DEFAULT_LIMIT,
    cursor: Annotated[str | None, Query(max_length=512)] = None,
) -> Page[DeltaRowResponse]:
    version = session.get(ListVersion, version_id)
    if version is None or version.list_code != code:
        msg = "list version not found"
        raise NotFoundError(msg)
    after_key = None if cursor is None else _decode_key(cursor)
    rows = delta_page(session, version_id, after_key=after_key, limit=limit + 1)
    next_cursor = None
    if len(rows) > limit:
        rows = rows[:limit]
        next_cursor = _encode_key(rows[-1].source_entry_key)
    return Page(
        items=[
            DeltaRowResponse(
                source_entry_key=row.source_entry_key,
                change=row.change,
                prev_entry_id=row.prev_entry_id,
                new_entry_id=row.new_entry_id,
                prev_name=row.prev_name,
                new_name=row.new_name,
            )
            for row in rows
        ],
        next_cursor=next_cursor,
    )


@router.get("/{code}/entries/{entry_id}")
def get_list_entry(
    code: str, entry_id: UUID, _staff: ListsStaff, session: SessionDep
) -> ListEntryResponse:
    row = session.get(ListEntryRow, entry_id)
    if row is None or row.list_code != code:
        msg = "list entry not found"
        raise NotFoundError(msg)
    return ListEntryResponse(
        id=row.id,
        list_code=row.list_code,
        source_entry_key=row.source_entry_key,
        from_seq=row.from_seq,
        to_seq=row.to_seq,
        entry=row.entry,
    )


@router.patch("/subscriptions/{code}")
def set_list_subscription(
    code: str, body: SubscriptionUpdate, _admin: ListsAdmin, session: SessionDep
) -> ListOverview:
    _ = set_subscription_enabled(session, code, enabled=body.enabled)
    return next(
        row for row in list_overview(session, now=clock.now()) if row.code == code
    )
