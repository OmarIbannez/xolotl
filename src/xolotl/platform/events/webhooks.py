"""Webhooks: signed delivery of events to a tenant's endpoints (program D8, spec §9.2).

This module is a public contract. Receivers implement it from the description below.

**What a receiver gets.** One ``POST`` per event and endpoint, with the event as JSON in
the body (``Content-Type: application/json``): ``id``, ``type``, ``source``,
``tenant_id``, ``subject``, ``time`` (UTC, RFC 3339), ``key``, ``correlation_id`` and
``policy_snapshot_id`` (either may be ``null``) and ``data``, which carries ids and
never personal data. And four headers:

* ``X-Xolotl-Event-Id``: the event's id. Deliveries are at least once, so a receiver
  that must act once keeps the ids it has handled.
* ``X-Xolotl-Event-Type``: the event's type, such as ``party.created.v1``.
* ``X-Xolotl-Timestamp``: when this attempt was signed, in whole Unix seconds. Every
  attempt, a retry included, carries a new one.
* ``X-Xolotl-Signature``: ``v1=`` and the lower-case hex HMAC-SHA256 of
  ``"<timestamp>.<body>"``, keyed by the endpoint's secret (the whole ``whsec_...``
  text, UTF-8), over the raw bytes of the body exactly as received.

**Verifying a delivery.** Check the signature over the raw body, before parsing it, and
refuse a timestamp more than five minutes from your clock: that bounds how long a
captured request can be replayed. With nothing but the standard library::

    import hashlib, hmac, time

    def verify(secret: str, headers: dict[str, str], body: bytes) -> bool:
        timestamp = headers["X-Xolotl-Timestamp"]
        if abs(time.time() - int(timestamp)) > 300:
            return False
        signed = f"{timestamp}.".encode() + body
        digest = hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest()
        expected = f"v1={digest}".encode()
        return any(
            hmac.compare_digest(entry.strip().encode(), expected)
            for entry in headers["X-Xolotl-Signature"].split(",")
        )

``verify_signature`` below does the same and is what the platform's own tests use. The
header may one day carry several comma-separated ``v1=`` entries while a secret is
rotated, or entries of a later version; a receiver accepts the delivery when any
``v1=`` entry matches and ignores the rest.

**Answering.** Any ``2xx`` status means delivered. Anything else, a timeout, a
connection or TLS failure or a redirect (which is not followed) counts as a failed
attempt. The response body is never kept beyond a short reason: see
``WebhookService.deliver``.

**Retries.** A failed attempt is tried again after ``backoff(attempt)``: 2, 4, 8, 16 and
32 minutes, then every 60 minutes, until 24 hours have passed since the event reached
the endpoint, when the delivery is ``DEAD``. An administrator can send a dead delivery
again.

**How it runs.** ``register_webhooks`` subscribes the ``webhooks`` consumer to every
event type and registers the ``webhook.deliver`` job. The consumer only writes: one
``webhook_delivery`` row and one job per active endpoint that wants the event, in the
transaction that records the consumption, so an event is fanned out exactly once or not
at all. The job sends, in a transaction that holds the delivery's row lock for the
duration of the request, so two jobs never send one delivery at once. A job that finds
the delivery finished, or not yet due, does nothing; the delivery row, not the job,
says what is owed and when, which is what lets a retry be brought forward by hand.
"""

import hashlib
import hmac
import logging
import re
import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import cast
from uuid import UUID

import httpx
from pydantic import ValidationError
from sqlalchemy import or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from xolotl.platform import clock
from xolotl.platform.config import Settings, get_settings
from xolotl.platform.crypto.errors import (
    DecryptionError,
    KekMismatchError,
    KeyDestroyedError,
    KeyNotFoundError,
)
from xolotl.platform.db import bound_context
from xolotl.platform.enums import DeliveryStatus, EndpointStatus
from xolotl.platform.errors import DomainError, NotFoundError
from xolotl.platform.events.bus import OutboxEventBus
from xolotl.platform.events.event import Event, event_from_row
from xolotl.platform.events.models import OutboxRow, WebhookDelivery, WebhookEndpoint
from xolotl.platform.events.registry import WILDCARD, register_subscriber
from xolotl.platform.events.url_policy import (
    Resolver,
    UrlPolicy,
    WebhookUrlError,
    system_resolver,
)
from xolotl.platform.ids import uuid7
from xolotl.platform.jobs.queue import Job, PostgresJobQueue, register_job_handler
from xolotl.platform.logging import safe_exception_summary

