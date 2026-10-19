import base64
import traceback
from collections.abc import Generator
from contextlib import contextmanager
from uuid import uuid4

import pytest
from psycopg import sql
from sqlalchemy import Engine, create_engine, event, text
from sqlalchemy.exc import DBAPIError, PendingRollbackError

from tests.helpers import platform_ctx, staff_ctx, system_ctx
from xolotl.platform import migrate
from xolotl.platform.config import get_settings
from xolotl.platform.db import (
    APP_ROLE,
    BootstrapError,
    RoleBypassError,
    apply_context,
    assert_app_role_safe,
    bound_context,
    get_sessionmaker,
    tenant_session,
)
from xolotl.platform.enums import Role
from xolotl.platform.errors import ContextMissingError
from xolotl.platform.tenancy.context import (
    bind_context,
    current_context,
    require_context,
)

GUCS = text(
    (
        "SELECT current_setting('app.tenant_id', true), "
        "current_setting('app.actor_type', true), "
        "current_setting('app.actor_id', true), "
        "current_setting('app.roles', true)"
    ),
)


@contextmanager
def recorded_statements(engine: Engine) -> Generator[list[str]]:
    """Collect every statement ``engine`` sends to the server meanwhile.

    Yields:
        The list the statements are appended to, in the order they run.

    """
    statements: list[str] = []

    def keep(conn, cursor, statement, parameters, context, executemany) -> None:
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", keep)
    try:
        yield statements
    finally:
        event.remove(engine, "before_cursor_execute", keep)


def _role_alterations(statements: list[str]) -> list[str]:
    # Every ALTER ROLE that ran, with its password literal cut off.
    return [
        s.partition(" PASSWORD ")[0] for s in statements if s.startswith("ALTER ROLE")
    ]


def test_engine_connects_as_the_application_role(engine: Engine):
    with engine.connect() as conn:
        assert conn.scalar(text("SELECT current_user")) == APP_ROLE


def test_engines_keep_bound_parameters_out_of_error_text(
    engines: tuple[Engine, Engine],
):
    for engine in engines:
        with pytest.raises(DBAPIError) as caught, engine.connect() as conn:
            _ = conn.execute(text("SELECT CAST(:value AS uuid)"), {"value": "a@b.mx"})
        assert "[parameters:" not in str(caught.value)


def test_session_refuses_to_start_without_context(engine: Engine):
    with get_sessionmaker()() as session, pytest.raises(ContextMissingError):
        _ = session.execute(text("SELECT 1"))


def test_session_without_context_refuses_every_statement(engine: Engine):
    with get_sessionmaker()() as session:
        with pytest.raises(ContextMissingError):
            _ = session.execute(text("SELECT 1"))
        # The failed begin must poison the transaction, not just the first statement.
        with pytest.raises((PendingRollbackError, ContextMissingError)):
            _ = session.execute(text("SELECT 1"))
        session.rollback()
        ctx = system_ctx(uuid4())
        with bind_context(ctx):
            assert session.scalar(
                text("SELECT current_setting('app.tenant_id', true)")
            ) == str(ctx.tenant_id)


def test_gucs_are_set_on_every_transaction(engine: Engine):
    tenant_id = uuid4()
    ctx = staff_ctx(tenant_id, actor_id="staff:1", roles=[Role.MLRO, Role.ADMIN])
    with tenant_session(ctx) as s:
        assert tuple(s.execute(GUCS).one()) == (
            str(tenant_id),
            "STAFF",
            "staff:1",
            ",ADMIN,MLRO,",
        )
        s.commit()  # ends the transaction; the next statement autobegins
        assert s.scalar(text("SELECT current_setting('app.tenant_id', true)")) == str(
            tenant_id
        )


def test_gucs_do_not_leak_into_a_fresh_connection(engine: Engine):
    with tenant_session(system_ctx(uuid4())) as s:
        _ = s.execute(text("SELECT 1"))
    with engine.connect() as conn:
        assert conn.scalar(text("SELECT current_setting('app.tenant_id', true)")) in {
            None,
            "",
        }


