"""What the idempotency store keeps for replay is sealed under the tenant's data key.

(Plan 1b task B8.) ``POST /v1/api-keys`` and ``POST /v1/staff`` return a secret once,
and the store keeps every 2xx response for 24 hours so a lost response can be
replayed: the full API key and a new member's initial password would sit in
``idempotency_key.response_body`` in the clear for that long. The column holds the
ciphertext of the stored record now, and these tests prove it from the outside, through
the real app and the real database: what the database holds, what a replay returns,
and what happens to a replay when the key is out of reach.

The behaviour of the replay itself (the same key, the same body, the 422 for another
request, the 24 hours, the stale reservation, 204s and text) is pinned in
``test_api_conventions.py`` and runs on the sealed store as well.
"""

import io
import json
import logging
from collections.abc import Generator
from datetime import datetime, timedelta
from uuid import UUID

import pytest
from fastapi.responses import PlainTextResponse
from fastapi.testclient import TestClient
from sqlalchemy import Engine, column, select, table, text

from tests.helpers import (
    data_key_of,
    first_login,
    leaked,
    new_tenant,
    system_ctx,
)
from xolotl.app import build_app
from xolotl.modules.tenants import service
from xolotl.modules.tenants.service import CreatedTenant
from xolotl.platform import clock
from xolotl.platform.api.idempotency import TTL
from xolotl.platform.api.models import IdempotencyKey
from xolotl.platform.api.router import v1_router
from xolotl.platform.audit import canonical_json
from xolotl.platform.crypto import (
    Encrypted,
    KeyNotFoundError,
    get_key_provider,
)
from xolotl.platform.crypto.primitives import (
    HEADER_LEN,
    NONCE_LEN,
    TAG_LEN,
    aad_for,
    decrypt_value,
    parse_header,
)
from xolotl.platform.db import bound_context, tenant_session
from xolotl.platform.enums import KeyPurpose
from xolotl.platform.logging import JsonFormatter, RedactionFilter
from xolotl.platform.tenancy.context import TenantContext

DATA = KeyPurpose.DATA
NEW_PASSWORD = "a-long-enough-password"
SECRET_TEXT = "one-time-secret-9f2c41d7b0a8"  # what a text response hands out once


def create_api_key(client: TestClient, headers, key: str = "k-1", **body):
    payload = {"name": "ingest", "scopes": ["tenants:read"], **body}
    return client.post(
        "/v1/api-keys", json=payload, headers={**headers, "Idempotency-Key": key}
    )


def create_staff(client: TestClient, headers, key: str = "k-1", **body):
    payload = {
        "email": "ana@acme.mx",
        "display_name": "Ana",
        "roles": ["ANALYST"],
        **body,
    }
    return client.post(
        "/v1/staff", json=payload, headers={**headers, "Idempotency-Key": key}
    )


# The one-time secret each creating endpoint puts in its response.
CREATORS = {
    "api-key": (create_api_key, "key"),
    "staff": (create_staff, "initial_password"),
}


# The response_body column of every row, by idempotency key, exactly as the database
# holds it: read as the owner past row-level security, with no column type to turn it
# into anything else. A ciphertext is ``bytes``.
def stored_bodies(admin_engine: Engine) -> dict[str, object]:
    statement = select(column("key"), column("response_body")).select_from(
        table("idempotency_key")
    )
    with admin_engine.connect() as conn:
        return {row[0]: row[1] for row in conn.execute(statement)}


# Every ``width``-character window of ``secret``, from each position.
def fragments(secret: str, width: int = 8) -> list[bytes]:
    return [secret[at : at + width].encode() for at in range(len(secret) - width + 1)]


# The spellings of ``secret`` and of each 8-character fragment of it found in what the
# database holds in the column; an empty list means none was stored.
def found_in_store(admin_engine: Engine, secret: str) -> list[str]:
    dump = repr(list(stored_bodies(admin_engine).values()))
    return leaked(dump, secret.encode(), *fragments(secret))


def destroy_keys(tenant_id: UUID) -> None:
    with tenant_session(TenantContext.platform("cli:test", tenant_id=tenant_id)) as s:
        service.destroy_tenant_keys(s)


