"""The private-list API (SP-03 spec §7, §12).

MLRO and ADMIN staff, and API keys with ``private_lists:read`` /
``private_lists:write``, create lists, upload versions and read the history. An
upload is the raw request body typed by its ``Content-Type`` (plan 3a Deviation 4).
Entries are shown only to MLRO and ADMIN staff, through the audited view. Another
tenant's list is ``NOT_FOUND`` (row-level security). Routes hold no business rule.
"""

import json
from collections.abc import Sequence
from itertools import starmap
from typing import Annotated, cast
from uuid import UUID

from fastapi import Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from xolotl.modules.screening.catalog.models import (
    PrivateList,
    PrivateListEntryRow,
    PrivateListVersion,
)
from xolotl.modules.screening.catalog.private import (
    create_private_list,
    get_private_list,
    upload_private_version,
    view_entries,
)
from xolotl.modules.screening.catalog.template import CSV_TYPE, XLSX_TYPE
from xolotl.modules.screening.enums import VersionStatus
from xolotl.modules.screening.lists import JsonValue, ListEntry
from xolotl.modules.screening.schemas import (
    PrivateListCreate,
    PrivateListEntryResponse,
    PrivateListResponse,
    PrivateListVersionResponse,
)
from xolotl.platform.api.app import app_state
from xolotl.platform.api.pagination import Page, PageDep, paginate
from xolotl.platform.api.router import v1_router
from xolotl.platform.auth.actor import Actor
from xolotl.platform.auth.principal import SessionDep, require_access
from xolotl.platform.enums import Role
from xolotl.platform.storage import ObjectStore

router = v1_router(prefix="/private-lists", tags=["private-lists"])

_ROLES = (Role.MLRO, Role.ADMIN)
PrivateListWriter = Annotated[
    Actor,
    Depends(require_access(roles=_ROLES, scope="private_lists:write", write=True)),
]
PrivateListReader = Annotated[
    Actor, Depends(require_access(roles=_ROLES, scope="private_lists:read"))
]
# Staff only: a route without a scope refuses every API client.
PrivateListViewer = Annotated[Actor, Depends(require_access(roles=_ROLES))]


def object_store(request: Request) -> ObjectStore:
    """Give a route the object store its application was built with.

    Returns:
        The application's object store.

    Raises:
        RuntimeError: When the application was built without one.

    """
    store = app_state(request).object_store
    if store is None:
        msg = "the application was built without an object store"
        raise RuntimeError(msg)
    return store


StoreDep = Annotated[ObjectStore, Depends(object_store)]


def _version_response(version: PrivateListVersion) -> PrivateListVersionResponse:
    return PrivateListVersionResponse(
        id=version.id,
        seq=version.seq,
        status=version.status,
        uploaded_by=version.uploaded_by,
        uploaded_at=version.uploaded_at,
        content_type=version.content_type,
        file_size=version.file_size,
        entry_count=version.entry_count,
        added=version.added,
        changed=version.changed,
        removed=version.removed,
    )


def _list_responses(
    s: Session, lists: Sequence[PrivateList]
) -> list[PrivateListResponse]:
    active = {
        version.list_id: version
        for version in s.scalars(
            select(PrivateListVersion).where(
                PrivateListVersion.list_id.in_([row.id for row in lists]),
                PrivateListVersion.status == VersionStatus.ACTIVE,
            )
        )
    }
    return [
        PrivateListResponse(
            id=row.id,
            list_code=row.list_code,
            list_kind=row.list_kind,
            title=row.title,
            created_at=row.created_at,
            active_version=(
                None if row.id not in active else _version_response(active[row.id])
            ),
        )
        for row in lists
    ]


@router.post("", status_code=201)
def create_private_list_endpoint(
    body: PrivateListCreate, _writer: PrivateListWriter, session: SessionDep
) -> PrivateListResponse:
    created = create_private_list(
        session, list_code=body.list_code, title=body.title, list_kind=body.list_kind
    )
    # The server-side created_at is read back before the response is built.
    session.refresh(created)
    [response] = _list_responses(session, [created])
    return response


@router.get("")
def list_private_lists(
    _reader: PrivateListReader, session: SessionDep, page: PageDep
) -> Page[PrivateListResponse]:
    rows, next_cursor = paginate(
        session,
        select(PrivateList),
        ts_col=PrivateList.created_at,
        id_col=PrivateList.id,
        key=lambda row: (row.created_at, row.id),
        page=page,
    )
    return Page(items=_list_responses(session, rows), next_cursor=next_cursor)


@router.post(
    "/{list_id}/versions",
    status_code=201,
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                CSV_TYPE: {"schema": {"type": "string", "format": "binary"}},
                XLSX_TYPE: {"schema": {"type": "string", "format": "binary"}},
            },
        }
    },
)
async def upload_private_list_version(
    list_id: UUID,
    request: Request,
    _writer: PrivateListWriter,
    session: SessionDep,
    store: StoreDep,
) -> PrivateListVersionResponse:
    data = await request.body()
    version = await run_in_threadpool(
        upload_private_version,
        session,
        store,
        list_id=list_id,
        data=data,
        content_type=request.headers.get("content-type", ""),
    )
    await run_in_threadpool(session.refresh, version)
    return _version_response(version)


@router.get("/{list_id}/versions")
def list_private_list_versions(
    list_id: UUID, _reader: PrivateListReader, session: SessionDep, page: PageDep
) -> Page[PrivateListVersionResponse]:
    _ = get_private_list(session, list_id)
    rows, next_cursor = paginate(
        session,
        select(PrivateListVersion).where(PrivateListVersion.list_id == list_id),
        ts_col=PrivateListVersion.uploaded_at,
        id_col=PrivateListVersion.id,
        key=lambda row: (row.uploaded_at, row.id),
        page=page,
    )
    return Page(items=[_version_response(row) for row in rows], next_cursor=next_cursor)


def _entry_response(
    row: PrivateListEntryRow, entry: ListEntry
) -> PrivateListEntryResponse:
    return PrivateListEntryResponse(
        id=row.id,
        source_entry_key=row.source_entry_key,
        entity_type=row.entity_type,
        entry=cast("dict[str, JsonValue]", json.loads(entry.canonical_json())),
    )


@router.get("/{list_id}/entries")
def list_private_list_entries(
    list_id: UUID, _viewer: PrivateListViewer, session: SessionDep, page: PageDep
) -> Page[PrivateListEntryResponse]:
    shown, next_cursor = view_entries(session, list_id, page=page)
    return Page(
        items=list(starmap(_entry_response, shown)),
        next_cursor=next_cursor,
    )
