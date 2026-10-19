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
from xolotl.platform.config import Settings, get_settings
from xolotl.platform.crypto.provider import (
    KeyProvider,
    LocalKeyProvider,
    set_key_provider,
)
from xolotl.platform.db import SessionFactory, get_sessionmaker
from xolotl.platform.logging import configure_logging
from xolotl.platform.storage import ObjectStore
from xolotl.platform.storage.local import LocalFsObjectStore


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


def build_app(session_factory: SessionFactory | None = None) -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level)
    factory = session_factory or get_sessionmaker()
    set_key_provider(build_key_provider(settings, factory))
    return create_app(
        session_factory=factory,
        actor_resolver=resolve_bearer,
        object_store=build_object_store(settings),
        routers=[
            routes_auth.router,
            routes_tenants.router,
            routes_api_keys.router,
            routes_staff.router,
            audit_routes.router,
        ],
    )
