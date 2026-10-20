"""The ``xolotl`` command line (spec §10)."""

import getpass
import json
import logging
import os
from collections.abc import Generator
from contextlib import contextmanager
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Annotated
from uuid import UUID

import psycopg
import typer
import uvicorn
from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from xolotl.app import build_key_provider, build_object_store, build_worker
from xolotl.modules.party.retention import RetentionSweeper
from xolotl.modules.screening import evaluation_report
from xolotl.modules.screening.catalog import (
    SOURCES_FILE,
    HttpListFetcher,
    IngestReport,
    ListSource,
    ListVersion,
    PollOutcome,
    SeedReport,
    active_versions,
    claim_source,
    ingest_file,
    poll_source,
    read_sources,
    seed_list_sources,
)
from xolotl.modules.screening.engine.enums import BatchKind, RunMode, TriggerType
from xolotl.modules.screening.engine.holder import (
    ScreeningIndexHolder,
    get_index_holder,
    set_index_holder,
)
from xolotl.modules.screening.engine.replay import ReplayReport, replay_run
from xolotl.modules.screening.engine.screen import Trigger, screen
from xolotl.modules.screening.engine.subjects import subject_for_party
from xolotl.modules.screening.enums import VersionStatus
from xolotl.modules.screening.errors import ListMassRemovalError
from xolotl.modules.screening.lists import ListFormatError
from xolotl.modules.screening.rescreen.walkers import start_rescreen
from xolotl.modules.tenants import service
from xolotl.modules.tenants.models import Tenant
from xolotl.platform import clock, migrate
from xolotl.platform.audit import verify_chain
from xolotl.platform.calendar import read_holiday_file, seed_holidays
from xolotl.platform.config import DEV_MASTER_KEY_BYTES, Settings, get_settings
from xolotl.platform.crypto import get_key_provider, set_key_provider
from xolotl.platform.crypto.provider import DEFAULT_CACHE_TTL
from xolotl.platform.db import (
    APP_ROLE,
    BootstrapError,
    RoleBypassError,
    assert_app_role_safe,
    get_admin_engine,
    get_engine,
    get_sessionmaker,
    tenant_session,
)
from xolotl.platform.enums import HolidayScope, KeyPurpose, TenantEnv
from xolotl.platform.errors import NotFoundError, ValidationFailedError, XolotlError
from xolotl.platform.events import InAppNotifier
from xolotl.platform.jobs import PostgresJobQueue
from xolotl.platform.logging import configure_logging
from xolotl.platform.policy import (
    PolicyPack,
    PolicyPackVersion,
    content_hash,
    install_pack,
    load_pack,
    pack_files,
    unverified_paths,
)
from xolotl.platform.tenancy.context import TenantContext
from xolotl.platform.units import (
    UNITS,
    BanxicoSieSource,
    SeedFileSource,
    UnitsFetchJob,
    fetch_missing,
    latest_valid_from,
    unit_spec,
    upsert_value,
)

app = typer.Typer(no_args_is_help=True, help="xolotl: multi-tenant KYC/AML platform")
db_app = typer.Typer(no_args_is_help=True, help="Database bootstrap and migrations")
tenant_app = typer.Typer(no_args_is_help=True, help="Tenant administration")
audit_app = typer.Typer(no_args_is_help=True, help="Audit chain tools")
keys_app = typer.Typer(
    no_args_is_help=True, help="Tenant data keys: rotation and destruction"
)
holidays_app = typer.Typer(no_args_is_help=True, help="Regulatory calendar")
packs_app = typer.Typer(no_args_is_help=True, help="Policy packs")
app.add_typer(db_app, name="db")
app.add_typer(tenant_app, name="tenant")
app.add_typer(audit_app, name="audit")
app.add_typer(keys_app, name="keys")
app.add_typer(holidays_app, name="holidays")
app.add_typer(packs_app, name="packs")
units_app = typer.Typer(
    no_args_is_help=True, help="Monetary units: seeds and Banxico fetches"
)
app.add_typer(units_app, name="units")
party_app = typer.Typer(no_args_is_help=True, help="Party retention")
app.add_typer(party_app, name="party")
lists_app = typer.Typer(no_args_is_help=True, help="Shared sanctions lists")
app.add_typer(lists_app, name="lists")
screening_app = typer.Typer(no_args_is_help=True, help="Screening")
app.add_typer(screening_app, name="screening")

# The library's evaluation corpus in a source checkout; absent from the image.
EVALUATION_CORPUS = (
    Path(__file__).resolve().parents[2] / "tests" / "modules" / "screening" / "corpus"
)

_IRREVERSIBLE = (
    "irreversible: everything encrypted for the tenant becomes unreadable for good"
)

logger = logging.getLogger(__name__)


def _cli_actor() -> str:
    return f"cli:{getpass.getuser()}"