def test_platform_operator_predicate_is_null_not_an_error(engine: Engine):
    with tenant_session(platform_ctx()) as s:
        assert tuple(s.execute(GUCS).one()) == ("", "PLATFORM_OPERATOR", "cli:test", "")
        assert (
            s.scalar(
                text(
                    (
                        "SELECT NULLIF(current_setting('app.tenant_id', true), '')"
                        "::uuid IS NULL"
                    ),
                )
            )
            is True
        )


def test_apply_context_switches_tenant_mid_transaction(engine: Engine):
    a, b = uuid4(), uuid4()
    with tenant_session(system_ctx(a)) as s:
        _ = s.execute(text("SELECT 1"))
        apply_context(s, system_ctx(b))
        assert s.scalar(text("SELECT current_setting('app.tenant_id', true)")) == str(b)
        assert bound_context(s).tenant_id == b


def test_tenant_session_binds_the_contextvar_and_rolls_back_on_error(engine: Engine):
    ctx = system_ctx(uuid4())
    with tenant_session(ctx) as s:
        assert require_context() is ctx
        assert bound_context(s) is ctx
    assert current_context.get() is None
    with pytest.raises(RuntimeError, match="boom"), tenant_session(ctx) as s:
        _ = s.execute(text("SELECT 1"))
        msg = "boom"
        raise RuntimeError(msg)
    assert current_context.get() is None


def test_bound_context_requires_a_context(engine: Engine):
    with get_sessionmaker()() as session, pytest.raises(ContextMissingError):
        _ = bound_context(session)


def test_bootstrap_is_idempotent_and_the_role_cannot_bypass_rls(admin_engine: Engine):
    migrate.bootstrap()
    with recorded_statements(admin_engine) as statements:
        migrate.bootstrap()
    # The role already satisfies the invariants: only LOGIN and the password are set.
    assert _role_alterations(statements) == [f'ALTER ROLE "{APP_ROLE}" WITH LOGIN']
    with admin_engine.connect() as conn:
        row = conn.execute(
            text(
                (
                    "SELECT rolbypassrls, rolsuper, rolcanlogin FROM pg_roles "
                    "WHERE rolname = :role"
                ),
            ),
            {"role": APP_ROLE},
        ).one()
    assert tuple(row) == (False, False, True)


def test_bootstrap_demotes_an_app_role_that_became_a_superuser(admin_engine: Engine):
    with admin_engine.begin() as conn:
        _ = conn.execute(text(f"ALTER ROLE {APP_ROLE} SUPERUSER BYPASSRLS"))
    try:
        migrate.bootstrap()
        with admin_engine.connect() as conn:
            flags = conn.execute(
                text("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = :r"),
                {"r": APP_ROLE},
            ).one()
        assert tuple(flags) == (False, False)
    finally:
        with admin_engine.begin() as conn:
            _ = conn.execute(text(f"ALTER ROLE {APP_ROLE} NOSUPERUSER NOBYPASSRLS"))


def test_bootstrap_refuses_an_app_role_that_can_act_as_the_owner(
    admin_engine: Engine,
):
    with admin_engine.begin() as conn:
        owner = conn.scalar(text("SELECT current_user"))
        _ = conn.execute(text(f'GRANT "{owner}" TO {APP_ROLE}'))
    try:
        with pytest.raises(RuntimeError, match="member of the owner role"):
            migrate.bootstrap()
    finally:
        with admin_engine.begin() as conn:
            _ = conn.execute(text(f'REVOKE "{owner}" FROM {APP_ROLE}'))


def test_bootstrap_through_a_createrole_owner_alters_only_login_and_password(
    managed_owner: tuple[Engine, str],
):
    owner, app_url = managed_owner
    with recorded_statements(owner) as statements:
        migrate.bootstrap()
    assert _role_alterations(statements) == [f'ALTER ROLE "{APP_ROLE}" WITH LOGIN']
    with owner.connect() as conn:
        superuser = conn.scalar(
            text("SELECT rolsuper FROM pg_roles WHERE rolname = current_user")
        )
        migrate.verify_app_role(conn)  # the invariants hold for this owner too
    assert superuser is False
    app = create_engine(app_url)
    try:
        with app.connect() as conn:  # and the app role signs in to its database
            assert conn.scalar(text("SELECT current_user")) == APP_ROLE
    finally:
        app.dispose()


