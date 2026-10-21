"""The transactions API for API clients and staff (SP-04 spec §15, plan 4a A10).

API keys with ``transactions:write`` submit batches and files; ``transactions:read``
reads files, batches and transactions. An API client only ever gets
``TransactionClientView`` (identity and status: tipping-off, D20); staff get the
staff view, and ``?reveal=true`` decrypts the counterparty and the payload with
``VIEW_PII``, which an API key is refused. The parked and backfill lists are staff
only. Literal paths are registered before ``/{transaction_id}``. Routes hold no
business rule.
"""

import hashlib
from typing import Annotated
from uuid import UUID

from fastapi import Depends, Query, Request
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from xolotl.modules.monitoring.enums import BatchSource, TransactionStatus
from xolotl.modules.monitoring.errors import (
    BatchInvalidError,
    BatchTooLargeError,
    FileInvalidError,
    FileTooLargeError,
    field_error_items,
)
from xolotl.modules.monitoring.schemas import (
    BatchAccepted,
    BatchResponse,
    BatchSubmission,
    FileStatusResponse,
    TransactionClientView,
    TransactionQuery,
    TransactionRevealedView,
    TransactionStaffView,
)
from xolotl.modules.monitoring.txn import queries, views
from xolotl.modules.monitoring.txn.files import (
    ACCEPTED_TYPES,
    media_type,
    read_object,
    upload_file,
)
from xolotl.modules.monitoring.txn.ingest import ingest_batch
from xolotl.modules.monitoring.txn.models import (
    Transaction,
    TransactionBatch,
    TransactionFile,
)
from xolotl.modules.monitoring.txn.queries import TransactionFilters
from xolotl.modules.monitoring.txn.schema import validate_batch
from xolotl.platform import clock
from xolotl.platform.api.app import app_services, app_state
from xolotl.platform.api.pagination import Page, PageDep, PageRequest, paginate
from xolotl.platform.api.router import v1_router
from xolotl.platform.auth.actor import Actor
from xolotl.platform.auth.principal import SessionDep, require_access
from xolotl.platform.config import get_settings
from xolotl.platform.db import bound_context
from xolotl.platform.errors import ForbiddenError, NotFoundError
from xolotl.platform.jobs.queue import PostgresJobQueue
from xolotl.platform.logging import request_id_var
from xolotl.platform.storage import ObjectStore

router = v1_router(prefix="/transactions", tags=["transactions"])

TransactionReader = Annotated[Actor, Depends(require_access(scope="transactions:read"))]
TransactionWriter = Annotated[
    Actor, Depends(require_access(scope="transactions:write", write=True))
]
# Staff only: a route without a scope refuses every API client.
TransactionStaff = Annotated[Actor, Depends(require_access())]

_MIB = 1024 * 1024
_REVEAL_REFUSED = "only staff reveal a transaction"
_NO_REPORT = "the file has no error report"


def job_queue(request: Request) -> PostgresJobQueue:
    """Give a route the job queue its application was composed with.

    Returns:
        The process's queue; ``enqueue`` uses only the session it is given.

    """
    return app_services(request).queue


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


QueueDep = Annotated[PostgresJobQueue, Depends(job_queue)]
StoreDep = Annotated[ObjectStore, Depends(object_store)]


def _submitted_by(actor: Actor) -> str:
    ctx = actor.context
    return f"{ctx.actor_type.value.lower()}:{ctx.actor_id}"


def _file_response(row: TransactionFile) -> FileStatusResponse:
    return FileStatusResponse(
        file_id=row.id,
        status=row.status,
        rows_total=row.rows_total,
        rows_accepted=row.rows_accepted,
        rows_duplicate=row.rows_duplicate,
        rows_rejected=row.rows_rejected,
        uploaded_at=row.uploaded_at,
        finished_at=row.finished_at,
        last_error=row.last_error,
    )


def _batch_response(row: TransactionBatch) -> BatchResponse:
    return BatchResponse(
        id=row.id,
        source=row.source,
        item_count=row.item_count,
        accepted=row.accepted,
        duplicates=row.duplicates,
        received_at=row.received_at,
        request_id=row.request_id,
        file_id=row.file_id,
    )


def _page(
    session: Session, filters: TransactionFilters, page: PageRequest
) -> tuple[list[Transaction], str | None]:
    return paginate(
        session,
        queries.transactions_query(filters),
        ts_col=Transaction.occurred_at,
        id_col=Transaction.id,
        key=lambda row: (row.occurred_at, row.id),
        page=page,
    )


@router.post("", status_code=202)
def submit_transactions(
    body: BatchSubmission,
    actor: TransactionWriter,
    session: SessionDep,
    queue: QueueDep,
) -> BatchAccepted:
    settings = get_settings()
    if len(body.transactions) > settings.monitoring_batch_max_items:
        raise BatchTooLargeError
    accepted, errors = validate_batch(
        body.transactions,
        now=clock.now(),
        max_future_minutes=settings.monitoring_max_future_minutes,
    )
    if errors:
        raise BatchInvalidError(errors=field_error_items(errors))
    report = ingest_batch(
        session,
        accepted,
        source=BatchSource.API,
        submitted_by=_submitted_by(actor),
        queue=queue,
        request_id=request_id_var.get() or None,
    )
    return BatchAccepted(
        batch_id=report.batch_id,
        accepted=report.accepted,
        duplicates=report.duplicates,
        transaction_ids=list(report.transaction_ids),
    )


