"""``xolotl keys`` under a master key other than the one a tenant's keys were made with.

A command line is a process of its own: it reads ``XOLOTL_MASTER_KEY`` from its own
environment. A mistake there (a sandbox shell, a stale ``.env``) must not leave a key
version that the servers cannot read, so rotating refuses, and writes nothing.
"""

import base64
from collections.abc import Generator
from contextlib import contextmanager
from uuid import UUID

import pytest
from sqlalchemy import Engine
from typer.testing import CliRunner

from tests.helpers import audit_events, key_states, make_tenant_with_keys
from xolotl.cli import app
from xolotl.platform.config import get_settings
from xolotl.platform.crypto import KeyDestroyedError, LocalKeyProvider, set_key_provider
from xolotl.platform.db import get_sessionmaker
from xolotl.platform.enums import KeyPurpose

runner = CliRunner()
OTHER_MASTER_KEY = base64.b64encode(bytes(range(100, 132))).decode()
MISMATCH = (
    "error: KEK_MISMATCH: "
    "the keys of this tenant were wrapped under a different master key\n"
)


@contextmanager
def another_master_key(monkeypatch: pytest.MonkeyPatch) -> Generator[None]:
    # The environment of a new process; settings are read once per process.
    monkeypatch.setenv("XOLOTL_MASTER_KEY", OTHER_MASTER_KEY)
    get_settings.cache_clear()
    set_key_provider(None)  # nothing has installed a provider yet
    try:
        yield
    finally:
        monkeypatch.undo()
        get_settings.cache_clear()
        set_key_provider(None)


def _tenant_with_keys(slug: str = "acme") -> UUID:
    # Made the way the servers make them: with the configured master key.
    provider = LocalKeyProvider(get_settings().master_key_bytes, get_sessionmaker())
    return make_tenant_with_keys(slug, provider=provider)


def _rotate(*, purpose: str = "DATA"):
    return runner.invoke(
        app, ["keys", "rotate", "--tenant", "acme", "--purpose", purpose]
    )


def test_keys_rotate_under_another_master_key_refuses_and_writes_nothing(
    no_key_provider: None, admin_engine: Engine, monkeypatch: pytest.MonkeyPatch
):
    tenant_id = _tenant_with_keys()
    before = key_states(admin_engine, tenant_id)
    with another_master_key(monkeypatch):
        refused = _rotate()
    assert refused.exit_code == 1
    assert refused.output == MISMATCH
    assert key_states(admin_engine, tenant_id) == before  # no version 2, no retirement
    assert audit_events(tenant_id) == []  # nothing happened, so nothing is audited
    # With the right master key the same command rotates, and it is version 2.
    set_key_provider(None)
    done = _rotate()
    assert done.exit_code == 0, done.output
    assert done.output == "rotated the DATA key of tenant acme: now version 2\n"


def test_keys_rotate_under_another_master_key_refuses_every_purpose(
    no_key_provider: None, admin_engine: Engine, monkeypatch: pytest.MonkeyPatch
):
    tenant_id = _tenant_with_keys()
    before = key_states(admin_engine, tenant_id)
    with another_master_key(monkeypatch):
        refused = [_rotate(purpose=purpose.value) for purpose in KeyPurpose]
    assert [result.exit_code for result in refused] == [1, 1, 1]
    assert refused[0].output == refused[2].output == MISMATCH
    # INDEX is refused for its own reason, before any key is looked at.
    assert "INDEX keys are never rotated" in refused[1].output
    assert key_states(admin_engine, tenant_id) == before


def test_keys_destroy_needs_no_master_key(
    no_key_provider: None, admin_engine: Engine, monkeypatch: pytest.MonkeyPatch
):
    # A lost or wrong master key must not stand in the way of shredding a tenant.
    tenant_id = _tenant_with_keys()
    with another_master_key(monkeypatch):
        result = runner.invoke(app, ["keys", "destroy", "--tenant", "acme", "--yes"])
    assert result.exit_code == 0, result.output
    assert {state for _, _, state in key_states(admin_engine, tenant_id)} == {
        "destroyed"
    }
    right = LocalKeyProvider(get_settings().master_key_bytes, get_sessionmaker())
    with pytest.raises(KeyDestroyedError):
        _ = right.data_key(tenant_id, KeyPurpose.DATA)
