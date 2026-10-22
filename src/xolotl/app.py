"""Composition root: binds adapters to ports and mounts the module routers.

``build_app`` is what ``xolotl serve`` runs (as a uvicorn factory) and what the
tests build. Each later task registers its router here. ``wire_platform`` composes
the platform's services once per process, for the API and for ``xolotl worker``
(``build_worker``) alike; the API also runs the worker loop on a thread of its own
unless ``XOLOTL_EMBEDDED_WORKER`` is false.
"""

import logging
from collections.abc import AsyncGenerator, Collection
from contextlib import asynccontextmanager
from datetime import datetime
from typing import cast
from uuid import UUID

from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool
from starlette.types import Lifespan

from xolotl.modules.monitoring import routes_captures as capture_routes
from xolotl.modules.monitoring import routes_decide as decide_routes
from xolotl.modules.monitoring import routes_hits as hit_routes
from xolotl.modules.monitoring import routes_monitoring as monitoring_routes
from xolotl.modules.monitoring import routes_transactions as transaction_routes
from xolotl.modules.monitoring.composition import register_monitoring
from xolotl.modules.monitoring.rules.wiring import register_rules
from xolotl.modules.party import routes as party_routes
from xolotl.modules.party import routes_documents as party_document_routes
from xolotl.modules.party import routes_relationships as party_relationship_routes
from xolotl.modules.party.retention import register_party
from xolotl.modules.screening import routes_counterparties as counterparty_routes
from xolotl.modules.screening import routes_lists as lists_routes
from xolotl.modules.screening import routes_private_lists as private_list_routes
from xolotl.modules.screening import routes_screening as screening_routes
from xolotl.modules.screening.api import party_view
from xolotl.modules.screening.catalog.fetcher import HttpListFetcher
from xolotl.modules.screening.catalog.poll import register_lists
from xolotl.modules.screening.composition import register_screening
from xolotl.modules.screening.engine.enums import SubjectStatus
from xolotl.modules.screening.engine.holder import get_index_holder
from xolotl.modules.screening.engine.models import ScreeningSubject
from xolotl.modules.screening.rescreen import (
    ensure_screening_index,
    register_rescreen,
)
from xolotl.modules.tenants import (
    routes_api_keys,
    routes_auth,
    routes_connectors,
    routes_notifications,
    routes_policy,
    routes_staff,
    routes_tenants,
    routes_units,
    routes_webhooks,
)
from xolotl.modules.tenants.service import ensure_platform_schedules, resolve_bearer
from xolotl.platform.api.app import AppState, create_app
from xolotl.platform.api.idempotency import PURGE_KIND, purge_expired_keys
from xolotl.platform.api.static import mount_console
from xolotl.platform.audit import routes as audit_routes
from xolotl.platform.config import Settings, get_settings
from xolotl.platform.connectors import default_router
from xolotl.platform.crypto.provider import (
    KeyProvider,
    LocalKeyProvider,
    set_key_provider,
)
from xolotl.platform.db import SessionFactory, get_sessionmaker
from xolotl.platform.events import InAppNotifier, OutboxEventBus
from xolotl.platform.events.webhooks import register_webhooks
from xolotl.platform.jobs import (
    JobRunner,
    PostgresJobQueue,
    Scheduler,
    register_job_handler,
)
from xolotl.platform.logging import configure_logging
from xolotl.platform.services import PlatformServices
from xolotl.platform.storage import ObjectStore
from xolotl.platform.storage.local import LocalFsObjectStore
from xolotl.platform.units import BanxicoSieSource, register_units
from xolotl.platform.workflows import PostgresWorkflowRunner
from xolotl.worker import EmbeddedWorker, Worker

logger = logging.getLogger(__name__)


def party_screening_status(s: Session, party_id: UUID) -> str | None:
    """Read a party's screening status for the rules' profile (SP-04 §9.5).

    Monitoring may not import the screening engine, so the composition root hands
    this reader to ``register_rules``. Only the status is read, which the staff
    and client views share, so the cheaper client view is built.

    Returns:
        The ``SubjectStatus`` value, or None when the party was never screened.

    """
    status = party_view(s, party_id, staff=False).status
    return None if status is None else status.value


def party_true_match_at(
    s: Session, party_ids: Collection[UUID]
) -> dict[UUID, datetime]:
    """Read when the parties whose screening status is ``TRUE_MATCH`` were screened.

    A rule simulation counts the parties matched after its simulated hits
    (SP-04 §13.1), a page of parties at a time; monitoring may not import the
    screening engine. It is the last screening, not the one that first matched,
    so a party matched long ago and screened again since counts as later.

    Returns:
        The last screening's instant of each of ``party_ids`` that is a true
        match; the others are left out.

    """
    rows = s.execute(
        select(ScreeningSubject.party_id, ScreeningSubject.last_screened_at).where(
            ScreeningSubject.party_id.in_(list(party_ids)),
            ScreeningSubject.status == SubjectStatus.TRUE_MATCH,
            ScreeningSubject.last_screened_at.is_not(None),
        )
    )
    return {
        party_id: screened_at
        for party_id, screened_at in rows
        if party_id is not None and screened_at is not None
    }


