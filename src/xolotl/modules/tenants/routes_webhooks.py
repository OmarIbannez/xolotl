"""Webhook endpoints and deliveries, for tenant administrators (spec §9.2).

Staff with the ``ADMIN`` role only: an API key has no scope for these and a read-only
auditor does not see them. The secret of an endpoint is in the response that creates it
or rotates it and in no other.
"""

from typing import Annotated
from uuid import UUID

from fastapi import Depends, Request
from sqlalchemy import select

from xolotl.modules.tenants import webhook_service
from xolotl.modules.tenants.schemas import (
    WebhookDeliveryResponse,
    WebhookEndpointCreatedResponse,
    WebhookEndpointCreateRequest,
    WebhookEndpointResponse,
    WebhookSecretResponse,
)
from xolotl.platform.api.app import app_services
from xolotl.platform.api.pagination import Page, PageDep, paginate
from xolotl.platform.api.router import v1_router
from xolotl.platform.auth.actor import Actor
from xolotl.platform.auth.principal import SessionDep, require_access
from xolotl.platform.config import get_settings
from xolotl.platform.enums import DeliveryStatus, Role
from xolotl.platform.events.models import WebhookDelivery, WebhookEndpoint
from xolotl.platform.events.url_policy import UrlPolicy
from xolotl.platform.jobs.queue import PostgresJobQueue

router = v1_router(prefix="/webhooks", tags=["webhooks"])

AdminWriter = Annotated[Actor, Depends(require_access(roles=(Role.ADMIN,), write=True))]
AdminReader = Annotated[Actor, Depends(require_access(roles=(Role.ADMIN,)))]


def webhook_url_policy() -> UrlPolicy:
    """Provide the rules endpoint URLs are held to: strict in production.

    Returns:
        The policy for the configured environment.

    """
    return UrlPolicy.for_settings(get_settings())


def webhook_queue(request: Request) -> PostgresJobQueue:
    """Provide the queue to put a retry's job on, in the request's own transaction.

    Returns:
        The queue the platform was composed with, the one the worker runs jobs from;
        ``enqueue`` uses only the session it is given.

    """
    return app_services(request).queue


PolicyDep = Annotated[UrlPolicy, Depends(webhook_url_policy)]
QueueDep = Annotated[PostgresJobQueue, Depends(webhook_queue)]


@router.post("/endpoints", status_code=201)
def create_webhook_endpoint(
    body: WebhookEndpointCreateRequest,
    _admin: AdminWriter,
    session: SessionDep,
    policy: PolicyDep,
) -> WebhookEndpointCreatedResponse:
    return webhook_service.create_webhook_endpoint(
        session, url=body.url, event_types=body.event_types, policy=policy
    )


@router.get("/endpoints")
def list_webhook_endpoints(
    _admin: AdminReader, session: SessionDep, page: PageDep
) -> Page[WebhookEndpointResponse]:
    rows, next_cursor = paginate(
        session,
        select(WebhookEndpoint),
        ts_col=WebhookEndpoint.created_at,
        id_col=WebhookEndpoint.id,
        key=lambda row: (row.created_at, row.id),
        page=page,
    )
    return Page(
        items=[WebhookEndpointResponse.model_validate(row) for row in rows],
        next_cursor=next_cursor,
    )


@router.delete("/endpoints/{endpoint_id}", status_code=204)
def disable_webhook_endpoint(
    endpoint_id: UUID, _admin: AdminWriter, session: SessionDep
) -> None:
    webhook_service.disable_webhook_endpoint(session, endpoint_id)


@router.post("/endpoints/{endpoint_id}/rotate-secret")
def rotate_webhook_secret(
    endpoint_id: UUID, _admin: AdminWriter, session: SessionDep
) -> WebhookSecretResponse:
    return webhook_service.rotate_webhook_secret(session, endpoint_id)


@router.get("/deliveries")
def list_webhook_deliveries(
    _admin: AdminReader,
    session: SessionDep,
    page: PageDep,
    status: DeliveryStatus | None = None,
    endpoint_id: UUID | None = None,
) -> Page[WebhookDeliveryResponse]:
    query = select(WebhookDelivery)
    if status is not None:
        query = query.where(WebhookDelivery.status == status)
    if endpoint_id is not None:
        query = query.where(WebhookDelivery.endpoint_id == endpoint_id)
    rows, next_cursor = paginate(
        session,
        query,
        ts_col=WebhookDelivery.created_at,
        id_col=WebhookDelivery.id,
        key=lambda row: (row.created_at, row.id),
        page=page,
    )
    return Page(
        items=[WebhookDeliveryResponse.model_validate(row) for row in rows],
        next_cursor=next_cursor,
    )


@router.post("/deliveries/{delivery_id}/retry")
def retry_webhook_delivery(
    delivery_id: UUID, _admin: AdminWriter, session: SessionDep, queue: QueueDep
) -> WebhookDeliveryResponse:
    return webhook_service.retry_webhook_delivery(session, queue, delivery_id)
