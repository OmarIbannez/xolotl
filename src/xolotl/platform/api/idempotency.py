r"""``Idempotency-Key`` for every POST under ``/v1`` except ``/v1/auth/*`` (spec §7).

Flow per request: validate the header, resolve the actor, hash
``method\npath\nbody``, then *reserve* ``(tenant, actor, key)`` with an
``INSERT ... ON CONFLICT DO NOTHING``. A hit with the same hash replays the
stored response; a different hash is a 422; a reservation whose request is
still running is a 409, unless it is older than ``RESERVATION_TTL`` (the
request died without releasing it), in which case it is replaced. Each
reservation first deletes a batch of the tenant's expired keys (``purge_expired``),
and the hourly ``idempotency.purge`` job (``purge_expired_keys``) deletes them all,
batch after batch, for every tenant, including the ones that make no requests.

What is stored: whenever the endpoint returns normally with a 2xx status, its
response, whatever the shape, because the endpoint's work commits on that same
normal exit. The record keeps the status and media type plus the parsed JSON
body, or the raw bytes of a body that is not JSON, or neither for an empty body
(a 204, an empty 202). A replay rebuilds exactly that, so a retry does not run
the endpoint again. Other headers are not replayed, and a streamed body cannot
be captured: it is stored, and replayed, as an empty one.

Where it is stored: in the endpoint's own session (``request.state.session``)
when it has one, whatever state that session is in, so the record commits or
rolls back atomically with the endpoint's work. A session that cannot store the
record cannot commit the work either, so the write raises, the client gets a
500 and the key stays reserved: retries get a 409 until ``RESERVATION_TTL`` has
passed and the reservation is reclaimed. Without an endpoint session the record
goes into a session of its own.

The reservation is released, so the client can retry with the same key, only
when the endpoint raises or returns a non-2xx response.

How it is protected: a response can carry a one-time secret (``POST /v1/api-keys``
returns the whole key, ``POST /v1/staff`` an initial password) and is kept for 24
hours, so the record is ``EncryptedJson``: sealed under the tenant's DATA key, read
and written through the request's own tenant-bound session (no second connection, and
a destruction of the keys is seen at once). A reservation holds a sealed ``{}``.
When the tenant's keys were destroyed, a replay and a new request alike fail with the
crypto layer's 410 ``KEY_DESTROYED``, a new one before its endpoint runs; a stored
body that does not open (another tenant's, altered) is a 500 ``DECRYPTION_FAILED``
and is never replayed. The secret is never logged: the stored value is an
``Encrypted``, which the logging filter masks. The purge in front of each reservation
commits on its own, so such a refusal cannot roll it back: a tenant whose keys are
destroyed still has its expired keys purged, and no stored body is read to do it.
"""

import base64
import hashlib
import json
import logging
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from datetime import datetime, timedelta
from http import HTTPStatus
from typing import cast, override
from uuid import UUID

from fastapi import Request, Response
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from sqlalchemy import delete, func, select, tuple_
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from xolotl.platform import clock
from xolotl.platform.api.app import app_state
from xolotl.platform.api.models import IdempotencyKey
from xolotl.platform.auth.actor import Actor
from xolotl.platform.auth.principal import resolve_request_actor
from xolotl.platform.crypto import encrypt_json
from xolotl.platform.db import SessionFactory, bound_context, tenant_session
from xolotl.platform.errors import (
    IdempotencyConflictError,
    IdempotencyInProgressError,
    IdempotencyKeyRequiredError,
)
from xolotl.platform.jobs.queue import Job

logger = logging.getLogger(__name__)

PURGE_KIND = "idempotency.purge"
# The most expired keys one purge deletes, and so the most row locks it takes.
PURGE_BATCH = 1000
RESERVED_STATUS = 0
TTL = timedelta(hours=24)
RESERVATION_TTL = timedelta(seconds=60)
MAX_KEY_LENGTH = 128
REPLAY_HEADER = "Idempotent-Replay"