def register_processing(*, queue: PostgresJobQueue) -> None:
    """Register what processing runs on each row beyond its aggregates.

    ``wire_platform`` calls it, and so do the CLI commands that drain rows in
    their own process (``monitoring process`` and ``monitoring backfill``), so a
    row is evaluated the same way wherever it is processed. Plan 4b's captures
    hook (B10) is registered from ``register_rules`` too.
    """
    register_rules(
        queue=queue,
        screening_status=party_screening_status,
        true_match=party_true_match_at,
    )


def build_object_store(settings: Settings) -> ObjectStore:
    """Pick the object store adapter that ``XOLOTL_OBJECT_STORE`` names.

    Returns:
        A store rooted at ``XOLOTL_OBJECT_STORE_ROOT``.

    Raises:
        RuntimeError: When the setting names a store this build has no adapter for.
            Settings validation already refuses that; the check keeps a new value
            from silently falling back to the local store until its adapter exists.

    """
    # Read as text on purpose: the setting's Literal has one member today, and the
    # RuntimeError below is for the member added before its adapter is.
    kind = str(settings.object_store)
    if kind == "local":
        return LocalFsObjectStore(settings.object_store_root)
    msg = f"unsupported XOLOTL_OBJECT_STORE: {kind!r}"
    raise RuntimeError(msg)


def build_key_provider(
    settings: Settings, session_factory: SessionFactory
) -> KeyProvider:
    """Build the key provider the configuration names.

    ``XOLOTL_MASTER_KEY`` wraps every tenant's data keys. The provider opens its short
    sessions through ``session_factory``. Every process that creates or opens encrypted
    data installs one with ``set_key_provider``: the application in ``build_app``, a
    command that creates tenants in its own entry point.

    Returns:
        A local provider, the only adapter so far, keyed by the master key.

    """
    return LocalKeyProvider(settings.master_key_bytes, session_factory)


def wire_platform(
    session_factory: SessionFactory, settings: Settings
) -> PlatformServices:
    """Compose the platform's services and register who handles which job and event.

    The API (``build_app``) and the worker process (``build_worker``) both call it,
    so both know the same handlers. Handlers and subscribers live in process-wide
    registries keyed by job kind and by (event type, consumer), and registering a
    key again replaces it: building another app in the same process, as the tests
    do, leaves one handler per kind and one subscription per consumer.

    Returns:
        The services, every one opening its sessions through ``session_factory``.

    """
    queue = PostgresJobQueue(session_factory)
    bus = OutboxEventBus(session_factory)
    workflows = PostgresWorkflowRunner(session_factory, queue)
    # workflow.advance, workflow.timer and the hourly workflow.sweep.
    workflows.register_job_handlers()
    register_job_handler(PURGE_KIND, purge_expired_keys)
    # The webhook fan-out (consumer "webhooks", for every event type) and the
    # webhook.deliver job, so whichever process dispatches an event or runs a job
    # (the API's embedded worker, or `xolotl worker`) reaches the tenants' endpoints.
    register_webhooks(bus, queue, settings=settings)
    # One store for the routes and the connectors' evidence alike.
    store = build_object_store(settings)
    # The daily party.retention_sweep job purges parties whose retention ended and
    # deletes their documents from this store.
    register_party(object_store=store)
    # The daily units.fetch job: Banxico SIE with a token, else the seed files
    # (demo mode, refused in prod, where the job notifies ADMIN and MLRO instead).
    _ = register_units(
        source=BanxicoSieSource(
            settings.banxico_token,
            seed_dir=settings.packs_dir / "_units",
            env=settings.env,
        ),
        notifier=InAppNotifier(),
    )
    # The per-minute lists.poll job: subscriptions from the effective policy, the
    # catch-up events, and the stale-source and missing-LPB notices.
    # The shared half fetches at most one due source per run under a lease, only
    # when network fetching is on (XOLOTL_LISTS_FETCH_ENABLED, by default in prod).
    _ = register_lists(
        session_factory=session_factory,
        notifier=InAppNotifier(),
        fetcher=HttpListFetcher(
            timeout_seconds=settings.lists_fetch_timeout_seconds,
            max_bytes=settings.lists_max_file_mb * 1024 * 1024,
        ),
        store=store,
        fetching=settings.lists_fetching,
    )
    # The staff-only screening events, and this process's in-memory screening
    # index (not warmed here: build_app and build_worker start the warm-up, so
    # the tests that only wire the platform start no thread), and consumer
    # "screening", which re-indexes and re-screens a party on its events.
    _ = register_screening(
        session_factory=session_factory, settings=settings, queue=queue
    )
    # The re-screen batches: list deltas, private-list versions, periodic and
    # re-index walks.
    register_rescreen(queue=queue)
    # The staff-only monitoring events and the monitoring jobs: ingest_file,
    # process, the per-minute sweep, both backfills and the daily partitions.
    register_monitoring(
        session_factory=session_factory,
        settings=settings,
        queue=queue,
        store=store,
        notifier=InAppNotifier(),
    )
    # The rules' staff-only events and their processing hook, which reads the
    # party's screening status through screening's API.
    register_processing(queue=queue)
    return PlatformServices(
        session_factory=session_factory,
        bus=bus,
        queue=queue,
        job_runner=JobRunner(queue, session_factory, worker_id=settings.worker_id),
        scheduler=Scheduler(queue, session_factory),
        workflows=workflows,
        notifier=InAppNotifier(),
        object_store=store,
        connectors=default_router(settings, store=store),
    )