def _upload(
    session: Session,
    store: ObjectStore,
    *,
    data: bytes,
    content_type: str,
    uploaded_by: str,
    queue: PostgresJobQueue,
) -> tuple[TransactionFile, bool]:
    # A concurrent upload of the same new file wins the unique (tenant_id, sha256)
    # index; this one then answers with the winner's row, as for an earlier upload.
    try:
        with session.begin_nested():
            return upload_file(
                session,
                store,
                data=data,
                content_type=content_type,
                uploaded_by=uploaded_by,
                queue=queue,
            )
    except IntegrityError:
        existing = session.scalar(
            select(TransactionFile).where(
                TransactionFile.sha256 == hashlib.sha256(data).digest()
            )
        )
        if existing is None:
            raise
        return existing, False


@router.post(
    "/files",
    status_code=202,
    responses={200: {"model": FileStatusResponse}},
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                kind: {"schema": {"type": "string", "format": "binary"}}
                for kind in sorted(ACCEPTED_TYPES)
            },
        }
    },
)
async def upload_transaction_file(
    request: Request,
    response: Response,
    actor: TransactionWriter,
    session: SessionDep,
    store: StoreDep,
    queue: QueueDep,
) -> FileStatusResponse:
    content_type = media_type(request.headers.get("content-type", ""))
    if content_type not in ACCEPTED_TYPES:
        msg = "the content type is not CSV, JSON Lines or XLSX"
        raise FileInvalidError(msg)
    cap = get_settings().monitoring_file_max_mb * _MIB
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > cap:
        raise FileTooLargeError
    data = await request.body()
    if len(data) > cap:
        raise FileTooLargeError
    row, created = await run_in_threadpool(
        _upload,
        session,
        store,
        data=data,
        content_type=content_type,
        uploaded_by=_submitted_by(actor),
        queue=queue,
    )
    if not created:
        response.status_code = 200
    return _file_response(row)


@router.get("/parked")
def list_parked_transactions(
    _staff: TransactionStaff, session: SessionDep, page: PageDep
) -> Page[TransactionStaffView]:
    rows, next_cursor = _page(
        session, TransactionFilters(status=TransactionStatus.PARKED), page
    )
    return Page(items=views.staff_views(session, rows), next_cursor=next_cursor)


@router.get("/backfill")
def list_backfill_transactions(
    _staff: TransactionStaff, session: SessionDep, page: PageDep
) -> Page[TransactionStaffView]:
    rows, next_cursor = _page(
        session, TransactionFilters(status=TransactionStatus.BACKFILL), page
    )
    return Page(items=views.staff_views(session, rows), next_cursor=next_cursor)


@router.get("/files/{file_id}")
def get_transaction_file(
    file_id: UUID, _reader: TransactionReader, session: SessionDep
) -> FileStatusResponse:
    return _file_response(queries.get_file(session, file_id))


@router.get("/files/{file_id}/errors", responses={200: {"content": {"text/csv": {}}}})
def get_transaction_file_errors(
    file_id: UUID, _reader: TransactionReader, session: SessionDep, store: StoreDep
) -> Response:
    row = queries.get_file(session, file_id)
    if row.errors_key is None:
        raise NotFoundError(_NO_REPORT)
    data = read_object(
        store, bound_context(session).require_tenant_id(), row.errors_key
    )
    if data is None:
        raise NotFoundError(_NO_REPORT)
    return Response(
        content=data,
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{file_id}.errors.csv"'},
    )


@router.get("/batches/{batch_id}")
def get_transaction_batch(
    batch_id: UUID, _reader: TransactionReader, session: SessionDep
) -> BatchResponse:
    return _batch_response(queries.get_batch(session, batch_id))


@router.get("")
def list_transactions(
    actor: TransactionReader,
    session: SessionDep,
    page: PageDep,
    query: Annotated[TransactionQuery, Query()],
) -> Page[TransactionStaffView] | Page[TransactionClientView]:
    filters = TransactionFilters(
        party_id=query.party_id,
        relationship_id=query.relationship_id,
        external_id=query.external_id,
        from_=query.from_,
        to=query.to,
        status=query.status,
        instrument=query.instrument,
        rail=query.rail,
        late=query.late,
    )
    rows, next_cursor = _page(session, filters, page)
    if actor.is_staff:
        return Page(items=views.staff_views(session, rows), next_cursor=next_cursor)
    return Page(items=[views.client_view(row) for row in rows], next_cursor=next_cursor)


@router.get("/{transaction_id}")
def get_transaction(
    transaction_id: UUID,
    actor: TransactionReader,
    session: SessionDep,
    *,
    reveal: bool = False,
) -> TransactionRevealedView | TransactionStaffView | TransactionClientView:
    if reveal and not actor.is_staff:
        raise ForbiddenError(_REVEAL_REFUSED)
    row = queries.get_transaction(session, transaction_id)
    if not actor.is_staff:
        return views.client_view(row)
    if reveal:
        return views.revealed_view(session, row)
    return views.staff_view(session, row)