@dataclass(frozen=True, slots=True)
class Stored:
    status_code: int
    media_type: str | None  # the Content-Type header, when the response had one
    body: object | None  # the parsed JSON body; None when the body is empty or not JSON
    raw: bytes | None  # any other non-empty body, byte for byte


def request_hash(method: str, path: str, body: bytes) -> bytes:
    digest = hashlib.sha256()
    digest.update(method.upper().encode())
    digest.update(b"\n")
    digest.update(path.encode())
    digest.update(b"\n")
    digest.update(body)
    return digest.digest()


def is_idempotent_request(request: Request) -> bool:
    path = request.url.path
    return (
        request.method == "POST"
        and path.startswith("/v1/")
        and not path.startswith("/v1/auth/")
    )


def _pk(actor: Actor, key: str) -> tuple[UUID, str, str]:
    return (actor.context.require_tenant_id(), actor.context.actor_id, key)


def _is_json(media_type: str | None) -> bool:
    if media_type is None:
        return False
    essence = media_type.partition(";")[0].strip().lower()
    return essence == "application/json" or essence.endswith("+json")


def _parse_json(content: bytes) -> object | None:
    try:
        parsed = cast("object", json.loads(content))
    except ValueError:
        return None
    return parsed


def _record(response: Response) -> Stored:
    # A streamed response has no body attribute; every other one holds the bytes.
    data = bytes(cast("bytes | memoryview[int]", getattr(response, "body", b"")))
    media_type = response.headers.get("content-type")
    # Only a JSON media type is parsed, so text that merely looks like JSON is kept
    # verbatim. A literal ``null`` parses to None, which already means "no JSON
    # body", so it falls through to the raw bytes as well.
    body = _parse_json(data) if data and _is_json(media_type) else None
    return Stored(
        status_code=response.status_code,
        media_type=media_type,
        body=body,
        raw=data if data and body is None else None,
    )


def _encode(stored: Stored) -> dict[str, object]:
    raw = stored.raw
    # "status" repeats the status_code column, which also marks a reservation, so
    # the record describes the whole response on its own.
    return {
        "status": stored.status_code,
        "media_type": stored.media_type,
        "body": stored.body,
        "raw": None if raw is None else base64.b64encode(raw).decode("ascii"),
    }


def _decode(s: Session, row: IdempotencyKey) -> Stored:
    # Opened in the request's own tenant-bound session: the key is read in that
    # transaction, so a destruction committed anywhere is seen at once and the replay
    # fails closed (410 KEY_DESTROYED), and no second connection is taken.
    record = row.response_body.reveal(session=s)
    media_type = record.get("media_type")
    raw = record.get("raw")
    return Stored(
        status_code=row.status_code,
        media_type=media_type if isinstance(media_type, str) else None,
        body=record.get("body"),
        raw=base64.b64decode(raw) if isinstance(raw, str) else None,
    )


def _replay(stored: Stored) -> Response:
    headers = {REPLAY_HEADER: "true"}
    if stored.body is not None:
        return JSONResponse(
            stored.body,
            status_code=stored.status_code,
            headers=headers,
            media_type=stored.media_type,
        )
    if stored.raw is not None:
        return Response(
            stored.raw,
            status_code=stored.status_code,
            headers=headers,
            media_type=stored.media_type,
        )
    return Response(status_code=stored.status_code, headers=headers)


def _is_dead(row: IdempotencyKey, now: datetime) -> bool:
    """Report whether a row is expired or an abandoned reservation.

    A reservation is abandoned once it is older than ``RESERVATION_TTL``: its
    request died without storing a response or releasing the key.

    Returns:
        ``True`` when the row no longer guards its key and may be replaced.

    """
    abandoned = (
        row.status_code == RESERVED_STATUS and now - row.created_at > RESERVATION_TTL
    )
    return row.expires_at <= now or abandoned