# A sealed stored response of ``actor`` that expires at ``expires_at``.
def add_stored(
    tenant_id: UUID, key: str, *, expires_at: datetime, actor: str = "an-actor"
) -> None:
    with tenant_session(system_ctx(tenant_id)) as s:
        s.add(
            IdempotencyKey(
                tenant_id=tenant_id,
                actor_id=actor,
                key=key,
                request_hash=b"",
                status_code=200,
                response_body={},
                expires_at=expires_at,
            )
        )


def rotate_data_key(tenant_id: UUID) -> int:
    with tenant_session(TenantContext.platform("cli:test", tenant_id=tenant_id)) as s:
        return service.rotate_tenant_key(s, DATA)


# ---- what is kept ---------------------------------------------------------------


@pytest.mark.parametrize("creator", CREATORS)
def test_a_replayed_creation_returns_the_same_secret_and_runs_once(
    client: TestClient, admin_headers, creator: str
):
    create, field = CREATORS[creator]
    first = create(client, admin_headers)
    again = create(client, admin_headers)
    assert (first.status_code, again.status_code) == (201, 201)
    assert again.json() == first.json()
    assert again.json()[field] == first.json()[field]
    assert "idempotent-replay" not in first.headers
    assert again.headers["idempotent-replay"] == "true"
    listing = {"api-key": "/v1/api-keys", "staff": "/v1/staff"}[creator]
    created = [
        item["id"]
        for item in client.get(listing, headers=admin_headers).json()["items"]
    ]
    assert created.count(first.json()["id"]) == 1  # the endpoint did not run again


@pytest.mark.parametrize("creator", CREATORS)
def test_the_database_holds_no_fragment_of_a_secret_it_stored_for_replay(
    client: TestClient, admin_headers, admin_engine: Engine, creator: str
):
    create, field = CREATORS[creator]
    created = create(client, admin_headers)
    assert created.status_code == 201
    secret = created.json()[field]
    assert len(secret) >= 20
    assert set(stored_bodies(admin_engine)) == {"k-1"}  # the row is really there
    assert found_in_store(admin_engine, secret) == []
    # The whole record is sealed, not just the secret in it.
    assert found_in_store(admin_engine, created.json()["id"]) == []


def test_a_text_response_is_sealed_whole_too(admin_headers, admin_engine: Engine):
    router = v1_router()

    @router.post("/probe/secret-text")
    def say() -> PlainTextResponse:
        return PlainTextResponse(SECRET_TEXT)

    app = build_app()
    app.include_router(router, prefix="/v1")
    headers = {**admin_headers, "Idempotency-Key": "k-text"}
    with TestClient(app) as probe:
        first = probe.post("/v1/probe/secret-text", headers=headers)
        again = probe.post("/v1/probe/secret-text", headers=headers)
    assert (first.text, again.text) == (SECRET_TEXT, SECRET_TEXT)
    assert again.headers["idempotent-replay"] == "true"
    # Not in the clear, and not as the base64 the record keeps non-JSON bodies in.
    assert found_in_store(admin_engine, SECRET_TEXT) == []


def test_the_stored_response_is_the_record_sealed_under_the_tenants_data_key(
    client: TestClient, tenant: CreatedTenant, admin_headers, admin_engine: Engine
):
    created = create_api_key(client, admin_headers)
    raw = stored_bodies(admin_engine)["k-1"]
    assert isinstance(raw, bytes)
    assert raw[:HEADER_LEN] == b"\x00\x01"  # key version 1
    # Opened with the primitives, the tenant's own key and the specified authenticated
    # data, not through the application.
    opened = decrypt_value(
        data_key_of(admin_engine, tenant.tenant_id),
        ciphertext=raw,
        aad=aad_for(tenant.tenant_id, DATA),
    )
    record = json.loads(opened)
    assert record == {
        "status": 201,
        "media_type": "application/json",
        "body": created.json(),
        "raw": None,
    }
    assert opened == canonical_json(record)  # canonical JSON in UTF-8
    assert len(raw) == HEADER_LEN + NONCE_LEN + len(opened) + TAG_LEN


