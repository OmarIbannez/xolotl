import json
import logging
import re
from contextlib import suppress
from datetime import timedelta

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.responses import PlainTextResponse
from fastapi.routing import APIRoute, iter_route_contexts
from fastapi.testclient import TestClient
from sqlalchemy import Engine, func, select
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from tests.helpers import make_tenant_with_keys, staff_session, system_ctx
from xolotl.app import build_app
from xolotl.modules.tenants import service
from xolotl.modules.tenants.models import ApiKeyLookup
from xolotl.modules.tenants.service import CreatedTenant
from xolotl.platform import clock
from xolotl.platform.api import health
from xolotl.platform.api.idempotency import (
    RESERVATION_TTL,
    RESERVED_STATUS,
    TTL,
    IdempotentRoute,
    request_hash,
)
from xolotl.platform.api.models import IdempotencyKey
from xolotl.platform.api.router import v1_router
from xolotl.platform.auth.principal import SessionDep
from xolotl.platform.crypto.errors import KeyDestroyedError
from xolotl.platform.db import RoleBypassError, tenant_session
from xolotl.platform.errors import (
    DomainError,
    ForbiddenError,
    NotFoundError,
    UnauthorizedError,
)
from xolotl.platform.logging import JsonFormatter, RedactionFilter

PROBLEM_KEYS = {"type", "title", "status", "detail", "instance", "code"}
# Shaped like psycopg's: a primary line, then a DETAIL line that echoes row values.
DRIVER_MESSAGE = """duplicate key value violates unique constraint
DETAIL: Key (email)=(a@b.mx) already exists."""


def _probe_router() -> APIRouter:
    router = APIRouter()

    @router.get("/probe/domain")
    def domain() -> dict[str, str]:
        msg = "duplicate slug"
        raise DomainError(msg)

    @router.get("/probe/notfound")
    def notfound() -> dict[str, str]:
        raise NotFoundError

    @router.get("/probe/unauth")
    def unauth() -> dict[str, str]:
        raise UnauthorizedError

    @router.get("/probe/forbidden")
    def forbidden() -> dict[str, str]:
        msg = "scope tenants:read required"
        raise ForbiddenError(msg, code="SCOPE_MISSING")

    @router.get("/probe/gone")
    def gone() -> dict[str, str]:
        raise KeyDestroyedError

    @router.get("/probe/boom")
    def boom() -> dict[str, str]:
        msg = "kaboom"
        raise RuntimeError(msg)

    @router.get("/probe/echo")
    def echo(q: int) -> dict[str, int]:
        return {"q": q}

    @router.get("/probe/dberror")
    def dberror() -> dict[str, str]:
        statement = "INSERT INTO person (email) VALUES (%(email)s)"
        params = {"email": "a@b.mx"}
        raise IntegrityError(statement, params, Exception(DRIVER_MESSAGE))

    return router


@pytest.fixture
def probe_app(engine: Engine) -> FastAPI:
    app = build_app()
    app.include_router(_probe_router(), prefix="/v1")
    return app


@pytest.fixture
def probe(probe_app: FastAPI):
    with TestClient(probe_app, raise_server_exceptions=False) as c:
        yield c


@pytest.mark.parametrize(
    ("path", "status", "code", "detail"),
    [
        ("/v1/probe/domain", 409, "CONFLICT", "duplicate slug"),
        ("/v1/probe/notfound", 404, "NOT_FOUND", "not found"),
        ("/v1/probe/unauth", 401, "UNAUTHENTICATED", "authentication required"),
        ("/v1/probe/forbidden", 403, "SCOPE_MISSING", "scope tenants:read required"),
        (
            "/v1/probe/gone",
            410,
            "KEY_DESTROYED",
            "the encryption keys of this tenant were destroyed",
        ),
        ("/v1/probe/boom", 500, "INTERNAL", "internal error"),
        ("/v1/no/such/route", 404, "NOT_FOUND", "Not Found"),
    ],
)
def test_errors_are_problem_json_with_stable_codes(probe, path, status, code, detail):
    resp = probe.get(path)
    assert resp.status_code == status
    assert resp.headers["content-type"] == "application/problem+json"
    body = resp.json()
    assert set(body) >= PROBLEM_KEYS
    assert body["code"] == code
    assert body["type"] == f"https://xolotl.dev/problems/{code}"
    assert body["status"] == status
    assert body["detail"] == detail
    assert body["instance"] == path
    if status == 401:
        assert resp.headers["www-authenticate"] == "Bearer"


