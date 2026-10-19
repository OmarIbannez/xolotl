"""The composition root installs the key provider that the configuration names.

``xolotl.app`` is where adapters meet ports: the object store is picked there, and so
is the key provider. The application and the command line both go through it.
"""

import base64

from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from tests.helpers import data_key_of, make_tenant
from xolotl.app import build_app, build_key_provider
from xolotl.platform.config import Settings
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
