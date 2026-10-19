"""Webhook endpoints and deliveries, as a tenant's administrators manage them (§9.2).

An endpoint's signing secret is made here, shown in the response that creates the
endpoint or rotates the secret and in no other, and stored encrypted under the tenant's
DATA key (``EncryptedStr``); nothing reads it back but the delivery that signs with it.
Every change is audited, by id: the URL is left out, since a URL often carries a token.

The sending itself, the fan-out and the retry schedule belong to
``xolotl.platform.events.webhooks``; this module is the tenant's side of it.
"""

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy.orm import Session

from xolotl.modules.tenants.schemas import (
    WebhookDeliveryResponse,
    WebhookEndpointCreatedResponse,
    WebhookSecretResponse,
)
from xolotl.platform.audit import AuditEvent, record
from xolotl.platform.crypto.encrypted import encrypt
from xolotl.platform.db import bound_context
from xolotl.platform.enums import EndpointStatus
from xolotl.platform.errors import DomainError, NotFoundError
from xolotl.platform.events.models import WebhookEndpoint
from xolotl.platform.events.url_policy import UrlPolicy
from xolotl.platform.events.webhooks import generate_webhook_secret, retry_delivery
from xolotl.platform.jobs.queue import PostgresJobQueue


def _endpoint(s: Session, endpoint_id: UUID) -> WebhookEndpoint:
    """Find and lock the tenant's endpoint.

    Returns:
        The endpoint row.

    Raises:
        NotFoundError: When the tenant has no such endpoint.

    """
    row = s.get(
        WebhookEndpoint,
        (bound_context(s).require_tenant_id(), endpoint_id),
        with_for_update=True,
    )
    if row is None:
        raise NotFoundError
    return row


def create_webhook_endpoint(
    s: Session, *, url: str, event_types: Sequence[str], policy: UrlPolicy
) -> WebhookEndpointCreatedResponse:
    """Create an endpoint and its secret.

    ``policy`` checks the URL the way delivery will (in production that resolves the
    host) and refuses one that breaks a rule with ``WebhookUrlError`` (422); the event
    types are kept once each, in order.

    Returns:
        The endpoint, with the secret it is signed by. This is the only time it is
        shown.

    """
    ctx = bound_context(s)
    tenant_id = ctx.require_tenant_id()
    cleaned = url.strip()
    _ = policy.destination(cleaned)
    secret = generate_webhook_secret()
    row = WebhookEndpoint(
        tenant_id=tenant_id,
        url=cleaned,
        secret=encrypt(secret, tenant_id=tenant_id, session=s),
        event_types=sorted(set(event_types)),
        status=EndpointStatus.ACTIVE,
        created_by=UUID(ctx.actor_id),
    )
    s.add(row)
    s.flush()
    _ = record(
        s,
        AuditEvent(
            action="webhook_endpoint.created",
            entity_type="webhook_endpoint",
            entity_id=str(row.id),
        ),
    )
    return WebhookEndpointCreatedResponse(
        id=row.id,
        url=row.url,
        event_types=list(row.event_types),
        status=row.status,
        created_at=row.created_at,
        secret=secret,
    )


def disable_webhook_endpoint(s: Session, endpoint_id: UUID) -> None:
    """Stop sending to an endpoint; its deliveries stay as they were.

    One already disabled is left alone and not audited again. What was waiting for it
    ends as ``DEAD`` when its turn comes. An endpoint the tenant does not have is a 404.
    """
    row = _endpoint(s, endpoint_id)
    if row.status is EndpointStatus.DISABLED:
        return
    row.status = EndpointStatus.DISABLED
    s.flush()
    _ = record(
        s,
        AuditEvent(
            action="webhook_endpoint.deleted",
            entity_type="webhook_endpoint",
            entity_id=str(row.id),
        ),
    )


def rotate_webhook_secret(s: Session, endpoint_id: UUID) -> WebhookSecretResponse:
    """Replace an endpoint's secret.

    The old secret stops signing at once: deliveries read the secret when they are
    sent, retries of earlier events included. The receiver has to change its copy
    before the next delivery. An endpoint the tenant does not have is a 404.

    Returns:
        The endpoint's id and its new secret. This is the only time it is shown.

    Raises:
        DomainError: When the endpoint is disabled (409).

    """
    row = _endpoint(s, endpoint_id)
    if row.status is EndpointStatus.DISABLED:
        msg = "webhook endpoint is disabled"
        raise DomainError(msg)
    secret = generate_webhook_secret()
    row.secret = encrypt(secret, tenant_id=row.tenant_id, session=s)
    s.flush()
    _ = record(
        s,
        AuditEvent(
            action="webhook_endpoint.secret_rotated",
            entity_type="webhook_endpoint",
            entity_id=str(row.id),
        ),
    )
    return WebhookSecretResponse(id=row.id, secret=secret)


def retry_webhook_delivery(
    s: Session, queue: PostgresJobQueue, delivery_id: UUID
) -> WebhookDeliveryResponse:
    """Send a dead or waiting delivery again now, and audit that.

    A delivery the tenant does not have is a 404; one already delivered, or whose
    endpoint is disabled, a 409.

    Returns:
        The delivery, ``PENDING`` and due now.

    """
    delivery = retry_delivery(s, queue, delivery_id)
    s.flush()
    _ = record(
        s,
        AuditEvent(
            action="webhook_delivery.retried",
            entity_type="webhook_delivery",
            entity_id=str(delivery.id),
            detail={
                "endpoint_id": str(delivery.endpoint_id),
                "event_id": str(delivery.event_id),
            },
        ),
    )
    return WebhookDeliveryResponse.model_validate(delivery)
