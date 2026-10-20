"""The composition root installs the key provider that the configuration names.

``xolotl.app`` is where adapters meet ports: the object store is picked there, and so
is the key provider. The application, the standalone worker and the command line all
go through it.
"""

import base64
import threading
from collections.abc import Callable, Iterator

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from tests.helpers import data_key_of, make_tenant
from xolotl.app import build_app, build_key_provider, build_worker
from xolotl.modules.screening.engine.holder import (
    ScreeningIndexHolder,
    get_index_holder,
)
from xolotl.platform.config import Settings, get_settings
from xolotl.platform.crypto.provider import LocalKeyProvider, get_key_provider
from xolotl.platform.db import get_sessionmaker
from xolotl.platform.enums import KeyPurpose

MASTER = bytes(range(64, 96))


def test_the_provider_is_keyed_by_the_configured_master_key(
    engine: Engine, admin_engine: Engine
):
    settings = Settings(master_key=SecretStr(base64.b64encode(MASTER).decode()))
    provider = build_key_provider(settings, get_sessionmaker())
    assert isinstance(provider, LocalKeyProvider)
    tenant_id = make_tenant("alpha")
    provider.create_tenant_keys(tenant_id)
    _, dek = provider.data_key(tenant_id, KeyPurpose.DATA)
    assert data_key_of(admin_engine, tenant_id, kek=MASTER) == dek


def test_build_app_installs_the_provider_and_serving_installs_no_other(
    engine: Engine, admin_engine: Engine, no_key_provider: None
):
    app = build_app()
    installed = get_key_provider()
    assert isinstance(installed, LocalKeyProvider)
    tenant_id = make_tenant("alpha")
    installed.create_tenant_keys(tenant_id)
    _, dek = installed.data_key(tenant_id, KeyPurpose.DATA)
    assert data_key_of(admin_engine, tenant_id) == dek  # the configured master key
    with TestClient(app) as client:
        assert client.get("/healthz").status_code == 200
    assert get_key_provider() is installed  # serving does not install another


def test_build_worker_installs_the_provider_a_process_without_an_app_needs(
    engine: Engine, admin_engine: Engine, no_key_provider: None
):
    # `xolotl worker` builds no app, so build_app never runs there; what it opens
    # (the secrets its webhook deliveries sign with) needs a provider all the same.
    with pytest.raises(RuntimeError):
        _ = get_key_provider()
    _ = build_worker()
    installed = get_key_provider()
    assert isinstance(installed, LocalKeyProvider)
    tenant_id = make_tenant("alpha")
    installed.create_tenant_keys(tenant_id)
    _, dek = installed.data_key(tenant_id, KeyPurpose.DATA)
    assert data_key_of(admin_engine, tenant_id) == dek  # the configured master key


def test_build_worker_reads_keys_through_the_session_factory_it_is_given(
    engine: Engine, no_key_provider: None
):
    opened: list[Session] = []

    def factory() -> Session:
        opened.append(get_sessionmaker()())
        return opened[-1]

    _ = build_worker(session_factory=factory)
    tenant_id = make_tenant("alpha")
    get_key_provider().create_tenant_keys(tenant_id)
    assert len(opened) == 1  # the provider's own short session came from the factory


def test_the_provider_reads_keys_through_the_session_factory_the_app_serves_with(
    engine: Engine, no_key_provider: None
):
    opened: list[Session] = []

    def factory() -> Session:
        opened.append(get_sessionmaker()())
        return opened[-1]

    _ = build_app(session_factory=factory)
    tenant_id = make_tenant("alpha")
    get_key_provider().create_tenant_keys(tenant_id)
    assert len(opened) == 1  # the provider's own short session came from the factory


@pytest.fixture
def warming(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[ScreeningIndexHolder]]:
    """Record the holders whose warm-up starts, without starting a thread.

    Yields:
        The holders, in the order their warm-up started.

    """
    started: list[ScreeningIndexHolder] = []

    def start_warming(holder: ScreeningIndexHolder) -> threading.Thread:
        started.append(holder)
        return threading.Thread(target=lambda: None)

    monkeypatch.setattr(ScreeningIndexHolder, "start_warming", start_warming)
    yield started
    get_settings.cache_clear()


@pytest.mark.parametrize(("flag", "warmed"), [("true", 1), ("false", 0)])
@pytest.mark.parametrize("build", [build_app, build_worker])
def test_the_screening_index_warms_at_start_up_when_the_setting_says_so(
    engine: Engine,
    no_key_provider: None,
    monkeypatch: pytest.MonkeyPatch,
    warming: list[ScreeningIndexHolder],
    build: Callable[[], object],
    flag: str,
    warmed: int,
):
    # The suite turns XOLOTL_SCREENING_INDEX_WARM off (tests/conftest.py); a deployed
    # process leaves it on, and its holder loads on a thread of its own.
    monkeypatch.setenv("XOLOTL_SCREENING_INDEX_WARM", flag)
    get_settings.cache_clear()
    _ = build()
    assert warming == [get_index_holder()] * warmed