def worker_loop(services: PlatformServices, settings: Settings) -> Worker:
    """Build the worker loop over a process's platform services.

    As it starts, the loop declares every tenant's platform schedules again, so a
    tenant created before one of them existed gets it too, and then opens a
    re-index batch for every tenant whose screening name index is stale.

    Returns:
        The loop, ticking every ``XOLOTL_WORKER_TICK_SECONDS``.

    """

    def start_up() -> tuple[int, int]:
        return (
            ensure_platform_schedules(services.session_factory, services.queue),
            ensure_screening_index(services.session_factory, services.queue),
        )

    return Worker(
        bus=services.bus,
        runner=services.job_runner,
        scheduler=services.scheduler,
        tick_seconds=settings.worker_tick_seconds,
        on_start=start_up,
    )


def build_worker(session_factory: SessionFactory | None = None) -> Worker:
    """Build what ``xolotl worker`` runs: the platform wired for this process.

    Returns:
        The worker loop, not yet started.

    """
    settings = get_settings()
    # Hook point: the process-wide adapters build_app installs (plan 1b's key
    # provider) belong here too, since a worker process builds no app.
    factory = session_factory or get_sessionmaker()
    set_key_provider(build_key_provider(settings, factory))
    services = wire_platform(factory, settings)
    if settings.screening_index_warm:
        _ = get_index_holder().start_warming()
    return worker_loop(services, settings)


def _embedded_worker(
    services: PlatformServices, settings: Settings
) -> Lifespan[FastAPI]:
    # The application's lifespan: uvicorn enters it once the app is built, so after
    # serve's migrations and role check, and leaves it on shutdown.
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
        if not settings.embedded_worker:
            # Events, jobs and schedules then wait for a `xolotl worker` process.
            logger.info("embedded worker off", extra={"embedded_worker": False})
            yield
            return
        embedded = EmbeddedWorker(worker_loop(services, settings))
        embedded.start()
        # /readyz answers 503 should the thread die while the API keeps serving.
        state = cast("AppState", app.state.xolotl)
        state.embedded_worker_alive = embedded.thread.is_alive
        try:
            yield
        finally:
            # A bounded join, off the event loop.
            _ = await run_in_threadpool(embedded.stop)

    return lifespan


def build_app(session_factory: SessionFactory | None = None) -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level)
    factory = session_factory or get_sessionmaker()
    set_key_provider(build_key_provider(settings, factory))
    # Hook point: process-wide adapters (plan 1b's key provider) are installed here,
    # before the platform is wired and anything is served.
    services = wire_platform(factory, settings)
    # The screening index loads in the background; screens answer 503 until then.
    if settings.screening_index_warm:
        _ = get_index_holder().start_warming()
    app = create_app(
        session_factory=factory,
        actor_resolver=resolve_bearer,
        object_store=services.object_store,
        services=services,
        lifespan=_embedded_worker(services, settings),
        routers=[
            routes_auth.router,
            routes_tenants.router,
            routes_api_keys.router,
            routes_staff.router,
            routes_notifications.router,
            routes_webhooks.router,
            routes_policy.router,
            routes_units.router,
            routes_connectors.router,
            party_routes.router,
            party_document_routes.router,
            party_relationship_routes.router,
            lists_routes.router,
            private_list_routes.router,
            screening_routes.router,
            counterparty_routes.router,
            # Before the transactions router: /transactions/decide is literal.
            decide_routes.router,
            transaction_routes.router,
            monitoring_routes.router,
            hit_routes.router,
            hit_routes.party_router,
            capture_routes.router,
            audit_routes.router,
        ],
    )
    # Last, so that the console's catch-all never shadows a route above.
    _ = mount_console(app, settings.static_dir)
    return app