def test_a_reservation_holds_a_sealed_placeholder_never_plain_json(
    admin_headers, admin_engine: Engine, tenant: CreatedTenant
):
    seen: list[object] = []
    router = v1_router()

    @router.post("/probe/reserved")
    def reserved() -> dict[str, str]:
        # The reservation was committed by its own session before the endpoint ran.
        statement = (
            select(column("response_body"))
            .select_from(table("idempotency_key"))
            .where(column("status_code") == 0)
        )
        with admin_engine.connect() as conn:
            seen.extend(conn.scalars(statement))
        return {"ok": "yes"}

    app = build_app()
    app.include_router(router, prefix="/v1")
    with TestClient(app) as probe:
        done = probe.post(
            "/v1/probe/reserved", headers={**admin_headers, "Idempotency-Key": "k-r"}
        )
    assert done.status_code == 200
    (raw,) = seen
    assert isinstance(raw, bytes)
    assert len(raw) == HEADER_LEN + NONCE_LEN + len(b"{}") + TAG_LEN
    opened = decrypt_value(
        data_key_of(admin_engine, tenant.tenant_id),
        ciphertext=raw,
        aad=aad_for(tenant.tenant_id, DATA),
    )
    assert opened == b"{}"


def test_a_replay_is_opened_through_the_requests_own_tenant_session(
    client: TestClient, tenant: CreatedTenant, admin_headers, monkeypatch
):
    provider = get_key_provider()
    real = provider.data_key
    calls: list[tuple[UUID, UUID | None]] = []

    # Records the tenant each key read was for and the tenant its session was bound to
    # (None for a read that went round the caller's session, through the cache).
    def spy(tenant_id, purpose, version=None, *, session=None):
        bound = None if session is None else bound_context(session).tenant_id
        calls.append((tenant_id, bound))
        return real(tenant_id, purpose, version, session=session)

    monkeypatch.setattr(provider, "data_key", spy)
    created = create_api_key(client, admin_headers)
    assert created.status_code == 201
    assert calls
    assert set(calls) == {(tenant.tenant_id, tenant.tenant_id)}
    calls.clear()
    again = create_api_key(client, admin_headers)
    assert again.json() == created.json()
    assert calls == [(tenant.tenant_id, tenant.tenant_id)]  # one read, in its session


# ---- whose it is ----------------------------------------------------------------


def test_another_tenants_session_cannot_open_the_stored_response(
    client: TestClient, tenant: CreatedTenant, admin_headers
):
    created = create_api_key(client, admin_headers)
    beta = new_tenant("beta", "admin@beta.mx")
    with tenant_session(system_ctx(tenant.tenant_id)) as s:
        value = s.scalars(select(IdempotencyKey.response_body)).one()
        assert isinstance(value, Encrypted)
        assert value.reveal(session=s)["body"] == created.json()  # the owner can
    with tenant_session(system_ctx(beta.tenant_id)) as other:
        # Row-level security shows the other tenant neither the row nor the key rows
        # that would open it, so a value it got hold of stays closed.
        assert other.scalars(select(IdempotencyKey)).all() == []
        with pytest.raises(KeyNotFoundError):
            _ = value.reveal(session=other)


def test_a_response_copied_into_another_tenants_row_fails_closed(
    client: TestClient, tenant: CreatedTenant, admin_headers, admin_engine: Engine
):
    alpha_key = create_api_key(client, admin_headers, "k-alpha").json()["key"]
    beta = new_tenant("beta", "admin@beta.mx")
    beta_headers = first_login(
        client, "beta", "admin@beta.mx", beta.admin_password, NEW_PASSWORD
    )
    assert create_api_key(client, beta_headers, "k-beta").status_code == 201
    # Someone with write access to the table moves alpha's stored response into beta's
    # row: the tenant and the purpose are authenticated data, and the keys differ.
    with admin_engine.begin() as conn:
        _ = conn.execute(
            text(
                """
                UPDATE idempotency_key AS target
                SET response_body = source.response_body
                FROM idempotency_key AS source
                WHERE target.tenant_id = :beta AND target.key = 'k-beta'
                  AND source.tenant_id = :alpha AND source.key = 'k-alpha'
                """
            ),
            {"alpha": tenant.tenant_id, "beta": beta.tenant_id},
        )
    replay = create_api_key(client, beta_headers, "k-beta")
    assert (replay.status_code, replay.json()["code"]) == (500, "DECRYPTION_FAILED")
    assert "idempotent-replay" not in replay.headers
    assert alpha_key not in replay.text


