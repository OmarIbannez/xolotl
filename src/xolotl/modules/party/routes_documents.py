"""A party's documents and its legal hold (SP-02 spec §9).

Uploads are raw request bodies, typed by their ``Content-Type`` header; staff and
API keys with ``parties:write`` may upload. Only staff download, and each
download records ``VIEW_PII`` on the document. The legal hold is the MLRO's.
"""

from typing import Annotated
from uuid import UUID

from fastapi import Depends, Query, Request
from fastapi.responses import Response
from starlette.concurrency import run_in_threadpool

from xolotl.modules.party import customer, documents, service
from xolotl.modules.party.enums import DocumentType
from xolotl.modules.party.routes import PartyStaff, PartyWriter
from xolotl.modules.party.schemas import (
    DocumentResponse,
    LegalHoldRequest,
    PartyResponse,
)
from xolotl.platform.api.app import app_state
from xolotl.platform.api.router import v1_router
from xolotl.platform.auth.actor import Actor
from xolotl.platform.auth.principal import SessionDep, require_access
from xolotl.platform.enums import Role
from xolotl.platform.storage import ObjectStore

router = v1_router(prefix="/parties", tags=["documents"])

LegalHoldWriter = Annotated[
    Actor, Depends(require_access(roles=(Role.MLRO,), write=True))
]


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


@router.post(
    "/{party_id}/documents",
    status_code=201,
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                "application/octet-stream": {
                    "schema": {"type": "string", "format": "binary"}
                }
            },
        }
    },
)
async def upload_party_document(
    party_id: UUID,
    request: Request,
    doc_type: Annotated[DocumentType, Query()],
    _writer: PartyWriter,
    session: SessionDep,
    store: StoreDep,
) -> DocumentResponse:
    data = await request.body()
    content_type = (
        request.headers.get("content-type", "").partition(";")[0].strip().lower()
    )
    row = await run_in_threadpool(
        documents.upload_document,
        session,
        store,
        party_id=party_id,
        doc_type=doc_type,
        content_type=content_type,
        data=data,
    )
    return service.document_response(row)


@router.get(
    "/{party_id}/documents/{document_id}/content",
    responses={200: {"content": {"application/octet-stream": {}}}},
)
def download_party_document(
    party_id: UUID,
    document_id: UUID,
    _staff: PartyStaff,
    session: SessionDep,
    store: StoreDep,
) -> Response:
    row, data = documents.download_document(
        session, store, party_id=party_id, document_id=document_id
    )
    filename = documents.download_filename(row)
    return Response(
        content=data,
        media_type=row.content_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/{party_id}/legal-hold")
def set_party_legal_hold(
    party_id: UUID, body: LegalHoldRequest, _mlro: LegalHoldWriter, session: SessionDep
) -> PartyResponse:
    party = customer.set_legal_hold(session, party_id, legal_hold=body.legal_hold)
    return service.summary(session, party)