def test_destroyed_keys_are_a_410_problem_titled_gone(probe):
    body = probe.get("/v1/probe/gone").json()
    assert (body["status"], body["title"], body["code"]) == (
        410,
        "Gone",
        "KEY_DESTROYED",
    )


def test_validation_errors_carry_locations(probe):
    resp = probe.get("/v1/probe/echo", params={"q": "abc"})
    assert resp.status_code == 422
    body = resp.json()
    assert body["code"] == "VALIDATION_FAILED"
    assert body["errors"][0]["loc"] == ["query", "q"]
    assert body["errors"][0]["msg"]
    assert body["errors"][0]["type"]


def test_method_not_allowed_is_a_problem(probe):
    resp = probe.post("/v1/probe/echo")
    assert resp.status_code == 405
    assert resp.json()["code"] == "METHOD_NOT_ALLOWED"


def test_request_id_is_echoed_or_generated(client: TestClient):
    resp = client.get("/healthz", headers={"X-Request-Id": "abc-123_X.y"})
    assert resp.headers["x-request-id"] == "abc-123_X.y"
    generated = client.get("/healthz").headers["x-request-id"]
    assert re.fullmatch(r"[0-9a-f]{32}", generated)
    rejected = client.get("/healthz", headers={"X-Request-Id": "has spaces"})
    assert rejected.headers["x-request-id"] != "has spaces"
    too_long = client.get("/healthz", headers={"X-Request-Id": "x" * 129})
    assert len(too_long.headers["x-request-id"]) == 32


def test_unhandled_errors_keep_the_request_id_and_are_logged_as_500(probe, caplog):
    with caplog.at_level(logging.INFO, logger="xolotl.request"):
        resp = probe.get("/v1/probe/boom", headers={"X-Request-Id": "boom-1"})
    assert resp.status_code == 500
    assert resp.headers["x-request-id"] == "boom-1"
    lines = [vars(r) for r in caplog.records if r.name == "xolotl.request"]
    assert [(line["status"], line["request_id"]) for line in lines] == [(500, "boom-1")]
    errors = [vars(r) for r in caplog.records if r.getMessage() == "unhandled error"]
    assert [line["request_id"] for line in errors] == ["boom-1"]


@pytest.fixture
def production_format(caplog):
    # Format with the production pair so caplog.text is what stdout would carry. The
    # handler is shared by every later test, so the pair has to come off again: the
    # filter drops SQLAlchemy's DEBUG row log, which another test reads from caplog.
    handler, previous, redaction = (
        caplog.handler,
        caplog.handler.formatter,
        RedactionFilter(),
    )
    handler.setFormatter(JsonFormatter())
    handler.addFilter(redaction)
    yield
    handler.removeFilter(redaction)
    handler.setFormatter(previous)


def test_database_errors_are_logged_as_a_shape_never_as_their_message(
    probe, caplog, production_format
):
    with caplog.at_level(logging.INFO):
        resp = probe.get("/v1/probe/dberror", headers={"X-Request-Id": "db-1"})
    assert resp.status_code == 500
    assert resp.json()["code"] == "INTERNAL"
    assert resp.json()["detail"] == "internal error"
    assert "a@b.mx" not in resp.text
    assert "a@b.mx" not in caplog.text
    assert "duplicate key" not in caplog.text
    lines = [json.loads(line) for line in caplog.text.splitlines()]
    (error,) = [line for line in lines if line["msg"] == "unhandled error"]
    assert error["request_id"] == "db-1"
    assert error["exc_info"].startswith("IntegrityError error_type=Exception")
    assert "dberror" in error["exc_info"]


