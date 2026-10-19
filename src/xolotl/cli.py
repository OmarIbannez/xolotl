"""The ``xolotl`` command line (spec §10)."""

import getpass
from collections.abc import Generator
from contextlib import contextmanager
from typing import Annotated
from uuid import UUID

import typer
import uvicorn
from sqlalchemy import select

from xolotl.app import build_key_provider
from xolotl.modules.tenants import service
from xolotl.modules.tenants.models import Tenant
from xolotl.platform import migrate
from xolotl.platform.audit import verify_chain
from xolotl.platform.config import get_settings
from xolotl.platform.crypto import get_key_provider, set_key_provider
from xolotl.platform.crypto.provider import DEFAULT_CACHE_TTL
from xolotl.platform.db import (
    APP_ROLE,
    BootstrapError,
    RoleBypassError,
    assert_app_role_safe,
    get_engine,
    get_sessionmaker,
    tenant_session,
)
from xolotl.platform.enums import KeyPurpose, TenantEnv
from xolotl.platform.errors import NotFoundError, XolotlError
from xolotl.platform.logging import configure_logging
from xolotl.platform.tenancy.context import TenantContext

app = typer.Typer(no_args_is_help=True, help="xolotl: multi-tenant KYC/AML platform")
db_app = typer.Typer(no_args_is_help=True, help="Database bootstrap and migrations")
tenant_app = typer.Typer(no_args_is_help=True, help="Tenant administration")
audit_app = typer.Typer(no_args_is_help=True, help="Audit chain tools")
keys_app = typer.Typer(
    no_args_is_help=True, help="Tenant data keys: rotation and destruction"
)
app.add_typer(db_app, name="db")
app.add_typer(tenant_app, name="tenant")
app.add_typer(audit_app, name="audit")
app.add_typer(keys_app, name="keys")

_IRREVERSIBLE = (
    "irreversible: everything encrypted for the tenant becomes unreadable for good"
)


def _cli_actor() -> str:
    return f"cli:{getpass.getuser()}"


def _ensure_key_provider() -> None:
    """Install the key provider if this process has none yet.

    The application installs it in ``build_app``, which ``serve`` runs in the server
    process. A command that creates or opens encrypted data runs in a process that
    never builds the app, so it calls this first (``tenant create`` does, and so
    should any command that rotates or destroys keys). A provider that is already
    installed is kept: one per process, one key cache.
    """
    try:
        _ = get_key_provider()
    except RuntimeError:
        set_key_provider(build_key_provider(get_settings(), get_sessionmaker()))


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


def _resolve_tenant(actor: str, slug: str) -> tuple[UUID, str]:
    """Look a tenant up by slug as the platform operator.

    Returns:
        The tenant's id and its slug as stored.

    Raises:
        NotFoundError: When no tenant has that slug.

    """
    with tenant_session(TenantContext.platform(actor)) as s:
        row = s.scalar(select(Tenant).where(Tenant.slug == slug.strip().lower()))
        if row is None:
            msg = f"no tenant {slug!r}"
            raise NotFoundError(msg)
        return row.id, row.slug


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
    """Apply migrations up to head, logging what they do like ``serve`` does."""
    configure_logging(get_settings().log_level)
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
    """Create a tenant, its keys and its first administrator."""
    with _cli_errors():
        _ensure_key_provider()
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
    with _cli_errors():
        tenant_id, _ = _resolve_tenant(actor, tenant)
    with tenant_session(TenantContext.platform(actor, tenant_id=tenant_id)) as s:
        result = verify_chain(s)
    typer.echo(result.model_dump_json(indent=2))
    if not result.valid:
        raise typer.Exit(code=1)


@keys_app.command("rotate")
def keys_rotate(
    *,
    tenant: Annotated[str, typer.Option("--tenant", help="Tenant slug")],
    # INDEX parses on purpose: the provider refuses it with the CONFLICT line and exit
    # status 1, but the help does not offer it.
    purpose: Annotated[
        KeyPurpose,
        typer.Option(
            "--purpose",
            case_sensitive=False,
            metavar="DATA|BIOMETRIC",
            help="Which key to rotate",
        ),
    ],
) -> None:
    """Add a new version of a tenant's key; values sealed earlier stay readable.

    Values written from now on use the new version; running servers switch to it
    within a few minutes, when the key they cached expires. Nothing is re-encrypted.
    """
    actor = _cli_actor()
    with _cli_errors():
        tenant_id, slug = _resolve_tenant(actor, tenant)
        _ensure_key_provider()
        with tenant_session(TenantContext.platform(actor, tenant_id=tenant_id)) as s:
            version = service.rotate_tenant_key(s, purpose)
    typer.echo(
        f"rotated the {purpose.value} key of tenant {slug}: now version {version}"
    )


@keys_app.command("destroy")
def keys_destroy(
    *,
    tenant: Annotated[str, typer.Option("--tenant", help="Tenant slug")],
    yes: Annotated[
        bool, typer.Option("--yes", help="Confirm the irreversible destruction")
    ] = False,
) -> None:
    """Destroy every key of a tenant for good (crypto-shredding). Irreversible.

    Everything encrypted for the tenant becomes unreadable and its keys can
    never be created again. The destruction is audited.

    Raises:
        Exit: With code 2 when --yes is missing.

    """
    if not yes:
        typer.echo(
            f"refusing to destroy the keys of tenant {tenant.strip()} without --yes",
            err=True,
        )
        typer.echo(f"this is {_IRREVERSIBLE}", err=True)
        raise typer.Exit(code=2)
    actor = _cli_actor()
    with _cli_errors():
        tenant_id, slug = _resolve_tenant(actor, tenant)
        plan = f"destroying every encryption key of tenant {slug}"
        # To stderr with the other diagnostics: stdout carries only a result, and a
        # destroy that fails has none.
        typer.echo(f"{plan} ({_IRREVERSIBLE})", err=True)
        _ensure_key_provider()
        with tenant_session(TenantContext.platform(actor, tenant_id=tenant_id)) as s:
            service.destroy_tenant_keys(s)
    minutes = DEFAULT_CACHE_TTL.total_seconds() / 60
    typer.echo(f"destroyed the encryption keys of tenant {slug}")
    typer.echo(
        f"servers that cached keys in memory stop using them within {minutes:g} minutes"
    )
    typer.echo("backups and archived WAL taken earlier still hold the wrapped keys")
    typer.echo(
        "while XOLOTL_MASTER_KEY is unchanged, a restore from them brings keys back"
    )