def _report_object_store(root: Path) -> None:
    """Log whether this process can create files under the object store's root.

    A host's persistent disk may mount for another user than the image's (Render
    documents nothing about disks and non-root images), and nothing writes an object
    at start, so the log says it before the first upload finds out. The nearest
    directory that exists decides; nothing is created. The warning names that
    directory and says what to do, so an operator reading the log needs no manual.
    """
    existing = root.absolute()
    while not existing.exists():
        existing = existing.parent
    where = {"object_store_root": str(root)}
    if os.access(existing, os.W_OK | os.X_OK):
        logger.info("object store root is writable", extra=where)
        return
    hint = (
        "nothing is written at start, but uploads will fail: in the service's shell "
        "compare 'id' with 'ls -ldn' of that directory, and make it writable by this "
        "uid or one of these gids; see docs/deploy-render.md, Disk permissions"
    )
    logger.warning(
        "object store root is not writable by this process",
        extra={
            **where,
            "uid": os.getuid(),
            "gids": os.getgroups(),
            "decided_by": str(existing),
            "hint": hint,
        },
    )


def _warn_about_the_dev_master_key(settings: Settings) -> None:
    """Log one warning when the public development master key wraps the tenant keys.

    ``XOLOTL_ENV=prod`` refuses that key when the settings load, but the environment
    defaults to ``dev``: a deployment that forgets ``XOLOTL_ENV`` and
    ``XOLOTL_MASTER_KEY`` would otherwise start without a word and create every
    tenant's keys under a key that anyone with the repository has. Nothing re-wraps
    keys afterwards, so the time to hear about it is before the first real tenant.
    The key itself is never logged.
    """
    # Compared as bytes, as the production refusal does: one key has more than one
    # base64 spelling.
    if settings.env != "prod" and settings.master_key_bytes == DEV_MASTER_KEY_BYTES:
        msg = (
            "the public development master key is in use; every tenant's keys are "
            "readable to anyone with the repository; set XOLOTL_MASTER_KEY before "
            "creating real tenants"
        )
        logger.warning(msg)


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


def _connection_failure(exc: OperationalError) -> str | None:
    """Say why the database could not be reached, or return None for any other error.

    The driver's cause is ``exc.orig``. A ``psycopg.OperationalError`` without a
    SQLSTATE is libpq's own report rather than an error a server sent to a running
    session: the connection was refused, the host is unreachable, the connect
    timeout expired, the server answered the attempt with a FATAL (a wrong password,
    a missing database) or the connection was lost mid-way. The reason is the first
    line of its text with the ``connection failed: `` libpq puts first removed; it
    reads ``connection to server at "127.0.0.1", port 1 failed: Connection refused``
    (a hint line follows) or ``connection timeout expired``, which goes on to name
    the wait and the setting that sets it, since nothing else in that line says the
    host was never reached. An ``OperationalError`` a server sent to a running
    session carries a SQLSTATE and keeps its traceback.

    Returns:
        The reason, or None when the error is not a failure to reach the database.

    """
    cause = exc.orig
    if not isinstance(cause, psycopg.OperationalError) or cause.sqlstate is not None:
        return None
    reason = str(cause).partition("\n")[0].removeprefix("connection failed: ")
    if isinstance(cause, psycopg.errors.ConnectionTimeout):
        seconds = get_settings().database_connect_timeout_seconds
        reason += f" after {seconds} s (XOLOTL_DATABASE_CONNECT_TIMEOUT_SECONDS)"
    return reason


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
    except OperationalError as exc:
        # A database that cannot be reached; an error a server sent to a running
        # session keeps its traceback, which says what went wrong once the database
        # was reached.
        reason = _connection_failure(exc)
        if reason is None:
            raise
        typer.echo(f"error: cannot connect to the database: {reason}", err=True)
        raise typer.Exit(code=1) from exc