def test_health_and_ready(client: TestClient):
    assert client.get("/healthz").json() == {"status": "ok"}
    assert client.get("/readyz").json() == {"status": "ok", "database": "ok"}


def test_readyz_refuses_a_database_role_that_can_bypass_rls(
    client: TestClient, monkeypatch
):
    def bypassing(_engine: Engine) -> None:
        msg = "database role can bypass row-level security (superuser or BYPASSRLS)"
        raise RoleBypassError(msg)

    monkeypatch.setattr(health, "assert_app_role_safe", bypassing)
    resp = client.get("/readyz")
    assert resp.status_code == 503
    assert (resp.json()["code"], resp.json()["detail"]) == (
        "NOT_READY",
        "database role can bypass row-level security",
    )


def test_readyz_reports_an_unreachable_database(client: TestClient, monkeypatch):
    def unreachable(_engine: Engine) -> None:
        statement, reason = "SELECT 1", "connection refused"
        raise OperationalError(statement, {}, Exception(reason))

    monkeypatch.setattr(health, "assert_app_role_safe", unreachable)
    resp = client.get("/readyz")
    assert resp.status_code == 503
    assert (resp.json()["code"], resp.json()["detail"]) == (
        "NOT_READY",
        "database unreachable",
    )


def test_openapi_lives_under_v1_with_function_operation_ids(client: TestClient):
    spec = client.get("/v1/openapi.json").json()
    assert spec["info"]["title"] == "xolotl"
    assert spec["paths"]["/healthz"]["get"]["operationId"] == "healthz"
    assert client.get("/v1/docs").status_code == 200


def _ping(client: TestClient, headers, key: str | None, message: str = "ping"):
    extra = {} if key is None else {"Idempotency-Key": key}
    return client.post(
        "/v1/ping", json={"message": message}, headers={**headers, **extra}
    )


def test_idempotency_key_is_required_before_authentication_is_checked(
    client: TestClient, admin_headers
):
    missing = _ping(client, admin_headers, None)
    assert (missing.status_code, missing.json()["code"]) == (
        400,
        "IDEMPOTENCY_KEY_REQUIRED",
    )
    too_long = _ping(client, admin_headers, "k" * 129)
    assert too_long.status_code == 400
    unauthenticated = _ping(client, {}, "k-1")
    assert unauthenticated.status_code == 401
    # No key is needed under /v1/auth.
    login = client.post(
        "/v1/auth/login", json={"tenant_slug": "x", "email": "y", "password": "z"}
    )
    assert login.status_code == 401


def test_same_key_replays_the_stored_response(client: TestClient, admin_headers):
    first = _ping(client, admin_headers, "k-replay", "uno")
    second = _ping(client, admin_headers, "k-replay", "uno")
    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()  # same `at`: the handler did not run again
    assert "idempotent-replay" not in first.headers
    assert second.headers["idempotent-replay"] == "true"


def test_same_key_with_a_different_body_is_rejected(client: TestClient, admin_headers):
    assert _ping(client, admin_headers, "k-conflict", "uno").status_code == 200
    conflict = _ping(client, admin_headers, "k-conflict", "dos")
    assert (conflict.status_code, conflict.json()["code"]) == (
        422,
        "IDEMPOTENCY_KEY_REUSED",
    )


def test_keys_are_scoped_per_actor(
    client: TestClient, tenant: CreatedTenant, admin_headers
):
    with staff_session(tenant.tenant_id, tenant.admin_user_id) as s:
        issued = service.create_api_key(s, name="ingest", scopes=["tenants:write"])
    machine = {"Authorization": f"Bearer {issued.key}"}
    assert _ping(client, admin_headers, "shared-key", "from-admin").status_code == 200
    other = _ping(client, machine, "shared-key", "from-machine")
    assert other.status_code == 200
    assert other.json()["pong"] == "from-machine"
    assert "idempotent-replay" not in other.headers