logger = logging.getLogger(__name__)

CONSUMER = "webhooks"
DELIVER_KIND = "webhook.deliver"

KEY_PREFIX = "whsec_"
SECRET_BYTES = 16

EVENT_ID_HEADER = "X-Xolotl-Event-Id"
EVENT_TYPE_HEADER = "X-Xolotl-Event-Type"
TIMESTAMP_HEADER = "X-Xolotl-Timestamp"
SIGNATURE_HEADER = "X-Xolotl-Signature"
SIGNATURE_VERSION = "v1"

DEFAULT_TOLERANCE = timedelta(minutes=5)
MAX_BACKOFF = timedelta(minutes=60)
# A failed attempt this long after the delivery was created is the last one.
RETRY_WINDOW = timedelta(hours=24)
# The most characters of a receiver's answer that are kept, and the most bytes of it
# that are read to find them.
REASON_LENGTH = 200
_READ_LIMIT = 1024
_PLAIN_TYPES = frozenset({"text/plain", "application/json"})

# 2 ** 6 minutes already exceeds the cap; the bound keeps huge counts from overflowing.
_MAX_BACKOFF_EXPONENT = 6
# A Unix timestamp in seconds has ten digits; more would only be an attack on int().
_TIMESTAMP = re.compile(r"[0-9]{1,12}")

ClockFn = Callable[[], datetime]


def generate_webhook_secret() -> str:
    """Make the signing secret of a new endpoint.

    Returns:
        ``whsec_`` and 32 hex digits (16 random bytes).

    """
    return f"{KEY_PREFIX}{secrets.token_hex(SECRET_BYTES)}"


def sign(secret: str, timestamp: int, body: bytes) -> str:
    """Sign one delivery.

    The signature covers the timestamp, a dot and the body's bytes as they are sent.

    Returns:
        ``v1=`` and the lower-case hex HMAC-SHA256 of ``f"{timestamp}.{body}"``.

    """
    signed = f"{timestamp}.".encode() + body
    digest = hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest()
    return f"{SIGNATURE_VERSION}={digest}"


def _timestamp_of(value: int | str) -> int | None:
    if isinstance(value, int):
        return value
    return int(value) if _TIMESTAMP.fullmatch(value) else None


def _moment(now: datetime | Callable[[], datetime] | None) -> datetime:
    if now is None:
        return clock.now()
    return now if isinstance(now, datetime) else now()


def verify_signature(
    secret: str,
    header: str,
    timestamp: int | str,
    body: bytes,
    *,
    tolerance: timedelta = DEFAULT_TOLERANCE,
    now: datetime | Callable[[], datetime] | None = None,
) -> bool:
    """Check a delivery the way a receiver should.

    ``header`` is the ``X-Xolotl-Signature`` value, ``timestamp`` the
    ``X-Xolotl-Timestamp`` value (as the header's text or as an int) and ``body`` the
    raw bytes of the request. The delivery is refused when the timestamp is not plain
    digits or is further than ``tolerance`` from ``now`` (in either direction), and
    when no ``v1=`` entry of the header matches. Every entry is compared with
    ``hmac.compare_digest``, whether or not an earlier one matched, so the time taken
    does not say how much of a signature was right. This never raises for a header or
    a timestamp that is malformed.

    ``now`` is an aware datetime or a function that returns one; it defaults to the
    platform clock.

    Returns:
        ``True`` when the delivery is authentic and recent.

    """
    stamp = _timestamp_of(timestamp)
    if stamp is None:
        return False
    if abs(_moment(now).timestamp() - stamp) > tolerance.total_seconds():
        return False
    expected = sign(secret, stamp, body).encode()
    matched = False
    for entry in header.split(","):
        # Left operand first: every entry is compared, a match does not end the loop.
        matched = hmac.compare_digest(entry.strip().encode(), expected) or matched
    return matched


def backoff(attempt: int) -> timedelta:
    """Say how long to wait after the ``attempt``-th attempt failed.

    Returns:
        ``2 ** attempt`` minutes, at most an hour: 2, 4, 8, 16, 32, then 60.

    Raises:
        ValueError: When ``attempt`` is negative.

    """
    if attempt < 0:
        msg = "attempt must not be negative"
        raise ValueError(msg)
    doubled = 1 << min(attempt, _MAX_BACKOFF_EXPONENT)  # 2 ** attempt, typed int
    return min(timedelta(minutes=doubled), MAX_BACKOFF)


