from datetime import UTC, datetime
from typing import Annotated
from uuid import uuid4

import pytest
from fastapi import APIRouter, BackgroundTasks, Depends, FastAPI, Request
from fastapi.dependencies.models import Dependant
from fastapi.routing import APIRoute, iter_route_contexts
from fastapi.testclient import TestClient
from sqlalchemy import Engine, select, text
from sqlalchemy.orm import Session

from tests.helpers import platform_ctx, staff_ctx
from xolotl.modules.tenants.models import Tenant
from xolotl.platform.api.app import create_app
from xolotl.platform.api.models import IdempotencyKey
from xolotl.platform.auth.actor import Actor
from xolotl.platform.auth.principal import (
    SAFE_METHODS,
    AccessRule,
    ActorDep,
    SessionDep,
    StaffDep,
    access_rule_of,
    require_access,
    resolve_request_actor,
)
from xolotl.platform.db import SessionFactory, get_sessionmaker, tenant_session
from xolotl.platform.enums import ActorType, Role, TenantEnv, TenantStatus
from xolotl.platform.errors import NotFoundError, UnauthorizedError
from xolotl.platform.tenancy.context import (
    TenantContext,
    current_context,
    require_context,
)

TENANT = uuid4()
RESOLVES: list[str] = []
SEEN_CONTEXTS: list[TenantContext | None] = []
PROBE_KEY = "commit-probe"
EXPIRES = datetime(2100, 1, 1, tzinfo=UTC)


def fake_resolver(_factory: SessionFactory, token: str) -> Actor:
    RESOLVES.append(token)
    match token:
        case "staff-admin":
            return Actor(staff_ctx(TENANT, actor_id="u-admin", roles=[Role.ADMIN]))
        case "staff-analyst":
            return Actor(staff_ctx(TENANT, actor_id="u-analyst", roles=[Role.ANALYST]))
        case "staff-auditor":
            return Actor(staff_ctx(TENANT, actor_id="u-aud", roles=[Role.AUDITOR_RO]))
        case "staff-pw":
            return Actor(
                staff_ctx(TENANT, actor_id="u-pw", roles=[Role.ADMIN]),
                must_change_password=True,
            )
        case "machine-read":
            ctx = TenantContext(
                tenant_id=TENANT, actor_type=ActorType.API_CLIENT, actor_id="key-1"
            )
            return Actor(ctx, scopes=frozenset({"tenants:read"}))
        case _:
            raise UnauthorizedError


def _seed_tenant() -> None:
    # The session routes write rows for the tenant the resolver hands out.
    with tenant_session(platform_ctx()) as session:
        session.add(
            Tenant(
                id=TENANT,
                slug="acme",
                legal_name="Acme SA de CV",
                country_code="MX",
                env=TenantEnv.SANDBOX,
                status=TenantStatus.ACTIVE,
            )
        )


def _add_probe_row(session: Session) -> None:
    ctx = require_context()
    session.add(
        IdempotencyKey(
            tenant_id=ctx.require_tenant_id(),
            actor_id=ctx.actor_id,
            key=PROBE_KEY,
            request_hash=b"",
            status_code=200,
            response_body={},
            expires_at=EXPIRES,
        )
    )


def _record_context() -> None:
    SEEN_CONTEXTS.append(current_context.get())


def _router() -> APIRouter:
    router = APIRouter()

    @router.get("/whoami")
    def whoami(request: Request, actor: ActorDep) -> dict[str, str]:
        again = resolve_request_actor(request)
        assert again is actor
        return {
            "actor_id": actor.context.actor_id,
            "bound": require_context().actor_id,
            "request_id": require_context().request_id,
        }

    @router.post("/admin-write")
    def admin_write(
        actor: Annotated[
            Actor, Depends(require_access(roles=(Role.ADMIN,), write=True))
        ],
    ) -> dict[str, str]:
        return {"ok": actor.context.actor_id}

    @router.get("/staff-read")
    def staff_read(
        actor: Annotated[Actor, Depends(require_access())],
    ) -> dict[str, str]:
        return {"ok": actor.context.actor_id}

    @router.get("/machine-read")
    def machine_read(
        actor: Annotated[Actor, Depends(require_access(scope="tenants:read"))],
    ) -> dict[str, str]:
        return {"ok": actor.context.actor_id}

    @router.post("/machine-write")
    def machine_write(
        actor: Annotated[Actor, Depends(require_access(scope="tenants:write"))],
    ) -> dict[str, str]:
        return {"ok": actor.context.actor_id}

    @router.get("/staff-only")
    def staff_only(actor: StaffDep) -> dict[str, str]:
        return {"ok": actor.context.actor_id}

    @router.get("/session-ctx")
    def session_ctx(session: SessionDep) -> dict[str, str | None]:
        return {
            "tenant_id": session.scalar(
                text("SELECT current_setting('app.tenant_id', true)")
            ),
            "actor_id": session.scalar(
                text("SELECT current_setting('app.actor_id', true)")
            ),
        }

    return router