def test_a_failed_request_releases_its_key(client: TestClient, admin_headers):
    invalid = client.post(
        "/v1/ping",
        json={"message": 123456},
        headers={**admin_headers, "Idempotency-Key": "k-fail"},
    )
    assert invalid.status_code == 422
    assert invalid.json()["code"] == "VALIDATION_FAILED"
    retry = _ping(client, admin_headers, "k-fail", "ok-now")
    assert retry.status_code == 200
    assert "idempotent-replay" not in retry.headers


def test_entries_expire_after_24_hours(client: TestClient, admin_headers, monkeypatch):
    first = _ping(client, admin_headers, "k-expire", "uno")
    later = clock.now() + timedelta(hours=25)
    monkeypatch.setattr(clock, "now", lambda: later)
    again = _ping(client, admin_headers, "k-expire", "uno")
    assert again.status_code == 200
    assert "idempotent-replay" not in again.headers
    assert again.json()["at"] != first.json()["at"]


def _stored_keys(tenant_id) -> set[str]:
    with tenant_session(system_ctx(tenant_id)) as s:
        return set(s.scalars(select(IdempotencyKey.key)))


def test_a_reservation_purges_the_tenants_expired_keys(
    client: TestClient, tenant: CreatedTenant, admin_headers
):
    # Its rows are sealed when they are added, which takes the tenant's keys.
    beta = make_tenant_with_keys("beta")
    now = clock.now()
    past, future = now - timedelta(minutes=1), now + TTL
    admin = str(tenant.admin_user_id)
    rows = [
        (tenant.tenant_id, admin, "k-expired", past),
        (tenant.tenant_id, "another-actor", "k-expired-elsewhere", past),
        (tenant.tenant_id, admin, "k-live", future),
        (beta, "beta-actor", "k-expired-beta", past),
    ]
    for tenant_id, actor_id, key, expires_at in rows:
        with tenant_session(system_ctx(tenant_id)) as s:
            s.add(
                IdempotencyKey(
                    tenant_id=tenant_id,
                    actor_id=actor_id,
                    key=key,
                    request_hash=b"",
                    status_code=200,
                    response_body={},
                    expires_at=expires_at,
                )
            )
    assert _ping(client, admin_headers, "k-new").status_code == 200
    # Every actor's expired keys in the tenant are gone; live ones stay...
    assert _stored_keys(tenant.tenant_id) == {"k-live", "k-new"}
    # ...and row-level security keeps the purge inside the tenant.
    assert _stored_keys(beta) == {"k-expired-beta"}


def test_a_stale_reservation_is_replaced_while_a_fresh_one_blocks_the_key(
    client: TestClient, tenant: CreatedTenant, admin_headers
):
    body = b'{"message":"uno"}'
    now = clock.now()
    with staff_session(tenant.tenant_id, tenant.admin_user_id) as s:
        for key, age in (("k-fresh", timedelta(0)), ("k-stale", 2 * RESERVATION_TTL)):
            s.add(
                IdempotencyKey(
                    tenant_id=tenant.tenant_id,
                    actor_id=str(tenant.admin_user_id),
                    key=key,
                    request_hash=request_hash("POST", "/v1/ping", body),
                    status_code=RESERVED_STATUS,
                    response_body={},
                    created_at=now - age,
                    expires_at=now + TTL,
                )
            )
    headers = {**admin_headers, "Content-Type": "application/json"}
    blocked = client.post(
        "/v1/ping", content=body, headers={**headers, "Idempotency-Key": "k-fresh"}
    )
    assert (blocked.status_code, blocked.json()["code"]) == (
        409,
        "IDEMPOTENCY_IN_PROGRESS",
    )
    replaced = client.post(
        "/v1/ping", content=body, headers={**headers, "Idempotency-Key": "k-stale"}
    )
    assert replaced.status_code == 200
    assert replaced.json()["pong"] == "uno"
    assert "idempotent-replay" not in replaced.headers