# ---- scheduling a delivery -------------------------------------------------------


def enqueue_delivery(
    queue: PostgresJobQueue, session: Session, delivery_id: UUID, *, run_after: datetime
) -> None:
    """Queue a ``webhook.deliver`` job in the caller's transaction.

    The job is a wake-up call: it carries only the delivery's id, and what it finds in
    the delivery row decides whether anything is sent, so a job that is queued twice
    does no harm.
    """
    _ = queue.enqueue(
        session,
        kind=DELIVER_KIND,
        payload={"delivery_id": str(delivery_id)},
        run_after=run_after,
    )


def retry_delivery(
    session: Session,
    queue: PostgresJobQueue,
    delivery_id: UUID,
    *,
    clock_fn: ClockFn = clock.now,
) -> WebhookDelivery:
    """Send a delivery again now: make it ``PENDING`` and due, and queue the job.

    A dead delivery gets one more attempt (its 24 hours are long over, so a failure
    ends it again); a pending one has its next attempt brought forward. Whoever
    retries it audits that, after this has run: the audit chain is the last write of a
    transaction.

    Returns:
        The delivery, locked and updated in the caller's transaction.

    Raises:
        NotFoundError: When the tenant has no such delivery.
        DomainError: When it was already delivered or its endpoint is disabled.

    """
    tenant_id = bound_context(session).require_tenant_id()
    delivery = session.get(
        WebhookDelivery, (tenant_id, delivery_id), with_for_update=True
    )
    if delivery is None:
        raise NotFoundError
    if delivery.status is DeliveryStatus.DELIVERED:
        msg = "webhook delivery was already delivered"
        raise DomainError(msg)
    endpoint_status = session.scalars(
        select(WebhookEndpoint.status).where(
            WebhookEndpoint.tenant_id == tenant_id,
            WebhookEndpoint.id == delivery.endpoint_id,
        )
    ).one()
    if endpoint_status is not EndpointStatus.ACTIVE:
        msg = "webhook endpoint is disabled"
        raise DomainError(msg)
    now = clock_fn()
    delivery.status = DeliveryStatus.PENDING
    delivery.next_attempt_at = now
    enqueue_delivery(queue, session, delivery.id, run_after=now)
    return delivery


# ---- the consumer and the job ----------------------------------------------------


class _UnsendableError(Exception):
    """A delivery that can never be sent; the message says why and names no secret."""


@dataclass(frozen=True, slots=True)
class _Request:
    url: str
    event_id: UUID
    event_type: str
    body: bytes
    secret: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class _Outcome:
    delivered: bool
    code: int | None = None
    reason: str | None = None


def _delivery_id(job: Job) -> UUID:
    raw = job.payload.get("delivery_id")
    try:
        return UUID(raw if isinstance(raw, str) else "")
    except ValueError:
        msg = "webhook.deliver job has no usable delivery_id"
        raise ValueError(msg) from None


def _plain(text: str) -> str:
    # Printable characters only (no NUL, which a text column refuses, and no escape
    # sequences), runs of whitespace as one space, and no more than REASON_LENGTH.
    printable = "".join(ch for ch in text if ch.isprintable() or ch.isspace())
    return " ".join(printable.split())[:REASON_LENGTH]


def _header(response: httpx.Response, name: str) -> str:
    # httpx types the lookup as returning Any.
    return cast("str", response.headers.get(name, ""))


def _reason(
    response: httpx.Response, timeout: float, monotonic: Callable[[], float]
) -> str:
    """Read the start of an answer that says why, if it is plain text or JSON.

    Anything else (HTML, binary, compressed, a type nobody declared) says nothing.
    Only the first ``_READ_LIMIT`` bytes are read, and not for longer than ``timeout``
    in all: a receiver that dribbles an endless body is not waited for.

    Returns:
        Up to ``REASON_LENGTH`` printable characters, or an empty string.

    """
    media = _header(response, "content-type").partition(";")[0].strip().lower()
    encoding = _header(response, "content-encoding").strip().lower()
    if media not in _PLAIN_TYPES and not media.endswith("+json"):
        return ""
    if encoding not in {"", "identity"}:
        return ""
    deadline = monotonic() + timeout
    raw = bytearray()
    try:
        for chunk in response.iter_raw():
            raw.extend(chunk)
            if len(raw) >= _READ_LIMIT or monotonic() > deadline:
                break
    except httpx.HTTPError:
        return ""  # the status is known; a body that fails to arrive adds nothing
    return _plain(bytes(raw[:_READ_LIMIT]).decode("utf-8", errors="replace"))