# ---- when the key is out of reach -----------------------------------------------


def test_after_the_keys_are_destroyed_a_replay_is_gone_never_a_server_error(
    client: TestClient, tenant: CreatedTenant, admin_headers, admin_engine: Engine
):
    created = create_api_key(client, admin_headers)
    secret = created.json()["key"]
    destroy_keys(tenant.tenant_id)
    replay = create_api_key(client, admin_headers)
    assert (replay.status_code, replay.json()["code"]) == (410, "KEY_DESTROYED")
    assert replay.json()["title"] == "Gone"
    assert "idempotent-replay" not in replay.headers
    assert secret not in replay.text
    # The row stays, closed for good; nothing was written in the clear to get here.
    assert set(stored_bodies(admin_engine)) == {"k-1"}
    assert found_in_store(admin_engine, secret) == []


def test_a_fresh_request_after_destruction_is_refused_before_it_runs(
    tenant: CreatedTenant, admin_headers, admin_engine: Engine
):
    attempts: list[int] = []
    router = v1_router()

    @router.post("/probe/work")
    def work() -> dict[str, int]:
        attempts.append(len(attempts) + 1)
        return {"attempt": len(attempts)}

    app = build_app()
    app.include_router(router, prefix="/v1")
    with TestClient(app) as probe:
        headers = {**admin_headers, "Idempotency-Key": "k-before"}
        assert probe.post("/v1/probe/work", headers=headers).status_code == 200
        destroy_keys(tenant.tenant_id)
        headers = {**admin_headers, "Idempotency-Key": "k-after"}
        refused = probe.post("/v1/probe/work", headers=headers)
    assert (refused.status_code, refused.json()["code"]) == (410, "KEY_DESTROYED")
    assert attempts == [1]  # the endpoint did not run for it
    assert set(stored_bodies(admin_engine)) == {"k-before"}  # nor was it reserved


@pytest.mark.parametrize("request_kind", ["new", "replay"])
def test_a_destroyed_tenants_expired_keys_are_purged_without_opening_them(
    client: TestClient,
    tenant: CreatedTenant,
    admin_headers,
    admin_engine: Engine,
    monkeypatch,
    request_kind: str,
):
    # A stored response to replay, two expired keys of two actors, and a live one.
    assert create_api_key(client, admin_headers, "k-stored").status_code == 201
    gone = clock.now() - timedelta(minutes=1)
    add_stored(tenant.tenant_id, "expired-1", expires_at=gone)
    add_stored(tenant.tenant_id, "expired-2", expires_at=gone, actor="another-actor")
    add_stored(tenant.tenant_id, "live", expires_at=clock.now() + TTL)
    destroy_keys(tenant.tenant_id)
    # Opening a sealed body starts with reading its header, so this counts the bodies
    # that were read at all, whether or not they could then be opened.
    read: list[int] = []

    def counting(data: bytes) -> int:
        read.append(len(data))
        return parse_header(data)

    monkeypatch.setattr("xolotl.platform.crypto.encrypted.parse_header", counting)

    key = "k-stored" if request_kind == "replay" else "k-new"
    refused = create_api_key(client, admin_headers, key)

    assert (refused.status_code, refused.json()["code"]) == (410, "KEY_DESTROYED")
    # The refusal did not roll the purge back: the expired keys are gone, the others
    # stay, closed for good.
    assert set(stored_bodies(admin_engine)) == {"k-stored", "live"}
    # And no body was read to do it: only a replay reads one, the response it asks for.
    assert len(read) == (1 if request_kind == "replay" else 0)


