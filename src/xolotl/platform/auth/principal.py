"""Who is calling: bearer resolution, session binding and access checks (spec §6).

``current_actor`` is an *async* dependency on purpose: FastAPI runs async
dependencies in the event-loop task, so the contextvar it binds is visible to
sync endpoints, which Starlette runs in a threadpool with a copy of the
current context. The blocking resolver itself runs in the threadpool.
"""

from collections.abc import AsyncIterator, Callable, Collection, Iterator
from dataclasses import dataclass, replace
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from xolotl.platform.api.app import app_state
from xolotl.platform.auth.actor import Actor
from xolotl.platform.db import apply_context
from xolotl.platform.enums import ActorType, Role
from xolotl.platform.errors import ForbiddenError, UnauthorizedError
from xolotl.platform.logging import request_id_var
from xolotl.platform.tenancy.context import current_context


def _bearer_token(request: Request) -> str:
    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise UnauthorizedError
    return token.strip()


def resolve_request_actor(request: Request) -> Actor:
    """Resolve the bearer once per request; later calls return the cached actor.

    Returns:
        The actor, with the request id stamped into its context.

    Raises:
        UnauthorizedError: When there is no resolver or the bearer is invalid.

    """
    cached: object = getattr(request.state, "actor", None)
    if isinstance(cached, Actor):
        return cached
    state = app_state(request)
    if state.actor_resolver is None:
        raise UnauthorizedError
    resolved = state.actor_resolver(state.session_factory, _bearer_token(request))
    actor = replace(
        resolved, context=replace(resolved.context, request_id=request_id_var.get())
    )
    request.state.actor = actor
    return actor


async def current_actor(request: Request) -> AsyncIterator[Actor]:
    actor = await run_in_threadpool(resolve_request_actor, request)
    # Not ``with bind_context(...)``: ruff ASYNC119 rejects a with-block around
    # this yield. FastAPI exits the dependency in this same task, so reset is safe.
    token = current_context.set(actor.context)
    try:
        yield actor
    finally:
        current_context.reset(token)


# Function scope: exit runs after the endpoint but before the response is sent.
ActorDep = Annotated[Actor, Depends(current_actor, scope="function")]


def get_session(request: Request, actor: ActorDep) -> Iterator[Session]:
    """Yield a session bound to the actor's context.

    Commits on success, rolls back on error and always closes the session.

    Yields:
        The session, with the actor's context applied to its transaction.

    """
    session = app_state(request).session_factory()
    request.state.session = session
    try:
        apply_context(session, actor.context)
        yield session
        session.commit()
    except BaseException:
        session.rollback()
        raise
    finally:
        session.close()


# Function scope: commit before the response is sent; a failed commit is a 500.
SessionDep = Annotated[Session, Depends(get_session, scope="function")]

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
ACCESS_RULE_ATTR = "access_rule"


@dataclass(frozen=True, slots=True)
class AccessRule:
    """What an access dependency enforces, kept on it so tests can check routes.

    ``write`` is what the route declared: ``None`` means "infer it from the HTTP
    method and the scope", ``False`` an explicit read-only opt-out.
    """

    roles: frozenset[Role] = frozenset()
    scope: str | None = None
    write: bool | None = None
    staff_only: bool = False


def _declares[F: Callable[..., object]](rule: AccessRule) -> Callable[[F], F]:
    # Decorator factory: keeps ``rule`` on the dependency for ``access_rule_of``.
    def attach(dependency: F) -> F:
        setattr(dependency, ACCESS_RULE_ATTR, rule)
        return dependency

    return attach


def access_rule_of(call: object) -> AccessRule | None:
    """Look up the rule a dependency declared.

    Returns:
        The rule ``require_access`` or ``current_staff`` attached to ``call``, or
        ``None`` for any other callable.

    """
    rule: object = getattr(call, ACCESS_RULE_ATTR, None)
    return rule if isinstance(rule, AccessRule) else None


def _is_write(request: Request, *, scope: str | None, write: bool | None) -> bool:
    if write is not None:
        return write
    if scope is not None and scope.endswith(":write"):
        return True
    return request.method not in SAFE_METHODS


def require_access(
    *,
    roles: Collection[Role] = (),
    scope: str | None = None,
    write: bool | None = None,
) -> Callable[[Request, Actor], Actor]:
    """Dependency factory.

    Staff: must not have a pending password change; ``AUDITOR_RO`` may never
    write; when ``roles`` is given one of them is required. API clients: need
    ``scope`` (a route without a scope is staff-only).

    A request is a write when ``write`` says so. With ``write=None`` it is inferred:
    the scope ends in ``:write`` or the method is not GET, HEAD or OPTIONS. A
    read-only POST passes ``write=False``.

    Returns:
        A dependency that returns the actor when access is allowed.

    """

    @_declares(AccessRule(roles=frozenset(roles), scope=scope, write=write))
    def dependency(request: Request, actor: ActorDep) -> Actor:
        ctx = actor.context
        if ctx.actor_type is ActorType.STAFF:
            if actor.must_change_password:
                msg = "password change required"
                raise ForbiddenError(msg, code="PASSWORD_CHANGE_REQUIRED")
            is_write = _is_write(request, scope=scope, write=write)
            if is_write and Role.AUDITOR_RO in ctx.roles:
                msg = "AUDITOR_RO is read-only"
                raise ForbiddenError(msg, code="ROLE_FORBIDDEN")
            if roles and not ctx.roles.intersection(roles):
                msg = f"requires one of {sorted(role.value for role in roles)}"
                raise ForbiddenError(msg, code="ROLE_FORBIDDEN")
            return actor
        if ctx.actor_type is ActorType.API_CLIENT:
            if scope is None:
                msg = "staff only"
                raise ForbiddenError(msg, code="ROLE_FORBIDDEN")
            if scope not in actor.scopes:
                msg = f"scope {scope} required"
                raise ForbiddenError(msg, code="SCOPE_MISSING")
            return actor
        raise ForbiddenError

    return dependency


# write=False: nothing here stops AUDITOR_RO, so a writing route needs require_access.
@_declares(AccessRule(write=False, staff_only=True))
def current_staff(actor: ActorDep) -> Actor:
    """Any staff member, including one who still has to change their password.

    Returns:
        The staff actor.

    Raises:
        ForbiddenError: When the caller is not a staff member.

    """
    if not actor.is_staff:
        msg = "staff only"
        raise ForbiddenError(msg, code="ROLE_FORBIDDEN")
    return actor


StaffDep = Annotated[Actor, Depends(current_staff)]
