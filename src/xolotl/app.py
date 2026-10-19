"""Composition root: binds adapters to ports and mounts the module routers.

``build_app`` is what ``xolotl serve`` runs (as a uvicorn factory) and what the
tests build. Each later task registers its router here. ``wire_platform`` composes
the platform's services once per process, for the API and for ``xolotl worker``
(``build_worker``) alike; the API also runs the worker loop on a thread of its own
unless ``XOLOTL_EMBEDDED_WORKER`` is false.
"""

import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import cast

from fastapi import FastAPI
from starlette.concurrency import run_in_threadpool
from starlette.types import Lifespan

from xolotl.modules.party import routes as party_routes
from xolotl.modules.tenants import (
    routes_api_keys,
    routes_auth,
    routes_connectors,
    routes_notifications,
    routes_staff,
    routes_tenants,
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
    tenant created before one of them existed gets it too.

    Returns:
        The loop, ticking every ``XOLOTL_WORKER_TICK_SECONDS``.

    """

    def declare_platform_schedules() -> int:
        return ensure_platform_schedules(services.session_factory, services.queue)

    return Worker(
        bus=services.bus,
        runner=services.job_runner,
        scheduler=services.scheduler,
        tick_seconds=settings.worker_tick_seconds,
        on_start=declare_platform_schedules,
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
            routes_connectors.router,
            party_routes.router,
            audit_routes.router,
        ],
    )
    # Last, so that the console's catch-all never shadows a route above.
    _ = mount_console(app, settings.static_dir)
    return app