def _write_router() -> APIRouter:
    # Routes that only differ in how a write is declared or inferred.
    router = APIRouter()

    @router.post("/any-write")
    def any_write(
        actor: Annotated[Actor, Depends(require_access(write=True))],
    ) -> dict[str, str]:
        return {"ok": actor.context.actor_id}

    @router.get("/scope-says-write")
    def scope_says_write(
        actor: Annotated[Actor, Depends(require_access(scope="tenants:write"))],
    ) -> dict[str, str]:
        return {"ok": actor.context.actor_id}

    @router.post("/method-says-write")
    def method_says_write(
        actor: Annotated[Actor, Depends(require_access(scope="tenants:read"))],
    ) -> dict[str, str]:
        return {"ok": actor.context.actor_id}

    @router.post("/read-only-post")
    def read_only_post(
        actor: Annotated[Actor, Depends(require_access(write=False))],
    ) -> dict[str, str]:
        return {"ok": actor.context.actor_id}

    return router


def _session_router() -> APIRouter:
    router = APIRouter()

    @router.post("/session-write", dependencies=[Depends(require_access(write=True))])
    def session_write(session: SessionDep) -> dict[str, str]:
        _add_probe_row(session)
        return {"key": PROBE_KEY}

    @router.post(
        "/session-write-then-fail", dependencies=[Depends(require_access(write=True))]
    )
    def session_write_then_fail(session: SessionDep) -> dict[str, str]:
        _add_probe_row(session)
        session.flush()  # the INSERT reaches the database inside the open transaction
        raise NotFoundError

    @router.get("/session-read", dependencies=[Depends(require_access())])
    def session_read(session: SessionDep) -> dict[str, list[str]]:
        return {"keys": list(session.scalars(select(IdempotencyKey.key)))}

    @router.get("/context-after-response", dependencies=[Depends(require_access())])
    def context_after_response(background: BackgroundTasks) -> dict[str, str]:
        background.add_task(_record_context)
        return {"ok": "1"}

    return router


@pytest.fixture
def api_app(engine: Engine) -> FastAPI:
    RESOLVES.clear()
    SEEN_CONTEXTS.clear()
    _seed_tenant()
    return create_app(
        session_factory=get_sessionmaker(),
        actor_resolver=fake_resolver,
        routers=[_router(), _write_router(), _session_router()],
    )


@pytest.fixture
def api(api_app: FastAPI):
    with TestClient(api_app) as c:
        yield c


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_missing_or_malformed_bearer_is_401(api: TestClient):
    for headers in ({}, {"Authorization": "Basic abc"}, {"Authorization": "Bearer "}):
        resp = api.get("/v1/whoami", headers=headers)
        assert resp.status_code == 401
        assert resp.json()["code"] == "UNAUTHENTICATED"
        assert resp.headers["www-authenticate"] == "Bearer"
    assert api.get("/v1/whoami", headers=_auth("nope")).status_code == 401


def test_actor_is_resolved_once_and_bound_for_the_request(api: TestClient):
    resp = api.get(
        "/v1/whoami", headers={**_auth("staff-admin"), "X-Request-Id": "r-1"}
    )
    assert resp.json() == {
        "actor_id": "u-admin",
        "bound": "u-admin",
        "request_id": "r-1",
    }
    assert RESOLVES == ["staff-admin"]