def test_every_post_route_under_v1_is_idempotent_except_auth(app):
    # FastAPI includes routers lazily: app.routes holds opaque entries, so walk
    # the effective routes the way its OpenAPI generator does.
    checked = []
    for context in iter_route_contexts(app.routes):
        route = context.original_route
        path = context.path or ""
        if not isinstance(route, APIRoute) or "POST" not in (context.methods or ()):
            continue
        if path.startswith("/v1/") and not path.startswith("/v1/auth/"):
            assert isinstance(route, IdempotentRoute), path
            checked.append(path)
    assert checked, "the walk found no POST route under /v1 to check"


def _work_router(attempts: list[int]) -> APIRouter:
    router = v1_router()

    @router.post("/probe/work")
    def work(session: SessionDep) -> dict[str, int]:
        attempts.append(len(attempts) + 1)
        if len(attempts) == 1:
            # The patched commit in the test refuses this session, and only this one.
            session.info["fail_commit"] = True
        return {"attempt": len(attempts)}

    return router


def test_a_failed_endpoint_commit_leaves_the_key_reserved_never_replayable(
    admin_headers, monkeypatch
):
    real_commit = Session.commit

    def commit(self: Session) -> None:
        # Only the endpoint's session is flagged: the idempotency layer commits
        # sessions of its own and those must keep working.
        if self.info.get("fail_commit"):
            msg = "simulated commit failure"
            raise RuntimeError(msg)
        real_commit(self)

    monkeypatch.setattr(Session, "commit", commit)
    attempts: list[int] = []
    app = build_app()
    app.include_router(_work_router(attempts), prefix="/v1")
    headers = {**admin_headers, "Idempotency-Key": "k-commit"}
    with TestClient(app, raise_server_exceptions=False) as probe:
        failed = probe.post("/v1/probe/work", headers=headers)
        assert (failed.status_code, failed.json().get("code")) == (500, "INTERNAL")
        # The stored 200 rolled back with the endpoint's work, so the key is still a
        # reservation: the retry is told to wait, not handed a success that was lost.
        retry = probe.post("/v1/probe/work", headers=headers)
        assert (retry.status_code, retry.json().get("code")) == (
            409,
            "IDEMPOTENCY_IN_PROGRESS",
        )
        assert attempts == [1]
        # Past the reservation window the request that died is presumed gone.
        later = clock.now() + 2 * RESERVATION_TTL
        monkeypatch.setattr(clock, "now", lambda: later)
        again = probe.post("/v1/probe/work", headers=headers)
        assert (again.status_code, again.json()) == (200, {"attempt": 2})
        assert "idempotent-replay" not in again.headers
        replay = probe.post("/v1/probe/work", headers=headers)
        assert replay.headers["idempotent-replay"] == "true"
        assert replay.json() == {"attempt": 2}
        assert attempts == [1, 2]


def _probe_client(router: APIRouter) -> TestClient:
    app = build_app()
    app.include_router(router, prefix="/v1")
    return TestClient(app, raise_server_exceptions=False)


def _lookup_rows(tenant: CreatedTenant, prefix: str) -> int:
    with staff_session(tenant.tenant_id, tenant.admin_user_id) as s:
        rows = select(func.count()).where(ApiKeyLookup.key_prefix.startswith(prefix))
        return s.scalars(rows).one()