def purge_expired(session: Session) -> int:
    """Delete one batch of the session tenant's expired keys, whoever holds them.

    An expired key no longer guards anything (``_is_dead``), so deleting it changes
    no answer. At most ``PURGE_BATCH`` keys go per call, the longest expired first,
    and a key another transaction has locked is passed by rather than waited for
    (``FOR UPDATE SKIP LOCKED``): a request's purge and the purge job never queue
    behind each other, and neither holds more than one batch of locks. The query
    names the tenant as well as relying on row-level security, so the
    ``(tenant_id, expires_at)`` index keeps it a range scan.

    Returns:
        How many keys were deleted; fewer than ``PURGE_BATCH`` means the tenant has
        no expired key left that was free to take.

    """
    tenant_id = bound_context(session).require_tenant_id()
    # A CTE, so Postgres takes the batch once instead of re-running the limited,
    # locking subquery per probe, which deleted more than one batch on CI.
    batch = (
        select(IdempotencyKey.tenant_id, IdempotencyKey.actor_id, IdempotencyKey.key)
        .where(
            IdempotencyKey.tenant_id == tenant_id,
            IdempotencyKey.expires_at <= clock.now(),
        )
        .order_by(IdempotencyKey.expires_at)
        .limit(PURGE_BATCH)
        .with_for_update(skip_locked=True)
        .cte("batch")
    )
    # Counted by Postgres (WITH deleted AS (DELETE ... RETURNING) SELECT count(*)), so
    # the deleted keys never travel back to the application.
    deleted = (
        delete(IdempotencyKey)
        .where(
            tuple_(
                IdempotencyKey.tenant_id, IdempotencyKey.actor_id, IdempotencyKey.key
            ).in_(select(batch.c.tenant_id, batch.c.actor_id, batch.c.key))
        )
        .returning(IdempotencyKey.key)
        .cte("deleted")
    )
    return session.scalar(select(func.count()).select_from(deleted)) or 0


def purge_expired_keys(session: Session, job: Job) -> None:
    """Run an ``idempotency.purge`` job: purge all of the job's tenant's expired keys.

    Every tenant gets the job hourly from a schedule made when the tenant is
    created; the job's session is already bound to that tenant. The keys go batch
    by batch, each batch committed before the next is taken, so the purge never
    holds the locks of more than one batch at a time. The session carries on in a
    new transaction after each commit, under the same tenant context.
    """
    purged = taken = purge_expired(session)
    while taken == PURGE_BATCH:
        session.commit()
        taken = purge_expired(session)
        purged += taken
    if purged:
        logger.info(
            "expired idempotency keys purged",
            extra={"job_id": str(job.id), "purged": purged},
        )


