"""FastAPI application factory carrying the kernel conventions (spec §7)."""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import cast

from fastapi import APIRouter, FastAPI, Request
from fastapi.routing import APIRoute
from starlette.types import Lifespan

from xolotl import __version__
from xolotl.platform.api.health import router as health_router
from xolotl.platform.api.problems import install_problem_handlers
from xolotl.platform.api.request_id import RequestIdMiddleware
from xolotl.platform.auth.actor import ActorResolver
from xolotl.platform.db import SessionFactory
from xolotl.platform.services import PlatformServices
from xolotl.platform.storage import ObjectStore


@dataclass(slots=True)
class AppState:
    session_factory: SessionFactory
    actor_resolver: ActorResolver | None = None
    object_store: ObjectStore | None = None
    # The bus, the queue, the workflow runner and the notifier the routes reach,
    # composed once for the process (xolotl.app.wire_platform).
    services: PlatformServices | None = None
    # Whether the embedded worker's thread still runs, for /readyz; None until the
    # app has started one, and always with XOLOTL_EMBEDDED_WORKER=false.
    embedded_worker_alive: Callable[[], bool] | None = None


def app_state(request: Request) -> AppState:
    app = cast("FastAPI", request.app)
    return cast("AppState", app.state.xolotl)


def _operation_id(route: APIRoute) -> str:
    return route.name


def create_app(
    *,
    session_factory: SessionFactory,
    actor_resolver: ActorResolver | None = None,
    object_store: ObjectStore | None = None,
    services: PlatformServices | None = None,
    lifespan: Lifespan[FastAPI] | None = None,
    routers: Sequence[APIRouter] = (),
) -> FastAPI:
    app = FastAPI(
        title="xolotl",
        version=__version__,
        openapi_url="/v1/openapi.json",
        docs_url="/v1/docs",
        redoc_url=None,
        generate_unique_id_function=_operation_id,
        lifespan=lifespan,
    )
    app.state.xolotl = AppState(
        session_factory=session_factory,
        actor_resolver=actor_resolver,
        object_store=object_store,
        services=services,
    )
    app.add_middleware(RequestIdMiddleware)
    install_problem_handlers(app)
    app.include_router(health_router)
    for router in routers:
        app.include_router(router, prefix="/v1")
    return app