def _prepare(session: Session, delivery: WebhookDelivery) -> _Request:
    try:
        endpoint = session.get(
            WebhookEndpoint, (delivery.tenant_id, delivery.endpoint_id)
        )
    except DecryptionError:
        # A secret too short to carry its header already fails when the row is loaded.
        msg = "signing secret unavailable (DecryptionError)"
        raise _UnsendableError(msg) from None
    if endpoint is None or endpoint.status is not EndpointStatus.ACTIVE:
        msg = "endpoint disabled"
        raise _UnsendableError(msg)
    row = session.get(OutboxRow, (delivery.tenant_id, delivery.event_id))
    if row is None:
        msg = "event no longer available"
        raise _UnsendableError(msg)
    try:
        event = event_from_row(row)
    except ValidationError:
        msg = "event no longer valid"
        raise _UnsendableError(msg) from None
    try:
        secret = endpoint.secret.reveal(session=session)
    except KekMismatchError:
        # A kind of DecryptionError, but not the secret's fault: this process holds
        # another master key than the one that wrapped the tenant's keys, and one that
        # holds the right key opens the secret. Out it goes, so that the job retries
        # (see ``WebhookService.deliver``); only the secret itself ends a delivery.
        raise
    except (KeyNotFoundError, KeyDestroyedError, DecryptionError) as error:
        msg = f"signing secret unavailable ({type(error).__name__})"
        raise _UnsendableError(msg) from None
    return _Request(
        url=endpoint.url,
        event_id=event.id,
        event_type=event.type,
        body=event.model_dump_json().encode(),
        secret=secret,
    )


def _bury(delivery: WebhookDelivery, reason: str) -> None:
    delivery.status = DeliveryStatus.DEAD
    delivery.next_attempt_at = None
    delivery.last_error = reason
    logger.warning(
        "webhook delivery abandoned",
        extra={
            "delivery_id": str(delivery.id),
            "endpoint_id": str(delivery.endpoint_id),
            "event_id": str(delivery.event_id),
        },
    )


def give_up(
    session: Session, tenant_id: UUID, delivery_id: UUID, error: Exception
) -> None:
    """End a delivery that is still waiting because trying it keeps failing.

    The delivery becomes ``DEAD`` with ``safe_exception_summary`` of ``error``. One
    that was finished in the meantime, delivered by another job, is left as it is.
    """
    delivery = session.get(
        WebhookDelivery, (tenant_id, delivery_id), with_for_update=True
    )
    if delivery is not None and delivery.status is DeliveryStatus.PENDING:
        _bury(delivery, safe_exception_summary(error))


