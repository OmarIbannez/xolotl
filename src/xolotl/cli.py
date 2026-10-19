"""The ``xolotl`` command line (spec §10)."""

import getpass
from collections.abc import Generator
from contextlib import contextmanager
from typing import Annotated

import typer
import uvicorn
from sqlalchemy import select

from xolotl.modules.tenants import service
from xolotl.modules.tenants.models import Tenant
from xolotl.platform import migrate
from xolotl.platform.audit import verify_chain
from xolotl.platform.config import get_settings
from xolotl.platform.db import (
    APP_ROLE,
    BootstrapError,
    RoleBypassError,
    assert_app_role_safe,
    get_engine,
    get_sessionmaker,
    tenant_session,
)
from xolotl.platform.enums import TenantEnv
from xolotl.platform.errors import XolotlError
from xolotl.platform.logging import configure_logging
from xolotl.platform.tenancy.context import TenantContext

app = typer.Typer(no_args_is_help=True, help="xolotl: multi-tenant KYC/AML platform")
db_app = typer.Typer(no_args_is_help=True, help="Database bootstrap and migrations")
tenant_app = typer.Typer(no_args_is_help=True, help="Tenant administration")
audit_app = typer.Typer(no_args_is_help=True, help="Audit chain tools")
app.add_typer(db_app, name="db")
app.add_typer(tenant_app, name="tenant")
app.add_typer(audit_app, name="audit")


def _cli_actor() -> str:
    return f"cli:{getpass.getuser()}"


@contextmanager
def _cli_errors() -> Generator[None]:
    # The errors a user can act on: one "error:" line on stderr and exit status 1.
    try:
        yield
    except XolotlError as exc:
        typer.echo(f"error: {exc.code}: {exc.detail}", err=True)
        raise typer.Exit(code=1) from exc
    except (RoleBypassError, BootstrapError) as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=1) from exc


@app.command()
def serve(
    *,
    reload: Annotated[
        bool, typer.Option(help="Auto-reload on code changes (development)")
    ] = False,
    port: Annotated[
        int | None, typer.Option(help="Port (defaults to XOLOTL_PORT)")
    ] = None,
) -> None:
    """Run pending migrations, check the app role, then serve the API.

    Exits 1 without serving when the app role can bypass row-level security.
    uvicorn runs with ``log_config=None``, so its loggers propagate to the JSON
    root handler that ``configure_logging`` installs, redaction filter included.
    """
    settings = get_settings()
    configure_logging(settings.log_level)
    migrate.upgrade()
    with _cli_errors():
        assert_app_role_safe(get_engine())
    uvicorn.run(
        "xolotl.app:build_app",
        factory=True,
        host="0.0.0.0",  # ruff: ignore[hardcoded-bind-all-interfaces]  # container entrypoint
        port=port or settings.port,
        reload=reload,
        log_config=None,
    )


@db_app.command("bootstrap")
def db_bootstrap() -> None:
    """Create the application role and its grants (idempotent).

    Exits 1 with a one-line error when the role could bypass row-level security or
    the database refuses to alter it.
    """
    with _cli_errors():
        migrate.bootstrap()
    typer.echo(f"role {APP_ROLE} is ready")


@db_app.command("upgrade")
def db_upgrade() -> None:
    """Apply migrations up to head."""
    migrate.upgrade()
    typer.echo("database upgraded to head")


@db_app.command("reset")
def db_reset(
    *,
    yes: Annotated[
        bool, typer.Option("--yes", help="Confirm dropping every table")
    ] = False,
) -> None:
    """Drop the public schema and migrate from scratch (never in prod).

    Raises:
        Exit: With code 2 when ``--yes`` is missing.

    """
    if not yes:
        typer.echo("refusing to reset without --yes", err=True)
        raise typer.Exit(code=2)
    migrate.reset()
    typer.echo("database reset and migrated")


@tenant_app.command("create")
def tenant_create(
    *,
    slug: Annotated[str, typer.Option(help="Workspace slug, e.g. acme-fintech")],
    legal_name: Annotated[str, typer.Option(help="Legal name of the obligated entity")],
    admin_email: Annotated[str, typer.Option(help="Email of the first ADMIN user")],
    country: Annotated[str, typer.Option(help="ISO 3166-1 alpha-2")] = "MX",
    cell: Annotated[str, typer.Option(help="Deployment cell")] = "mx-1",
    env: Annotated[TenantEnv, typer.Option(help="SANDBOX or PROD")] = TenantEnv.SANDBOX,
) -> None:
    """Create a tenant and its first administrator."""
    with _cli_errors():
        created = service.create_tenant(
            get_sessionmaker(),
            slug=slug,
            legal_name=legal_name,
            country_code=country,
            admin_email=admin_email,
            env=env,
            cell=cell,
            actor_id=_cli_actor(),
        )
    typer.echo(f"tenant {created.slug} created ({created.tenant_id})")
    typer.echo(f"admin {created.admin_email}")
    typer.echo(f"one-time password: {created.admin_password}")
    typer.echo("Shown once; it must be changed at first login.")


@tenant_app.command("list")
def tenant_list() -> None:
    """List tenants as slug, env, status and id."""
    with tenant_session(TenantContext.platform(_cli_actor())) as s:
        for tenant in service.list_tenants(s):
            typer.echo(
                f"{tenant.slug}\t{tenant.env.value}\t{tenant.status.value}\t{tenant.id}"
            )


@audit_app.command("verify")
def audit_verify(
    *,
    tenant: Annotated[str, typer.Option("--tenant", help="Tenant slug")],
) -> None:
    """Verify a tenant's audit chain; exit 1 when it is broken.

    Raises:
        Exit: With code 1 when the tenant is unknown or its chain is broken.

    """
    actor = _cli_actor()
    with tenant_session(TenantContext.platform(actor)) as s:
        row = s.scalar(select(Tenant).where(Tenant.slug == tenant.strip().lower()))
        if row is None:
            typer.echo(f"error: NOT_FOUND: no tenant {tenant!r}", err=True)
            raise typer.Exit(code=1)
        tenant_id = row.id
    with tenant_session(TenantContext.platform(actor, tenant_id=tenant_id)) as s:
        result = verify_chain(s)
    typer.echo(result.model_dump_json(indent=2))
    if not result.valid:
        raise typer.Exit(code=1)