def test_role_and_write_rules(api: TestClient):
    assert api.post("/v1/admin-write", headers=_auth("staff-admin")).status_code == 200
    denied = api.post("/v1/admin-write", headers=_auth("staff-analyst"))
    assert (denied.status_code, denied.json()["code"]) == (403, "ROLE_FORBIDDEN")
    auditor = api.post("/v1/admin-write", headers=_auth("staff-auditor"))
    assert (auditor.status_code, auditor.json()["code"]) == (403, "ROLE_FORBIDDEN")
    assert api.get("/v1/staff-read", headers=_auth("staff-auditor")).status_code == 200


def test_auditor_ro_may_never_write(api: TestClient):
    # /any-write requires no role, so the write ban is the only thing that can refuse.
    auditor = api.post("/v1/any-write", headers=_auth("staff-auditor"))
    assert (auditor.status_code, auditor.json()["code"]) == (403, "ROLE_FORBIDDEN")
    assert auditor.json()["detail"] == "AUDITOR_RO is read-only"
    assert api.post("/v1/any-write", headers=_auth("staff-analyst")).status_code == 200
    assert api.post("/v1/any-write", headers=_auth("staff-admin")).status_code == 200


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("POST", "/v1/machine-write"),  # the :write scope and the method both say write
        ("GET", "/v1/scope-says-write"),  # only the :write scope says write
        ("POST", "/v1/method-says-write"),  # only the method says write
    ],
)
def test_the_auditor_is_refused_wherever_a_write_is_inferred(
    api: TestClient, method: str, path: str
):
    refused = api.request(method, path, headers=_auth("staff-auditor"))
    assert (refused.status_code, refused.json()["code"]) == (403, "ROLE_FORBIDDEN")
    assert refused.json()["detail"] == "AUDITOR_RO is read-only"
    assert api.request(method, path, headers=_auth("staff-analyst")).status_code == 200


def test_reads_and_explicit_read_only_posts_stay_open_to_the_auditor(api: TestClient):
    auditor = _auth("staff-auditor")
    assert api.get("/v1/machine-read", headers=auditor).status_code == 200
    assert api.post("/v1/read-only-post", headers=auditor).status_code == 200


def test_password_change_required_blocks_everything_but_plain_actor_routes(
    api: TestClient,
):
    blocked = api.get("/v1/staff-read", headers=_auth("staff-pw"))
    assert (blocked.status_code, blocked.json()["code"]) == (
        403,
        "PASSWORD_CHANGE_REQUIRED",
    )
    assert api.get("/v1/whoami", headers=_auth("staff-pw")).status_code == 200
    assert api.get("/v1/staff-only", headers=_auth("staff-pw")).status_code == 200


def test_scope_rules_for_api_clients(api: TestClient):
    assert api.get("/v1/machine-read", headers=_auth("machine-read")).status_code == 200
    missing = api.post("/v1/machine-write", headers=_auth("machine-read"))
    assert (missing.status_code, missing.json()["code"]) == (403, "SCOPE_MISSING")
    staff_only = api.get("/v1/staff-read", headers=_auth("machine-read"))
    assert (staff_only.status_code, staff_only.json()["code"]) == (
        403,
        "ROLE_FORBIDDEN",
    )
    assert api.get("/v1/staff-only", headers=_auth("machine-read")).status_code == 403
    assert api.get("/v1/machine-read", headers=_auth("staff-admin")).status_code == 200


def test_session_dependency_runs_under_the_actor_context(api: TestClient):
    resp = api.get("/v1/session-ctx", headers=_auth("staff-analyst"))
    assert resp.json() == {"tenant_id": str(TENANT), "actor_id": "u-analyst"}


def test_a_write_through_the_session_dependency_is_committed(api: TestClient):
    written = api.post("/v1/session-write", headers=_auth("staff-admin"))
    assert written.status_code == 200
    # The next request opens its own session, so it only sees what was committed.
    read = api.get("/v1/session-read", headers=_auth("staff-analyst"))
    assert read.json() == {"keys": [PROBE_KEY]}


def test_an_error_inside_a_session_route_discards_the_write(api: TestClient):
    failed = api.post("/v1/session-write-then-fail", headers=_auth("staff-admin"))
    assert (failed.status_code, failed.json()["code"]) == (404, "NOT_FOUND")
    read = api.get("/v1/session-read", headers=_auth("staff-admin"))
    assert read.json() == {"keys": []}