def _seed_list_sources(path: Path = SOURCES_FILE) -> str:
    """Seed the shared list catalog from a sources file as the database owner.

    Only the owner role writes the catalog columns of ``list_source``; ``db
    upgrade`` and ``serve`` run this right after migrating, so a URL change ships
    as a change to ``sources.yaml``.

    Returns:
        The one-line report, such as ``list sources: 5 inserted, 0 updated, ...``.

    """
    specs = read_sources(path)
    owner = sessionmaker(bind=get_admin_engine(), expire_on_commit=False)
    with tenant_session(TenantContext.platform(_cli_actor()), factory=owner) as s:
        report: SeedReport = seed_list_sources(s, specs)
    return (
        f"list sources: {report.inserted} inserted, {report.updated} updated, "
        f"{report.unchanged} unchanged, {report.disabled} disabled"
    )


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
    """Bootstrap the app role if needed, run pending migrations, check it, serve.

    When XOLOTL_DATABASE_ADMIN_URL is set and the app role cannot sign in (missing on
    a new database, or its password changed), ``db bootstrap`` runs first, so a host
    can deploy the image with no step of its own. Unless XOLOTL_EMBEDDED_WORKER=false,
    the API also runs the worker loop (events, jobs, schedules) on a thread of its
    own, from the application's startup, which comes after the migrations and the
    role check, to its shutdown. It logs whether it can write the object store's root
    (a persistent disk may not let it), and warns when the public development master
    key is in use (XOLOTL_ENV=prod refuses it). Exits 1 without serving when the app
    role can bypass row-level security, bootstrap fails or the database cannot be
    reached (one line on stderr, within the connect timeout). uvicorn runs with
    ``log_config=None``, so its loggers propagate to the JSON root handler that
    ``configure_logging`` installs, redaction filter included.
    """
    settings = get_settings()
    configure_logging(settings.log_level)
    _warn_about_the_dev_master_key(settings)
    with _cli_errors():
        _ = migrate.ensure_app_role()
        migrate.upgrade()
        logger.info(_seed_list_sources())
        assert_app_role_safe(get_engine())
    _report_object_store(settings.object_store_root)
    uvicorn.run(
        "xolotl.app:build_app",
        factory=True,
        host="0.0.0.0",  # ruff: ignore[hardcoded-bind-all-interfaces]  # container entrypoint
        port=port or settings.port,
        reload=reload,
        log_config=None,
    )


@app.command()
def worker() -> None:
    """Run the dispatcher, the job runner and the scheduler until SIGTERM or SIGINT.

    The loop the API runs itself unless XOLOTL_EMBEDDED_WORKER=false, as a process
    of its own. A signal ends it after the tick in progress, letting a running job
    finish. Exits 1 without running when the app role can bypass row-level security.
    """
    settings = get_settings()
    configure_logging(settings.log_level)
    with _cli_errors():
        assert_app_role_safe(get_engine())
    loop = build_worker()
    with loop.handle_signals():
        _ = loop.run()


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
    """Apply migrations up to head, then seed the shared list catalog.

    Logs what the migrations do like ``serve`` does. Exits 1 with one line on stderr
    when the database cannot be reached.
    """
    configure_logging(get_settings().log_level)
    with _cli_errors():
        migrate.upgrade()
        seeded = _seed_list_sources()
    typer.echo(seeded)
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
    with _cli_errors():
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
    with _cli_errors(), tenant_session(TenantContext.platform(_cli_actor())) as s:
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


@holidays_app.command("seed")
def holidays_seed(
    path: Annotated[
        Path | None,
        typer.Argument(help="Holidays file; default <packs_dir>/_holidays/MX.yaml"),
    ] = None,
) -> None:
    """Load a holidays file into the shared holiday table (idempotent).

    Runs as the database owner, the only role that writes the table. Says how many
    rows of each scope the file holds and how many still await the compliance
    review (``verify: true``). Exits 1 with one line on stderr when the file is
    missing or malformed.
    """
    source = path or get_settings().packs_dir / "_holidays" / "MX.yaml"
    owner = sessionmaker(bind=get_admin_engine(), expire_on_commit=False)
    with _cli_errors():
        rows = read_holiday_file(source)
        with tenant_session(TenantContext.platform(_cli_actor()), factory=owner) as s:
            seeded = seed_holidays(s, rows)
    countries = ", ".join(sorted({row.country_code for row in rows}))
    per_scope = ", ".join(
        f"{scope.value} {sum(1 for row in rows if row.scope is scope)}"
        for scope in HolidayScope
    )
    flagged = sum(1 for row in rows if row.verify)
    seeded_line = f"seeded {seeded} holidays for {countries} ({per_scope})"
    typer.echo(f"{seeded_line}; {flagged} dates still marked verify")


@units_app.command("seed")
def units_seed(
    seed_dir: Annotated[
        Path | None,
        typer.Argument(help="Seed directory; default <packs_dir>/_units"),
    ] = None,
    *,
    allow_unverified: Annotated[
        bool,
        typer.Option(
            "--allow-unverified", help="Load UNVERIFIED rows too (refused in prod)"
        ),
    ] = False,
) -> None:
    """Load every unit's seed file into the shared unit_value table (idempotent).

    A changed value becomes a correction; an unchanged one is left alone. Exits 1
    with one line on stderr when a file holds an UNVERIFIED row and the flag (or
    ``XOLOTL_ALLOW_UNVERIFIED_PACK_VALUES``) is not given.

    Raises:
        Exit: With code 2 when ``--allow-unverified`` is given in prod.

    """
    settings = get_settings()
    if allow_unverified and settings.env == "prod":
        typer.echo(
            "error: --allow-unverified is refused when XOLOTL_ENV=prod", err=True
        )
        raise typer.Exit(code=2)
    source = SeedFileSource(
        seed_dir or settings.packs_dir / "_units",
        allow_unverified=allow_unverified or settings.allow_unverified_pack_values,
    )
    lines: list[str] = []
    with _cli_errors(), tenant_session(TenantContext.platform(_cli_actor())) as s:
        for code in UNITS:
            rows = source.fetch(code, date.min, date.max)
            now = clock.now()
            new = sum(
                1 for row in rows if upsert_value(s, row, fetched_at=now) is not None
            )
            lines.append(
                f"{code}: {len(rows)} rows ({new} new, {len(rows) - new} unchanged)"
            )
    for line in lines:
        typer.echo(line)