def test_a_tenant_without_keys_is_refused_with_the_crypto_error_and_stores_nothing(
    tenant: CreatedTenant, admin_headers, admin_engine: Engine
):
    attempts: list[int] = []
    router = v1_router()

    @router.post("/probe/work")
    def work() -> dict[str, int]:
        attempts.append(len(attempts) + 1)
        return {"attempt": len(attempts)}

    # A tenant put into the table by hand: the ones the application creates get their
    # keys on the way in, and an older one gets them from migration 0004.
    with admin_engine.begin() as conn:
        _ = conn.execute(
            text("DELETE FROM tenant_key WHERE tenant_id = :tenant_id"),
            {"tenant_id": tenant.tenant_id},
        )
    app = build_app()
    app.include_router(router, prefix="/v1")
    with TestClient(app) as probe:
        headers = {**admin_headers, "Idempotency-Key": "k-1"}
        refused = probe.post("/v1/probe/work", headers=headers)
    # The crypto layer's own error, not the generic INTERNAL a stray exception gets.
    assert (refused.status_code, refused.json()["code"]) == (500, "KEY_NOT_FOUND")
    assert refused.json()["detail"] == "no such encryption key for this tenant"
    assert attempts == []  # the endpoint did not run
    assert stored_bodies(admin_engine) == {}  # and no reservation was left behind


def test_a_replay_still_opens_after_the_data_key_rotates(
    client: TestClient, tenant: CreatedTenant, admin_headers, admin_engine: Engine
):
    first = create_api_key(client, admin_headers, "k-old")
    assert rotate_data_key(tenant.tenant_id) == 2
    again = create_api_key(client, admin_headers, "k-old")
    assert again.json() == first.json()
    assert again.headers["idempotent-replay"] == "true"
    assert create_api_key(client, admin_headers, "k-new").status_code == 201
    bodies = stored_bodies(admin_engine)
    assert isinstance(bodies["k-old"], bytes)
    assert isinstance(bodies["k-new"], bytes)
    # The old record still names version 1, the new one is sealed under version 2.
    assert bodies["k-old"][:HEADER_LEN] == b"\x00\x01"
    assert bodies["k-new"][:HEADER_LEN] == b"\x00\x02"


# ---- logs -----------------------------------------------------------------------


# Everything the application logs at DEBUG, through the handler and filter that
# `xolotl serve` installs, including what SQLAlchemy says about its statements.
@pytest.fixture
def debug_log() -> Generator[io.StringIO]:
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter())
    handler.addFilter(RedactionFilter())
    root, engine = logging.getLogger(), logging.getLogger("sqlalchemy.engine")
    levels = (root.level, engine.level)
    root.addHandler(handler)
    root.setLevel(logging.DEBUG)
    engine.setLevel(logging.DEBUG)
    try:
        yield stream
    finally:
        root.removeHandler(handler)
        root.setLevel(levels[0])
        engine.setLevel(levels[1])


def test_the_stored_value_is_masked_when_a_row_is_logged(
    client: TestClient, tenant: CreatedTenant, admin_headers, debug_log: io.StringIO
):
    secret = create_api_key(client, admin_headers).json()["key"]
    with tenant_session(system_ctx(tenant.tenant_id)) as s:
        row = s.scalars(select(IdempotencyKey)).one()
        assert isinstance(row.response_body, Encrypted)
        assert repr(row.response_body) == str(row.response_body) == "Encrypted(***)"
        shown = {"status": row.status_code, "response_body": row.response_body}
        logging.getLogger("xolotl.b8").debug(
            "stored %s", shown, extra={"response_body": row.response_body}
        )
    lines = [json.loads(line) for line in debug_log.getvalue().splitlines()]
    (mine,) = [line for line in lines if line["logger"] == "xolotl.b8"]
    assert mine["msg"] == "stored {'status': 201, 'response_body': '***'}"
    assert mine["response_body"] == "***"
    assert leaked(debug_log.getvalue(), secret.encode(), *fragments(secret)) == []


def test_a_debug_log_of_the_whole_exchange_holds_no_plaintext_of_the_secret(
    client: TestClient, admin_headers, debug_log: io.StringIO
):
    created = create_api_key(client, admin_headers)
    again = create_api_key(client, admin_headers)
    assert again.json() == created.json()
    secret = created.json()["key"]
    log = debug_log.getvalue()
    assert log  # the exchange was logged, so the scan has something to read
    assert leaked(log, secret.encode(), *fragments(secret)) == []