def test_a_204_post_runs_once_and_its_retry_replays_an_empty_204(
    tenant: CreatedTenant, admin_headers
):
    attempts: list[int] = []
    router = v1_router()

    @router.post("/probe/note", status_code=204)
    def note(session: SessionDep) -> None:
        attempts.append(len(attempts) + 1)
        row = ApiKeyLookup(
            key_prefix=f"note-{len(attempts)}", tenant_id=tenant.tenant_id
        )
        session.add(row)

    headers = {**admin_headers, "Idempotency-Key": "k-note"}
    with _probe_client(router) as probe:
        first = probe.post("/v1/probe/note", headers=headers)
        retry = probe.post("/v1/probe/note", headers=headers)
    assert (first.status_code, first.content) == (204, b"")
    assert "idempotent-replay" not in first.headers
    assert (retry.status_code, retry.content) == (204, b"")
    assert retry.headers.get("idempotent-replay") == "true"
    assert attempts == [1]
    assert _lookup_rows(tenant, "note-") == 1


@pytest.mark.parametrize(
    "text",
    ["línea uno\nlínea dos\n", '{"a":   1}  '],
    ids=["plain-text", "text-that-parses-as-json"],
)
def test_a_text_response_replays_byte_for_byte_with_its_media_type(admin_headers, text):
    attempts: list[int] = []
    router = v1_router()

    @router.post("/probe/text")
    def say() -> PlainTextResponse:
        attempts.append(len(attempts) + 1)
        return PlainTextResponse(text)

    headers = {**admin_headers, "Idempotency-Key": "k-text"}
    with _probe_client(router) as probe:
        first = probe.post("/v1/probe/text", headers=headers)
        retry = probe.post("/v1/probe/text", headers=headers)
    assert (first.status_code, first.content) == (200, text.encode())
    assert (retry.status_code, retry.content) == (200, first.content)
    assert first.headers["content-type"] == "text/plain; charset=utf-8"
    assert retry.headers["content-type"] == first.headers["content-type"]
    assert retry.headers.get("idempotent-replay") == "true"
    assert attempts == [1]


def test_a_json_null_body_replays_as_null(admin_headers):
    attempts: list[int] = []
    router = v1_router()

    @router.post("/probe/nothing")
    def nothing() -> None:
        attempts.append(len(attempts) + 1)

    headers = {**admin_headers, "Idempotency-Key": "k-null"}
    with _probe_client(router) as probe:
        first = probe.post("/v1/probe/nothing", headers=headers)
        retry = probe.post("/v1/probe/nothing", headers=headers)
    assert (first.status_code, first.content) == (200, b"null")
    assert (retry.status_code, retry.content) == (200, b"null")
    assert retry.headers["content-type"] == first.headers["content-type"]
    assert retry.headers.get("idempotent-replay") == "true"
    assert attempts == [1]


def test_a_swallowed_flush_failure_leaves_the_key_reserved_never_replayable(
    tenant: CreatedTenant, admin_headers
):
    attempts: list[int] = []
    router = v1_router()

    @router.post("/probe/swallow")
    def swallow(session: SessionDep) -> dict[str, str]:
        attempts.append(len(attempts) + 1)
        session.add_all([
            ApiKeyLookup(key_prefix="dup", tenant_id=tenant.tenant_id) for _ in "ab"
        ])
        # A duplicate primary key: the failed flush leaves the transaction unable
        # to commit, yet the endpoint carries on and answers 200.
        with suppress(IntegrityError):
            session.flush()
        return {"ok": "yes"}

    headers = {**admin_headers, "Idempotency-Key": "k-flush"}
    with _probe_client(router) as probe:
        failed = probe.post("/v1/probe/swallow", headers=headers)
        assert (failed.status_code, failed.json().get("code")) == (500, "INTERNAL")
        # The work cannot commit, so no response may be stored for the key: the
        # retry is told to wait instead of replaying a 200 for work that never was.
        retry = probe.post("/v1/probe/swallow", headers=headers)
    assert (retry.status_code, retry.json().get("code")) == (
        409,
        "IDEMPOTENCY_IN_PROGRESS",
    )
    assert attempts == [1]
