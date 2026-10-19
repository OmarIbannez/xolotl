"""The composition roots keep one connector router on ``PlatformServices``."""

from typing import TYPE_CHECKING, cast

import pytest
from fastapi import FastAPI
from sqlalchemy import Engine

from xolotl.app import build_app, wire_platform
from xolotl.platform.config import get_settings
from xolotl.platform.connectors import ConnectorMode, ConnectorRouter
from xolotl.platform.db import get_sessionmaker

if TYPE_CHECKING:
    from xolotl.platform.api.app import AppState


def state_of(app: FastAPI) -> "AppState":
    return cast("AppState", app.state.xolotl)


def test_the_app_is_wired_with_a_connector_router_that_follows_the_settings(
    engine: Engine, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("XOLOTL_CONNECTOR_MODE_DEFAULT", "LIVE")
    get_settings.cache_clear()
    try:
        app = build_app()
    finally:
        get_settings.cache_clear()
    services = state_of(app).services
    assert services is not None
    router = services.connectors
    assert isinstance(router, ConnectorRouter)
    assert router.default_mode is ConnectorMode.LIVE
    assert [c.name for c in router.connectors] == []


def test_the_worker_process_is_wired_with_the_same_router(engine: Engine):
    services = wire_platform(get_sessionmaker(), get_settings())
    assert isinstance(services.connectors, ConnectorRouter)
    assert services.connectors.default_mode is ConnectorMode.SANDBOX
    assert [c.name for c in services.connectors.connectors] == []
