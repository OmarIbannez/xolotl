"""FastAPI application factory carrying the kernel conventions (spec §7)."""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import cast

from fastapi import APIRouter, FastAPI, Request
from fastapi.routing import APIRoute

from xolotl import __version__
from xolotl.platform.api.health import router as health_router
from xolotl.platform.api.problems import install_problem_handlers
from xolotl.platform.api.request_id import RequestIdMiddleware
from xolotl.platform.auth.actor import ActorResolver
from xolotl.platform.db import SessionFactory
from xolotl.platform.storage import ObjectStore


@dataclass(slots=True)
class AppState:
    session_factory: SessionFactory
    actor_resolver: ActorResolver | None = None
    object_store: ObjectStore | None = None


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
    routers: Sequence[APIRouter] = (),
) -> FastAPI:
    app = FastAPI(
        title="xolotl",
        version=__version__,
        openapi_url="/v1/openapi.json",
        docs_url="/v1/docs",
        redoc_url=None,
        generate_unique_id_function=_operation_id,
    )
    app.state.xolotl = AppState(
        session_factory=session_factory,
        actor_resolver=actor_resolver,
        object_store=object_store,
    )
    app.add_middleware(RequestIdMiddleware)
    install_problem_handlers(app)
    app.include_router(health_router)
    for router in routers:
        app.include_router(router, prefix="/v1")
    return app