@pytest.mark.parametrize("attribute", ["SUPERUSER", "BYPASSRLS"])
def test_bootstrap_names_an_attribute_the_owner_cannot_remove(
    admin_engine: Engine, managed_owner: tuple[Engine, str], attribute: str
):
    with admin_engine.begin() as conn:  # the suite's superuser owner grants it
        _ = conn.execute(text(f"ALTER ROLE {APP_ROLE} {attribute}"))
    try:
        with pytest.raises(BootstrapError) as caught:
            migrate.bootstrap()
    finally:
        with admin_engine.begin() as conn:
            _ = conn.execute(text(f"ALTER ROLE {APP_ROLE} NOSUPERUSER NOBYPASSRLS"))
    assert str(caught.value) == (
        f"could not remove {attribute} from role {APP_ROLE} (sqlstate 42501)"
    )


def test_a_refused_role_alter_keeps_the_password_out_of_the_error(
    owner_without_admin_option: str,
):
    with pytest.raises(BootstrapError) as caught:
        migrate.bootstrap()
    error = caught.value
    assert str(error) == (
        f"could not set LOGIN and the password of role {APP_ROLE} (sqlstate 42501)"
    )
    assert owner_without_admin_option in str(error.__context__)  # the driver's did
    shown = [str(error), repr(error), *traceback.format_exception(error)]
    assert [text for text in shown if owner_without_admin_option in text] == []


@pytest.mark.parametrize(
    ("setup", "match"),
    [
        (["CREATE ROLE {probe} SUPERUSER"], "row-level security: SUPERUSER$"),
        (["CREATE ROLE {probe} BYPASSRLS"], "row-level security: BYPASSRLS$"),
        (
            ["CREATE ROLE {probe}", 'GRANT "{owner}" TO {probe}'],
            "row-level security: member of the owner role$",
        ),
    ],
    ids=["superuser", "bypassrls", "owner-member"],
)
def test_role_verification_refuses_every_way_round_row_security(
    admin_engine: Engine, setup: list[str], match: str
):
    probe = "xolotl_probe_role"
    with admin_engine.begin() as conn:
        owner = conn.scalar(text("SELECT current_user"))
        for statement in setup:
            _ = conn.execute(text(statement.format(probe=probe, owner=owner)))
    try:
        with admin_engine.connect() as conn, pytest.raises(RuntimeError, match=match):
            migrate.verify_app_role(conn, role=probe)
    finally:
        with admin_engine.begin() as conn:
            _ = conn.execute(text(f"DROP ROLE {probe}"))


def test_the_runtime_role_check_passes_the_app_role_and_refuses_a_superuser(
    engines: tuple[Engine, Engine],
):
    app_engine, admin_engine = engines
    assert_app_role_safe(app_engine)  # xolotl_app: neither superuser nor BYPASSRLS
    with pytest.raises(RoleBypassError, match="bypass row-level security"):
        assert_app_role_safe(admin_engine)  # the suite's owner role is a superuser


def test_literal_ddl_helper_keeps_percent_signs(admin_engine: Engine):
    statement = sql.SQL("CREATE TEMP TABLE pct AS SELECT {} AS v").format(
        sql.Literal("p%ss")
    )
    with admin_engine.connect() as conn:
        migrate.exec_literal_ddl(conn, statement)
        assert conn.scalar(text("SELECT v FROM pct")) == "p%ss"


def test_reset_refuses_in_prod(monkeypatch):
    monkeypatch.setenv("XOLOTL_ENV", "prod")
    monkeypatch.setenv("XOLOTL_JWT_SECRET", "p" * 40)
    monkeypatch.setenv("XOLOTL_MASTER_KEY", base64.b64encode(b"p" * 32).decode())
    get_settings.cache_clear()
    try:
        with pytest.raises(RuntimeError, match="prod"):
            migrate.reset()
    finally:
        get_settings.cache_clear()


def test_platform_context_has_no_tenant_for_tenant_scoped_checks(engine: Engine):
    with tenant_session(platform_ctx()) as s, pytest.raises(ContextMissingError):
        _ = bound_context(s).require_tenant_id()