def test_a_failed_commit_is_a_500_not_a_lost_write(
    api_app: FastAPI, monkeypatch: pytest.MonkeyPatch
):
    def failing_commit(_session: Session) -> None:
        msg = "commit failed"
        raise RuntimeError(msg)

    with TestClient(api_app, raise_server_exceptions=False) as client:
        with monkeypatch.context() as patched:
            patched.setattr(Session, "commit", failing_commit)
            failed = client.post("/v1/session-write", headers=_auth("staff-admin"))
        # The commit runs before the response is sent, so its failure is the response.
        assert (failed.status_code, failed.json()["code"]) == (500, "INTERNAL")
        read = client.get("/v1/session-read", headers=_auth("staff-admin"))
        assert read.json() == {"keys": []}


def test_the_actor_context_is_unbound_before_the_response_is_sent(api: TestClient):
    resp = api.get("/v1/context-after-response", headers=_auth("staff-admin"))
    assert resp.status_code == 200
    # Background tasks run after the response is sent: the context is gone by then.
    assert SEEN_CONTEXTS == [None]


def test_app_without_resolver_rejects_every_bearer(engine: Engine):
    app = create_app(session_factory=get_sessionmaker(), routers=[_router()])
    with TestClient(app) as c:
        assert c.get("/v1/whoami", headers=_auth("staff-admin")).status_code == 401


def _rules_below(dependant: Dependant) -> list[AccessRule]:
    rules: list[AccessRule] = []
    for sub in dependant.dependencies:
        rule = access_rule_of(sub.call)
        if rule is not None:
            rules.append(rule)
        rules.extend(_rules_below(sub))
    return rules


def _unguarded_routes(app: FastAPI) -> list[str]:
    problems: list[str] = []
    # include_router keeps routers lazy, so app.routes holds one opaque entry per
    # router. Walk the effective routes, as FastAPI's own OpenAPI generator does.
    for context in iter_route_contexts(app.routes):
        if not isinstance(context.original_route, APIRoute):
            continue
        path = context.path or ""
        # /v1/auth/* is exempt: login runs before an actor exists, and the other auth
        # routes use current_staff, which declares no write check on purpose.
        if not path.startswith("/v1/") or path.startswith("/v1/auth/"):
            continue
        methods = context.methods or set()
        label = f"{','.join(sorted(methods))} {path}"
        rules = _rules_below(context.dependant)
        if not rules:
            problems.append(f"{label}: no access rule")
        # Some rule must be able to refuse AUDITOR_RO on a write. A genuinely
        # read-only POST (write=False) needs an explicit exception here.
        elif not methods <= SAFE_METHODS and all(rule.write is False for rule in rules):
            problems.append(f"{label}: a write route opts out of the write check")
    return sorted(problems)


def test_every_v1_route_declares_an_access_rule(app: FastAPI):
    # Every router build_app mounts is walked, so a new route is checked as it lands.
    assert _unguarded_routes(app) == []


def test_the_route_walk_sees_included_routers_and_flags_the_gaps(engine: Engine):
    def staff_wrapper(actor: StaffDep) -> Actor:
        return actor

    router = APIRouter()

    @router.get("/actor-only")
    def actor_only(actor: ActorDep) -> dict[str, str]:
        return {}

    @router.get("/session-only")
    def session_only(session: SessionDep) -> dict[str, str]:
        return {}

    @router.get("/staff-get")
    def staff_get(actor: StaffDep) -> dict[str, str]:
        return {}

    @router.get("/nested")
    def nested(actor: Annotated[Actor, Depends(staff_wrapper)]) -> dict[str, str]:
        return {}

    @router.post("/staff-post")
    def staff_post(actor: StaffDep) -> dict[str, str]:
        return {}

    @router.post("/opt-out")
    def opt_out(
        actor: Annotated[Actor, Depends(require_access(write=False))],
    ) -> dict[str, str]:
        return {}

    @router.post("/guarded", dependencies=[Depends(require_access())])
    def guarded() -> dict[str, str]:
        return {}

    @router.post("/auth/login")
    def login() -> dict[str, str]:
        return {}

    app = create_app(session_factory=get_sessionmaker(), routers=[router])
    assert _unguarded_routes(app) == [
        "GET /v1/actor-only: no access rule",
        "GET /v1/session-only: no access rule",
        "POST /v1/opt-out: a write route opts out of the write check",
        "POST /v1/staff-post: a write route opts out of the write check",
    ]