class WebhookService:
    """The ``webhooks`` consumer and the ``webhook.deliver`` job.

    ``policy`` decides where a delivery may go, ``timeout_seconds`` is the connect,
    write and read timeout of one request, and ``clock_fn`` is the time every
    delivery is stamped and scheduled by (the 24 hours are counted on it).
    ``monotonic`` times the reading of an answer: no more than ``timeout_seconds`` in
    all, however slowly it arrives.
    """

    def __init__(
        self,
        queue: PostgresJobQueue,
        *,
        policy: UrlPolicy,
        timeout_seconds: int,
        clock_fn: ClockFn = clock.now,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._queue: PostgresJobQueue = queue
        self._policy: UrlPolicy = policy
        self._timeout: int = timeout_seconds
        self._clock: ClockFn = clock_fn
        self._monotonic: Callable[[], float] = monotonic

    # ---- fan-out ----------------------------------------------------------------

    def fanout(self, session: Session, event: Event) -> None:
        """Owe the event to every active endpoint that wants it.

        An endpoint wants an event whose type it lists, or every event when it lists
        ``*``. For each one this inserts a ``PENDING`` delivery, due now, and queues its
        job, in the dispatcher's transaction: nothing is sent here. A delivery that
        already exists (a replay) is left alone and gets no second job.
        """
        now = self._clock()
        endpoint_ids = session.scalars(
            select(WebhookEndpoint.id)
            .where(
                WebhookEndpoint.tenant_id == event.tenant_id,
                WebhookEndpoint.status == EndpointStatus.ACTIVE,
                or_(
                    WebhookEndpoint.event_types.contains([event.type]),
                    WebhookEndpoint.event_types.contains([WILDCARD]),
                ),
            )
            .order_by(WebhookEndpoint.created_at, WebhookEndpoint.id)
        ).all()
        for endpoint_id in endpoint_ids:
            delivery_id = session.execute(
                insert(WebhookDelivery)
                .values(
                    tenant_id=event.tenant_id,
                    id=uuid7(),
                    endpoint_id=endpoint_id,
                    event_id=event.id,
                    attempt=0,
                    status=DeliveryStatus.PENDING,
                    next_attempt_at=now,
                    created_at=now,
                )
                .on_conflict_do_nothing(
                    index_elements=["tenant_id", "endpoint_id", "event_id"]
                )
                .returning(WebhookDelivery.id)
            ).scalar_one_or_none()
            if delivery_id is not None:
                enqueue_delivery(self._queue, session, delivery_id, run_after=now)

    # ---- delivery ---------------------------------------------------------------

    def deliver(self, session: Session, job: Job) -> None:
        """Send one delivery, once it is due, and record how it went.

        The delivery row is locked for the whole attempt. A job that finds it finished
        or not yet due returns without sending. A 2xx answer makes it ``DELIVERED``.
        Anything else, a refused destination included, counts as an attempt: the next
        is queued ``backoff(attempt)`` later, or, when 24 hours have passed since the
        delivery was created, the delivery is ``DEAD``. A failed attempt keeps its
        status code and, when the answer was plain text or JSON, up to 200 characters
        of it as the reason; an exception is kept as ``safe_exception_summary`` of it.
        Nothing else of the answer is stored.

        A delivery that can never be sent (the endpoint is disabled, the event is gone
        or invalid, the secret does not open, the tenant's keys are destroyed) is
        ``DEAD`` at once. An error that is not about the receiver (the database, a bug)
        fails the job, and the queue retries it; on the job's last attempt the delivery
        is given up as ``DEAD`` instead, so it never stays ``PENDING`` with no job
        behind it. A job whose payload names no delivery fails with ``ValueError``.

        A master key that does not fit this process is such an error, since another
        process may hold the right one, but it never ends a delivery: on the job's last
        attempt the delivery stays ``PENDING`` and a new job, with attempts of its own,
        is queued ``MAX_BACKOFF`` later.

        Raises:
            KekMismatchError: When this process holds another master key than the one
                that wrapped the tenant's keys, on every attempt but the job's last.

        """
        delivery_id = _delivery_id(job)
        ids = {"delivery_id": str(delivery_id), "job_id": str(job.id)}
        try:
            self._attempt(session, job.tenant_id, delivery_id)
        except KekMismatchError:
            if job.attempts < job.max_attempts:
                raise
            logger.exception("webhook delivery parked: master key mismatch", extra=ids)
            session.rollback()
            self._park(session, job.tenant_id, delivery_id)
        except Exception as error:
            if job.attempts < job.max_attempts:
                raise
            logger.exception("webhook delivery given up", extra=ids)
            session.rollback()
            give_up(session, job.tenant_id, delivery_id, error)

    def _park(self, session: Session, tenant_id: UUID, delivery_id: UUID) -> None:
        # The job is out of attempts, the delivery is not at fault: keep it owed and
        # wake it later with a job of its own, as `_record` does for a failed request.
        delivery = session.get(
            WebhookDelivery, (tenant_id, delivery_id), with_for_update=True
        )
        if delivery is not None and delivery.status is DeliveryStatus.PENDING:
            enqueue_delivery(
                self._queue, session, delivery.id, run_after=self._clock() + MAX_BACKOFF
            )

    def _attempt(self, session: Session, tenant_id: UUID, delivery_id: UUID) -> None:
        delivery = session.get(
            WebhookDelivery, (tenant_id, delivery_id), with_for_update=True
        )
        now = self._clock()
        if delivery is None or delivery.status is not DeliveryStatus.PENDING:
            return
        if delivery.next_attempt_at is not None and delivery.next_attempt_at > now:
            return  # a stale job: the one for next_attempt_at is on its way
        try:
            request = _prepare(session, delivery)
        except _UnsendableError as why:
            _bury(delivery, str(why))
            return
        outcome = self._send(request, now)
        self._record(session, delivery, outcome)

    def _send(self, request: _Request, now: datetime) -> _Outcome:
        try:
            destination = self._policy.destination(request.url)
        except WebhookUrlError as refusal:
            return _Outcome(delivered=False, reason=refusal.detail)
        stamp = int(now.timestamp())
        headers = {
            "Content-Type": "application/json",
            EVENT_ID_HEADER: str(request.event_id),
            EVENT_TYPE_HEADER: request.event_type,
            TIMESTAMP_HEADER: str(stamp),
            SIGNATURE_HEADER: sign(request.secret, stamp, request.body),
        }
        extensions: dict[str, str] = {}
        if destination.host is not None:
            headers["Host"] = destination.host
        if destination.sni_hostname is not None:
            extensions["sni_hostname"] = destination.sni_hostname
        try:
            # A client of its own for each request: a pooled connection to an address
            # was verified for one name and must not be reused for another.
            with (
                httpx.Client(
                    timeout=httpx.Timeout(self._timeout),
                    follow_redirects=False,
                    trust_env=False,
                ) as client,
                client.stream(
                    "POST",
                    destination.url,
                    content=request.body,
                    headers=headers,
                    extensions=extensions,
                ) as response,
            ):
                return self._outcome(response)
        except (httpx.HTTPError, OSError) as error:
            return _Outcome(delivered=False, reason=safe_exception_summary(error))

    def _outcome(self, response: httpx.Response) -> _Outcome:
        code = response.status_code
        if httpx.codes.is_success(code):
            return _Outcome(delivered=True, code=code)
        reason = _reason(response, self._timeout, self._monotonic)
        return _Outcome(
            delivered=False,
            code=code,
            reason=f"HTTP {code}: {reason}" if reason else f"HTTP {code}",
        )

    def _record(
        self, session: Session, delivery: WebhookDelivery, outcome: _Outcome
    ) -> None:
        now = self._clock()
        delivery.attempt += 1
        delivery.response_code = outcome.code
        ids = {
            "delivery_id": str(delivery.id),
            "endpoint_id": str(delivery.endpoint_id),
            "event_id": str(delivery.event_id),
            "attempt": delivery.attempt,
            "status_code": outcome.code,
        }
        if outcome.delivered:
            delivery.status = DeliveryStatus.DELIVERED
            delivery.delivered_at = now
            delivery.next_attempt_at = None
            delivery.last_error = None
            logger.info("webhook delivered", extra=ids)
            return
        delivery.last_error = outcome.reason
        if now - delivery.created_at >= RETRY_WINDOW:
            delivery.status = DeliveryStatus.DEAD
            delivery.next_attempt_at = None
            logger.warning("webhook delivery dead", extra=ids)
            return
        retry_at = now + backoff(delivery.attempt)
        delivery.next_attempt_at = retry_at
        enqueue_delivery(self._queue, session, delivery.id, run_after=retry_at)
        logger.info("webhook delivery failed, will retry", extra=ids)


def register_webhooks(
    bus: OutboxEventBus,
    queue: PostgresJobQueue,
    *,
    settings: Settings | None = None,
    resolver: Resolver = system_resolver,
    clock_fn: ClockFn = clock.now,
) -> None:
    """Subscribe the ``webhooks`` consumer and register the ``webhook.deliver`` job.

    Call it at composition time in every process that dispatches events (the
    consumer) or runs jobs (the delivery); calling it again replaces both, so
    composing the application twice leaves one of each. ``bus`` is where events are
    dispatched; subscribers live in the process-wide registry (``events.registry``),
    so it is not consulted. ``settings`` decide the URL policy (strict in production)
    and the request timeout, and default to the configured ones.
    """
    del bus  # the subscriber registry is process-wide, not owned by a bus
    configured = settings or get_settings()
    service = WebhookService(
        queue,
        policy=UrlPolicy.for_settings(configured, resolver=resolver),
        timeout_seconds=configured.webhook_timeout_seconds,
        clock_fn=clock_fn,
    )
    register_subscriber(WILDCARD, service.fanout, consumer=CONSUMER)
    register_job_handler(DELIVER_KIND, service.deliver)