def _parse_day(option: str, value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        typer.echo(f"error: {option} must be a date (YYYY-MM-DD)", err=True)
        raise typer.Exit(code=2) from exc


@units_app.command("fetch")
def units_fetch(
    unit: Annotated[str, typer.Option("--unit", help="Unit code: UDI or USD_MXN_FIX")],
    start: Annotated[
        str | None, typer.Option("--from", help="First day, YYYY-MM-DD")
    ] = None,
    end: Annotated[
        str | None, typer.Option("--to", help="Last day, YYYY-MM-DD")
    ] = None,
) -> None:
    """Fetch a unit's values from Banxico SIE (demo mode without a token).

    Without ``--from`` and ``--to`` it fetches what the store lacks, as the daily
    job does. Exits 1 with one line on stderr when the source fails or the unit is
    unknown.

    Raises:
        Exit: With code 2 when only one of ``--from`` and ``--to`` is given.

    """
    if (start is None) != (end is None):
        typer.echo("error: give both --from and --to, or neither", err=True)
        raise typer.Exit(code=2)
    settings = get_settings()
    source = BanxicoSieSource(
        settings.banxico_token,
        seed_dir=settings.packs_dir / "_units",
        env=settings.env,
    )
    window = (
        None
        if start is None or end is None
        else (_parse_day("--from", start), _parse_day("--to", end))
    )
    with _cli_errors(), tenant_session(TenantContext.platform(_cli_actor())) as s:
        _ = unit_spec(unit)
        if window is None:
            job = UnitsFetchJob(source, notifier=InAppNotifier(), clock_fn=clock.now)
            window = job.window(s, unit)
        if window is None:
            latest = latest_valid_from(s, unit)
            line = f"{unit}: up to date (latest {latest})"
        else:
            first, last = window
            report = fetch_missing(
                s, source, unit, start=first, end=last, now=clock.now()
            )
            line = (
                f"{unit}: fetched {report.fetched}, inserted {report.inserted}, "
                f"corrected {report.corrected} "
                f"({first.isoformat()}..{last.isoformat()}) [{source.mode}]"
            )
    typer.echo(line)


@party_app.command("purge-due")
def party_purge_due(
    *,
    tenant: Annotated[str, typer.Option("--tenant", help="Tenant slug")],
    dry_run: Annotated[
        bool,
        typer.Option(
            "--dry-run",
            help="Count the parties due for purge without changing anything",
        ),
    ] = False,
) -> None:
    """Purge the tenant's parties whose retention period has ended.

    A party on legal hold or with an open customer relationship is kept. This is
    the daily ``party.retention_sweep`` job, run by hand.
    """
    actor = _cli_actor()
    with _cli_errors():
        tenant_id, slug = _resolve_tenant(actor, tenant)
        _ensure_key_provider()
        store = build_object_store(get_settings())
        with tenant_session(TenantContext.platform(actor, tenant_id=tenant_id)) as s:
            count = RetentionSweeper(store).sweep(
                s, today=clock.now().date(), dry_run=dry_run
            )
    typer.echo(
        f"{count} parties of tenant {slug} due for purge"
        if dry_run
        else f"purged {count} parties of tenant {slug}"
    )


def _short_hash(digest: bytes) -> str:
    return f"sha256 {digest.hex()[:12]}"


@packs_app.command("load")
def packs_load(
    path: Annotated[
        Path | None,
        typer.Argument(help="A pack file or a directory of packs; default packs_dir"),
    ] = None,
    *,
    allow_unverified: Annotated[
        bool,
        typer.Option(
            "--allow-unverified", help="Install values still marked UNVERIFIED"
        ),
    ] = False,
) -> None:
    """Validate and install policy packs (idempotent; versions are immutable).

    Runs as the database owner, the only role that writes the pack tables. Every
    pack is validated before the first is installed, and all are installed in one
    transaction. Prints one ``installed`` or ``unchanged`` line per pack.

    Raises:
        Exit: With code 2 when UNVERIFIED values are allowed in prod.

    """
    settings = get_settings()
    allow = allow_unverified or settings.allow_unverified_pack_values
    if allow and settings.env == "prod":
        typer.echo("refusing to install UNVERIFIED pack values in prod", err=True)
        raise typer.Exit(code=2)
    source = path or settings.packs_dir
    files = pack_files(source) if source.is_dir() else [source]
    actor = _cli_actor()
    lines: list[str] = []
    with _cli_errors():
        configs = [(file, load_pack(file, allow_unverified=allow)) for file in files]
        owner = sessionmaker(bind=get_admin_engine(), expire_on_commit=False)
        with tenant_session(TenantContext.platform(actor), factory=owner) as s:
            for file, config in configs:
                before = s.scalar(
                    select(func.count())
                    .select_from(PolicyPackVersion)
                    .join(PolicyPack, PolicyPack.id == PolicyPackVersion.pack_id)
                    .where(
                        PolicyPack.code == config.pack,
                        PolicyPackVersion.version == config.version,
                    )
                )
                _ = install_pack(s, config, source_path=str(file), loaded_by=actor)
                verb = "unchanged" if before else "installed"
                digest = _short_hash(content_hash(config))
                lines.append(f"{verb} {config.pack} v{config.version} ({digest})")
    for line in lines:
        typer.echo(line)


@packs_app.command("validate")
def packs_validate(
    path: Annotated[Path, typer.Argument(help="The pack file to check")],
) -> None:
    """Validate one pack file without a database and name its UNVERIFIED values."""
    with _cli_errors():
        config = load_pack(path, allow_unverified=True)
    unverified = ", ".join(unverified_paths(config)) or "none"
    digest = _short_hash(content_hash(config))
    typer.echo(
        f"valid {config.pack} v{config.version} ({digest}); unverified: {unverified}"
    )


def _pack_version(session: Session, code: str, version: int) -> PolicyPackVersion:
    """Find an installed pack version by pack code and number.

    Returns:
        The version row.

    Raises:
        NotFoundError: When no such version is installed.

    """
    row = session.scalar(
        select(PolicyPackVersion)
        .join(PolicyPack, PolicyPack.id == PolicyPackVersion.pack_id)
        .where(PolicyPack.code == code, PolicyPackVersion.version == version)
    )
    if row is None:
        msg = f"no pack {code} version {version}"
        raise NotFoundError(msg)
    return row


@packs_app.command("show")
def packs_show(
    code: Annotated[str, typer.Argument(help="Pack code, such as MX/ITF-IFPE")],
    *,
    version: Annotated[int, typer.Option(help="The installed version to show")],
) -> None:
    """Print an installed pack version and its config as JSON."""
    with (
        _cli_errors(),
        tenant_session(TenantContext.platform(_cli_actor())) as s,
    ):
        row = _pack_version(s, code, version)
        shown = {
            "code": code,
            "version": row.version,
            "effective_from": row.effective_from.isoformat(),
            "content_hash": row.content_hash.hex(),
            "config": row.config,
        }
    typer.echo(json.dumps(shown, indent=2, ensure_ascii=False))


@lists_app.command("seed")
def lists_seed(
    path: Annotated[
        Path | None,
        typer.Argument(help="Sources file; default the shipped sources.yaml"),
    ] = None,
) -> None:
    """Upsert the shared list sources from the sources file (idempotent).

    Runs as the database owner, the only role that writes the catalog columns. A
    code no longer in the file is disabled, never deleted. Exits 1 with one line on
    stderr when the file is missing or malformed.
    """
    with _cli_errors():
        line = _seed_list_sources(path or SOURCES_FILE)
    typer.echo(line)


def _minute(at: datetime, now: datetime) -> str:
    # The time alone when it is today in UTC, else the date too.
    at = at.astimezone(UTC)
    if at.date() == now.date():
        return at.strftime("%H:%MZ")
    return at.strftime("%Y-%m-%dT%H:%MZ")


def _freshness(source: ListSource, now: datetime) -> str:
    if source.consecutive_failures > 0:
        code = (source.last_error or "UNKNOWN").partition(":")[0]
        return f"failing: {code} ({source.consecutive_failures})"
    stale_before = now - 2 * source.poll_interval
    if source.last_success_at is None or source.last_success_at < stale_before:
        return "stale"
    return "ok"


def _status_line(source: ListSource, active: ListVersion | None, now: datetime) -> str:
    version = (
        "no version"
        if active is None
        else f"v{active.seq}  {active.entry_count} entries"
    )
    success = (
        "never"
        if source.last_success_at is None
        else source.last_success_at.astimezone(UTC).strftime("%Y-%m-%dT%H:%MZ")
    )
    enabled = "enabled " if source.enabled else "disabled"
    return (
        f"{source.code:<15}  {enabled}  {version}  last success {success}  "
        f"next {_minute(source.next_poll_at, now)}  {_freshness(source, now)}"
    )


@lists_app.command("status")
def lists_status() -> None:
    """Print one line per shared list source: version, freshness and next poll.

    A source is stale when it has never succeeded or its last success is older than
    twice its poll interval; one whose last attempts failed shows the error code and
    the number of failures in a row.
    """
    now = clock.now().astimezone(UTC)
    with _cli_errors(), tenant_session(TenantContext.platform(_cli_actor())) as s:
        sources = s.scalars(select(ListSource).order_by(ListSource.code)).all()
        active = {
            version.list_code: version
            for version in s.scalars(
                select(ListVersion).where(ListVersion.status == VersionStatus.ACTIVE)
            )
        }
        lines = [_status_line(src, active.get(src.code), now) for src in sources]
    if not lines:
        typer.echo("no list sources; run xolotl lists seed")
        return
    for line in lines:
        typer.echo(line)


def _load_line(report: IngestReport, active: ListVersion | None) -> str:
    if report.same_file:
        seq = "?" if active is None else active.seq
        return f"{report.list_code}: unchanged (same file as v{seq})"
    return (
        f"{report.list_code} v{report.seq}: {report.entry_count} entries "
        f"({report.added} added, {report.changed} changed, {report.removed} removed, "
        f"{report.unchanged} unchanged) [{report.parser_version}]"
    )


# The refusals ingest_file raises before it stores the raw file.
_REFUSED_BEFORE_PUT = (NotFoundError, ListFormatError, ListMassRemovalError)


@lists_app.command("load-file")
def lists_load_file(
    code: Annotated[str, typer.Argument(help="List code, such as UN_CONSOLIDATED")],
    path: Annotated[
        Path,
        typer.Argument(
            exists=True, dir_okay=False, readable=True, help="The published file"
        ),
    ],
    *,
    accept_drop: Annotated[
        bool,
        typer.Option(
            "--accept-drop",
            help="Accept a version that removes more entries than the guard allows",
        ),
    ] = False,
) -> None:
    """Ingest a list file from disk as the list's next version (offline path).

    The raw file goes to the object store under the shared namespace. A file equal
    to the active version's makes no version. A malformed file, or one that removes
    more than ``XOLOTL_LISTS_MAX_REMOVAL_RATIO`` of the entries without
    ``--accept-drop``, exits 1 with one line on stderr and changes nothing; an
    accepted mass removal is logged as a WARNING naming the operator.
    """
    actor = _cli_actor()
    data = path.read_bytes()
    settings = get_settings()
    with _cli_errors():
        store = build_object_store(settings)
        try:
            with tenant_session(TenantContext.platform(actor)) as s:
                report = ingest_file(
                    s,
                    code,
                    data,
                    store=store,
                    loaded_by=actor,
                    fetched_at=clock.now(),
                    accept_drop=accept_drop,
                )
                line = _load_line(report, active_versions(s).get(code))
        except Exception as exc:
            if not isinstance(exc, _REFUSED_BEFORE_PUT):
                logger.exception(
                    "loading %s failed; a raw file under lists/%s/ %s may be orphaned",
                    code,
                    code,
                    "in the shared namespace",
                )
            raise
    before = report.removed + report.changed + report.unchanged
    mass_removal = (
        before > 0
        and Decimal(report.removed) / before > settings.lists_max_removal_ratio
    )
    if accept_drop and not report.same_file and mass_removal:
        logger.warning(
            "%s accepted a mass removal on %s v%s: removed %s of %s",
            actor,
            code,
            report.seq,
            report.removed,
            before,
        )
    typer.echo(line)


def _fetch_line(outcome: PollOutcome) -> str:
    report = outcome.report
    if outcome.result == "NOT_MODIFIED" or report is None:
        return f"{outcome.code}: not modified"
    if report.same_file:
        return f"{outcome.code}: unchanged (same file as the active version)"
    return (
        f"{outcome.code}: ingested v{report.seq} ({report.added} added, "
        f"{report.changed} changed, {report.removed} removed)"
    )


def _lease_holder(session: Session, code: str) -> str:
    """Name who holds the lease of a source that could not be claimed.

    Returns:
        The lease holder.

    Raises:
        NotFoundError: When the source is unknown or disabled.

    """
    source = session.get(ListSource, code)
    if source is None or not source.enabled:
        msg = f"no enabled list source {code}"
        raise NotFoundError(msg)
    return source.lease_holder or "another poll"


@lists_app.command("fetch")
def lists_fetch(
    code: Annotated[str, typer.Argument(help="List code, such as UN_CONSOLIDATED")],
    *,
    force_network: Annotated[
        bool,
        typer.Option(
            "--force-network",
            help="Fetch even when XOLOTL_LISTS_FETCH_ENABLED leaves fetching off",
        ),
    ] = False,
) -> None:
    """Fetch one shared list now, due or not, and ingest it when it changed.

    Takes the source's lease as the poll job does, so it never races a poll; a
    lease held by someone else, an unknown code or a failed fetch exits 1 with one
    line on stderr. Outside prod network fetching is off unless
    ``--force-network`` is given.

    Raises:
        Exit: With code 2 when fetching is off and ``--force-network`` is not
            given; with code 1 when the lease is held or the fetch failed.

    """
    settings = get_settings()
    if not settings.lists_fetching and not force_network:
        refusal = (
            "error: network fetching is off (XOLOTL_LISTS_FETCH_ENABLED); "
            "pass --force-network"
        )
        typer.echo(refusal, err=True)
        raise typer.Exit(code=2)
    actor = _cli_actor()
    now = clock.now()
    with _cli_errors():
        store = build_object_store(settings)
        with tenant_session(TenantContext.platform(actor)) as s:
            claimed = claim_source(s, code, holder=actor, now=now)
            holder = None if claimed is not None else _lease_holder(s, code)
    if holder is not None:
        typer.echo(f"error: {code} is being fetched by {holder}", err=True)
        raise typer.Exit(code=1)
    fetcher = HttpListFetcher(
        timeout_seconds=settings.lists_fetch_timeout_seconds,
        max_bytes=settings.lists_max_file_mb * 1024 * 1024,
    )
    with _cli_errors():
        outcome = poll_source(
            get_sessionmaker(),
            code,
            holder=actor,
            fetcher=fetcher,
            store=store,
            now=now,
        )
    if outcome.result == "FAILED":
        typer.echo(f"error: {code}: failed {outcome.error}", err=True)
        raise typer.Exit(code=1)
    typer.echo(_fetch_line(outcome))


def _when(at: datetime | None) -> str:
    return "-" if at is None else at.astimezone(UTC).strftime("%Y-%m-%dT%H:%MZ")


def _shown_version(session: Session, code: str, seq: int | None) -> ListVersion:
    """Find the active version of a list, or the numbered one.

    Returns:
        The version row.

    Raises:
        NotFoundError: When there is no such version.

    """
    if seq is None:
        active = active_versions(session).get(code)
        if active is None:
            msg = f"no active {code} version"
            raise NotFoundError(msg)
        return active
    row = session.scalar(
        select(ListVersion).where(ListVersion.list_code == code, ListVersion.seq == seq)
    )
    if row is None:
        msg = f"no {code} version {seq}"
        raise NotFoundError(msg)
    return row


@lists_app.command("show")
def lists_show(
    code: Annotated[str, typer.Argument(help="List code, such as UN_CONSOLIDATED")],
    *,
    version: Annotated[
        int | None, typer.Option("--version", help="The version number; default active")
    ] = None,
) -> None:
    """Print one version of a shared list: provenance, parser, hash and counts."""
    with (
        _cli_errors(),
        tenant_session(TenantContext.platform(_cli_actor())) as s,
    ):
        row = _shown_version(s, code, version)
        lines = [
            f"{code} v{row.seq}  {row.status}",
            f"published  {_when(row.published_at)}",
            f"fetched    {_when(row.fetched_at)}",
            f"loaded by  {row.loaded_by}",
            f"parser     {row.parser_version}",
            f"sha256     {row.file_sha256.hex()[:12]}",
            (
                f"entries    {row.entry_count} ({row.added} added, "
                f"{row.changed} changed, {row.removed} removed)"
            ),
        ]
    for line in lines:
        typer.echo(line)


def _ensure_index_holder() -> None:
    """Install a screening index holder if this process has none yet.

    The API and the worker install theirs in ``wire_platform``; a command that
    screens runs in a process that never wires the platform. The holder loads the
    shared lists on the first screen.
    """
    try:
        _ = get_index_holder()
    except RuntimeError:
        settings = get_settings()
        set_index_holder(
            ScreeningIndexHolder(
                get_sessionmaker(),
                refresh_seconds=settings.screening_index_refresh_seconds,
                private_shards=settings.screening_private_shards,
            )
        )


@screening_app.command("check")
def screening_check(
    *,
    tenant: Annotated[str, typer.Option("--tenant", help="Tenant slug")],
    party: Annotated[UUID, typer.Option("--party", help="Party id")],
) -> None:
    """Screen one party now against the tenant's lists."""
    actor = _cli_actor()
    with _cli_errors():
        tenant_id, _ = _resolve_tenant(actor, tenant)
        _ensure_key_provider()
        _ensure_index_holder()
        with tenant_session(TenantContext.platform(actor, tenant_id=tenant_id)) as s:
            subject = subject_for_party(s, party, for_update=True)
            run = screen(
                s,
                subject,
                mode=RunMode.AD_HOC,
                trigger=Trigger(TriggerType.CLI, actor),
            ).run
            line = (
                f"run {run.id}: {run.outcome} ({run.hit_count} hits, "
                f"{run.new_match_count} new findings)"
            )
    typer.echo(line)


def _replay_lines(report: ReplayReport) -> list[str]:
    lines = [
        " ".join((
            row.list_code,
            row.entry_key,
            str(row.stored_score),
            row.stored_band,
            "->",
            str(row.replayed_score),
            row.replayed_band,
            "same" if row.same else "DIFFERENT",
        ))
        for row in report.rows
    ]
    if report.subject_changed:
        lines.append("subject changed")
    elif report.identical:
        lines.append("identical")
    else:
        lines.append(f"{sum(not row.same for row in report.rows)} differences")
    return lines


@screening_app.command("replay")
def screening_replay(
    run_id: Annotated[UUID, typer.Argument(help="The screening run id")],
    *,
    tenant: Annotated[str, typer.Option("--tenant", help="Tenant slug")],
) -> None:
    """Re-score a run's hits and say whether they are identical (NFR-SC-007).

    Raises:
        Exit: With code 1 when the subject changed or a hit replays differently.

    """
    actor = _cli_actor()
    with _cli_errors():
        tenant_id, _ = _resolve_tenant(actor, tenant)
        _ensure_key_provider()
        with tenant_session(TenantContext.platform(actor, tenant_id=tenant_id)) as s:
            report = replay_run(s, run_id)
    for line in _replay_lines(report):
        typer.echo(line)
    if not report.identical:
        raise typer.Exit(code=1)


@screening_app.command("rescreen")
def screening_rescreen(
    *,
    tenant: Annotated[str, typer.Option("--tenant", help="Tenant slug")],
    kind: Annotated[
        str, typer.Option("--kind", help="PERIODIC or REINDEX")
    ] = "PERIODIC",
) -> None:
    """Start a full re-screen of the tenant's parties; the worker runs it.

    Raises:
        ValidationFailedError: For a kind other than PERIODIC or REINDEX.

    """
    actor = _cli_actor()
    with _cli_errors():
        try:
            batch_kind = BatchKind(kind.strip().upper())
        except ValueError:
            msg = "kind must be PERIODIC or REINDEX"
            raise ValidationFailedError(msg) from None
        tenant_id, slug = _resolve_tenant(actor, tenant)
        factory = get_sessionmaker()
        with tenant_session(TenantContext.platform(actor, tenant_id=tenant_id)) as s:
            batch_id = start_rescreen(
                s,
                PostgresJobQueue(factory),
                kind=batch_kind,
                trigger=Trigger(TriggerType.CLI, actor),
            )
    if batch_id is None:
        typer.echo("nothing to do")
        return
    typer.echo(f"batch {batch_id} started for tenant {slug}")


def _ratio(value: Decimal | None) -> str:
    return "-" if value is None else str(value)


def _eval_lines(report: evaluation_report.EvaluationReportResponse) -> list[str]:
    lines = [
        (
            f"corpus {report.corpus_pairs} pairs, {report.hard_negatives} hard "
            f"negatives, matcher {report.matcher_version}"
        )
    ]
    for kind in report.kinds:
        lines.extend((
            f"{kind.list_kind} pairs {kind.pairs}",
            f"{kind.list_kind} recall_review {kind.recall_review}",
            f"{kind.list_kind} precision_alert {_ratio(kind.precision_alert)}",
            f"{kind.list_kind} retrieval_recall {kind.retrieval_recall}",
        ))
    negatives = (
        evaluation_report.ALL_KINDS,
        evaluation_report.NEGATIVE_ALERTS_METRIC,
        str(report.alerts_per_1000_negatives),
    )
    lines.append(" ".join(negatives))
    lines.extend(
        f"FLOOR {f.metric} {f.list_kind} {f.floor} {f.value} "
        + ("OK" if f.met else "BELOW")
        for f in report.floors
    )
    return lines


@screening_app.command("eval")
def screening_eval(
    *,
    corpus: Annotated[
        Path, typer.Option("--corpus", help="The evaluation corpus directory")
    ] = EVALUATION_CORPUS,
    write: Annotated[
        bool,
        typer.Option("--write", help="Write the report the build ships"),
    ] = False,
) -> None:
    """Evaluate the matcher on the corpus and check its floors (FR-SC-014).

    Raises:
        Exit: With code 1 when the corpus is missing or a floor is not met.

    """
    with _cli_errors():
        report = evaluation_report.evaluate_corpus(corpus, generated_at=clock.now())
    for line in _eval_lines(report):
        typer.echo(line)
    if not all(floor.met for floor in report.floors):
        if write:
            typer.echo(
                "error: FLOOR_BELOW: refusing to write a report below a floor",
                err=True,
            )
        raise typer.Exit(code=1)
    if write:
        target = evaluation_report.SHIPPED_REPORT
        _ = target.write_text(
            evaluation_report.canonical_json(report), encoding="utf-8"
        )
        typer.echo(f"wrote {target}")
