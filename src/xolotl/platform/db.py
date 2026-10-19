"""Engines, declarative base and the tenant session contract (spec §4.2).

Every transaction starts by setting ``app.tenant_id``, ``app.actor_type``,
``app.actor_id`` and ``app.roles`` with ``set_config(..., true)`` so the values
are transaction-local. Row-level security policies read them with
``current_setting(name, true)``. A session with no bound context refuses to run
any statement.
"""

from collections.abc import Callable, Generator
from contextlib import contextmanager
from functools import lru_cache
from typing import ClassVar, cast

from sqlalchemy import Connection, Engine, MetaData, create_engine, event, text
from sqlalchemy.orm import DeclarativeBase, Session, SessionTransaction, sessionmaker

from xolotl.platform.config import get_settings
from xolotl.platform.errors import ContextMissingError
from xolotl.platform.tenancy.context import (
    TenantContext,
    bind_context,
    current_context,
    roles_guc,
    tenant_guc,
)

APP_ROLE = "xolotl_app"
CONTEXT_KEY = "tenant_context"
SessionFactory = Callable[[], Session]

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_N_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

SET_CONTEXT_SQL = text(
    (
        "SELECT set_config('app.tenant_id', :tenant_id, true), "
        "set_config('app.actor_type', :actor_type, true), "
        "set_config('app.actor_id', :actor_id, true), "
        "set_config('app.roles', :roles, true)"
    ),
)
ROLE_BYPASS_SQL = text(
    "SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname = current_user"
)


class RoleBypassError(RuntimeError):
    """A database role that row-level security does not bind, so isolation fails."""


class BootstrapError(RuntimeError):
    """``xolotl db bootstrap`` could not alter the application role.

    The message names the role and the SQLSTATE, never the statement, which can
    carry the role's password.
    """


class Base(DeclarativeBase):
    metadata: ClassVar[MetaData] = MetaData(naming_convention=NAMING_CONVENTION)


def context_params(ctx: TenantContext) -> dict[str, str]:
    return {
        "tenant_id": tenant_guc(ctx),
        "actor_type": ctx.actor_type.value,
        "actor_id": ctx.actor_id,
        "roles": roles_guc(ctx.roles),
    }


# Both engines hide bound parameters: error text and SQL logs must not carry
# row values (emails, CURPs, password hashes).
@lru_cache
def get_engine() -> Engine:
    return create_engine(
        get_settings().database_url, pool_pre_ping=True, hide_parameters=True
    )


@lru_cache
def get_admin_engine() -> Engine:
    return create_engine(
        get_settings().database_admin_url, pool_pre_ping=True, hide_parameters=True
    )


@lru_cache
def get_sessionmaker() -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(), expire_on_commit=False)


def assert_app_role_safe(engine: Engine) -> None:
    """Refuse an engine whose role row-level security does not bind.

    ``serve`` checks the app engine before it starts and ``/readyz`` on every probe:
    a superuser or ``BYPASSRLS`` role would see every tenant's rows.

    Raises:
        RoleBypassError: When the role ``engine`` logs in as is a superuser or has
            ``BYPASSRLS``.

    """
    with engine.connect() as conn:
        bypass = cast("bool | None", conn.scalar(ROLE_BYPASS_SQL))
    if bypass is not False:
        msg = "database role can bypass row-level security (superuser or BYPASSRLS)"
        raise RoleBypassError(msg)


def bound_context(session: Session) -> TenantContext:
    """Return the context a session runs under; raise when none is bound.

    Returns:
        The context bound to ``session``.

    Raises:
        ContextMissingError: When ``session`` has no bound context.

    """
    raw: object = session.info.get(CONTEXT_KEY)
    if not isinstance(raw, TenantContext):
        raise ContextMissingError
    return raw


def apply_context(session: Session, ctx: TenantContext) -> None:
    """Bind ``ctx`` to the session and set the GUCs in the current transaction.

    Works mid-transaction too: ``set_config(..., true)`` overrides the values for
    the rest of the transaction, which is how login switches from the platform
    scope to the tenant it just resolved.
    """
    session.info[CONTEXT_KEY] = ctx
    _ = session.execute(SET_CONTEXT_SQL, context_params(ctx))


def _set_context_on_begin(
    session: Session, transaction: SessionTransaction, connection: Connection
) -> None:
    if transaction.nested:
        return  # savepoints inherit the enclosing transaction's settings
    raw: object = session.info.get(CONTEXT_KEY)
    ctx = raw if isinstance(raw, TenantContext) else current_context.get()
    if ctx is None:
        # Connection is already registered; invalidate so the whole transaction refuses.
        connection.invalidate()
        raise ContextMissingError
    session.info[CONTEXT_KEY] = ctx
    _ = connection.execute(SET_CONTEXT_SQL, context_params(ctx))


event.listen(Session, "after_begin", _set_context_on_begin)


@contextmanager
def tenant_session(
    ctx: TenantContext, *, factory: SessionFactory | None = None
) -> Generator[Session]:
    """Yield a session bound to ``ctx``: commits on success, rolls back on error.

    Yields:
        The session, with ``ctx`` applied to its current transaction.

    """
    session = (factory or get_sessionmaker())()
    try:
        with bind_context(ctx):
            apply_context(session, ctx)
            yield session
            session.commit()
    except BaseException:
        session.rollback()
        raise
    finally:
        session.close()
