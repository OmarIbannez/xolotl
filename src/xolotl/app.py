"""Composition root: binds adapters to ports and mounts the module routers.

``build_app`` is what ``xolotl serve`` runs (as a uvicorn factory) and what the
tests build. Each later task registers its router here.
"""

from fastapi import FastAPI

from xolotl.modules.tenants import (
    routes_api_keys,
    routes_auth,
    routes_staff,
    routes_tenants,
)
from xolotl.modules.tenants.service import resolve_bearer
from xolotl.platform.api.app import create_app
from xolotl.platform.audit import routes as audit_routes
from xolotl.platform.config import get_settings
from xolotl.platform.db import SessionFactory, get_sessionmaker
from xolotl.platform.logging import configure_logging


def build_app(session_factory: SessionFactory | None = None) -> FastAPI:
    configure_logging(get_settings().log_level)
    return create_app(
        session_factory=session_factory or get_sessionmaker(),
        actor_resolver=resolve_bearer,
        routers=[
            routes_auth.router,
            routes_tenants.router,
            routes_api_keys.router,
            routes_staff.router,
            audit_routes.router,
        ],
    )