def _reserve(
    factory: SessionFactory, actor: Actor, key: str, digest: bytes
) -> Stored | None:
    """Return the stored response for a replay, or ``None`` after reserving the key.

    The reservation is sealed and a stored response opened with the tenant's keys, read
    in this session. A tenant whose keys were destroyed gets the crypto layer's 410
    (``KeyDestroyedError``) for a replay and for a new request alike, and a stored body
    that does not authenticate fails as ``DecryptionError`` and is never replayed.

    Returns:
        The stored response when the same request already completed, otherwise
        ``None`` once the key is reserved for this request.

    Raises:
        IdempotencyConflictError: When the key was used with a different request.
        IdempotencyInProgressError: When a request holding the key is still running.

    """
    now = clock.now()
    with tenant_session(actor.context, factory=factory) as s:
        # Opportunistic purge: the tenant's expired keys go before a new one is
        # reserved, between the hourly runs of the purge job.
        _ = purge_expired(s)
        # Committed on its own, so that nothing the reservation does next can undo it. A
        # tenant whose keys are gone is refused on every request (the placeholder below
        # cannot be sealed, a replay cannot be opened) and a refusal rolls the
        # transaction back: without this its expired keys would wait for the purge job.
        # The delete reads no column back, so no sealed body is opened or even read.
        s.commit()
        existing = s.get(IdempotencyKey, _pk(actor, key))
        if existing is not None and _is_dead(existing, now):
            s.delete(existing)
            s.flush()
            existing = None
        if existing is None:
            tenant_id, actor_id, _ = _pk(actor, key)
            # Sealed like the response that replaces it, so the column never holds
            # anything else. A tenant whose keys are gone is refused here, before its
            # request runs, with the same 410 a replay gets.
            placeholder = encrypt_json({}, tenant_id=tenant_id, session=s)
            inserted = s.execute(
                insert(IdempotencyKey)
                .values(
                    tenant_id=tenant_id,
                    actor_id=actor_id,
                    key=key,
                    request_hash=digest,
                    status_code=RESERVED_STATUS,
                    response_body=placeholder,
                    expires_at=now + TTL,
                )
                .on_conflict_do_nothing(index_elements=["tenant_id", "actor_id", "key"])
                .returning(IdempotencyKey.key)
            ).scalar_one_or_none()
            if inserted is not None:  # RETURNING yields no row when the key exists
                return None
            existing = s.get(IdempotencyKey, _pk(actor, key))
            if existing is None:  # lost a race and the winner already released it
                raise IdempotencyInProgressError
        if existing.request_hash != digest:
            raise IdempotencyConflictError
        if existing.status_code == RESERVED_STATUS:
            raise IdempotencyInProgressError
        return _decode(s, existing)


def _store(s: Session, actor: Actor, key: str, stored: Stored) -> None:
    row = s.get(IdempotencyKey, _pk(actor, key))
    if row is not None:
        row.status_code = stored.status_code
        row.response_body = encrypt_json(
            _encode(stored), tenant_id=row.tenant_id, session=s
        )
        s.flush()


def _finalize(
    request: Request,
    factory: SessionFactory,
    actor: Actor,
    key: str,
    *,
    response: Response,
) -> None:
    stored = _record(response)
    endpoint_session: object = getattr(request.state, "session", None)
    if isinstance(endpoint_session, Session):
        # FastAPI closes function-scoped dependencies after the route handler
        # returns, so this write commits (or rolls back) with the endpoint's work.
        # No is_active check: a session stuck after a failed flush cannot commit
        # the work, so the write must raise and leave the key reserved instead of
        # storing a response on the side.
        _store(endpoint_session, actor, key, stored)
        return
    with tenant_session(actor.context, factory=factory) as s:
        _store(s, actor, key, stored)


def _release(factory: SessionFactory, actor: Actor, key: str) -> None:
    with tenant_session(actor.context, factory=factory) as s:
        row = s.get(IdempotencyKey, _pk(actor, key))
        if row is not None and row.status_code == RESERVED_STATUS:
            s.delete(row)


class IdempotentRoute(APIRoute):
    @override
    def get_route_handler(
        self,
    ) -> Callable[[Request], Coroutine[object, object, Response]]:
        original = super().get_route_handler()

        async def handler(request: Request) -> Response:
            if not is_idempotent_request(request):
                return await original(request)
            key = request.headers.get("idempotency-key")
            if key is None or not 1 <= len(key) <= MAX_KEY_LENGTH:
                raise IdempotencyKeyRequiredError
            actor = await run_in_threadpool(resolve_request_actor, request)
            body = await request.body()
            digest = request_hash(request.method, request.url.path, body)
            factory = app_state(request).session_factory
            stored = await run_in_threadpool(_reserve, factory, actor, key, digest)
            if stored is not None:
                return _replay(stored)
            try:
                response = await original(request)
            except BaseException:
                await run_in_threadpool(_release, factory, actor, key)
                raise
            succeeded = (
                HTTPStatus.OK <= response.status_code < HTTPStatus.MULTIPLE_CHOICES
            )
            if succeeded:
                await run_in_threadpool(
                    _finalize, request, factory, actor, key, response=response
                )
            else:
                await run_in_threadpool(_release, factory, actor, key)
            return response

        return handler
